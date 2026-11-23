# Config Drift (D1–D6)

VireoNet detects when a device's running config differs from what the
system of record says it should be, and gives operators a way to
resolve the difference — roll it back, merge it into Git as the new
intent, or grant a time-boxed exception — with RBAC, audit, and
ServiceNow references throughout.

## Concepts

A **drift record** (`drift_records`) is one detected difference on one
device. It carries the device, kind, severity, status, the diff, the
baseline and running hashes, who made the manual change when that can
be proven (`attributed_actor`, best-effort), and the linked ServiceNow
ticket. A **scan run** (`drift_scans`) is one scheduled or triggered
scan over a site. **Decisions** (`drift_decisions`) are append-only:
one row per operator decision. **Audit** (`drift_audit_log`) is
append-only: one row per state move, written by
`drift.state_machine.transition_drift` — a state change without an
audit row must be impossible.

States: `detected -> under_review -> reverting | merge_pending |
exception_active | resolved`; `exception_active ->
exception_expired -> under_review`; `reverting | merge_pending ->
resolved | under_review`. See `src/vireonet/drift/state_machine.py`
(`DRIFT_TRANSITIONS`).

## The three kinds (and where each static reference comes from)

| Kind | Compares | Static reference |
|---|---|---|
| `unmanaged` | Running config hash vs baseline hash | **Golden baseline** = the last committed config after an approved change passed post-check (stored by `ConfigStore`, committed to the per-device repo by `drift.baseline.commit_baseline`; see E3 reconciliation). Equal hashes mean no finding and no diff is computed. |
| `intent` | Rendered intent vs running config | **Intent** = today's render from Git templates + Git vars + NetBox data (see `src/vireonet/intent.py`, `config_store/`). Requires a deterministic render; a non-deterministic render produces false intent drift. |
| `state` | NetBox state vs collected facts, by key | **State** = NetBox (device/interface/VLAN facts as stored there). A key missing on either side is a finding, not a crash. |
| `compliance` | Running config vs rules | Rules from `drift.compliance.load_rules` (only when rules are wired in). |

Detection is `src/vireonet/drift/detect.py` (`DetectionEngine`).
Dedup is by (device, kind, running hash): a repeat finding bumps
`last_seen` instead of creating a duplicate record
(`reconciliation/drift.py: DriftStore.find_existing`). A different
running hash is a different finding. While a change is in progress on
a device, a finding is linked to that change's ticket and does **not**
become a new unmanaged record until the change completes (D3
attribution).

## Ignore rules

Two layers:

1. **File rules** (`policies/drift_ignore.yaml`): volatile config
   lines that must never count as drift (regexes per platform, e.g.
   `Last configuration change`, `ntp clock-period`, `uptime is`).
   Loaded by `drift.normalise.load_ignore_rules`, applied by
   `normalize_for_drift` before hashing. Missing file = no rules.
2. **DB rules** (`drift_ignore_rules` table): operator-defined rules
   via `GET /drift/ignore-rules` (admin) and `PUT /drift/ignore-rules`
   (admin; body `{"rules": [...]}` replaces the whole set). Patterns
   must compile as regexes (422 otherwise). Replacement is logged
   with the actor and rule count — ignore rules have no drift record,
   so there is no `DriftAuditLog` row for them.

Authoring guide: one pattern per line of volatile output; anchor
with `^` where the line is distinctive; keep patterns narrow — a
broad pattern (e.g. `description`) hides real drift. Prefer the file
for platform-wide volatile lines and the DB for one-off suppressions
that an admin can change without a deploy.

## Resolution actions (D4)

All in `src/vireonet/drift/resolve.py`; all API endpoints in
`src/vireonet/api/drift.py`.

| Action | Who | What happens |
|---|---|---|
| Roll back (`POST /drift/{id}/rollback`) | approver/admin | Starts a normal change (`ChangeService.create_config_restore`, change_type `config_restore`, ticket `DRIFT-RB-<short>`): validated → planned → ci_passed (CI recorded as not-applicable — restoring already-approved config has no MR) → approved → pre-check → dry-run → apply → post-check through the ExecutionEngine, with engine rollback on apply/post-check failure. The record goes `reverting`; it resolves only when `confirm_rollback` re-collects the running config and its hash matches the baseline. A failed post-check leaves the record `under_review` (open) and the change `rolled_back`. Idempotent: a record already `reverting` returns the in-flight rollback instead of starting a second change; a retry after failure gets a new ticket (`DRIFT-RB-<short>-2`). |
| Merge (`POST /drift/{id}/merge`) | approver/admin | Creates branch `drift/<device>/<record-short>` and a merge request that adds a per-device vars override at `netops/vars/device/<device>.yaml`. **Never a shared template**: a diff touching `templates/`, `netops/templates/` or `ansible/templates/` is refused. The MR description carries the diff, who/when (attributed actor/at), severity, and the drift record id. The record goes `merge_pending` until the MR-merged webhook (GitLab/GitHub/Gitea handlers in `api/webhooks.py`) or `POST /drift/{id}/mr-merged` arrives: merged → the running config is re-collected and committed as the new baseline (`commit_baseline`), then resolved; closed unmerged → back to `under_review`. Note: `netops/vars/device/<device>.yaml` is a new convention — `VarsStore` does not read per-device overrides yet, so rendering does not pick the override up until it learns the path. |
| Exception (`POST /drift/{id}/exception`) | approver/admin | Keeps the config. Needs owner, reason, ServiceNow reference, and an expiry (max `drift_exception_max_days`, default 30). Detection ignores that exact diff hash until expiry (`is_excepted(device, running_hash)` queries active exception decisions joined to the record's running hash). Changes beyond the approved diff (a different running hash) create a new record. `expire_exceptions()` moves `exception_active -> exception_expired -> under_review` and notifies. |
| Emergency accept (`POST /drift/{id}/emergency-accept`) | operator | Marks the drift emergency-accepted with a mandatory retrospective decision deadline (default `drift_emergency_deadline_hours`, 24h). `check_emergency_deadlines()` raises severity to `critical` (no new severity level is invented) and notifies when the deadline is missed. |

**RBAC and self-approval.** Operator proposes, approver/admin
decides. Whoever made the manual change (when known via
`attributed_actor`) cannot approve their own exception, merge, or
rollback — `check_self_approval()` raises, the API returns 403.
Every action needs a non-empty reason and ServiceNow reference (422
otherwise) and takes the device lock (409 when another ticket holds
it). Every action records a `DriftDecision` and is audited via
`DriftAuditLog`.

**ServiceNow incidents.** Above the severity threshold
(`drift_incident_severity_threshold`, default `high`) the resolver
adds a work note to the record's linked ticket when one exists
(`ensure_incident()`; idempotent per record — the existing ticket is
updated, never a second incident). When the record has no linked
ticket, **no incident is opened**: the ServiceNow adapter has no
incident-creation method (`create_request` / `create_incident` do not
exist on `ServiceNowProtocol` or `ServiceNowAdapter`), so none is
invented — the gap is logged and notified instead. Work notes are
written per decision; a note is added on resolve. `NotificationAdapter`
messages cover new critical drift, pending decisions, expiry soon, and
expired exceptions.

## API (D5)

Base `/drift` (JWT, roles via `require_role`):

| Endpoint | Role | Notes |
|---|---|---|
| `POST /drift/scan` | operator | Starts a DriftScanWorkflow (202) or queues the scan in the DB when no Temporal client can start one. |
| `POST /drift/events` | event token/HMAC, no JWT | Debounced per device; fail-closed 401 without `drift_event_secret`. |
| `GET /drift` | viewer | Filters `device, kind, severity, status, site`; pagination `limit`/`offset`. Site is a device-prefix match (`<site>-<name>`): records carry no site column. |
| `GET /drift/{id}` | viewer | Record + decisions + audit. |
| `GET /drift/summary` | viewer | Counts by severity/kind/status, last scan per site from `DriftScan`, scanned devices, never-scanned devices. Never-scanned is best-effort (see below). |
| `GET /drift/ignore-rules`, `PUT /drift/ignore-rules` | admin | List / replace the rule set. |
| `POST /drift/{id}/rollback|merge|exception` | approver | See the table above. |
| `POST /drift/{id}/emergency-accept` | operator | See above. |
| `POST /drift/{id}/mr-merged` | operator | Fallback for the Git webhook: `{"merged": bool, "branch": "drift/<device>/<short>"}`. |

**Summary, never-scanned.** Scans are per site, not per device, so
"scanned" means "has at least one drift record" and never-scanned is
NetBox devices with no drift record at all. When NetBox is
unavailable the list is empty and `never_scanned_best_effort` is
true. The OpenAPI client is **not** regenerated by script:
`npm run generate:api` overwrites `frontend/src/lib/api-types.ts`
with garbage (E5 finding) — drift types were added to that file by
hand, field-for-field against `/openapi.json`.

## UI (D5)

- **Drift list** (`/drift`): table (device, kind, severity badge,
  status, first seen, last seen, owner, age), URL-synced filters,
  keyboard navigation (Enter on a row opens the record; rows are
  also clickable), CSV export, Start scan.
- **Drift detail** (`/drift/:id`): status timeline; tabs Diff vs
  baseline, Diff vs intent, State findings, Compliance, Batfish
  impact, Attribution, Decisions/Audit. Tabs with no stored data say
  so honestly (e.g. Batfish impact is attached at scan time and this
  record carries none) instead of inventing content. Actions Roll
  back / Merge / Grant exception sit behind confirm dialogs stating
  exactly what will happen, with a required reason field, a required
  ServiceNow reference, and an expiry picker for exceptions.
- **Exceptions** (`/exceptions`): active exceptions with an expiry
  countdown.
- **System health** (`/health`): scan summary — last scan per site,
  never-scanned devices, failures.

## Hardening (D6)

Settings (`src/vireonet/config.py`, `validate_drift_settings`):

- `drift_max_concurrent` (default 5, range 1–50) — scan concurrency.
- `drift_cron` — must be a valid 5-field cron (same simple field
  check as `drift.schedules._valid_cron`; croniter is not a
  dependency).
- `drift_exception_max_days` (30, 1–365), `drift_emergency_deadline_hours`
  (24, 1–168), `drift_incident_severity_threshold` (`high`, one of
  critical|high|medium|low).
- `drift_ignore_path` (`policies/drift_ignore.yaml`) — must exist.
- `drift_severity_policy` (`policies/drift.yaml`) — section names
  must be known sections (`drift.sections.SECTION_NAMES`).
- `drift_compliance_rules` (empty by default) — when set, the file
  must parse and every referenced config-context key/value must be a
  string. Best-effort only: no NetBox schema check is invented.

Real mode refuses to start on any of these; fake/recorded mode only
warns (`main.create_app`). Scale is covered by
`tests/test_d6_scale.py`: 500 fake devices scan under the concurrency
limit (peak in-flight ≤ 5), and a normal change started mid-scan is
not starved of its lock (device locks are per device; a change on an
unlocked device acquires immediately while the scan holds others).

## Runbook

- **New critical drift.** The record appears in `/drift` (severity
  critical) and on System health. Check the Diff vs baseline tab and
  Attribution (who made the change). If the change was a mistake:
  Roll back. If it was intended: Merge (per-device override) or, for
  a short-lived deviation, Exception with an owner and expiry.
- **Expiry soon / expired.** Exceptions list shows the countdown.
  Before expiry, decide: merge the config, roll it back, or grant a
  new exception (a new decision; the old one stays in the audit).
  After expiry the record is `under_review` again and detection
  fires on the next scan.
- **MR closed unmerged.** The record returns to `under_review`
  automatically (webhook or `/mr-merged` with `merged: false`). No
  baseline change happened; decide again.
- **Change-in-progress suppression.** Drift found while a change is
  in progress on the device is linked to that change's ticket and
  does not become a new unmanaged record until the change completes.
  If a record seems missing right after a change, wait for the next
  scan after the change closes.

## Limitations — honest list (D1–D5)

- Normaliser `eos`/`junos` is UNCERTAIN (golden-file coverage only;
  vendor output varies).
- Attribution is best-effort: when the scan cannot prove who made a
  change, `attributed_actor` is unknown — and the self-approval block
  only applies when the actor is known.
- Intent drift needs a deterministic render; a non-deterministic
  render produces false intent drift.
- Containerlab is CLI-driven (`containerlab` CLI over SSH); no stable
  Containerlab REST API is known.
- E2 `set_stats` harvesting is UNCERTAIN — how AWX exposes
  `set_stats` via its API is unverified; the engine falls back to
  parsing `VIREONET-RESULT` stdout markers.
- The AWX project ref vs manifest SHA: the script SHA is verified
  against the Git provider at the manifest ref, not against the AWX
  project's checkout — if AWX's project is at a different ref, the
  script AWX runs may differ from the verified one.
- Netmiko dry-run reports what *would* be sent ("would send"), it
  does not prove the device would accept it.
- NAPALM rollback on EOS/Junos is UNCERTAIN.
- The ServiceNow adapter cannot open incidents (see above); a drift
  with no linked ticket gets a log entry and a notification, not a
  ticket.
- The per-device vars override path (`netops/vars/device/<device>.yaml`)
  is not yet read by `VarsStore`; a merged drift is recorded in Git
  and the baseline, but rendering does not pick the override up until
  `VarsStore` learns the path.
- The rollback change runs through the ExecutionEngine steps
  in-process; it does not start the Temporal `ChangeWorkflow`, because
  that workflow only understands catalog intents (vlan/acl/route/
  interface/ztp) and a baseline restore is not one of them. The
  change row still moves through the normal legal states.
- `GET /drift?site=` is a device-prefix match, not a true site join;
  devices not named `<site>-<name>` will not match.
- Playwright e2e (`frontend/tests/e2e/drift.spec.ts`) is written but
  not run in this sandbox (no system Chromium).
