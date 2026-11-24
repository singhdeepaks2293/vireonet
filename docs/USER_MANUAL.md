# VireoNet User Manual

> **Who this is for:** network operators, approvers and administrators who use
> VireoNet every day, and engineers who set it up. It covers every screen, every
> button, every setting and every file you can edit, from A to Z. You do not need to
> read any other document first.
>
> **How this manual was written:** from the code of this build, and from a live run
> of the UI in fake mode (light and dark themes) on 2026-11-23. The reference tables
> (settings, API routes, permissions, change states, vendor support) were generated
> from the code, so they match this build exactly. Where something has a known
> limitation, the manual says so plainly.
>
> Companion documents: `docs/ADAPTERS.md` (per-adapter integration detail),
> `docs/DRIFT.md` (drift internals), `docs/SCRIPTS.md` (custom script contract),
> `docs/LAB.md` (EVE-NG / containerlab), `docs/runbook.md`, `docs/SDLC.md`.

## Contents

**Part I — Getting started**
1. [What VireoNet is](#1-what-vireonet-is)
2. [Key ideas in five minutes](#2-key-ideas-in-five-minutes)
3. [Installing and running](#3-installing-and-running)
4. [First login](#4-first-login)
5. [Roles and permissions](#5-roles-and-permissions)
6. [The screen layout](#6-the-screen-layout)

**Part II — Every screen**
7. [Login](#7-login)
8. [Changes](#8-changes)
9. [Change detail](#9-change-detail)
10. [Approvals](#10-approvals)
11. [Locks and change windows](#11-locks-and-change-windows)
12. [Drift and Exceptions](#12-drift-and-exceptions)
13. [Topology](#13-topology)
14. [ZTP devices](#14-ztp-devices)
15. [Config repos](#15-config-repos)
16. [Check templates](#16-check-templates)
17. [Config templates](#17-config-templates)
18. [Scripts](#18-scripts)
19. [Intelligence (machine learning and neural networks)](#19-intelligence-machine-learning-and-neural-networks)
20. [System health](#20-system-health)
21. [Adapters](#21-adapters)
22. [IAM](#22-iam)
23. [Gitea setup](#23-gitea-setup)
24. [Style reference](#24-style-reference)

**Part III — How it works**
25. [The change lifecycle, step by step](#25-the-change-lifecycle-step-by-step)
26. [Writing check templates](#26-writing-check-templates)
27. [Writing config render templates](#27-writing-config-render-templates)
28. [Multi-vendor support matrix](#28-multi-vendor-support-matrix)
29. [Git version control and rollback](#29-git-version-control-and-rollback)
30. [Telemetry and monitoring tools](#30-telemetry-and-monitoring-tools)
31. [Machine learning in detail](#31-machine-learning-in-detail)
32. [Integrations: webhooks, signals, callbacks](#32-integrations-webhooks-signals-callbacks)
33. [AWX job templates and playbooks](#33-awx-job-templates-and-playbooks)

**Part IV — Operating VireoNet**
34. [Daily workflows](#34-daily-workflows)
35. [Health, logs and metrics](#35-health-logs-and-metrics)
36. [Security notes](#36-security-notes)
37. [Troubleshooting and FAQ](#37-troubleshooting-and-faq)

**Part VI — Integration guide**
43. [End-to-end flow, adapter by adapter](#43-end-to-end-flow-adapter-by-adapter)
44. [Adapter integration checklist](#44-adapter-integration-checklist)
45. [Excel config push](#45-excel-config-push)
46. [TextFSM](#46-textfsm)
47. [Log analytics (show logging → ML)](#47-log-analytics-show-logging--ml)
48. [Writing pre- and post-check templates — a how-to](#48-writing-pre--and-post-check-templates--a-how-to)
49. [Trying the full workflow in fake mode](#49-trying-the-full-workflow-in-fake-mode)
50. [Workflow builder (flows)](#50-workflow-builder-flows)
51. [Vendor controllers (software upgrades and controller APIs)](#51-vendor-controllers-software-upgrades-and-controller-apis)
52. [Log platforms (Elasticsearch, OpenSearch, Splunk, Grafana Loki, Graylog)](#52-log-platforms-elasticsearch-opensearch-splunk-grafana-loki-graylog)
53. [Configuring adapters from the UI](#53-configuring-adapters-from-the-ui)
54. [The Gitea tab](#54-the-gitea-tab)
55. [Documentation space](#55-documentation-space)
56. [Neural networks (Intelligence tab)](#56-neural-networks-intelligence-tab)
57. [Pre/Post checks (snapshots, tests and intelligent diff)](#57-prepost-checks-snapshots-tests-and-intelligent-diff)

**Part V — Reference**
38. [Settings reference (every environment variable)](#38-settings-reference-every-environment-variable)
39. [API reference (every route)](#39-api-reference-every-route)
40. [Policy and template files](#40-policy-and-template-files)
41. [Glossary A–Z](#41-glossary-az)
42. [Index A–Z](#42-index-az)

---

# Part I — Getting started

## 1. What VireoNet is

VireoNet is a network change orchestrator. It sits between the systems you already
use (ServiceNow, NetBox, Git, AWX, your monitoring tool) and the network, and makes
sure every change is **validated, reviewed, tested, applied carefully, checked,
recorded — and undone if it goes wrong.**

A normal change flows like this:

```
ServiceNow request
  → validate against NetBox and policy, render config for every device's vendor
  → Git merge request → CI (lint, render, Batfish, optional lab test)
  → approval + merge
  → pre-check (locks, backups, Git commit recorded, your pre-check templates)
  → dry-run on every device → apply in waves → post-check (your post-check templates)
  → optional soak window → reconcile NetBox + Git → close the ticket
  ↘ any failure after apply: automatic rollback (from Git, or the pre-change backup)
```

A new device (ZTP, zero-touch provisioning) flows like this:

```
register in NetBox → DHCP reservation → day-0 bootstrap → device calls back
  → identity check (serial/model/version vs NetBox) → day-1 config rendered
  → Batfish gate → AWX push → post-check → NetBox "active" → ServiceNow closed
```

Four rules shape everything:

1. **Only AWX/Ansible touches devices.** VireoNet never logs in to a device itself.
2. **Dry-run first, always.** A real apply only happens for an approved change.
3. **Every change has a state machine.** Every move is recorded with time, actor,
   reason and evidence in an append-only audit trail.
4. **Machine learning is advisory.** The Intelligence tab recommends; it never changes a device,
   a workflow or a setting.

## 2. Key ideas in five minutes

| Idea | What it means for you |
|---|---|
| **Intent** | What you want, not how: "add VLAN 120 named app-tier on leaf-01 and leaf-02". Seven change types: `vlan_add`, `vlan_remove`, `acl_entry_add`, `acl_entry_remove`, `static_route_add`, `static_route_remove`, `interface_set` (plus `ztp_device` for provisioning). |
| **Change** | One intent being carried out, identified by its ServiceNow ticket (for example `RITM0001234`). |
| **Rendering** | Turning the intent into each device's own CLI, per vendor. Cisco, Arista and Juniper are built in; Fortinet, Palo Alto, Nokia and Extreme come from templates you can edit (§17, §27). |
| **Platform** | The device's operating system, taken from NetBox (`ios`, `ios-xe`, `nxos`, `eos`, `junos`, `fortios`, `panos`, `sros`, `srlinux`, `exos`). It decides which renderer and which check commands are used. |
| **Wave** | A batch of devices applied together. Waves run one after another; a failure stops the rollout. Default wave size: 2. |
| **Lock** | While a change works on a device, the device is locked so no other change can touch it. |
| **Pre-check / post-check** | Checks before and after apply. Built-in checks plus your own per-change-type checks written in YAML (§16, §26). |
| **Rollback** | Putting devices back. Preferred source: the device's config in Git at the commit recorded before the change (§29). Fallback: the AWX pre-change backup. |
| **Drift** | A device's running config differs from what Git/NetBox says it should be (someone changed it outside VireoNet). |
| **Fake mode** | Every external system is simulated, including a 10-device multi-vendor network with live-looking traffic. For learning and demos; nothing real is touched. |

## 3. Installing and running

### What you need

- A Linux host with Docker and Docker Compose (supported path), or Python 3.12,
  Node 20, PostgreSQL 16 and Temporal if you run without Docker.
- A browser. There is nothing to install on your workstation.

### Start the stack with Docker

```bash
cp .env.example .env            # then set VIREONET_JWT_SECRET to a long random value
docker compose up --build -d
docker compose ps
curl http://localhost:8000/healthz     # {"status":"ok"}
```

| Service | Host port | URL |
|---|---|---|
| Web UI | 5173 | http://localhost:5173 |
| API | 8000 | http://localhost:8000 (interactive API docs at `/docs`) |
| Temporal UI | 8233 | http://localhost:8233 |
| PostgreSQL | 5433 | database only |

The API runs database migrations at start (`alembic upgrade head`); the worker does not
(the two used to race). Start order is handled by Compose health checks.

> **Browsing from another machine.** The UI calls the API at the address baked in at
> build time (`VITE_API_URL`, default `http://localhost:8000`). From another machine,
> rebuild with the host's address — `VITE_API_URL=http://<host-ip>:8000 docker compose up --build -d` —
> and add `http://<host-ip>:5173` to `VIREONET_CORS_ORIGINS`. Alternatively build the
> UI with `VITE_API_URL=/api` and serve it behind the bundled nginx config, which
> proxies `/api/` to the API, so the browser only needs the UI address.

### Start without Docker

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn vireonet.main:app --host 0.0.0.0 --port 8000     # terminal 1: API
python -m vireonet.worker                                 # terminal 2: workflow worker
cd frontend && npm ci && npm run dev                      # terminal 3: UI on :5173
```

PostgreSQL and Temporal must already be running.

### Fake mode and real mode

| | Fake mode (`VIREONET_ADAPTER_MODE=fake`, default) | Real mode (`real`) |
|---|---|---|
| External systems | All simulated | Your ServiceNow, NetBox, AWX, Git, Vault, Batfish… |
| Topology | Demo network: 2 Arista spines, 4 leaves (IOS-XE, NX-OS, EOS, Junos), FortiGate and Palo Alto firewalls, Nokia SR OS border router, Extreme access switch | From NetBox devices and cables |
| Traffic | Synthetic, with a daily cycle and a few injected bursts | From your telemetry backend (§30) |
| Startup checks | Warn only | **Refuses to start** on default secrets, missing webhook auth, invalid templates, missing AWX job templates |

**Fake data is not real data.** A device on screen in fake mode does not exist.

## 4. First login

There is no default username or password. Create the first admin (only works while
the users table is empty):

```bash
docker compose exec -e VIREONET_ADMIN_USER=admin \
  -e VIREONET_ADMIN_PASSWORD='a-long-password-12+' api python scripts/seed_admin.py
```

It prints `Admin user admin created.` — or `Users already exist (N). Nothing created.`
if an admin was already made. Passwords must be at least 12 characters. There is no
"forgot password"; an admin resets other users in IAM (§22), and a lost last-admin
password must be reset in the database.

Open the UI and sign in (§7). A session lasts 8 hours (`VIREONET_ACCESS_TOKEN_EXPIRE_MINUTES=480`).

## 5. Roles and permissions

Four roles, each including everything the one above it can do:

| Role | Rank | Can do |
|---|---|---|
| **viewer** | 1 | Read everything operators read: changes, approvals queue, locks, drift, topology, ZTP, health, adapters, templates; preview check/config templates; run the ML tab. |
| **operator** | 2 | + abort changes, request rollbacks, release locks, retry/reset ZTP devices, start drift scans, emergency-accept drift, validate template drafts. |
| **approver** | 3 | + approve and reject changes; resolve drift (roll back, merge, grant exception); approver override for reconciliation. |
| **admin** | 4 | + manage users and roles (IAM), view the script manifest, edit drift ignore rules, install Gitea. |

Permission matrix (from the code):

| Permission | viewer | operator | approver | admin |
|---|---|---|---|---|
| `adapters:read` | yes | yes | yes | yes |
| `approvals:read` | yes | yes | yes | yes |
| `changes:read` | yes | yes | yes | yes |
| `health:read` | yes | yes | yes | yes |
| `locks:read` | yes | yes | yes | yes |
| `ztp:read` | yes | yes | yes | yes |
| `changes:create` | — | yes | yes | yes |
| `changes:signal` | — | yes | yes | yes |
| `changes:rollback` | — | yes | yes | yes |
| `locks:release` | — | yes | yes | yes |
| `ztp:retry` | — | yes | yes | yes |
| `ztp:reset` | — | yes | yes | yes |
| `approvals:approve` | — | — | yes | yes |
| `iam:manage` | — | — | — | yes |

Your role is shown in the toolbar (`admin · Role: admin`). Buttons you may not use are
hidden or disabled. If an admin changes your role or deactivates you, your current
session is revoked: the next request signs you out with "Your session expired or was
revoked. Sign in again."

## 6. The screen layout

The UI follows the KDE Plasma "Breeze" look (Breeze Dark in dark mode).

- **Sidebar (left)**, grouped like a file manager's Places panel. The current page is
  highlighted in Breeze blue.

  | Group | Pages |
  |---|---|
  | Operations | Changes, Approvals, Locks and change windows, Drift, Exceptions |
  | Network | Topology, ZTP devices, Config repos, Controllers, Gitea (when installed) |
  | Automation | Flows (workflow builder), Pre/Post checks, Check templates, Config templates, Config push (Excel), TextFSM, Scripts (admin only) |
  | Insights | Intelligence, System health |
  | Administration | Adapters (Status, Configure), IAM, Style reference, Documentation |

  Gitea setup has no sidebar entry; it is linked from the Adapters page.
- **Help and user manual**: this manual, inside the app. The left column lists every
  section with a **Find a section** filter; links inside the manual jump to their
  section, and the address (`/help#45-excel-config-push`) can be bookmarked.
- **Toolbar (top)**: the page title; an adapter status chip with a coloured dot
  (`Adapters: ok` green, `Adapters down: N` red, `Adapter health: unavailable` red,
  `Checking adapters…` grey); your user and role; **Theme: light/dark**; **Sign out**.
- **Content**: each page sits in a framed white (or dark) view.
- **Tables**: sortable column headers, a **Density: comfortable/compact** toggle
  (remembered per browser), **Previous / Next** paging, filters kept in the URL so a
  filtered view can be bookmarked or shared.
- **Retry**: appears when something fails to load; it only re-fetches.
- **Confirm dialogs**: every action that changes something asks first and says
  exactly what will happen. **Cancel** closes the dialog without doing anything.
- **Keyboard**: a "Skip to main content" link appears on the first Tab press; every
  control is reachable with Tab and shows a blue focus ring.
- **Live updates**: the Changes list refreshes every 15 s; a change's detail page every
  3 s until the change finishes; Locks, System health and Topology every 30 s.

---

# Part II — Every screen

## 7. Login

Shown whenever you have no session.

- A centred dialog: the VireoNet icon and name; "Sign in with your username and
  password. Your role is set by an admin."; **Username**; **Password**; **Sign in**.
- If your session ended (expired, revoked, user disabled) you also see: "Your session
  expired or was revoked. Sign in again."

| Button | What it does |
|---|---|
| **Sign in** (or Enter) | Sends the credentials to `POST /auth/login`. Success opens Changes. Failure shows "Invalid username or password." — this same message appears if the request never reached the API (CORS or connection error), so see §37 before resetting a password. |

No sign-up, no "remember me", no password reset here.

## 8. Changes

**Route** `/changes` — the home page. **Sidebar** Operations → Changes.

Every change, one row each. Refreshes every 15 s.

**Filters:** **State** (`All states` or one state), **Type** (`All types` or one change
type), **Device** (hostname), **From / To** (start date range), **Clear filters**.

**Table:** Ticket, Type, Devices, State (coloured badge), Requester, Started, Duration.
Click a row to open its detail page (§9).

**Empty state:** "No changes match these filters." / "0 changes" / "Page 1 of 1" — normal
on a new system; changes arrive from ServiceNow (§32).

| Button | Who | What it does |
|---|---|---|
| Density | anyone | Comfortable/compact rows. |
| Export CSV | anyone | Downloads the filtered list. |
| Clear filters | anyone | Resets every filter. |
| Column sort | anyone | Sorts; click again to reverse. |
| Previous / Next | anyone | Pages. |
| Retry | anyone | Only after a load failure; re-fetches. |

State badge colours: green = done/healthy (`applied`, `post_checked`, `closed`),
red = failed (`rejected`, `ci_failed`, `pre_check_failed`, `rollback_failed`, `failed`),
amber = `rolled_back`, blue = in progress.

## 9. Change detail

**Route** `/changes/<id>`. Opened from a Changes row or **Open full detail** on Approvals.
Refreshes every 3 s while the change is still moving; stops once it reaches a final state.

**What you see:**
- Ticket number, change type and state badge.
- Requester · Devices · Started.
- **State timeline**: `received → validated → planned → ci_passed → approved →
  pre_checked → applied → post_checked → closed`, with completed steps green, the
  current one blue, and a red final box if it ended in a failure state.
- **Action buttons** legal for the current state (below). Otherwise: "No actions are
  legal for state …".
- A red warning when the state is `rollback_failed`: "Rollback failed. Devices may be
  partially changed. Device locks are still held; check the devices and the AWX
  rollback job, then release the locks from Locks and change windows."
- **Execution** section and **Declared changes** table.
- Tabs: **Checks**, **Diff**, **Batfish results**, **AWX job output**, **Audit trail**.

### Action buttons

| Button | Shown when | Who | What it does |
|---|---|---|---|
| **Approve** | `ci_passed` | approver+ | Confirm: "This moves the change to approved and allows pre-check and apply to run. It does not apply config yet." Sends the approval. The workflow continues once the merge request is also merged (it waits for **both**). Message: "Approval sent. The workflow continues once the merge request is merged." |
| **Abort** | `received`, `validated`, `planned`, `ci_passed`, `approved`, `pre_checked` | operator+ | Confirm: "The workflow stops at its next checkpoint, before any device is touched, and releases its device locks. No config will be applied." Calls `POST /changes/<id>/signals/abort`. The change ends `rejected`. |
| **Roll back** | `applied`, `post_checked` | operator+ | Confirm: "The workflow runs the AWX rollback playbook to restore every device from its pre-change backup, as soon as the current step finishes. If the restore fails, the change moves to rollback_failed and needs manual action." Calls `POST /changes/<id>/signals/rollback`. Watch the page: it ends `rolled_back` or `rollback_failed`. |

When each action takes effect is explained in §25.5. Excel config pushes also show **Download workbook** (§45). In fake mode, a **Demo:
simulate CI pass + merge** button appears while the change waits for CI (§49).

An action sent too late is refused
with a 409 and a clear message (for example "rollback is not possible from state closed").

### Execution section

| Row | Shows |
|---|---|
| Job type | `playbook` or `python` (default `playbook`). |
| AWX job | Job ID; a link into AWX when the AWX URL is configured. |
| Script | Script name and SHA (12 chars) for script jobs. |
| Reconciliation | `reconciled`, `reconciliation_pending` (with the drift count and "an approver override is required before close"), or `—`. |

**Declared changes**: Object, Identifier, Field, Old, New — what the job reported it
changed; otherwise "No declared changes recorded for this change."

### Tabs

| Tab | Shows | Empty text |
|---|---|---|
| Checks | Every pre/post-check: name, pass/fail/skip, message. Warn-only failures show as skip with "WARN (non-blocking): …". | "No checks recorded yet. Pre-checks run after approval; post-checks run after apply." |
| Diff | Rendered config diff, with line numbers. | — |
| Batfish results | Passed/failed, blocking or not, failures, reachability differential (flows lost/gained/changed), ACL impact. | "No Batfish results yet. The Batfish gate runs in CI before approval." |
| AWX job output | Job ID, status, step. Full stdout stays in AWX. | "No AWX jobs recorded yet." |
| Audit trail | Every transition: from → to, actor, reason, evidence, time. | — |

## 10. Approvals

**Route** `/approvals`. The queue for approvers — check it first each day.

**Tabs:** **Awaiting approval** (changes in `ci_passed`) and **Processed**.
**Table:** Ticket, Type, Devices, Requester, Waiting since, Evidence, Actions.

| Button | Who | What it does |
|---|---|---|
| Show / Hide (Evidence) | anyone | Expands the checks, diff summary and Batfish result. |
| Open full detail | anyone | Opens the change detail page. |
| Approve | approver+ | Confirm with a reason; sends the approval. |
| Reject | approver+ | Confirm with a reason; the change ends `rejected`. |
| Density, sort, paging, Retry | anyone | As on Changes. |

Empty: "No changes waiting for approval."

## 11. Locks and change windows

**Route** `/locks`. Refreshes every 30 s.

**Device locks:** Device, Ticket, Holder, Acquired, Expires, TTL remaining, Actions.
Locks are taken at pre-check and released at close, abort or a successful rollback.
They are **kept** after a failed rollback so nothing else lands on a half-changed device.
Locks expire after `VIREONET_LOCK_TTL_SECONDS` (default 30 min).

| Button | Who | What it does |
|---|---|---|
| Release (per lock) | operator+ | Confirm, then releases immediately. Only release a lock when you are sure its change is finished or dead. |

Empty: "No device locks held." / "0 locks held."

**Change windows** (read-only, from `policies/default.yaml`; default Mon–Fri
01:00–05:00 UTC): Days, Start, End, Timezone, In window now, Next window, Emergency
bypass. Empty: "No change windows configured." Windows are edited in the policy file.

## 12. Drift and Exceptions

**Routes** `/drift`, `/drift/<id>`, `/exceptions`. Full internals: `docs/DRIFT.md`.

Drift kinds: **unmanaged** (running config vs baseline), **intent** (rendered intent vs
running), **state** (NetBox vs collected facts), **compliance** (rules). Record states:
`detected → under_review → reverting | merge_pending | exception_active | resolved`;
an expired exception goes `exception_active → exception_expired → under_review`.
Scans run nightly per site (`VIREONET_DRIFT_CRON`, default `0 2 * * *`), on demand, or
on device events.

### Drift list

Table: Device, Kind, Severity (critical/high/medium/low badge), Status, First seen,
Last seen, Owner, Age. Filters: Device, Site, Kind, Severity, Status.

| Button | Who | What it does |
|---|---|---|
| Start scan | operator+ | Starts a scan for the filtered site; shows the workflow/scan id. |
| Export CSV | anyone | Downloads the filtered records. |
| Row / Enter | anyone | Opens the record. |

### Drift detail

- Heading "Drift <device>" with severity; a status timeline.
- Tabs: **Diff vs baseline**, **Diff vs intent**, **State findings**, **Compliance**,
  **Batfish impact**, **Attribution** (who/when, best-effort, "unknown" if not
  provable), **Decisions / Audit**. Tabs without data say so.
- **Resolve this drift** (approver/admin): **Reason (required)** and **ServiceNow
  reference (required)** — buttons stay disabled until both are filled.
- **Roll back to (from Git)** — choose what to restore:

  | Option | Restores |
  |---|---|
  | Head of main (or master): the approved running config | `<device>/config` at the branch head. **Default.** |
  | Last commit before the head | The version before the latest commit to this device's config. |
  | A specific commit… | Pick from the device's Git history (SHA · date · message). |

  Under the picker: the repo, the file path and how many commits exist. If Git is
  unreachable: "Git history is unavailable (…). The default rollback falls back to the
  stored baseline; other targets are refused."

| Button | What it does |
|---|---|
| **Roll back** | Starts a normal change restoring the chosen version (backup, pre-check, dry-run, apply, post-check; automatic rollback on failure). Resolves only when a re-collect confirms the device now matches **what was restored**. |
| **Merge** | Creates branch `drift/<device>/<record>` and a merge request adding a per-device vars override that makes the drifted config the new intent. Never edits a shared template. Stays merge-pending until merged (baseline updated) or closed unmerged (back to under review). |
| **Grant exception** | Keeps the current config and ignores this exact diff until **Exception expiry**; needs **Exception owner** too. Maximum `VIREONET_DRIFT_EXCEPTION_MAX_DAYS` (30). |

Whoever made the manual change cannot approve their own merge or exception (403).
Operators can **emergency-accept** from the API (`POST /drift/<id>/emergency-accept`)
with a deadline (`VIREONET_DRIFT_EMERGENCY_DEADLINE_HOURS`, 24 h).

### Exceptions

`/exceptions`: active exceptions with a countdown ("2d 3h left" or "expired"); **Open**
jumps to the record.

## 13. Topology

**Route** `/topology`. **Sidebar** Network → Topology. Built with the NeXt UI toolkit.

- **Map**: one node per device, icon by role (switch, router, firewall), label = hostname.
  One line per cable. Colour = utilisation of the busier direction: green under 50%,
  amber 50–80%, red 80% and above, grey = no traffic data. Thicker = busier.
- **Traffic labels**: at the A end, `→ 3.4 Gbps` is traffic leaving A towards B; at the
  B end, `4.8 Gbps ←` is traffic leaving B towards A. Labels appear on links with room
  for them; zoom in to see the rest.
- **Map controls** (left edge, from NeXt): select / pan mode, zoom in, zoom out, zoom
  to selection, fit to screen, full screen, settings. Drag nodes to rearrange; the
  layout is kept across the 30 s refreshes.
- **Links table** below the map: A end, B end, A → B, B → A, Speed, Utilisation — every
  number, readable without the map.
- **Site** box filters to one NetBox site; **Refresh** reloads now.
- The intro line names the traffic source (`zabbix`, `prometheus/gnmic`, `fake`, … or
  "no telemetry backend"). Telemetry errors are shown in amber; the map still draws
  without traffic.

Data sources: devices and cables from NetBox (NetBox 3.3+, interface-to-interface
cables only; cables to circuits or power are skipped). Traffic from the telemetry
backend (§30).

## 14. ZTP devices

**Route** `/ztp`. Devices being zero-touch provisioned.

Filters: Hostname, Serial, Stage, Site, Platform, Clear filters.
Table: Serial, Hostname, Model, Site, NetBox status, ZTP stage, Last callback, DHCP
reservation, Actions. Empty: "No ZTP devices match these filters." / "0 devices".

| Button | Who | What it does |
|---|---|---|
| Retry (per device) | operator+ | Confirm; re-runs the ZTP flow and issues a fresh token. |
| Reset (per device) | operator+ | Confirm; resets the ZTP state so it can be provisioned again. |
| Density, sort, paging, Retry | anyone | As elsewhere. |

There is no "register" button: devices are registered through `POST /ztp/devices`
(§32.5).

## 15. Config repos

**Route** `/config`. Read-only view of how configuration is stored in Git:

| Store | Repo | Contents |
|---|---|---|
| Templates | `netops/templates` | Jinja templates only. |
| Variables | `netops/vars` | YAML only, no Jinja. |
| Per-device config | `netops/config-<host>` (per-device layout) or `<device>/config` in one repo (monorepo layout, §29) | Rendered/running config, candidate, backup, vars snapshot, metadata. |
| NetBox export | `netops/netbox-export` | Read-only copy; never a render source. |

A **Filter by hostname or repo** box and a table (Hostname, Repo). Note: this page's
history column shows "No commits yet" because the per-repo history view is not
implemented in this build; the drift rollback picker reads real history through
`GET /config/devices/<host>/commits`.

## 16. Check templates

**Route** `/checks`. **Sidebar** Automation → Check templates.

Your pre- and post-checks per change type, from `policies/checks.yaml`. How to write
them: §26.

- Intro: the source file, how command checks pick a command per platform, the allowed
  read-only prefixes, and "Locks and backups always happen, whatever the templates say."
- If the active file is invalid: a red list of errors and "the worker will refuse to start".
- **Change type** dropdown.
- Tab **Active templates**: Pre-checks and Post-checks tables — Check (name and
  description), Kind (built-in / command), On fail (block / warn only), Command per
  platform, Expect (per platform or "all").
- Tab **Edit and preview**:
  - **Template YAML** editor (starts from the active file; "draft, edited" once changed).
  - **Validate** (operator+): "Valid. Commit it to policies/checks.yaml to activate."
    or a red list of exact errors (file location and problem).
  - **Discard draft**.
  - **Platform** dropdown and **Sample intent (JSON)** (pre-filled per change type).
  - **Preview checks**: for the chosen platform, each check's exact command after
    variables are filled in and the exact expectation; built-in checks say "built-in
    check"; a platform without a command says "no command for this platform: skipped".

Drafts are never saved from the UI. To activate, commit the file through a merge request.

## 17. Config templates

**Route** `/render`. **Sidebar** Automation → Config templates.

See exactly what config a change produces for each vendor. How to write templates: §27.

- Intro: which vendors are built in, which come from `policies/render_templates.yaml`,
  and that a change a platform cannot render is rejected before approval.
- Amber note: vendor templates follow documented CLI but have not been run against real
  devices by the project — test each in a lab first.
- **Vendor**: Cisco IOS, Cisco IOS-XE, Cisco ISR (IOS-XE), Cisco NX-OS, Arista EOS,
  Juniper Junos, Fortinet FortiOS, Palo Alto PAN-OS, Nokia SR OS, Nokia SR Linux,
  Extreme EXOS.
- **Change type** and **Intent (JSON)** (pre-filled sample).
- **Render**: shows the platform, the format (CLI or set), whether it came from a
  template or a built-in renderer, and the exact lines. If it cannot render, the red
  error says why and which template to add.
- **Render templates YAML** editor: edit to preview a draft; **Discard draft**. Not saved here.

## 18. Scripts

**Route** `/scripts`. Admin only (hidden otherwise; the page says "Admin role is
required to view the script manifest").

Read-only table of `policies/scripts.yaml`: Name, SHA (12 chars, hover for full),
Allowed change types, Platforms, Writes scope, Last run (`never` if none). Scripts are
added by merge request only; the contract is in `docs/SCRIPTS.md`.

## 19. Intelligence (machine learning and neural networks)

> Formerly *Machine learning*. Neural-network tabs: see §56.

**Route** `/intelligence` (old `/ml` redirects). **Sidebar** Insights → Intelligence. Anyone signed in can use it.

"Models train on your change history and interface telemetry when there is enough of
it. Results are advisory: nothing here changes a device or a workflow."

Every result says where its data came from: `Data: zabbix` (or another backend),
`database`, or `synthetic` — with an amber "synthetic training data, not your
network's history" note and the reason (for example "3 finished changes in the
database; need 40").

| Tab | Controls | Output |
|---|---|---|
| **Anomaly detection** | Algorithm (Isolation Forest, Robust z-score, Local Outlier Factor), Hours of history; **Detect anomalies** | "N of M interfaces have anomalous traffic." Then one card per affected interface: device and interface, anomalous points, peak utilisation, and a chart (solid = in, dashed = out, red dots = anomalies). |
| **Log anomalies (show logging)** | Hours of logs; **Write summaries to NetBox journal** (operator+); **Analyse logs** | Source and counts; a devices table (status, events, top events, activity sparkline); anomaly cards (burst, unusual mix, critical, new event) with sample log lines and interfaces; NetBox results; TextFSM collection details. See §47. |
| **Clustering (k-means)** | Clusters (k): auto or 2–6; **Run k-means** | k and silhouette score (and the k values tried), a scatter chart (mean in vs mean out, one colour per cluster), and a table: cluster, profile ("hot, bursty, mostly outbound"…), size, peak in/out, members. |
| **Classification** | **Train classifiers**; then **Score a proposed change**: Change type, Devices, Vendors/platforms, Hour, Emergency; **Score** | Changes used, failure rate, test-set size, best model; a table per algorithm (ROC AUC, Recall, Precision, F1, Accuracy, Threshold); a "What drives risk" bar chart. Scoring shows the failure probability and a suggested wave size. |
| **Regression** | Predict: Change duration, or Traffic forecast (1 h ahead); algorithm checkboxes (linear, ridge, lasso, random_forest, gradient_boosting, knn, svr); **Train regressors** | A table ranked by RMSE (MAE, R²; best marked), and an actual-vs-predicted chart for the best model (points on the dashed diagonal are perfect). |
| **Reinforcement learning** | Episodes, Blast-radius penalty; **Train agent** | Learning curve, and the learned policy table: devices × risk → recommended wave size. |

What the numbers mean and their limits: §31.

## 20. System health

**Route** `/health`. Check here first when anything looks wrong. Refreshes every 30 s;
**Refresh** reloads now.

| Section | Columns / meaning |
|---|---|
| Components | api, database (`SELECT 1`), temporal. `database: unknown` means not verified by that check, not "down". |
| AWX and Execution Environment | AWX reachability; the EE (`VIREONET_AWX_EXECUTION_ENVIRONMENT`) is honestly `unknown` — AWX has no API to prove the EE is attached; verify in AWX. |
| Lab backends | EVE-NG and/or containerlab (`VIREONET_LAB_BACKEND`). |
| Adapters | servicenow, netbox, awx, gitlab, batfish, vault, eveng, containerlab, dhcp, notification, monitoring: status, latency, last success. In fake mode all `ok`, last success `never`. |
| Drift scans | Per site: status, started, scanned, with drift, failed; open drift count; never-scanned devices. "No drift scan has run yet." when none. |

## 21. Adapters

**Route** `/adapters`. Read-only configuration of every adapter: Category (Core, ITSM,
Telemetry), Adapter, Enabled, Mode, Endpoint (scheme/host/port only), Timeout, Retries,
Rate limit, Capabilities, Note. ITSM providers (ServiceNow, Jira, Freshservice, BMC Helix)
and telemetry backends (Zabbix, Prometheus SNMP, Prometheus gNMI, LibreNMS, PRTG,
SolarWinds) are all listed: the active one shows `yes` and its endpoint; the others show
`available` and the setting that enables them. No
secrets are shown. To change one, update its setting or Vault entry and restart the
API and worker. Includes the link to **Gitea setup** (§23). Per-adapter detail:
`docs/ADAPTERS.md`.

## 22. IAM

**Route** `/iam`. Admin only ("Admin role is required to manage users and roles.").

**Users tab** — Create user: Username, Display name, Password (12+), Role (default
viewer); **Create user** ("Created user <name> with role <role>."). Table: Username,
Display name, Role, Active, Actions.

| Control | What it does |
|---|---|
| Role dropdown (per user) | Changes the role immediately and **revokes that user's sessions** (they sign in again). |
| Deactivate / Activate | Deactivated users are refused on their next request, even with an existing token. You cannot deactivate yourself or the last active admin. |

**Roles tab** — the read-only permission matrix (§5). Roles are fixed in code.

## 23. Gitea setup

**Route** `/setup/gitea` (link on Adapters). Admin only.

Two cards: **Manual install** (**Show manual steps** — numbered steps with copyable
commands) and **Automatic install** (**Install automatically** → form: Admin
username, Repo (owner/repo), Gitea URL → **Install Gitea automatically** → confirm:
"Install Gitea with Docker Compose at <url>, create admin <user> and repo <repo>,
create an API token and store it in Vault. This starts containers on this host.").
Needs Docker on the host and `GITEA_ADMIN_PASSWORD` + `GITEA_DB_PASSWORD` (12+ chars).
Progress shows `Status:` and per-step lines; finish: "Done. Restart the VireoNet API +
worker with VIREONET_GIT_PROVIDER=gitea, then check System health for the git
adapter." **Back** returns to the cards.

## 24. Style reference

**Route** `/style`. For developers: the design tokens (colours, type, buttons, badges)
so new screens match. No actions.

---

# Part III — How it works

## 25. The change lifecycle, step by step

### 25.1 States

| State | Meaning | Next possible states |
|---|---|---|
| `received` | Request accepted. | validated, rejected, failed |
| `validated` | Passed NetBox, policy and render checks. | planned, rejected, failed |
| `planned` | Waves planned, config rendered per device. Waiting for CI. | ci_passed, ci_failed, rejected, failed |
| `ci_passed` | CI green. Waiting for approval **and** merge. | approved, rejected, failed |
| `approved` | Approved and merged. | pre_checked, rejected, failed |
| `pre_checked` | Locks held, backups taken, Git commit recorded, pre-checks passed. | applied, pre_check_failed, rejected, rolled_back, rollback_failed, failed |
| `applied` | Every wave applied. | post_checked, rolled_back, rollback_failed, failed |
| `post_checked` | Post-checks passed. | reconciling, rolled_back, rollback_failed, failed |
| `reconciling` | Updating Git and NetBox to match the devices. | reconciled, reconciliation_pending, failed |
| `reconciliation_pending` | Git/NetBox do not match yet; needs an approver override. | reconciling, failed |
| `reconciled` | Everything matches. | closed |
| **Final states** | `closed`, `rejected`, `ci_failed`, `pre_check_failed`, `rolled_back`, `rollback_failed`, `failed` | — |

`rolled_back` means devices were restored successfully. **`rollback_failed` means they
may not have been** — a person must check (§34.5). VireoNet never reports a failed
restore as a success.

### 25.2 Intake and validation

ServiceNow (or a client with the webhook secret) calls `POST /changes` with an intent
or a ticket number. VireoNet checks the intent (field types, interface names),
NetBox (devices exist), policy (`policies/default.yaml`: at most 10 devices, protected
devices `core-01`, `core-02`, `fw-01`, protected VLANs 1 and 1002–1005, change window;
`emergency: true` bypasses only the window) and **renders the config for every
device's platform**. An unsupported (change type, platform) pair is rejected here
with the exact template to add — before CI and approval, never at apply time.

The Temporal workflow ID is the ticket number, so a repeated webhook does not start a
second change.

### 25.3 CI and approval

The workflow waits (up to 24 h) for a CI result from the Git provider webhook, then
(up to 24 h) for **both** an approval and the merge. A CI timeout ends `ci_failed`; an
approval timeout ends `rejected` ("approval/merge timed out").

### 25.4 Pre-check, apply, post-check

1. **Pre-check**: lock every device; silence monitoring (if configured); back up every
   device (AWX `backup`); record each device's current Git commit (§29); capture a
   baseline (AWX `pre-check`); run the pre-check templates for this change type (§26).
   Any failure releases the locks and ends `pre_check_failed`.
2. **Dry-run**: every device gets a dry-run job with **its own rendered config**. Any
   failure stops before a single device is changed.
3. **Policy window re-check**: just before apply, the policy change window
   (`policies/default.yaml`) is checked again — approval may arrive hours after intake.
   Outside the window the change ends `rejected` with "outside change window … at apply
   time; nothing was pushed" and the locks are released. Emergency changes
   (`"emergency": true` on intake) skip this check.
4. **Apply in waves**: `VIREONET_WAVE_SIZE` devices per wave (default 2), waves one
   after another, one AWX job per device. A wave with `VIREONET_ABORT_THRESHOLD`
   (default 1) or more failures stops the rollout and triggers rollback. Device locks
   are renewed for another `VIREONET_LOCK_TTL_SECONDS` at the start of apply and
   before every wave, so long changes keep their locks.
5. **Wave health gate** (`VIREONET_WAVE_HEALTH_CHECKS=true`, default): after each wave,
   that wave's **command** post-checks (the ones that read the devices, §26.3) run on
   the devices of that wave before the next wave starts. A failure stops the rollout
   ("wave health check failed on [...]") and rolls back. Built-in checks that compare
   against NetBox (such as `config_matches_intent`) wait for the final post-check,
   because NetBox is updated after the whole rollout. Results are in the change's
   evidence under `wave_health`.
6. **Post-check**: per device, the AWX post-check re-runs the rendered config in check
   mode and fails on any remaining diff (Cisco/Arista); then the post-check templates
   run. Failure triggers rollback.
7. **Soak** (optional): `VIREONET_POST_APPLY_SOAK_SECONDS` > 0 holds the change after
   post-check so operators can watch the network and press **Roll back**.
8. **Reconcile and close**: Git and NetBox are updated to match; the ticket is closed;
   locks are released.

### 25.5 When Abort and Roll back take effect

| Action | Takes effect | Ignored after |
|---|---|---|
| Abort | While waiting for CI; right after approval; right after pre-check (locks released). | Apply has started (use Roll back instead). |
| Roll back | When apply finishes; when post-check finishes; at any time during the soak window. | Reconcile has started (API returns 409 once the state has moved on). |

### 25.6 Rollback

Automatic after any apply or post-check failure, or on request. For each device, one
AWX `rollback` job restores, in this order of preference:

1. **Git**: the device's config at the commit recorded at pre-check — the version
   before this change, even if `main` has moved since (`VIREONET_ROLLBACK_SOURCE=git`, default).
2. **AWX backup**: the pre-change backup file, if Git could not serve it (the reason is
   recorded per device in the rollback evidence).

Platforms with a safe full-config restore: IOS, IOS-XE, NX-OS, EOS (replace), Junos
(override). FortiOS, PAN-OS, SR OS, SR Linux and EXOS have **no automatic restore**: the
rollback job fails on purpose and the change ends `rollback_failed` with the devices
still locked, so a person restores them from the named file.

### 25.7 Known limitations of the lifecycle

- Each apply is an AWX job per device inside one workflow step; very large changes can
  approach the 30-minute step timeout.

## 26. Writing check templates

File: `policies/checks.yaml`. Preview and validate in the UI (§16) before committing.

### 26.1 Structure

```yaml
version: 1
allowed_command_prefixes: ["show ", "display ", "get "]

defaults:                     # every change type gets these…
  pre: [reachability, no_active_lock, interface_state, backup_taken, neighbour_baseline]
  post: [reachability, no_unexpected_diff, neighbour_baseline]

change_types:
  vlan_add:
    description: Add a VLAN
    pre:
      checks: [ ... ]         # …plus these
    post:
      extends_defaults: true  # default; false replaces the defaults
      checks: [ ... ]
```

Short forms: `pre: [a, b]` means `pre: {checks: [a, b]}`; `- reachability` means
`- {builtin: reachability}`. A missing file means the built-in defaults.

### 26.2 Built-in checks

| Name | Checks |
|---|---|
| `reachability` | Each device is `active` in NetBox with a primary IP. |
| `no_active_lock` | This change holds the device locks. |
| `interface_state` | Interfaces in the intent exist in NetBox (skipped if none). |
| `backup_taken` | A pre-change backup exists for every device. |
| `neighbour_baseline` | A neighbour baseline was captured. |
| `config_matches_intent` | VLAN add/remove is reflected in NetBox. |
| `no_unexpected_diff` | No unexpected diff after apply. |

Add `on_fail: warn` to any built-in to make it non-blocking:
`- {builtin: interface_state, on_fail: warn}`.

### 26.3 Command checks

```yaml
- name: vlan_active                       # lowercase, digits, underscore
  description: The VLAN is active with the requested name
  command:                                # platform -> read-only command
    ios: "show vlan id {{ intent.vlan_id }}"
    nxos: "show vlan id {{ intent.vlan_id }}"
    eos: "show vlan {{ intent.vlan_id }}"
    junos: "show vlans {{ intent.vlan_name }}"
    default: "show vlan id {{ intent.vlan_id }}"   # used when no platform matches
  expect:                                 # one for all platforms…
    regex: "{{ intent.vlan_id }}\\s+{{ intent.vlan_name }}\\s+active"
  # …or per platform:
  # expect:
  #   default: {regex: "..."}
  #   junos:   {regex: "{{ intent.vlan_name }}\\s+{{ intent.vlan_id }}\\b"}
  on_fail: block                          # block (default) | warn
  on_missing_platform: skip               # skip (default) | fail
```

**Expectations** (all listed conditions must hold on every device): `contains`,
`not_contains`, `regex`, `not_regex` (multi-line regex, `^`/`$` match line ends).

**Variables**: `intent.<field>` (any intent field, for example `intent.vlan_id`,
`intent.prefix`, `intent.prefix.network_address`, `intent.next_hop`,
`intent.interfaces`), `device`, `platform`, `ticket`. Undefined names are errors — a
typo fails the check instead of silently matching nothing.

**Results**: pass on every device → pass; any mismatch → fail (or "WARN
(non-blocking): …" with `on_fail: warn`); no command for a device's platform → skip
(or fail with `on_missing_platform: fail`). Evidence keeps each device's command and the
last 2,000 characters of output.

### 26.4 Safety rules

- Commands must be one line and start with an allowed prefix (`show `, `display `,
  `get ` by default). Checked when the file loads, again after variables are filled
  in, and again by the AWX playbook.
- Templates run in a sandbox: no access to Python internals.
- Locks and backups always happen; removing `backup_taken` removes the check, not the backup.

### 26.5 Validation errors you may see

| Error contains | Fix |
|---|---|
| `unknown change type` | Use one of the seven change types (or `ztp_device`). |
| `unknown built-in` | Use a name from §26.2. |
| `must start with one of [...] (read-only only)` | Only show/display/get commands. |
| `must start with literal text, not a template tag` | Begin with the command word, e.g. `show …`. |
| `does not parse` | Fix the Jinja (`{{ … }}` / `{% … %}` balance). |
| `listed twice` | Remove the duplicate. |
| `clashes with a built-in check` | Rename the command check. |
| `expect needs one of …` / `expect: must not be empty` | Add at least one expectation. |
| `no command for that platform` | A per-platform expectation needs a command for that platform (or `default`). |

## 27. Writing config render templates

File: `policies/render_templates.yaml`. Preview in the UI (§17).

### 27.1 Which renderer is used

For each device: normalise its NetBox platform (§27.4); if
`templates.<change_type>.<platform>` exists, use it; else use the built-in renderer
for that platform; else reject the change at validation.

### 27.2 Structure

```yaml
version: 1
templates:
  vlan_add:
    huawei-vrp:                 # a new vendor: just add it
      format: cli               # cli (default) | set
      config: |
        vlan {{ intent.vlan_id }}
         description {{ intent.vlan_name }}
        {% for i in intent.interfaces %}
        interface {{ i }}
         port link-type access
         port default vlan {{ intent.vlan_id }}
        {% endfor %}
    ios:                        # overrides the built-in IOS renderer for vlan_add only
      config: |
        vlan {{ intent.vlan_id }}
         name {{ intent.vlan_name }}
```

The output is split into lines, blank lines are dropped, indentation is kept.

### 27.3 Variables, filters and guards

Variables: `intent.<field>`, `device`, `platform`, `ticket`.

| Filter | Example | Result |
|---|---|---|
| `netmask` | `{{ "10.1.0.0/16" \| netmask }}` | `10.1.0.0 255.255.0.0` |
| `wildcard` | `{{ "10.1.0.0/16" \| wildcard }}` | `10.1.0.0 0.0.255.255` (`/32` → `host A`, `any` stays) |
| `cidr` | `{{ "host 10.0.0.1" \| cidr }}` | `10.0.0.1/32` |
| `ip` | `{{ "10.1.0.0/16" \| ip }}` | `10.1.0.0` |
| `safe_name` | `{{ "a/b c" \| safe_name }}` | `a_b_c` (max 63 chars) |
| `quote` | `{{ 'say "hi"' \| quote }}` | `"say \"hi\""` |

Guard: `{{ required(intent.next_hop, 'next_hop') }}` fails the render with "this platform
needs next_hop in the intent" when the field is empty — instead of producing `None`.

### 27.4 Platform names (NetBox slug → platform)

| Platform | NetBox slugs accepted |
|---|---|
| `ios` | ios, cisco-ios, cisco_ios, isr-g2 (ISR 1900/2900/3900) |
| `ios-xe` | ios-xe, iosxe, cisco-ios-xe, cisco_iosxe, cisco-iosxe, isr, cisco-isr (ISR 1000/4000) |
| `nxos` | nxos, nx-os, cisco-nxos, cisco_nxos |
| `eos` | eos, arista, arista-eos, arista_eos |
| `junos` | junos, juniper-junos, juniper_junos |
| `fortios` | fortinet, fortios, fortigate, fortinet-fortios |
| `panos` | paloalto, palo-alto, panos, pan-os, paloalto-panos |
| `sros` | nokia, nokia-sros, sros, timos |
| `srlinux` | nokia-srlinux, srlinux, srl |
| `exos` | extreme, exos, extreme-exos |

Any other slug is used as-is (lower-cased), so a template can target it.

### 27.5 Safety

- Intent values containing a newline or any control character are refused before
  rendering, for every vendor (this blocks smuggling extra config lines in a
  description or VLAN name).
- Interface names must match `^[A-Za-z0-9][A-Za-z0-9/:._-]{0,63}$` — no spaces or quotes.
- Templates run in the same sandbox as check templates.
- The shipped vendor templates follow each vendor's documented CLI but have not been
  run against real devices by the project. Test each in a lab and pin the software
  versions you tested.

## 28. Multi-vendor support matrix

What this build can render out of the box (generated from the code). "—" means the
change is rejected at validation until you add a template.

| Change type | ios | ios-xe | nxos | eos | junos | fortios | panos | sros | srlinux | exos |
|---|---|---|---|---|---|---|---|---|---|---|
| `vlan_add` | built-in | built-in | built-in | built-in | built-in | template | template | — | template | template |
| `vlan_remove` | built-in | built-in | built-in | built-in | built-in | template | template | — | template | template |
| `acl_entry_add` | built-in | built-in | built-in | built-in | built-in | template | template | — | — | — |
| `acl_entry_remove` | built-in | built-in | built-in | built-in | — | template | — | template | template | — |
| `static_route_add` | built-in | built-in | built-in | built-in | built-in | template | template | template | template | template |
| `static_route_remove` | built-in | built-in | built-in | built-in | built-in | — | template | template | template | template |
| `interface_set` | built-in | built-in | built-in | built-in | built-in | template | template | template | template | template |

Why the gaps exist:
- **SR OS VLANs**: L2 on SR OS is a VPLS service with SAPs, which needs service IDs and
  customers that the intent does not carry.
- **Numbered ACL entries** (SR OS/SR Linux add, EXOS): the add intent has no entry
  number; EXOS uses ACL policy files.
- **Junos / PAN-OS ACL remove**: terms and rules are named, not numbered.
- **FortiOS route remove**: routes are numbered entries; the intent carries the prefix,
  not the FortiOS ID.

Per-intent field requirements by vendor: Junos `vlan_remove` needs `vlan_name`;
NX-OS and EXOS `static_route_remove` need `next_hop`; FortiOS `vlan_add` needs
`interfaces` (the parent port). FortiOS assumes VDOM `root` for VLANs and maps a VRF to
a VDOM; PAN-OS maps a VRF to a virtual router.

Syntax notes for the built-ins: IOS uses netmasks for routes and wildcard masks for
ACLs; NX-OS uses CIDR and `vrf context`; EOS uses CIDR; Junos uses `set`/`delete`
lines and names ACL terms `vn-<hash>`.

## 29. Git version control and rollback

Every device's config lives in Git; the **main** (or **master**) branch holds the
approved running config.

### 29.1 Layouts

| Setting | Repo | File |
|---|---|---|
| `VIREONET_GIT_LAYOUT=per_device` (default) | `netops/config-<host>` | `backups/running-config.txt` |
| `VIREONET_GIT_LAYOUT=monorepo` | `VIREONET_GIT_CONFIG_REPO` (or `VIREONET_GIT_REPO`) | `VIREONET_GIT_CONFIG_PATH`, default `{device}/config` |

Branch: `VIREONET_GIT_DEFAULT_BRANCH` (`main` or `master`). Providers: GitLab, GitHub,
Gitea, Bitbucket (`VIREONET_GIT_PROVIDER`).

### 29.2 What happens

| Situation | Rollback restores |
|---|---|
| Change pushed from Git (normal change) | The commit recorded at pre-check — the last commit before this change. |
| Drift (changed outside Git) | Head of main/master by default; optionally the commit before it, or any commit you pick (§12). |

An explicit drift target never falls back: if Git cannot serve it, the rollback is
refused. Without a target, VireoNet uses the branch head when Git is reachable and the
stored baseline otherwise. Confirmation after a drift rollback compares the device
against **what was restored**.

`VIREONET_ROLLBACK_SOURCE=backup` disables Git for change rollbacks (always the AWX backup).

## 30. Telemetry and monitoring tools

Telemetry feeds interface in/out traffic to the Topology map and the ML tab. Choose one
backend with `VIREONET_TELEMETRY_BACKEND`. Device and interface names in the tool must
match NetBox.

| Backend | Settings | Notes |
|---|---|---|
| `zabbix` | `VIREONET_TELEMETRY_BASE_URL` (Zabbix front end), `VIREONET_TELEMETRY_TOKEN` (API token, Zabbix 6.4+) | Uses the standard SNMP/agent templates: items `net.if.in[…]` / `net.if.out[…]` named "Interface X: Bits received/sent". History supported. |
| `prometheus` | `VIREONET_TELEMETRY_BASE_URL`, optional `VIREONET_TELEMETRY_TOKEN`, `VIREONET_TELEMETRY_PROMETHEUS_PRESET` | Preset `snmp_exporter` (SNMP, `ifHCInOctets` with `instance`/`ifName` labels) or `gnmic` (**gNMI/gRPC streaming**, OpenConfig counters with `source`/`interface_name` labels). History supported. |
| `librenms` | URL, token | Current rates only; LibreNMS history is graphs, not data. |
| `prtg` | URL, API token (PRTG 22.1+) | Bandwidth sensors (tag `bandwidthsensor`); sensor name = interface. History supported. |
| `solarwinds` | URL (SWIS, port 17774), `VIREONET_TELEMETRY_USERNAME`, `VIREONET_TELEMETRY_PASSWORD` | Orion NPM interfaces; history from `InterfaceTraffic`. |
| `fake` | — | Synthetic traffic. Used automatically in fake mode when the backend is `none`. |
| `none` | — | Telemetry off: no traffic on the map; traffic ML tabs return "telemetry is disabled". |

`VIREONET_TELEMETRY_VERIFY_TLS=true` (default) verifies certificates. All rates are bits
per second; utilisation uses the interface speed when the tool reports it.

**Direct gNMI**: VireoNet does not dial gNMI itself. Run gnmic with its Prometheus
output and use `prometheus` + preset `gnmic`.

Separate from telemetry, `VIREONET_MONITORING_BACKEND` (`alertmanager` or `librenms`)
**silences alerts** for the devices during a change and lifts the silence afterwards.

Check the connection: `GET /telemetry/health`.

## 31. Machine learning in detail

### 31.1 Data

- **Change history**: finished changes in the database (closed or failed). Features:
  change type, number of devices, number of platforms, hour of day (as a circle),
  weekend, emergency, and the devices' previous failure rate. Labels: failed (rolled
  back, rollback failed, failed, pre-check failed) and duration (approval → end).
  **Fewer than 40 finished changes** → a synthetic history is used and labelled
  `synthetic` everywhere.
- **Telemetry**: per cabled interface (up to 60), 10-minute points over the chosen
  hours, as utilisation 0–1.

### 31.2 Algorithms

| Family | Algorithm | How it is used here |
|---|---|---|
| Unsupervised | **Isolation Forest** (default) | Each interface's points are scaled to its own median and spread; one model learns "normal" network-wide; a point must also be unusual for its own interface (robust z > 3). Contamination 0.5%. |
| Unsupervised | **Robust z-score** | Per interface, |value − median| / MAD > 4 on in or out. Transparent baseline. |
| Unsupervised | **Local Outlier Factor** | Density-based; offered for comparison, tends to miss short bursts on bursty traffic. |
| Unsupervised | **k-means** | Clusters interfaces by mean, 95th percentile, variability and in/out balance; k picked by silhouette (2–6) unless you choose. |
| Supervised classification | **Gradient boosting, random forest, logistic regression** | Predict change failure. Held-out 25% stratified test set; the decision threshold is tuned by 5-fold cross-validation for F1. |
| Supervised regression | **Linear, ridge, lasso, random forest, gradient boosting, k-nearest neighbours, support vector regression** | Predict change duration (minutes), or utilisation 6 steps (1 h) ahead from recent lags and time of day. Ranked by RMSE on a held-out 25%. |
| Reinforcement learning | **Tabular Q-learning** | In a simulated rollout, learns the wave size per (devices, risk) bucket that balances speed against devices hit by a failed wave (blast-radius penalty). Recommendations never exceed the change size; ties go to the smaller wave. |

### 31.3 Reading the numbers

- **ROC AUC**: 0.5 is chance, 1.0 perfect. **Recall**: share of failing changes the
  model catches. **Precision**: share of flagged changes that really failed.
  **Threshold**: the probability above which a change is called risky.
- **RMSE / MAE**: typical error in the target's units (minutes, or utilisation 0–1).
  **R²**: 1.0 perfect, 0 no better than the average.
- **Silhouette**: −1 to 1; above ~0.25 means clusters are reasonably distinct.

### 31.4 Limits

Models are trained on request and kept in memory until the API restarts. They are
advisory: nothing changes `VIREONET_WAVE_SIZE` or any workflow. Change-history models
only reflect your network once you have enough finished changes.

## 32. Integrations: webhooks, signals, callbacks

### 32.1 Authentication for machine callers

`POST /changes`, `/changes/<id>/signals/ci`, `/changes/<id>/signals/merge`,
`/webhooks/github|gitea|bitbucket` accept either:
- **HMAC**: header `X-VireoNet-Signature: sha256=<hex HMAC-SHA256 of the raw body with VIREONET_WEBHOOK_SECRET>`, or
- **Bearer**: `Authorization: Bearer <VIREONET_WEBHOOK_SECRET or VIREONET_API_TOKEN>`.

With neither secret set, calls are refused unless `VIREONET_DEV=true`. An HMAC
signature is only accepted when `VIREONET_WEBHOOK_SECRET` is set (a signature header
with only `VIREONET_API_TOKEN` configured gets 401, so nobody can sign with an empty
key). Secrets are compared in constant time.
`/webhooks/gitlab` checks `X-Gitlab-Token` against `VIREONET_GITLAB_WEBHOOK_TOKEN`.
`/drift/events` checks `X-VireoNet-Event-Token` (or an HMAC signature) against
`VIREONET_DRIFT_EVENT_SECRET`.

### 32.2 Create a change (ServiceNow)

```bash
curl -X POST http://vireonet:8000/changes \
  -H "Authorization: Bearer $VIREONET_WEBHOOK_SECRET" -H "Content-Type: application/json" \
  -d '{"intent": {"change_type": "vlan_add", "vlan_id": 120, "vlan_name": "app-tier",
       "interfaces": ["Ethernet1/10"], "devices": ["leaf-01", "leaf-02"], "site": "dc1",
       "ticket_number": "RITM0001234", "requested_by": "alice"}}'
```

Or `{"ticket_number": "RITM0001234"}` / `{"sys_id": "…"}` to fetch the intent from
ServiceNow. `"emergency": true` bypasses the change window only.

**Intent fields by change type**

| Change type | Fields (besides devices, site, ticket_number, requested_by) |
|---|---|
| `vlan_add` | `vlan_id` 1–4094, `vlan_name` 1–32 chars, `interfaces` (optional list) |
| `vlan_remove` | `vlan_id`, `vlan_name` (optional; required for Junos/PAN-OS) |
| `acl_entry_add` | `acl_name`, `action` permit/deny, `protocol` tcp/udp/icmp/ip, `source`, `destination` (CIDR, `host A`, or `any`), `destination_port` 1–65535 (optional) |
| `acl_entry_remove` | `acl_name`, `sequence` |
| `static_route_add` | `prefix` (IPv4 CIDR), `next_hop`, `vrf` (default `default`), `admin_distance` 1–255 (default 1) |
| `static_route_remove` | `prefix`, `vrf`, `next_hop` (optional; required for NX-OS/EXOS) |
| `interface_set` | `interface`, `description`, `enabled` true/false, `mtu` 576–9216 (all optional except interface) |

### 32.3 Git provider webhooks

Point your Git provider at `/webhooks/gitlab` (or github/gitea/bitbucket). Pipeline
events signal CI pass/fail (the ticket comes from the pipeline variable `ticket`);
merged merge requests signal the merge (ticket from the title prefix `RITM…:`).
Drift merge requests (`drift/<device>/<record>`) update their drift records when
merged or closed.

### 32.4 Signals from people

| Signal | Route | Role |
|---|---|---|
| Approve / reject | `POST /changes/<id>/signals/approval` `{"approved": true, "reason": "…"}` | approver |
| Abort | `POST /changes/<id>/signals/abort` `{"reason": "…"}` | operator |
| Roll back | `POST /changes/<id>/signals/rollback` `{"reason": "…"}` | operator |
| Reconcile override | `POST /changes/<id>/reconcile-override` `{"reason": "…"}` | approver |

### 32.5 ZTP

`POST /ztp/devices` needs an operator login, or (for provisioning systems) the header
`X-VireoNet-ZTP-Token: <VIREONET_ZTP_REGISTRATION_TOKEN>`; anonymous calls get 401.
`GET /ztp/devices/<serial>` needs a viewer login. It registers a device (hostname, serial, mac_address, model, platform,
site) and returns a one-time token (shown once, stored hashed, expires after
`VIREONET_ZTP_TOKEN_TTL_SECONDS` = 15 min). The device calls `POST /ztp/callback` with
its serial and token; a bad token gets 401.

## 33. AWX job templates and playbooks

Real mode refuses to start if a mapped job template is missing in AWX. Mapping:
`policies/execution.yaml`.

| Step | AWX job template | Playbook | Key extra vars |
|---|---|---|---|
| backup / collect_state | `backup` | `ansible/playbooks/backup.yml` | `platform`, `ticket` |
| pre_check | `pre-check` | `pre_check.yml` | — |
| dry_run | `dry-run` | `dry_run.yml` | `intended_config`, `config_format`, `platform` |
| apply | `apply` | `apply.yml` | `intended_config`, `intended_lines` (JSON), `config_format`, `platform`, `change_type` |
| post_check | `post-check` | `post_check.yml` | as apply |
| rollback | `rollback` | `rollback.yml` | `platform`, `restore_config` + `git_ref` (Git source) or the backup file |
| run_commands | `run-commands` | `run_commands.yml` | `commands` (JSON list, read-only) |
| ZTP day-1 | `ztp-day1` | `ztp_day1.yml` | — |
| python runner | `run-python` | `run_python.yml` | `request_json` |
| DHCP reload | `dhcp-reload` (`VIREONET_DHCP_RELOAD_TEMPLATE`) | `dhcp_reload.yml` | — |

Vendor modules used by the playbooks: `cisco.ios`, `cisco.nxos`, `arista.eos`,
`junipernetworks.junos` (confirmed commit, 10-minute auto-rollback window then
confirm), `community.network.exos`. Other platforms use `ansible.netcommon.cli_config`,
which needs a cliconf plugin for that platform in the AWX Execution Environment; if it
is missing, the job fails and the change rolls back. Their dry-run is render-only (the
config is printed, not validated on the device).

Output contracts the engine reads: `VIREONET-BACKUP-BEGIN/END` (backup),
`VIREONET-COMMANDS-BEGIN/END` (run-commands, a JSON list), `VIREONET-RESULT-BEGIN/END`
(python runners).

---

# Part IV — Operating VireoNet

## 34. Daily workflows

### 34.1 Submit a change

1. Raise the request in ServiceNow; its webhook calls `POST /changes` (§32.2).
2. VireoNet validates (NetBox, policy, rendering for every device's vendor). A bad
   request is rejected with the reason; nothing is created.
3. The change appears on **Changes** as `received` → `validated` → `planned`. Open it
   and check the **Diff** tab: it shows the exact config for each device.
4. A merge request opens in Git; CI runs (lint, render, Batfish, optional lab). A red
   pipeline ends `ci_failed` — fix the intent and resubmit; never merge a red pipeline.
5. Green CI → `ci_passed` → the change appears on **Approvals**.

### 34.2 Approve a change

1. **Approvals** → **Awaiting approval** → **Show** evidence, or **Open full detail**.
2. Check the Diff, Checks and Batfish tabs. Optionally score it on **Intelligence →
   Classification → Score a proposed change**.
3. **Approve** (with a reason). Also make sure the merge request is merged — the
   workflow needs both.
4. Watch the detail page: `approved` → `pre_checked` → `applied` → `post_checked` →
   `closed`. It updates by itself.

### 34.3 Stop a change before it touches devices

Open the change → **Abort** → confirm. Works from `received` to `pre_checked`. The
change ends `rejected`; locks are released; nothing is applied.

### 34.4 Undo a change

Automatic after any failed apply or post-check. To undo by hand: open the change while
it is `applied` or `post_checked` (set a soak window, §25.4, to give yourself time) →
**Roll back** → confirm → wait for `rolled_back`. Each device is restored from its Git
commit recorded before the change, or from the AWX backup.

### 34.5 A rollback failed (`rollback_failed`)

1. The devices may be half-changed and are **still locked**, so no other change can land.
2. Open the change → **AWX job output** and the AWX rollback job for each device.
3. Restore by hand from the file named in the job (the Git commit content or the
   backup at `/var/lib/awx/backups/<ticket>/<device>.cfg`). For FortiOS, PAN-OS,
   Nokia and Extreme this is always manual (§25.6).
4. Verify the device, then **Locks and change windows** → **Release** each lock.
5. Run a drift scan (**Drift → Start scan**) to confirm the device matches Git.

### 34.6 Handle a failed check

| State | Meaning | What to do |
|---|---|---|
| `ci_failed` | Lint, render, Batfish or lab test failed. | Fix the intent; push a new commit; do not merge. |
| `pre_check_failed` | A pre-check failed (device inactive in NetBox, lock held, no backup, your template check). | Read the **Checks** tab. "locked by RITM…": see Locks. Fix the cause; submit a new change. |
| `rolled_back` | Apply or post-check failed and the devices were restored. | Read Checks and AWX output; fix; resubmit. |
| `rejected` with "render: …" | A device's vendor cannot render this change type. | Add the template named in the message (§27) or change the device list. |

### 34.7 Investigate drift

**Drift** → filter by site/severity → open a record → read **Diff vs baseline** and
**Attribution** → decide: **Roll back** (choose the Git target), **Merge** (make the
drifted config the new intent) or **Grant exception** (with owner and expiry).

### 34.8 Look at the network

**Topology**: red links are busy (80%+). Click through to the links table for exact
rates. **Intelligence → Anomaly detection** lists interfaces whose traffic broke
their own pattern; **Clustering** groups interfaces by behaviour.

### 34.9 Add a check or a vendor

1. **Check templates → Edit and preview** (or **Config templates**), edit the draft.
2. **Validate** and **Preview** on each platform you care about.
3. Copy the YAML into `policies/checks.yaml` / `policies/render_templates.yaml`, open a
   merge request, get it reviewed, merge, restart the API and worker.

### 34.10 Register and bootstrap a ZTP device

Register in NetBox → `POST /ztp/devices` (§32.5) → the device boots via DHCP and calls
back → identity check → day-1 render, Batfish, AWX push → `active`. Stuck? **ZTP
devices → Retry** or **Reset**.

### 34.11 Manage users

**IAM → Users**: create (12+ character password), change role (revokes their
sessions), deactivate. You cannot deactivate yourself or the last admin.

## 35. Health, logs and metrics

1. **System health** (§20) and the toolbar chip: everything `ok`.
2. **Temporal UI** (`:8233`): search the ticket number (the workflow ID) to see the
   exact step a change is on or failed at.
3. **Logs**: `docker compose logs -f api worker` — JSON, ticket number as correlation
   ID, no secrets.
4. **Metrics**: `GET /metrics` (Prometheus; when `VIREONET_METRICS_TOKEN` is set, the
   scraper must send `Authorization: Bearer <token>`): `vireonet_changes_total{state}`,
   `vireonet_adapter_calls_total`, `vireonet_check_failures_total`,
   `vireonet_device_locks_held`, `vireonet_ztp_total`. Alert on
   `vireonet_changes_total{state="rollback_failed"}` increasing.
5. **Telemetry**: `GET /telemetry/health`.

## 36. Security notes

- **Secrets** belong in Vault or the environment, never in Git, tickets or logs. The
  UI never shows secrets.
- **Real mode refuses to start** with the default JWT secret, the default database
  password, no webhook authentication, invalid templates, or missing AWX templates.
- **Dev mode** (`VIREONET_DEV=true`) relaxes webhook authentication. Never use it in
  production.
- **Template safety**: read-only command prefixes, sandboxed Jinja, control characters
  refused in intent values, interface names without spaces or quotes (§26.4, §27.5).
- **Login lockout**: after `VIREONET_LOGIN_MAX_FAILURES` (5) failed sign-ins for one
  username, further attempts get **429** for `VIREONET_LOGIN_LOCKOUT_SECONDS` (300 s).
  The counter is per API process; with several replicas add a shared limiter at the
  gateway as well.
- **Health endpoints** `/adapters/health` and `/system/health` need a viewer login;
  `/healthz` stays open for liveness probes. `/metrics` can be protected with
  `VIREONET_METRICS_TOKEN`.
- **ZTP registration** needs an operator login or `VIREONET_ZTP_REGISTRATION_TOKEN` (§32.5).
- **Webhook HMAC** needs `VIREONET_WEBHOOK_SECRET`; an empty key is never used (§32.1).
- **Remaining caveat**: the UI keeps its login token in browser local storage; keep
  the UI on a trusted origin and keep `VIREONET_ACCESS_TOKEN_EXPIRE_MINUTES` short.

## 37. Troubleshooting and FAQ

**"Invalid username or password." but the password is right.** The request may not
have reached the API. Test with
`curl -X POST http://<api>:8000/auth/login -H 'Content-Type: application/json' -d '{"username":"…","password":"…"}'`.
If curl gets a token, it is CORS or the API address (next two items).

**CORS error / login fails with 400.** Add the UI's origin to `VIREONET_CORS_ORIGINS`
and restart. Check: `curl -i -X OPTIONS http://<api>:8000/auth/login -H "Origin: http://<ui-host>:5173" -H "Access-Control-Request-Method: POST"` must return `access-control-allow-origin: http://<ui-host>:5173`.

**`ERR_CONNECTION_REFUSED`.** The UI was built for `http://localhost:8000` and you are on
another machine. Rebuild with `VITE_API_URL=http://<host>:8000`, or `/api` behind nginx (§3).

**Blank white page.** Hard refresh (Ctrl+Shift+R); then check the browser console.

**Signed out unexpectedly.** Your 8-hour session ended, or an admin changed your role or
deactivated you. Sign in again.

**A change was rejected with "render: … no render template for X on 'Y'".** That vendor
cannot render that change type (§28). Add the template the message names, or split the change.

**"no renderer for platform …" / "platform unknown".** Set the device's platform in
NetBox to a supported slug (§27.4).

**A check template change does nothing.** Templates load at start: restart the API
and worker. An invalid file stops the worker — read its log or the red list on the
Check templates page.

**Abort or Roll back returns 409.** The change has moved past the point where that
action works (§25.5). For a closed change, raise a new change that reverts it.

**`rollback_failed`.** §34.5.

**A lock will not clear.** Locks and change windows → Release (operator), only when
the change holding it is finished or dead. Locks also expire after the TTL.

**The topology has no traffic.** `VIREONET_TELEMETRY_BACKEND` is `none`, or the tool's
device/interface names differ from NetBox. Check `GET /telemetry/health` and the amber
error line on the page.

**The topology map is empty.** No devices in NetBox for that site, or no
interface-to-interface cables.

**ML says "synthetic training data".** Fewer than 40 finished changes exist; results are
illustrative until real history builds up.

**ML anomaly / clustering returns 503.** Telemetry is disabled.

**Drift rollback refused: "cannot restore … from Git".** The chosen commit is not in the
device's history, or Git is unreachable. Choose "Head of main", or fix the Git settings (§29).

**Lists are empty in real mode.** Expected until real systems create things.

**Everything says ok but nothing is connected.** You are in fake mode.

**System health: database "unknown" but overall "ok".** Not verified by that check; only
worry if writes fail.

**Config repos say "No commits yet".** That page's history view is not implemented; real
history is used by the drift rollback picker.

**The worker exits at start with "TypeError: __annotations__ must be set to a dict object".**
Fixed in this build (Temporal sandbox pass-through for SQLAlchemy 2.1+). If you see it,
you are running an older worker image.

**Drift scans are scheduled at worker start.** The worker creates the drift schedules in
Temporal (an existing schedule is left as it is). Earlier builds logged "drift schedule
not created"; that is fixed. You can still trigger a scan from **Drift → Start scan**.

**An Excel push was rejected: "… is not allowed in a config push".** A line matched the
raw-config denylist (§45.2). Remove it, or change the policy if your organisation allows it.

**Reported in an earlier walkthrough (not re-verified in this build):** filtering
Changes by some device values (for example `leaf-02`, a blank, or `-`) failed with
"Failed to load changes: Failed to fetch" — clear the Device filter; and some filter
fields repopulated after **Clear filters**.

---

# Part VI — Integration guide

## 43. End-to-end flow, adapter by adapter

This is exactly what the orchestrator does for a change, which adapter does each step,
which pre/post-checks run where, and what happens when a step fails. Every step is a
Temporal activity, so it survives restarts and is visible in the Temporal UI under the
ticket number.

| # | Step | Adapter / system | What happens | On failure |
|---|---|---|---|---|
| 1 | **Intake** | ITSM (ServiceNow / Jira / Freshservice / BMC Helix) → VireoNet API | The ticket arrives by webhook: `POST /changes` (intent), `POST /webhooks/servicenow` or `/webhooks/itsm/state` (state trigger, §45.5: the ticket entering Implement starts a fully automatic Excel push inside its planned window), or `POST /webhooks/itsm/config-push`. | 401/409/422 back to the caller; nothing is created. |
| 2 | **Validate** | NetBox, policy, renderer | Devices exist and are `active` in NetBox; policy (device limit, protected devices/VLANs, change window, raw-config denylist for Excel); every device's platform (from NetBox) can render this change (§27, §28). | Change ends `rejected` with the reason; ITSM gets a note. |
| 3 | **Plan** | Renderer | Waves are planned; each device's config is rendered (or taken from Excel). Shown on the **Diff** tab. | `rejected` (render errors). |
| 4 | **CI** | Git provider (GitLab / GitHub / Gitea / Bitbucket) + Batfish (+ EVE-NG / containerlab) | A merge request opens; CI runs lint, render, Batfish, optional lab test, and posts the result back by webhook. | `ci_failed`; ITSM note. |
| 5 | **Approve + merge** | People (Approvals page) + Git — or the ITSM state | The workflow waits for **both** an approval and the merge. Fully automatic pushes are pre-approved by the ITSM state (§45.4). | `rejected` on reject or 24 h timeout. |
| 5b | **Planned window** | Ticket planned start/end | Wait for the start; refuse after the end (§45.6). | `rejected`, nothing pushed. |
| 6 | **Pre-check** | Lock service, monitoring, AWX, Git, check templates, Excel/ITSM | Lock every device → silence alerts (Alertmanager/LibreNMS) → **AWX `backup`** of every device → record each device's **Git commit** on main/master → **AWX `pre-check`** baseline → **pre-check templates** (built-ins + your command checks via AWX `run-commands`, raw or **TextFSM**) → for Excel pushes, write each running config into the workbook's backup sheet and attach it to the ticket. | Locks released; `pre_check_failed`; ITSM note. |
| 7 | **Dry-run** | AWX `dry-run` | Every device, its own vendor config, in check mode. Nothing changes. | Stops before any device is touched → rollback path not needed; `rolled_back` is recorded with no device changed. |
| 8 | **Apply in waves** | AWX `apply` | `VIREONET_WAVE_SIZE` devices per wave, one job per device, waves one after another. | Wave failure stops the rollout → **rollback** (step 11). |
| 9 | **Post-check** | AWX `post-check`, check templates | Idempotency re-check per device (Cisco/Arista) + **post-check templates** (raw or TextFSM). | **Rollback**. |
| 10 | **Soak** (optional) | People | `VIREONET_POST_APPLY_SOAK_SECONDS`: watch the network, press **Roll back** if needed. | Manual rollback. |
| 11 | **Rollback** (only on failure/request) | AWX `rollback`, Git, Excel | Per device, in this order: the **Excel Rollback config** (pushed like an apply) → the **Git commit recorded at pre-check** (full restore) → the **AWX pre-change backup**. | `rollback_failed`, devices stay locked, ITSM note, chat alert. |
| 12 | **Reconcile** | Git, NetBox | The new running config is committed to the device's Git file (main = running config); NetBox is updated. | `reconciliation_pending` (approver override). |
| 13 | **Close** | ITSM, NetBox, monitoring, notifications | NetBox journal entry per device; alerts un-silenced; ticket **closed** in the ITSM tool; Slack/Teams/email notice; locks released. | Recorded in evidence; the change still closes. |

ITSM work notes are posted at: planned, ci_failed, approved, pre_checked,
pre_check_failed, applied, post_checked, rolled_back, rollback_failed, rejected,
reconciliation_pending, failed. Notes and closure are best effort: an unreachable ITSM
tool never blocks a change.

**Drift** runs a separate flow: AWX `collect_state` (scheduled, on demand, or on a
device event) → compare with Git/NetBox/intent/compliance → record → people decide
(roll back to a Git version, merge into intent, exception) (§12, §29).

**Log analytics** (§47): AWX `run-commands` `show logging` → TextFSM → anomaly
detection → optional NetBox journal entry.

## 44. Adapter integration checklist

Set each adapter's variables (§38), restart the API and worker, then verify on
**System health** and **Adapters**. In real mode the API refuses to start when
required settings are missing or unsafe.

| Adapter | Used for | Minimum settings | Verify |
|---|---|---|---|
| **ServiceNow** | Intake, approvals state, notes, closure, attachments | `VIREONET_SERVICENOW_BASE_URL`, OAuth client id/secret (or basic user/password with `VIREONET_SERVICENOW_AUTH_MODE=basic`) | System health → servicenow `ok`; `GET /itsm/tickets/<RITM>` |
| **Jira** (Software / Service Management) | Intake (via webhook), notes, attachments, closure | `VIREONET_ITSM_PROVIDER=jira`, `VIREONET_JIRA_BASE_URL`, Cloud: `VIREONET_JIRA_EMAIL` + `VIREONET_JIRA_API_TOKEN`; Data Center: `VIREONET_JIRA_PAT`; `VIREONET_JIRA_CLOSE_TRANSITION` (default `Done`) | `GET /itsm/health`, `GET /itsm/tickets/NET-1` |
| **Freshservice** | Same as Jira | `VIREONET_ITSM_PROVIDER=freshservice`, `VIREONET_FRESHSERVICE_BASE_URL`, `VIREONET_FRESHSERVICE_API_KEY` | `GET /itsm/health` |
| **BMC Helix ITSM / Remedy** | Same (incidents, work-log attachments) | `VIREONET_ITSM_PROVIDER=bmc_helix`, `VIREONET_BMC_BASE_URL`, `VIREONET_BMC_USERNAME`, `VIREONET_BMC_PASSWORD` | `GET /itsm/health` |
| **NetBox** | Devices, platforms, cables (topology), VLANs, journal entries | `VIREONET_NETBOX_BASE_URL`, `VIREONET_NETBOX_TOKEN`. Every device needs a **platform** (§27.4) and a primary IP; cables between interfaces for the topology. | System health → netbox `ok`; Topology shows devices |
| **AWX** | Every device action | `VIREONET_AWX_BASE_URL`, `VIREONET_AWX_TOKEN`, job templates from §33 in the EE named by `VIREONET_AWX_EXECUTION_ENVIRONMENT` | Real mode refuses to start if a template is missing |
| **Git provider** | Merge requests, CI webhooks, device config versions, rollback source | `VIREONET_GIT_PROVIDER`, `VIREONET_GIT_BASE_URL`, `VIREONET_GIT_REPO`, token in Vault at `VIREONET_GIT_TOKEN_SECRET`; layout §29 | Drift page → "Roll back to" shows commits |
| **Batfish** | CI gate | `VIREONET_BATFISH_HOST`, `_PORT` | Batfish tab on a change |
| **Vault** | All secrets | `VIREONET_VAULT_ADDR`, AppRole id/secret | System health → vault `ok` |
| **Telemetry** (Zabbix, Prometheus SNMP/gNMI, LibreNMS, PRTG, SolarWinds) | Topology traffic, ML | §30 | `GET /telemetry/health`; Topology labels |
| **Monitoring** (Alertmanager, LibreNMS) | Silence alerts during changes | `VIREONET_MONITORING_BACKEND`, URL, token | Alerts silenced during a change |
| **Notifications** (Slack, Teams, email) | Completed / failed / rolled back notices | webhook URLs or SMTP | A test change |
| **Labs** (EVE-NG, containerlab) | Optional CI lab test | §38 Labs | System health → Lab backends |
| **DHCP** (ISC / Kea) | ZTP reservations | §38 ZTP and DHCP | A ZTP registration |

**Wiring the ITSM tool to VireoNet**
- *ServiceNow*: a Flow Designer / Business Rule on the catalog item (or change) calls
  `POST /changes` with the intent or `{"ticket_number": …}`; for Excel, call
  `POST /webhooks/itsm/config-push` with `{"ticket_number", "site"}`. Authenticate with
  `Authorization: Bearer <VIREONET_WEBHOOK_SECRET>`.
- *Jira*: an Automation rule ("Issue transitioned to Approved" → "Send web request")
  calling the same endpoints.
- *Freshservice / BMC Helix*: a workflow automator / process with an HTTP call.

## 45. Excel config push

For teams whose ITSM process already carries device config in a spreadsheet.

### 45.1 The workbook

| Sheet | Columns | Notes |
|---|---|---|
| `config` (required) | **Device** \| **Config** | One row per device. Device = NetBox hostname. Config = the full config block (a multi-line cell). A row with an empty Device continues the previous device's block. |
| `backup` (optional) | **Device** \| **Rollback config** \| **Running config (pre-change)** \| **Taken at** | *Rollback config* is yours: pushed to undo the change if apply or post-check fails. The other two columns are filled in by VireoNet at pre-check. |
| `readme` | — | Instructions; ignored. |

Sheet and header names are case-insensitive; extra sheets and columns are ignored.
Download a template from **Config push (Excel) → Download template workbook**.

### 45.2 Rules (checked before anything is created)

- Every device appears once and is a valid hostname; config is not empty; no control characters.
- Size: workbook ≤ `VIREONET_EXCEL_MAX_BYTES` (2 MB), each device ≤
  `VIREONET_EXCEL_MAX_DEVICE_CONFIG_BYTES` (200 KB).
- **Raw-config denylist** (policy `raw_config_deny_patterns`, case-insensitive per line):
  `reload`, `write erase`, `erase`, `format`, `delete flash/bootflash/nvram/disk`,
  `username`, `enable secret/password`, `crypto key zeroize`, `aaa …`, Junos
  `request system reboot/halt/power-off/zeroize`, `set/delete system login|root-authentication`,
  FortiOS `execute factoryreset/reboot/shutdown`, `config system admin`, PAN-OS
  `mgt-config users`, SR OS `admin reboot`, EXOS `unconfigure switch` / `reboot`. Edit
  the list in `policies/default.yaml` if your organisation needs different rules.
- Normal policy applies too (device limit, protected devices, change window).

### 45.3 Using it

**Config push (Excel)** page (Automation):
- *Upload a workbook*: choose the `.xlsx` → the preview lists every device with config
  and rollback line counts, errors (red) and warnings (amber, for example "no rollback
  config; rollback uses Git / the AWX backup") → enter **Ticket number** and **Site**
  (optionally **Emergency**) → **Create change from workbook** (operator+).
- *From an ITSM ticket*: enter the ticket → **Look up** shows the ticket and its
  attachments → **Create change from ticket**. VireoNet uses the newest `.xlsx`/`.xlsm`
  attachment with a `config` sheet.
- The change then runs the full pipeline (§43). Its detail page has **Download
  workbook**; after pre-check that copy includes every device's pre-change running
  config, and the same file is attached to the ticket with a note.

The pushed config is exactly what is in the cell (one line per line). Format is
detected: all `set`/`delete` lines → set format (Junos, PAN-OS, SR Linux), else CLI.

### 45.4 Manual and end-to-end automation

Every Excel push runs in one of two modes. Both keep every safety step: policy and
denylist, locks, backups, Git commit, dry-run on every device, waves, post-checks,
automatic rollback, ITSM notes and closure. CI is always "not applicable" for Excel
pushes (there is no merge request; the dry-run on every device is the gate), recorded
in the audit trail.

| | **Manual** | **Fully automatic (end to end)** |
|---|---|---|
| Started by | An operator on the Config push page (upload or ticket) | The ITSM tool, when the ticket enters a trigger state (default **Implement**) — or an approver on the Config push page |
| Approval | An approver presses **Approve** on the change page | The ITSM state is the approval (CAB happened there), or the submitting approver's submission. Recorded, e.g. "approved in servicenow: ticket CHG0030001 entered state 'Implement'" |
| Planned window | Optional; enforced when given | **Required** (`VIREONET_ITSM_REQUIRE_PLANNED_WINDOW=true`) |
| Clicks | Approve | None |

Choosing **Fully automatic** on the Config push page needs the **approver** role, because
it skips the approval click.

### 45.5 The ITSM state trigger

Point your ITSM tool at **`POST /webhooks/servicenow`** (or the generic
**`POST /webhooks/itsm/state`**) on every state change of the ticket type you use, with
the webhook secret (`Authorization: Bearer <VIREONET_WEBHOOK_SECRET>`) and a body like
`{"number": "CHG0030001"}` (Jira: `{"key": "NET-42"}`; any: `{"ticket_number": "…"}`).

What VireoNet does:
1. Re-reads the ticket from the ITSM tool — the state in the payload is never trusted.
2. If the state is not in `VIREONET_ITSM_TRIGGER_STATES` (default `implement`, `-1`, matched
   case-insensitively against the state name and its raw value): returns `ignored`.
3. Reads the planned window from the ticket (§45.6). Refuses (409) when there is none, or
   when it is already over.
4. Reads the newest `.xlsx` attachment with a `config` sheet and creates the change, fully
   automatic (`VIREONET_ITSM_TRIGGER_APPROVES=true`; set it to `false` to have a person
   still approve in VireoNet). The site comes from the payload, the ticket's
   `site`/`u_site` field, or `VIREONET_ITSM_DEFAULT_SITE`.
5. Repeats are harmless: the ticket number is the change key (`exists`).

ServiceNow example (Business Rule on `change_request`, *after update*, when *State changes*):

```javascript
(function executeRule(current, previous) {
  var r = new sn_ws.RESTMessageV2();
  r.setEndpoint('https://vireonet.example.com/api/webhooks/servicenow');
  r.setHttpMethod('POST');
  r.setRequestHeader('Content-Type', 'application/json');
  r.setRequestHeader('Authorization', 'Bearer ' + gs.getProperty('x_vireonet.webhook_secret'));
  r.setRequestBody(JSON.stringify({ number: current.getValue('number') }));
  r.executeAsync();
})(current, previous);
```

Both Change Requests (`CHG…`, table `change_request`) and request items (`RITM…`,
`sc_req_item`) are supported. Work notes are written without touching the state, and
Change Requests are closed with close code *successful*.

### 45.6 The planned window

The window comes from the ticket: ServiceNow Change Request **Planned start / Planned
end** (`start_date`, `end_date`), Jira custom fields (`VIREONET_JIRA_PLANNED_START_FIELD`,
`VIREONET_JIRA_PLANNED_END_FIELD`), Freshservice `planned_start_date` / `planned_end_date`,
BMC Helix *Scheduled Start / End Date*; or from the Planned start/end fields of an upload.

| Moment | What happens |
|---|---|
| Before the planned start | The change is created and approved but **waits** (durable timer; survives restarts). No lock, backup or command touches any device. The change page says "waiting for the planned start; nothing is pushed before it". Abort still works. |
| Planned end passed before pre-check or apply | The change ends `rejected` ("planned window ended …; nothing was pushed"); locks released. |
| Planned end reached during the rollout | No further wave starts; the waves already applied are **rolled back**, so the network is never left half-changed outside the window. |

The policy change window (`policies/default.yaml`) is judged at the **planned start**, so a
ticket approved during the day for a 02:00 window is accepted; `emergency` bypasses only
the policy window, never the planned window.

## 46. TextFSM

TextFSM turns show-command output into fields. VireoNet ships the full
**ntc-templates** library (~1,000 templates, Apache-2.0) and lets you add your own.

### 46.1 Where templates come from

1. **Your templates**: `VIREONET_TEXTFSM_USER_DIR` (default `policies/textfsm/`), with an
   ntc-style `index` file. **They win** over ntc for the same platform and command.
2. **ntc-templates**, bundled.

Platform mapping: `ios`→`cisco_ios`, `ios-xe`→`cisco_xe` then `cisco_ios`,
`nxos`→`cisco_nxos`, `eos`→`arista_eos`, `junos`→`juniper_junos`, `fortios`→`fortinet`,
`panos`→`paloalto_panos`, `sros`→`alcatel_sros`/`nokia_sros`, `srlinux`→`nokia_srl`,
`exos`→`extreme_exos`; other names are used as-is.

### 46.2 Writing your own template — step by step

1. Collect real output from the device (`show …`), including edge cases.
2. On the **TextFSM** page, paste it into **Device output** and set the **Command**.
3. Click an existing template to start from it (or start empty), edit it in the
   template box, and tick **Parse with this template**.
4. **Validate template** (it must compile; you see the Value names), then **Parse** —
   the table shows the rows.
5. Save it as `policies/textfsm/<platform>_<command>.textfsm`, add an index line:

   ```
   Template, Hostname, Platform, Command
   acme_show_widgets.textfsm, .*, cisco_ios, sh[[ow]] wid[[gets]]
   ```

   (`sh[[ow]]` matches `sh`, `sho`, `show`.) Open a merge request; restart the API and worker.

Example template (`show vlan brief` style):

```
Value VLAN_ID (\d+)
Value NAME (\S+)
Value STATUS (active|suspend|act/lshut|sus/lshut)

Start
  ^${VLAN_ID}\s+${NAME}\s+${STATUS} -> Record
```

### 46.3 Using parsed fields in checks

See §48.4 — `parse: textfsm` plus `expect.row` / `expect.no_row`.

## 47. Log analytics (show logging → ML)

**Intelligence → Log anomalies (show logging)**.

1. **Collect**: `show logging` (Junos: `show log messages | last 2000`) from every device
   — via AWX `run-commands` in real mode, a deterministic fake syslog in fake mode
   (with injected incidents: link flapping on leaf-02, BGP flaps on border-01, SSH brute
   force on fw-02, power/fan failure on spine-02).
2. **Parse** with TextFSM: VireoNet's syslog templates for Cisco IOS/IOS-XE/NX-OS/Arista
   (`%FAC-SEV-MNEMONIC`) and Junos; your own templates in `policies/textfsm` override them.
   Vendors without a template are tried with the Cisco-style template and say so.
3. **Detect**, three explainable ways:
   - **Burst**: one event type far above the device's own normal per 30-minute window
     (robust z-score > 6 and ≥ 10 events) — e.g. "LINK-UPDOWN x55 in 30 min (normal 0/window)".
   - **Unusual mix**: Isolation Forest over every device's windows (counts of the most
     common event types and severities, scaled per device).
   - **Critical / new**: any severity 0–2 event; an event type not seen earlier in the
     period (if repeated or error-level).
4. **NetBox** (optional, operator+): tick **Write summaries to NetBox journal** — one
   journal entry per anomalous device (`danger` for critical events, else `warning`)
   with the explanations. **Raw logs never go to NetBox** — NetBox is a source of truth,
   not a log store.

Results: devices table (status, events, top events, activity sparkline), anomaly cards
with sample lines and interfaces, NetBox results, and collection details per device
(lines, parsed, unparsed, template).

**Recommended production design**: send device syslog to a log platform (Loki, Elastic,
Graylog, Splunk) for retention and search, and run this analysis on demand or on a
schedule; use NetBox journal entries only for the human-readable summary.

## 48. Writing pre- and post-check templates — a how-to

### 48.1 Decide what to prove

For each change type ask: *before* — is it safe to start? *after* — did exactly what we
wanted happen, and nothing else? Write one check per fact, named in plain words
(`vlan_id_unused`, `route_installed`).

### 48.2 Step by step

1. Open **Check templates → Edit and preview**. The editor starts from the active
   `policies/checks.yaml`.
2. Find (or add) your change type under `change_types:`; add a check under `pre:` or `post:`.
3. Give it a `command` for every platform you run (`ios`, `nxos`, `eos`, `junos`, …) or a
   `default`.
4. Write the expectation: `contains` / `not_contains` / `regex` / `not_regex` on the
   text, or `parse: textfsm` with `row` / `no_row` on fields.
5. Decide severity: `on_fail: block` (default) or `warn`; and
   `on_missing_platform: skip` (default) or `fail`.
6. **Validate** (fix any error, §26.5), then **Preview checks** on each platform with a
   sample intent and read the exact commands and expectations.
7. Commit to `policies/checks.yaml` through a merge request; restart the API and worker.
8. Run a change in a lab (or fake mode) and read the **Checks** tab.

### 48.3 Cookbook (copy and adapt)

**Pre: the VLAN id is free** (text)

```yaml
- name: vlan_id_unused
  command: {ios: "show vlan id {{ intent.vlan_id }}", nxos: "show vlan id {{ intent.vlan_id }}", eos: "show vlan {{ intent.vlan_id }}"}
  expect: {not_regex: "^{{ intent.vlan_id }}\\s+\\S+\\s+active"}
```

**Pre: the next hop is reachable**

```yaml
- name: next_hop_reachable
  command: {ios: "show ip route {{ intent.next_hop }}", junos: "show route {{ intent.next_hop }}"}
  expect:
    default: {not_contains: "not in table"}
    junos: {regex: "\\*\\["}
```

**Pre: the interface is up before changing it (warn only)**

```yaml
- name: interface_up_before
  on_fail: warn
  command: {default: "show interfaces {{ intent.interface }}"}
  expect: {regex: "line protocol is up"}
```

**Post: route installed via the right next hop**

```yaml
- name: route_installed
  command: {ios: "show ip route {{ intent.prefix.network_address }}", nxos: "show ip route {{ intent.prefix }}"}
  expect: {contains: "{{ intent.next_hop }}"}
```

**Post: ACL entry present**

```yaml
- name: acl_entry_present
  command: {ios: "show ip access-lists {{ intent.acl_name }}"}
  expect: {contains: "{{ intent.action }} {{ intent.protocol }}"}
```

**Post: no interface went err-disabled anywhere** (negative check)

```yaml
- name: no_err_disabled
  command: {ios: "show interfaces status err-disabled", nxos: "show interface status err-disabled"}
  expect: {not_regex: "err-disabled"}
```

### 48.4 Checks on parsed fields (TextFSM)

```yaml
- name: vlan_active_parsed
  parse: textfsm                    # your template in policies/textfsm, else ntc-templates
  command: {ios: "show vlan brief", nxos: "show vlan brief", eos: "show vlan"}
  expect:
    row:                            # at least one parsed row where every field matches (regex)
      vlan_id: "{{ intent.vlan_id }}"
      vlan_name: "{{ intent.vlan_name }}"
      status: "active"

- name: bgp_peers_established
  parse: textfsm
  command: {ios: "show ip bgp summary"}
  expect:
    no_row:                         # no parsed row may match
      state_pfxrcd: "(Idle|Active|Connect).*"
```

Field names are the template's Values in lower case (see the TextFSM page's table
header). List fields are joined with spaces before matching. A missing template fails
the check with "no TextFSM template for …".

### 48.5 Good practice

- Prefer **parsed fields** over regex on text: fewer false matches across versions.
- Keep checks **read-only** and fast; one command per check.
- Use `warn` for "nice to know", `block` for "must be true".
- Test every platform you own in the preview before merging.
- Post-checks should prove the **intent**, not just "no error".

## 49. Trying the full workflow in fake mode

For learning and demos, with nothing real connected:

```bash
VIREONET_ADAPTER_MODE=fake VIREONET_TEMPORAL_MODE=real   # fake devices, real Temporal workflows
```

**Fully automatic (no clicks)**: **Config push (Excel)** → *End-to-end trigger* → click
**CHG0030001 → Implement**; open the change and watch it run to `closed`.
**CHG0030002** waits for its window (opens 3 minutes after start-up); **CHG0030003** (no
window) and **CHG0030004** (window over) are refused.

**Manual**:

1. Sign in; open **Config push (Excel)** → *From an ITSM ticket* → `RITM0005001` (a demo
   ticket with a workbook) → **Create change from ticket**; or download the template,
   upload it with a new ticket number.
2. Open the change. Click **Demo: simulate CI pass + merge** (shown only in fake mode; in
   production these come from your Git provider's webhooks).
3. **Approve**. Watch the timeline move to `closed`; read the **Checks** tab; **Download
   workbook** to see the pre-change running configs.
4. Explore **Topology** (traffic), **Intelligence** (every tab, including
   **Regression** and **Log anomalies**), **TextFSM**, **Check templates**, **Config
   templates**, **Drift**.

In fake mode with a separate worker (`VIREONET_TEMPORAL_MODE=real`), the demo ITSM's
ticket state — work notes, closure, and the updated workbook the worker attaches — is
shared between the API and the worker through the `demo_state` table (migration
`0010`), so `GET /itsm/tickets/CHG0030001` shows what the worker did. The fake **Git**
is still per process (each one is seeded identically from the demo devices), so the
API's commit list does not show commits the worker made; with real Git they are the same.

## 50. Workflow builder (flows)

**Flows** (Automation → *Flows (workflow builder)*) are automations in the style of iPhone
Shortcuts, Power Automate and Nokia NSP Workflow Manager. A **trigger** starts a flow. The flow
is a list of **actions** with variables, conditions, loops, error handling, API calls, Python
scripts, device commands, checks, full VireoNet changes, ITSM updates and approvals by people.
Flows run in Temporal, so they are durable: a wait, an approval or a worker restart picks up
where the flow stopped.

### 50.0 Your first flow in 10 minutes (beginner tutorial)

You need no programming for this. You need a login with the **operator** role (to build and
run) and, for the last part, someone with the **approver** role (to switch the trigger on). In
fake mode the demo user `demo-admin` has every role.

![The Flows page](images/flows-list.png)

**Part A: a flow you run by hand**

1. In the left sidebar, under **Automation**, click **Flows (workflow builder)**. You see your
   flows (empty at first), **Start from a template**, and **Recent runs**.
2. Click **New flow** (top right). The editor opens on the **Designer** tab, in
   **Visual (drag and drop)** mode.
3. In **Flow name**, type `Hello network`. In **Description**, type `My first flow`.
4. The blue **When** box is the trigger. Leave it on **Manually (Run button or API)**.
5. Look at the **Actions** panel on the left. Under *Network*, drag **Get device (NetBox)** and
   drop it on the dashed line that appears under the **When** box. (Or just click it: a click
   adds the action at the end.)
6. A card appears. In **Device name**, type `leaf-01`. In **Save result as variable**, type
   `device`.
7. Drag **Log message** under that card. Click into **Message**, then, in the **Dynamic
   content** panel on the right, click `vars.device`. The field now reads `{{ vars.device }}`.
   Change it to: `leaf-01 runs {{ vars.device.platform }} at site {{ vars.device.site }}`.
8. Click **Validate**. "Valid." appears in green. If a card turns red, read the red text on it:
   it says what is missing.
9. Click **Save**. The message says "Saved as version 1 (draft)". The address bar now shows
   the flow's id.
10. Click **Test run** (tab), then the **Test run** button. The right side shows each step with
    a green *succeeded*. Nothing real happened: a test run only *simulates* actions.
11. Click **Run…** in the toolbar, then **Run**. This time it is real: the run page opens and
    the steps turn green one by one. Click **output** under *Get device* to see what NetBox
    returned.

**Part B: make it decide (If / Otherwise)**

12. Go back to the flow (click its name at the top of the run page).
13. Drag **If / Else if / Otherwise** (under *Flow control*) between the two cards.
14. In its condition, set the first box to `vars.device.platform`, the operator to
    **contains**, and the value to `ios`.
15. Drag the **Log message** card (hold its **⠿** handle) into the **Then** part of the If.
16. Drag a new **Log message** into **Otherwise**, with the message `Not a Cisco device`.
17. **Save** (version 2) and **Run…** again. In the run, the If step's output shows
    `"branch": 0`, meaning the first branch ran.

**Part C: start it from a ticket automatically**

18. Change the **When** box to **ITSM ticket event**. In **Only in states**, type `Implement`.
19. Under *Only when*, click **+ Condition**. Set the field to `ticket.summary`, the operator
    to **matches regex**, and the value to `HELLO (?P<device>\S+)`. The part `(?P<device>…)`
    *captures* the device name from the ticket title into `trigger.match.device`.
20. In the Get device card, replace `leaf-01` with `{{ trigger.match.device }}` (click the
    field, then `trigger.match.device` in Dynamic content).
21. Open **Test with sample data** in the When box. Change the sample's `"summary"` to
    `HELLO leaf-03` and click **Test trigger**. It says **Matches** and shows
    `"device": "leaf-03"`.
22. **Save**. Now an approver clicks **Publish v3** and then **Enable trigger**. (Publishing
    picks which version the trigger runs; enabling switches the trigger on.)
23. From now on, every ticket that reaches *Implement* with a title like `HELLO spine-01` runs
    this flow on its own. Each ticket runs once per state change: repeated webhooks are
    ignored.

**What to read next**:
- §50.3 every trigger
- §50.4 conditions
- §50.5 every action
- §50.8 the full OS-upgrade example, which uses Try / On error, approvals and a real change

**Common mistakes (and the fix)**

| You see | Why | Fix |
|---|---|---|
| A card is red: "… is required" | A required field is empty. | Fill it in, or click the field and pick a value from Dynamic content. |
| Run fails: `'dict object' has no attribute 'x'` | The template names a value that does not exist (a typo, or a step that did not run). | Check the spelling against Dynamic content. Use *Run only if* or an If when a value may be missing. |
| The trigger never starts the flow | Not published, not enabled, the condition does not match, or the run was already done for this ticket and state. | Use **Test with sample data** with the real event. Check that the flows list shows "● on" and "live vN". Look for `duplicate` in the webhook response. |
| "Enable trigger" answers "publish a version before enabling" | Nothing is published yet. | An approver clicks **Publish vN** first. |
| A script step: "script refused: import of 'os' is not allowed" | Scripts may only import safe modules (§50.7). | Use the allowed modules, or use the Run show commands / Call API actions instead. |
| Call API: "… is not in VIREONET_FLOW_HTTP_ALLOWED_HOSTS" | The host is not allow-listed. | Ask an admin to add the host pattern. |
| A run stays `waiting` | An *Ask for approval* step is waiting. | Open the run: someone with the shown role clicks **Approve** or **Reject**. |
| A run stays `queued` | No worker is running flows (Temporal is not connected). | Start the worker; check **System health**. |

![A flow in the designer](images/flows-designer.png)
![A finished run](images/flows-run.png)
![Waiting for approval](images/flows-approval.png)

### 50.1 Concepts

| Term | Meaning |
|---|---|
| Flow | A named automation: one trigger plus a list of steps. |
| Version | Every save that changes the trigger or steps is a new version (v1, v2, …). |
| Published version | The version the trigger runs. Publishing needs the **approver** role. |
| Enabled | Whether the trigger is live. Enabling also needs an approver; a flow must have a published version first. |
| Run | One execution: trigger event, inputs, a step-by-step log, final variables and outputs. |
| Step id | Each step has an id (e.g. `push_boot`). Its result is always at `steps.<id>`, the "magic variable". |

Roles: viewers read flows and runs. Operators build, test and run flows by hand. Approvers
publish versions, enable triggers and answer approval steps. **Custom-script steps can only be
saved by an admin.** Admins can also delete flows and rotate webhook tokens.

### 50.2 The editor

- **Visual (drag and drop)** / **YAML** toggle at the top of the Designer.
  - *Visual*:
    - The left **Actions** panel lists every action by category. Drag one into any drop slot,
      including inside If / Loop / Try blocks, or click it to add it at the end.
    - Drag a card by its **⠿** handle to move it, also into or out of blocks.
    - ↑ ↓ move a card one place, ⧉ duplicates it, ⚙ opens its settings, ✕ deletes it.
  - *YAML*: the whole flow (`trigger` + `definition`) as text. Switching back to Visual parses
    and validates it; invalid YAML keeps you in YAML with the error shown.
- **When** card (trigger): the trigger type, its options and its conditions. *Test with sample
  data* runs the trigger against an event (a sample is filled in) and shows whether it matches,
  what it captured and its dedup key.
- **Dynamic content** (right panel): click a field, then a value (`trigger.ticket.summary`,
  `trigger.match.version`, `vars.device`, `steps.pre`, …) to insert `{{ … }}`. Text fields also
  autocomplete these values.
- Tabs:
  - **Inputs, variables, outputs**: the run form (typed inputs like an NSP workflow input
    form), the initial variables, and the outputs a run returns (a sub-flow's result).
  - **Test run**: runs the logic for real (variables, conditions, loops, errors) while
    *simulating every action*. Nothing is called, pushed or changed, and approvals are approved.
  - **Runs** and **Versions**: history; open any old version in the editor or publish it.
- Toolbar:
  - **Save**: a new version.
  - **Validate**: problems are shown on the card they belong to.
  - **Run…**: a form for the inputs.
  - **Publish vN** and **Enable / Disable trigger**: approvers.
  - **Export**: a `.flow.json` file. **Import…** on the Flows page reads one back.
- **Templates**: the Flows page has ready-made flows (OS upgrade from a ticket, Zabbix link
  down, NetBox onboarding, nightly VLAN audit, API + script). *Use template* opens a copy.

### 50.3 Triggers

| Trigger | Starts when | Point it at / options |
|---|---|---|
| Manually | Someone presses **Run** or calls `POST /flows/<id>/run` | Inputs form |
| ITSM ticket event | A ticket is created / updated / changes state (ServiceNow, Jira, Freshservice, BMC Helix) | The ITSM webhook `POST /webhooks/servicenow` (or `/webhooks/itsm/state`). Options: events, states |
| Webhook (any JSON) | Any system POSTs to the flow's own URL | `POST /webhooks/flows/<flow id>` with `X-VireoNet-Flow-Token` (shown once on create; admins can rotate it) |
| Schedule | Cron (UTC) or every N minutes | A Temporal schedule, created when the flow is enabled |
| Kafka message | A message on a topic (optional) | `VIREONET_KAFKA_BOOTSTRAP`; JSON values are parsed; option: topic |
| VireoNet event | A change ends (`change.closed`, `change.rolled_back`, …), drift is found (`drift.detected`), log anomalies (`logs.anomaly`) | Built in |
| NetBox change | An object is created / updated / deleted | NetBox webhook → `POST /webhooks/flows/source/netbox` |
| Zabbix problem | Problem / recovery / update | Zabbix webhook media type → `/webhooks/flows/source/zabbix` |
| Prometheus alert | Alertmanager firing / resolved (one run per alert) | Alertmanager webhook receiver → `/webhooks/flows/source/prometheus` |
| LibreNMS / PRTG / SolarWinds | Alert / recovery, sensor down / up | Their HTTP alert actions → `/webhooks/flows/source/librenms` (`prtg`, `solarwinds`) |
| Git push / merge | GitHub, GitLab or Gitea push or merge | Repo webhook → `/webhooks/flows/source/git` |
| AWX job finished | An AWX / AAP job succeeds or fails | AWX notification webhook → `/webhooks/flows/source/awx` |

Adapter event webhooks authenticate with `X-VireoNet-Event-Token: <VIREONET_FLOW_EVENT_TOKEN>`
(or `Authorization: Bearer …`), an HMAC-SHA256 of the body in `X-VireoNet-Signature`, or
NetBox's own `X-Hook-Signature` (HMAC-SHA512) with the same secret.

Each source becomes an event with documented fields. For example, Zabbix gives `host`,
`trigger`, `severity`, `severity_level`, `tags`; NetBox gives `model`, `name`, `object`,
`changed_fields`, `previous.object`; ITSM gives `ticket.key`, `ticket.summary`, `ticket.status`,
`ticket.fields.*`. The sample on each trigger shows them all.

**Run once per (dedup key)**: a repeated event does not start a second run. The defaults are:

| Source | Default dedup key |
|---|---|
| ITSM | ticket + state |
| Zabbix | event id + event |
| Prometheus | fingerprint + status + start |
| Kafka | topic + partition + offset |

Set your own template (e.g. `{{ ticket.key }}-{{ match.version }}`), or `none` to run on every
event.

### 50.4 Conditions (triggers, If, While, Run only if)

Conditions are built without code. A group is **ALL** / **ANY** / **NONE** of its rules and can
contain groups (nested). Each rule is *field · operator · value*.

- **Field**: a path such as `ticket.summary`, `object.status.value`, `vars.count` or
  `steps.pre.leaf-01`, or `*` for *any text anywhere in the event*.
- **Operators**:
  - text: equals, contains, starts / ends with, wildcard (`*`, `?`), contains any / all of the
    words
  - **regex**: matches / does not match
  - lists: is one of / is not one of
  - numbers: greater / less / at least / at most, has more items than
  - presence: exists / is missing / is empty / is not empty, is true / false
  - state change: **changed (from → to)**
- **Aa**: case-sensitive (off by default). Numbers compare as numbers.
- **Regex captures**: named groups become variables. In a trigger condition,
  `OS UPGRADE (?P<device>\S+) to (?P<version>[\d.]+)` gives `trigger.match.device` and
  `trigger.match.version`. In an If, the captures are in `match.*` inside the branch.
- **Expression**: anything else, as a Jinja expression, e.g.
  `trigger.ticket.fields.priority | int <= 2 and 'core' in trigger.ticket.summary`.

### 50.5 Actions

**Variables and scripting**:
- *Set variable*, *Add to list*, *Log message*
- *Wait*: a durable timer
- *Stop flow*: success, with a message
- *Fail*: an error, caught by an enclosing Try

**Flow control**:
- *If / Else if / Otherwise*
- *Switch*: cases and Default
- *Repeat with each*: a list; **Items at a time** > 1 runs items concurrently (like Mistral
  `with-items` concurrency), each with its own copy of the variables
- *Repeat N times*
- *Repeat while*: a **maximum** is required, plus an optional wait between iterations
- *Try / On error / Finally*: `vars.error.message` and `vars.error.step` hold the error
- *Run in parallel*: branches, then join
- *Exit loop*, *Next iteration*
- *Run another flow*: a sub-flow; its outputs are the result

**Network**:
- *Run show commands*: read-only commands only; optionally parsed with TextFSM
- *Parse text (TextFSM)*
- *Run pre/post checks*: the command checks of a change type's check template
- *Change: push config*: creates a real VireoNet change with policy, locks, backup, dry-run,
  waves with health gates, post-checks and automatic Git rollback, then waits for it.
  *Automation full* means pre-approved by this flow with a planned window from now.
  A change that does not end `closed` fails the step.
- *Change: roll back*: signals a running change
- *Get device (NetBox)*

**ITSM**: *Get ticket*, *Add work note*, *Close ticket*. These use the configured ITSM provider.

**API, scripts and people**:
- *Call API (HTTP)*: any REST API; see §50.7
- *Run custom script (Python)*: see §50.7
- *Send notification*
- *Ask for approval*: the run waits (status `waiting`) until someone with the chosen role
  presses **Approve** / **Reject** on the run page, or the timeout passes (*On timeout*:
  reject or approve).

**Settings on every action** (⚙):

| Setting | What it does |
|---|---|
| Run only if | A condition; the step is skipped when it does not match. |
| Run after previous step | `success` (default), `failure`, or `always`, like Power Automate's "configure run after". `failure` steps run only after a step that failed with *Continue on error*. |
| Retries, Delay between retries, Backoff multiplier | Retry with backoff, like NSP / Mistral retry policies. |
| Step timeout | Limit how long the step may take. |
| Continue on error | Record the error in `vars.error` and carry on. |
| Disabled | Skip the step. |
| Note | A comment for whoever edits the flow next. |

Safety limits:
- every loop has a maximum (≤ 1000)
- a run stops after 5000 executed steps
- flows hold at most 500 steps and 12 levels of nesting
- sub-flows nest at most 5 deep

### 50.6 Templates and values

Every text field accepts Jinja templates: `Upgrade {{ trigger.match.device }}`.

- **Keeping the type**: a field that is exactly one `{{ expression }}` keeps the value's type
  (list, number, object), so `{{ vars.devices }}` passes a list.
- **Keys**: dictionary keys can be templates too, e.g.
  `configs: {"{{ vars.device }}": "boot system …"}`.
- **Filters** beyond Jinja's own: `regex_search`, `regex_findall`, `regex_replace`, `to_int`,
  `to_float`, `split_lines`.
- **Sandbox**: templates run in an immutable sandbox, so they cannot reach Python internals.

Names available in templates:

| Name | Contents |
|---|---|
| `trigger` | The event that started the run (`trigger.match` holds the trigger's regex captures). |
| `inputs` | The run's inputs. |
| `vars` | Flow variables (and `save_as` results). |
| `steps.<id>` | Each step's result. |
| `match` | Captures of the last matching If condition. |
| `run` | `id`, `flow`, `version`, `started_by`, `started_at`. |
| `secrets.<NAME>` | A secret; see below. |

**Secrets**: `{{ secrets.NETBOX_TOKEN }}` reads `VIREONET_FLOW_SECRET_NETBOX_TOKEN` *inside the
activity, at the moment of the call*. The value never reaches Temporal history, the run log or
the database; logs show `<secret NETBOX_TOKEN>`.

### 50.7 API calls and Python scripts: security

**Call API (HTTP)**:

| Control | Detail |
|---|---|
| Schemes | Only `http` / `https`. No redirects are followed. TLS is verified (`VIREONET_FLOW_HTTP_VERIFY_TLS`). |
| Hosts | Must match `VIREONET_FLOW_HTTP_ALLOWED_HOSTS` (fnmatch patterns, default `*`). |
| Always blocked | `VIREONET_FLOW_HTTP_DENIED_HOSTS` (default: cloud metadata `169.254.0.0/16`, `metadata.google.internal`, `fd00:ec2::254`). |
| DNS-rebinding guard | The name is resolved first, and every resolved address is checked against the deny ranges. `VIREONET_FLOW_HTTP_BLOCK_PRIVATE=true` also refuses private and loopback addresses. |
| Responses | Status codes outside *Expected status codes* fail the step. Set-Cookie / Authorization headers are dropped. Bodies are capped at 100 kB. |

**Run custom script (Python)**: the script reads `inputs` and sets `result`, which must be
JSON-serialisable. Protection is layered:

1. **Who**: only an admin can save a flow that contains a script step.
   `VIREONET_FLOW_SCRIPTS_ENABLED=false` turns scripts off everywhere.
2. **Static check before running**:
   - only allow-listed imports (`VIREONET_FLOW_SCRIPT_ALLOWED_IMPORTS`; the default is json,
     re, math, statistics, datetime, time, ipaddress, collections, itertools, functools,
     operator, string, textwrap, difflib, hashlib, base64, uuid, decimal, random, csv and
     similar)
   - no `os`, `subprocess`, `socket`, `sys`, `importlib`
   - no dunder attributes (`__class__`, `__subclasses__` …) or strings containing `__`
   - no eval / exec / compile / open / getattr / globals
3. **Restricted builtins while it runs**: the script's `__import__` only loads allow-listed
   modules, and open / eval / exec / input are removed.
4. **Process**:
   - a separate `python -I` process with an empty environment (none of VireoNet's secrets)
     and its own temp directory
   - limits on CPU, memory (`VIREONET_FLOW_SCRIPT_MEMORY_MB`), file size, open files and
     processes
   - a hard timeout (`VIREONET_FLOW_SCRIPT_MAX_SECONDS`), then the process is killed
5. **Privileges**: when the worker runs as root, the script runs as **nobody**, so it cannot
   read the worker's `/proc/<pid>/environ` or config files. Where the kernel allows it, the
   script also gets an empty network namespace (no network). Every result reports
   `isolation: {uid, non_root, network_isolated}`, and
   `VIREONET_FLOW_SCRIPT_REQUIRE_ISOLATION=true` refuses to run scripts without both.
6. **Production: the script-runner service** (`VIREONET_FLOW_SCRIPT_RUNNER=sidecar`). The
   `script-runner` container in `docker-compose.yml` has:
   - no VireoNet secrets
   - a read-only root filesystem and a tmpfs `/tmp`
   - user 65534, all capabilities dropped, `no-new-privileges`, and a pids / memory limit
   - an **internal-only** network shared with the worker

   Requests are signed with HMAC-SHA256 (`VIREONET_FLOW_SCRIPT_RUNNER_SECRET`).

In real mode the API logs a "flow security" warning when scripts would run locally without
required isolation, when the runner secret is unset, or when Call API may reach any host.

### 50.8 Example: OS upgrade from a ServiceNow ticket

The *OS upgrade from an ITSM ticket* template, step by step:

1. **Trigger**: ITSM, state `Implement`, summary *matches*
   `OS UPGRADE (?P<device>…) to (?P<version>\d+\.\d+\.\d+)`, and category is network.
2. *Add work note*: "OS upgrade of leaf-01 to 17.9.4 started".
3. *Set variable* `target`, `parts`, `image` (`flash:cat9k_iosxe.17.09.04.SPA.bin`).
4. *Get device* (NetBox). *Run show commands* `show version`, `show boot`.
5. *Set variable* `current`, using a regex filter on `show version`.
6. *If* the current version is the target: *Close ticket* "nothing to do" and *Stop*.
7. *If* the priority is 1 or 2: *Ask for approval* (approver).
8. *Try*:
   - *Change: push config*: `boot system <image>` through the **full change pipeline**,
     automation full, with the old boot statement as the rollback config.
   - *Run show commands* `show boot`, with 2 retries.
   - *If* the boot variable is not the new image: *Fail*.
   - *Close ticket* with the result.
9. *On error*: *Add work note* with the error, *Send notification*, *Fail*.
10. **Outputs**: device, from, to, image.

To try it in fake mode:
1. *Use template* → *Save*.
2. As an approver: *Publish v1* → *Enable trigger*.
3. Send the ticket event: `curl -X POST …/api/webhooks/servicenow -d '{"number":"CHG0030005"}'`,
   or move a real ticket to Implement.
4. Open the run: the step log shows every step, and the change `CHG0030005` runs its pipeline
   and ends `closed`.
5. The ticket is closed with a note, and `show boot` on leaf-01 shows the new image.

Stage 3 adds controller actions (Catalyst Center SWIM, Arista CloudVision …) that distribute
and activate the image itself.

### 50.9 Durability and limits

- A run is a Temporal workflow `flow-run-<run id>`. Waits are timers and approvals are signals,
  so restarts of the API or worker do not lose runs. A *Change: push config* step is a child
  `ChangeWorkflow` with the ticket number as its id. A sub-flow is a child `FlowWorkflow`.
- Step retries are the flow's own (*Retries*): Temporal does not retry a failed action again
  on its own, so a non-idempotent API call is never repeated by surprise.
- Flows need the Temporal worker (`VIREONET_TEMPORAL_MODE=real`, or `auto` in real adapter
  mode). With the in-memory Temporal client (tests), runs are created but stay `queued`.
- **Known limits**:
  - Kafka is consumed by the worker; with several workers, Kafka's consumer group spreads the
    partitions across them.
  - A concurrent *Repeat with each* gives each item a copy of the variables, so changes made
    inside one item are not merged back. Use the per-item results the loop returns instead.
  - Network isolation for local scripts needs `CAP_SYS_ADMIN`, which Docker does not grant by
    default, so use the script-runner service in production.

## 51. Vendor controllers (software upgrades and controller APIs)

Many networks are run through a vendor controller: Cisco Catalyst Center, Arista CloudVision,
Juniper Mist or Apstra, Fortinet FortiManager, Palo Alto Panorama, Nokia NSP, ExtremeCloud IQ,
Cisco Meraki, HPE Aruba Central. VireoNet talks to each one through the same set of flow actions,
so **one flow can upgrade any vendor's device**.

### 51.1 What you can do

| Action (Flows → Actions → *Vendor controllers*) | What it does |
|---|---|
| **Controller: get device** | Version, model, serial, reachability, as the controller sees it. |
| **Controller: list software images** | The versions the controller can install for that device's platform. |
| **Controller: distribute image** | Copy (stage) a version to the device. Nothing reloads. |
| **Controller: activate image** | Install and boot a staged version (the device reloads). Activating the *previous* version is the OS rollback. |
| **Controller: upgrade (distribute + activate)** | One step. VireoNet uses the controller's own one-step upgrade where there is one, and otherwise distributes, waits, then activates. |
| **Controller: job status** | Read a job once. |
| **Controller: API call** | Any other API of that controller, with its login handled for you, e.g. `GET /dna/intent/api/v1/site` on Catalyst Center. |

**Controller = auto**: VireoNet looks up the device's platform in NetBox and uses the
controller that manages it. EOS goes to CloudVision, PAN-OS to Panorama, and so on. You can
also pick a controller by name.

**Waiting**: with *Wait until the job finishes* on (the default), the step checks the job
every `VIREONET_CONTROLLER_POLL_SECONDS` (15 s) using durable timers, so a restart does not
lose the wait. It fails if the job fails or takes longer than *Give up after (minutes)*.

The **Controllers** page (sidebar → Network) shows each controller, its status, its devices with
their current versions, and the versions it offers. The page is read-only: changes happen in
flows, where they are versioned, approved and logged.

### 51.2 Beginner walkthrough: upgrade a switch through its controller (fake mode)

![The Controllers page](images/controllers.png)

1. Open **Controllers**. Click **cloudvision**. *spine-01* runs `4.30.2F`, and `4.32.1F` is
   offered.
2. Open **Flows** → *Start from a template* → **OS upgrade via controller (any vendor)** →
   **Use template** → **Save**.
3. As an approver: **Publish v1** → **Enable trigger**.
4. Send the demo ticket `CHG0030006` ("OS UPGRADE spine-01 to 4.32.1F"):
   `curl -X POST …/api/webhooks/servicenow -H 'Content-Type: application/json' -d '{"number":"CHG0030006"}'`.
5. Open the run (Flows → *Recent runs*). Watch the steps:
   - *Controller: get device* shows 4.30.2F.
   - *Controller: list software images*.
   - *Upgrade through the controller* waits for the job.
   - *Controller: get device* now shows 4.32.1F.
   - *Close ticket*.
6. Back on **Controllers → cloudvision**: spine-01 now runs 4.32.1F. Run the same with ticket
   `CHG0030007` (fw-02 to 11.1.2): Panorama has no one-step upgrade, so the step distributes,
   waits, installs and reboots by itself.
7. **What happens on failure**: if the upgrade job fails, or the version is wrong afterwards,
   the *On error* branch:
   - adds a work note
   - **activates the previous version** (still on the device)
   - notifies and fails the run

   No manual rollback is needed.

> In fake mode the controllers are simulated, but the upgrade really changes `show version` on
> the simulated device, so pre/post checks see the change.

![A Panorama upgrade run: distribute, install and reboot in one step](images/controller-run.png)

### 51.3 Configure your controllers

Set `VIREONET_CONTROLLERS` (on the API **and** the worker) to a JSON list. Secrets can be given
as an environment-variable name with `password_env` / `token_env`, so they stay out of the list
itself.

```json
[
  {"name": "dnac-dc1", "type": "catalyst_center", "url": "https://dnac.example.net",
   "username": "vireonet", "password_env": "DNAC_PASSWORD", "verify_tls": true},
  {"name": "cvp", "type": "cloudvision", "url": "https://cvp.example.net", "token_env": "CVP_TOKEN"},
  {"name": "mist", "type": "mist", "token_env": "MIST_TOKEN", "options": {"org_id": "…", "site_id": "…"}},
  {"name": "apstra", "type": "apstra", "url": "https://apstra.example.net", "username": "admin", "password_env": "APSTRA_PW"},
  {"name": "fmg", "type": "fortimanager", "url": "https://fmg.example.net", "token_env": "FMG_API_KEY", "options": {"adom": "root"}},
  {"name": "panorama", "type": "panorama", "url": "https://panorama.example.net", "token_env": "PAN_KEY", "options": {"image_prefix": "PanOS_3400-"}},
  {"name": "nsp", "type": "nsp", "url": "https://nsp.example.net", "username": "admin", "password_env": "NSP_PW", "options": {"upgrade_workflow": "sros_upgrade"}},
  {"name": "xiq", "type": "xiq", "token_env": "XIQ_TOKEN"},
  {"name": "meraki", "type": "meraki", "token_env": "MERAKI_KEY", "options": {"org_id": "…", "network_id": "…", "product": "switch"}},
  {"name": "aruba", "type": "aruba_central", "url": "https://apigw-uswest4.central.arubanetworks.com", "token_env": "ARUBA_TOKEN", "options": {"device_type": "CX"}}
]
```

Common fields: `name`, `type`, `url` (Mist, XIQ and Meraki have defaults), `username` /
`password` (or `password_env`), `token` (or `token_env`), `verify_tls` (default true),
`timeout_seconds` (30), and `options` (vendor-specific, see the table).

| Type | Login | Upgrade method | Notes |
|---|---|---|---|
| `catalyst_center` | Username / password → token (`X-Auth-Token`), or `token` | SWIM: distribution task, then activation task (`distributeIfNeeded` for one step) | Platforms ios, ios-xe, nxos |
| `cloudvision` | Service-account token (Bearer) | Associate the image bundle with the device, then execute the task | Bundle names `EOS-<version>` |
| `mist` | API token (`Token …`) | `POST …/devices/{id}/upgrade` (reboot false = distribute only) | Needs `options.site_id`; the job is "the device reports the new version" |
| `apstra` | `/api/aaa/login` → AuthToken | System-agent OS upgrade job | Distribute and activate are one job |
| `fortimanager` | API key (Bearer) or user / password session | `/um/image/upgrade` task | `options.adom` |
| `panorama` | API key or keygen | Batch upload (distribute), then install, then reboot | `options.image_prefix` = the PAN-OS image file prefix for your model |
| `nsp` | OAuth client credentials | Runs *your* NSP Workflow Manager upgrade workflow | Needs `options.upgrade_workflow`, which receives `neId`, `neName`, `version`, `phase`. `options.versions` lists the offered versions. |
| `xiq` | `/login` or API token | Firmware upgrade (activate false = distribute) | The job is "device reports the version" |
| `meraki` | API key (Bearer) | Schedules the network's next firmware upgrade for the product | Meraki upgrades *every* device of that product in the network |
| `aruba_central` | OAuth access token | `/firmware/v1/upgrade` (reboot false = distribute) | `options.device_type` CX / HP |

**Verified vs. unverified**: every adapter is tested against a mock of the vendor API that
follows the vendor's published documentation. Calls marked **UNCERTAIN** in
`src/vireonet/controllers/vendors.py` have not been run against a live controller yet. They are:
- Apstra's OS-upgrade endpoint
- the CloudVision `saveTopology` payload
- FortiManager's `/um/image/*` calls
- Panorama image-file naming
- NSP's RESTCONF fields and Workflow Manager response
- ExtremeCloud IQ firmware endpoints
- Aruba Central status values

Check them on a lab controller before production use; the integration check (§44) covers it.

### 51.4 Rollback for software upgrades

A config change rolls back through Git (§29). A **software** upgrade rolls back by **activating
the previous version**, which every controller keeps on the device after an upgrade. The
template's *On error* branch does this automatically with *Controller: activate image* and
`version = {{ vars.before.version }}`. If the device cannot boot the old image either, the step
fails with *continue on error*: the work note says so, and the run ends **failed** for a person
to follow up.

## 52. Log platforms (Elasticsearch, OpenSearch, Splunk, Grafana Loki, Graylog)

VireoNet can **search** your log platform and **send** its own events to it.

| What | Where |
|---|---|
| Search logs from a flow | Flows → action **Search logs** (platform, query, look back, maximum lines). The result is `{count, hits: [{timestamp, host, message, fields}]}`. |
| Send an event from a flow | Flows → action **Send event to log platform** (message + any fields). |
| Ship VireoNet's own events | `VIREONET_LOG_EXPORT_TO=["es"]`: every change result, drift finding and flow trigger event is also written to those platforms, so your SIEM has the automation audit trail next to the device logs. |
| See them | **Adapters** (category *Logs*); `GET /logs/backends`; `POST /logs/search`. |

**Configure** `VIREONET_LOG_BACKENDS` (a JSON list), or use **Adapters → Configure → Log platforms**:

```json
[
  {"name": "es", "type": "elasticsearch", "url": "https://es.example:9200", "token_env": "ES_API_KEY",
   "index": "syslog-*", "export_index": "vireonet-events"},
  {"name": "os", "type": "opensearch", "url": "https://opensearch:9200", "username": "vireonet", "password_env": "OS_PW", "index": "logs-*"},
  {"name": "splunk", "type": "splunk", "url": "https://splunk:8089", "token_env": "SPLUNK_TOKEN", "index": "network",
   "send_url": "https://splunk:8088", "send_token_env": "SPLUNK_HEC_TOKEN"},
  {"name": "loki", "type": "loki", "url": "http://loki:3100", "tenant": "network", "index": "{job=\"syslog\"}"},
  {"name": "graylog", "type": "graylog", "url": "https://graylog:9000", "token_env": "GRAYLOG_TOKEN", "send_url": "http://graylog:12201/gelf"}
]
```

| Type | Search API | Send API | Login |
|---|---|---|---|
| `elasticsearch` | `POST /<index>/_search`: `query_string` plus a `@timestamp` range (fields configurable with `options.timestamp_field`, `message_field`, `host_field`) | `POST /<export_index>/_doc` | API key (`Authorization: ApiKey`) or basic |
| `opensearch` | Same as Elasticsearch | Same | Basic, or a bearer token |
| `splunk` | `POST /services/search/jobs/export` (SPL, JSON lines; port 8089) | HTTP Event Collector `POST /services/collector/event` (port 8088, `Authorization: Splunk <HEC token>`) | Bearer token or basic |
| `loki` | `GET /loki/api/v1/query_range` (LogQL; plain text becomes `<index> \|= "text"`) | `POST /loki/api/v1/push` | Basic or bearer; `X-Scope-OrgID` for multi-tenant |
| `graylog` | `POST /api/search/messages` (with `X-Requested-By`) | GELF HTTP input (`send_url`) | Access token (as user, password `token`) or basic |

In fake mode, a **fake** platform holds the demo network's syslog (the same lines Log analytics
uses). Try Flows → Search logs with platform `fake` and query `fw-02 login`.

## 53. Configuring adapters from the UI

![Adapters → Configure](images/adapters-configure.png)

**Adapters → Configure** (admins). Every adapter setting is listed by group:
- ServiceNow, the other ITSM tools, NetBox, AWX, Git, Gitea, Vault, Batfish, labs, DHCP
- notifications, monitoring, telemetry, controllers, log platforms, Kafka, flow security

Each row shows the setting, its environment variable and where the current value comes from:
`ui` (saved here), `env` (environment variable) or `default`.

1. Pick a group, e.g. **NetBox**.
2. Type the new values. Secret fields (passwords, tokens, keys) are password boxes. They show
   only "set" or "not set": a stored secret is never sent back to the browser. Leave a secret
   empty to keep it.
3. **Test connection** checks the values you typed against the real system, without saving
   anything (for example, it calls NetBox's health check with the new URL and token).
4. **Save**. Then restart the API and the worker: saved values apply at start.
5. **Reset to env** (on a `ui` row) deletes the saved value, so the environment value applies
   again.

How it is stored:
- Values live in the `adapter_overrides` table (migration 0012).
- Secrets are encrypted with Fernet, with a key derived from `VIREONET_JWT_SECRET`. If you
  change the JWT secret, enter the saved secrets again; VireoNet then ignores the unreadable
  ones and falls back to the environment.
- Values are validated exactly like environment variables. Wrong types or invalid JSON are
  refused with a message.

Environment variables keep working exactly as before. Use whichever suits you: environment and
Vault for infrastructure-as-code, the UI for quick changes.

## 54. The Gitea tab

![Gitea inside VireoNet](images/gitea-tab.png)

When Gitea is installed and answering, a **Gitea** entry appears in the sidebar (Network). It
shows Gitea itself inside VireoNet (repositories, merge requests, commits), plus the Gitea
version and the number of repositories. **Open in a new tab** opens Gitea on its own.

Configure it in Adapters → Configure → *Gitea tab*, or with environment variables:

| Variable | Meaning |
|---|---|
| `VIREONET_GITEA_UI_URL` | Where browsers reach Gitea, e.g. `https://vireonet.example/gitea`. Default: `VIREONET_GIT_BASE_URL` without `/api/v1` when `VIREONET_GIT_PROVIDER=gitea`. |
| `VIREONET_GITEA_API_URL` | Where the API server reaches Gitea's API, e.g. `http://gitea:3000/api/v1`. Default: UI URL + `/api/v1`. |
| `VIREONET_GITEA_EMBED_URL` | The address used inside VireoNet's page (default: the UI URL). |

**Embedding**: browsers only show Gitea inside VireoNet when Gitea allows it. The simple, safe
way is to serve Gitea under the same address as VireoNet. Proxy `/gitea/` to Gitea, set Gitea's
`ROOT_URL` to `https://<vireonet>/gitea/`, and set `VIREONET_GITEA_EMBED_URL=/gitea/`. The
Docker demo does exactly this. Otherwise use **Open in a new tab**.

## 55. Documentation space

![The documentation space](images/docs-space.png)

**Documentation** (sidebar → Administration) works like a Confluence space:

- **Space**: pick a document: User manual, Adapter integration guide, SDLC, Drift internals,
  Custom scripts, Labs, Runbook.
- **Page tree** (left): parts and chapters; click a part to open or close it.
- **Search this space**: finds every page that mentions the words, with a short snippet. Click
  a result to open that page.
- **Breadcrumbs** (top): Docs › document › part › page.
- **On this page** (right): the sections of the chapter.
- **← Previous / Next →** at the bottom, to read chapter by chapter.
- Links between chapters open the right page. The address (`/help?doc=USER_MANUAL&page=…`) can
  be bookmarked or shared.

## 56. Neural networks (Intelligence tab)

Five neural-network models sit next to the classic ones on the **Intelligence** tab (formerly
*Machine learning*; the old address `/ml` still works).

- They are scikit-learn multilayer perceptrons: small, CPU-only, no GPU and no extra packages.
- Each one shows on screen what it is and how well it scored on data it was **not** trained on.
- All are **advisory**: they inform people and flow conditions, and never change anything by
  themselves.

| Tab | What it does | Model | Measured on the demo data | Use it from a flow |
|---|---|---|---|---|
| **Neural: log sequences** | Flags devices whose recent log has **new kinds of events** or events in an **unusual order** | Learns "which event comes next" from the oldest 50 % of each device's log; the next 20 % sets the device's own baseline; the newest 30 % is scored (DeepLog-style) | Flags *spine-02* (power-supply and fan failure) and *border-01* (BGP flaps); no false positives on the 10 demo devices | — |
| **Neural: traffic forecast** | Forecasts each interface's utilisation for the next 3 hours and says **when it would saturate** | Network on the *change* over the last 6 hours (36 samples), run step by step | Beats the naive "stays as it is" forecast; the growing link is the only one predicted to saturate | — |
| **Neural: change risk** | Scores how likely a change is to fail or roll back | Network (32-16) on blast radius, vendor mix, hour, emergency and past failure rate | Held-out AUC ≈ 0.71 on 3000 synthetic changes (real history is used once there are enough finished changes) | **Predict change risk** → `{risk, level, top_factors}` |
| **Neural: root cause (graph)** | Ranks which device most likely caused a set of alarms | Simplified graph convolution: alarms spread over the topology links, then a network trained on 1500 simulated failures of *your* topology | AUC ≈ 0.98. A silent dead device whose neighbours alarm ranks first. When two devices have identical neighbours (both spines), they rank equally, which is honest. | — |
| **Neural: ticket text** | Reads a free-text request: change type, devices, VLAN id / version | TF-IDF of words and word pairs, then a network | ≈ 100 % on held-out generated tickets. "pls add vlan 300 on leaf 1 and leaf 3" → `vlan_add`, `leaf-01`, `leaf-03`, VLAN 300 | **Classify ticket text** → `{change_type, confidence, devices, fields}` |

**Which detector for which problem**

| Symptom | Best tab |
|---|---|
| A burst of the same event (link flapping, login storm) | Log anomalies (burst and Isolation Forest) |
| Something new or out of order (PSU fail, BGP notification never seen before) | Neural: log sequences |
| Traffic unusual right now | Anomaly detection |
| Traffic about to fill a link | Neural: traffic forecast |

**Example: gate a change on its risk** (a flow):

1. *Predict change risk*: `change_type = {{ vars.intent.change_type }}`,
   `devices = {{ vars.intent.devices | length }}`, *save as* `risk`.
2. *If* `vars.risk.risk` *is at least* `0.5`, then *Ask for approval*: "High risk
   ({{ vars.risk.risk }}): {{ vars.risk.top_factors | map(attribute='feature') | join(', ') }}".
3. *Change: push config* …

**Example: turn free-text tickets into changes**:

1. Trigger: ITSM.
2. *Classify ticket text* (text = `{{ trigger.ticket.summary }}`), *save as* `t`.
3. *Switch* on `vars.t.change_type`:
   - case `vlan_add`: *Change: push config* with `devices = {{ vars.t.devices }}` and
     `vlan_id = {{ vars.t.fields.vlan_id }}`
   - case `os_upgrade`: *Run another flow* "OS upgrade via controller"
   - default: *Add work note* "Could not classify; a person will handle it".

**Limits**:
- The models retrain when you press the button (seconds on CPU); nothing is stored between runs.
- Change risk and ticket text learn from synthetic data until your own history is large
  enough. Treat their numbers as a starting point.
- Root cause assumes failures show up on neighbouring devices; a problem that alarms only on
  the faulty device itself is already obvious and needs no ranking.

## 57. Pre/Post checks (snapshots, tests and intelligent diff)

**Pre/Post checks** (sidebar → Automation) do what a careful engineer does around any piece of
work:
1. Before the work, capture the state of the devices.
2. Afterwards, capture it again.
3. Prove the expected things are still true.
4. See exactly what changed, ignoring the noise (counters, uptime, timestamps).

### 57.1 The pieces

| Piece | What it is |
|---|---|
| **Profile** (saved template) | A named list of read-only commands plus **tests** and **diff rules**. Built-in: *VLAN baseline*, *Software version*. Create your own on the *Profiles* tab. |
| **Snapshot** | One run of a profile on some devices at one moment, stored with each command's **raw output** and its **TextFSM rows**. It is labelled `pre`, `post` or `adhoc`, optionally with a ticket. Every snapshot is kept, so you can compare any two later. |
| **Tests** | Assertions on the **TextFSM-parsed rows only**, never on screen-scraped text. Some test one snapshot; *compare* tests need a pre and a post. |
| **Diff** | Pre against post, both **parsed** (rows matched by key, field by field) and **raw** (unified diff). |

### 57.2 Writing a profile (YAML, *Profiles* tab)

```yaml
name: Uplinks healthy
description: Uplink interfaces up, CRC errors low, nothing removed.
commands:
  - id: intf                       # used by tests
    command: {default: "show interfaces status", junos: "show interfaces terse"}
    key: [port]                    # which fields identify a row between snapshots
    ignore_fields: [last_input]    # never compared
    tolerances:                    # numeric fields allowed to move a little
      crc: {abs: 5}
      rate: {pct: 10}
    raw_ignore: ['uptime is .*']   # regexes blanked before the raw diff
tests:
  - {name: no-down,      command: intf, type: no_rows,  where: {status: down}}
  - {name: full-duplex,  command: intf, type: all_rows, expect: {duplex: full}}
  - {name: two-uplinks,  command: intf, type: count,    where: {port: {op: matches, value: "^Te"}}, op: gte, value: 2}
  - {name: crc-low,      command: intf, type: field_range, field: crc, max: 100}
  - {name: none-removed, command: intf, type: no_rows_removed}          # compare
  - {name: vlan-same,    command: intf, type: unchanged, fields: [vlan]} # compare
  - {name: crc-growth,   command: intf, type: delta, field: crc, aggregate: sum, max_abs: 20} # compare
```

| Test type | Passes when |
|---|---|
| `row_exists` | at least one row matches `where` |
| `no_rows` | no row matches `where` (e.g. no interface is down) |
| `all_rows` | every row (matching `where`) satisfies `expect` |
| `count` | the number of matching rows compared with `value` using `op` (`gte`, `lte`, `equals`, …) |
| `field_range` | a numeric field is between `min` and `max` on every matching row |
| `no_rows_removed` | *compare*: every keyed row of pre still exists in post |
| `no_rows_added` | *compare*: post has no new keyed rows |
| `unchanged` | *compare*: the listed `fields` keep their value on every keyed row |
| `delta` | *compare*: the row count (or the `sum` of a field) changes by at most `max_abs` / `max_pct` % |

- **Conditions** (`where`, `expect`): `{field: value}` means *equals*. A full condition is
  `{op, value}` with `op` one of `equals`, `not_equals`, `contains`, `not_contains`,
  `matches` (regex), `in`, `not_in`, `gt`, `gte`, `lt`, `lte`, `exists` or `empty`.
- **Validate** checks the profile before you save it.
- **Save** creates a new version.
- A test with no parsed data fails with "tests need TextFSM rows". Add a template on the
  **TextFSM** page and the test works.

### 57.3 Using it

| Where | How |
|---|---|
| **By hand** | *Snapshots* tab: profile, devices, `pre`, **Take snapshot**. After the work, take a `post` (optionally "compare with" the pre). *Compare* tab: pick the pre and post snapshots from the lists, then **Compare**. |
| **In a change** | Add `"check_profile": "VLAN baseline"` to the change intent. Pre-check takes a `pre` snapshot, and a failing test **blocks** the change. Post-check takes a `post` snapshot and runs the compare tests; a failing test **fails the post-check**, so the change **rolls back** automatically. The change's evidence lists the snapshot ids and which outputs changed. |
| **In a flow** | Action **Run Pre/Post check profile** (profile, devices, `pre`/`post`, *compare with* `{{ steps.pre_snap.snapshot_id }}`). Its output is `{snapshot_id, ok, failed, tests, diff}`. *Fail the step when a test fails* is on by default; turn it off to branch on `vars.x.ok` instead. |

### 57.4 Reading a comparison

![Comparing a pre and a post snapshot](images/prepost-compare.png)

The Compare view has three parts:

1. **Headline**: passed / failed tests and how many command outputs changed.
2. **Test results**: one row per device × test, with the reason (e.g. `removed: ['100']`).
3. **Diff per device and command**, which you can switch between:
   - **Parsed (TextFSM)**: removed rows in red (−), added rows in green (+), and a table of
     changed fields with pre → post values. Ignored fields and values within tolerance don't
     count as changes.
   - **Raw**: the unified diff of the outputs after the `raw_ignore` patterns are removed, so
     a changing uptime does not show as a difference.

*Show only changed outputs* hides the identical ones.

**Note on the built-in NetBox check**: for VLAN changes, `config_matches_intent` (does NetBox
hold the VLAN?) is a **warning** in `policies/checks.yaml`. VireoNet does not write playbook
changes to NetBox, so a new VLAN is often not there yet. The device checks (`vlan_active`,
`vlan_gone`) decide pass or fail. If you run NetBox-first (design in NetBox, then push), switch
it back to `block`.

**Example (demo)**: take a *VLAN baseline* `pre` on leaf-01 and spine-01, remove VLAN 100 from
spine-01, take a `post`, then compare:
- `no-vlan-removed` fails on spine-01 with `removed: ['100']`.
- leaf-01 shows "no change".

---

# Part V — Reference

## 38. Settings reference (every environment variable)

All settings are environment variables with the prefix `VIREONET_` (or lines in `.env`).
Restart the API and worker after changing one.

### Core

| Variable | Default | Meaning |
|---|---|---|
| `VIREONET_DEV` | `false` | Explicit dev flag; relaxes webhook auth. Never in production. |
| `VIREONET_ADAPTER_MODE` | `fake` | `fake`, `real` or `recorded`. |
| `VIREONET_LOG_LEVEL` | `INFO` | Log level. |
| `VIREONET_DATABASE_URL` | `postgresql+asyncpg://vireonet:vireonet@localhost:5432/vireonet` | Database. Real mode refuses the default password. |
| `VIREONET_TEMPORAL_ADDRESS` | `localhost:7233` | Temporal server. |
| `VIREONET_TEMPORAL_NAMESPACE` | `default` | Temporal namespace. |
| `VIREONET_TEMPORAL_TASK_QUEUE` | `vireonet-changes` | Worker task queue. |
| `VIREONET_TEMPORAL_MODE` | `auto` | `auto` (fake client in fake mode), `real` (always the server — fake devices with real workflows), `fake`. |
| `VIREONET_CORS_ORIGINS` | `http://localhost:5173,http://localhost:4173,http://localhost:8000,http://127.0.0.1:5173` | Browser origins allowed to call the API (comma-separated). |

### Authentication

| Variable | Default | Meaning |
|---|---|---|
| `VIREONET_JWT_SECRET` | `dev-only-change-me` | Signs login tokens. Set a long random value; real mode refuses the default. |
| `VIREONET_JWT_ALGORITHM` | `HS256` | Token algorithm. |
| `VIREONET_ACCESS_TOKEN_EXPIRE_MINUTES` | `480` | Session length. |
| `VIREONET_ADMIN_USER` / `VIREONET_ADMIN_PASSWORD` | unset | Used by `scripts/seed_admin.py`. |
| `VIREONET_API_TOKEN` | empty | Bearer token for machine callers. |
| `VIREONET_WEBHOOK_SECRET` | empty | HMAC/Bearer secret for webhooks. **Set it.** HMAC is refused without it. |
| `VIREONET_LOGIN_MAX_FAILURES` | `5` | Failed sign-ins per username before lockout. |
| `VIREONET_LOGIN_LOCKOUT_SECONDS` | `300` | Lockout length (and the window failures are counted in). |
| `VIREONET_ZTP_REGISTRATION_TOKEN` | empty | Lets a provisioning system call `POST /ztp/devices` without a login (header `X-VireoNet-ZTP-Token`). |
| `VIREONET_METRICS_TOKEN` | empty | When set, `/metrics` needs `Authorization: Bearer <token>`. |

### Change workflow and policy

| Variable | Default | Meaning |
|---|---|---|
| `VIREONET_POLICY_FILE` | `policies/default.yaml` | Limits, protected devices/VLANs, change windows. |
| `VIREONET_EXECUTION_FILE` | `policies/execution.yaml` | Step → AWX job template mapping. |
| `VIREONET_CHECK_TEMPLATES_FILE` | `policies/checks.yaml` | Per-intent pre/post checks (§26). |
| `VIREONET_RENDER_TEMPLATES_FILE` | `policies/render_templates.yaml` | Vendor render templates (§27). |
| `VIREONET_WAVE_SIZE` | `2` | Devices per wave. |
| `VIREONET_ABORT_THRESHOLD` | `1` | Failures in a wave that stop the rollout. |
| `VIREONET_LOCK_TTL_SECONDS` | `1800` | Device lock lifetime; renewed at apply start and before every wave. |
| `VIREONET_WAVE_HEALTH_CHECKS` | `true` | Run each wave's command post-checks before the next wave (§25.4). |
| `VIREONET_USER_MANUAL_FILE` | empty | Markdown file shown on **Help** (default `docs/USER_MANUAL.md`). |
| `VIREONET_FLOW_HTTP_ALLOWED_HOSTS` | `["*"]` | Hosts flows may call (fnmatch patterns, JSON list). |
| `VIREONET_FLOW_HTTP_DENIED_HOSTS` | metadata ranges | Always refused (CIDRs or host patterns). |
| `VIREONET_FLOW_HTTP_BLOCK_PRIVATE` | `false` | Refuse private / loopback addresses in Call API. |
| `VIREONET_FLOW_HTTP_VERIFY_TLS` | `true` | Verify TLS certificates in Call API. |
| `VIREONET_FLOW_SCRIPTS_ENABLED` | `true` | Allow custom Python script steps (admin-authored). |
| `VIREONET_FLOW_SCRIPT_RUNNER` | `local` | `local` (sandboxed subprocess on the worker) or `sidecar` (the script-runner service). |
| `VIREONET_FLOW_SCRIPT_RUNNER_URL` / `_SECRET` | `http://script-runner:8100` / empty | Script-runner address and HMAC secret. |
| `VIREONET_FLOW_SCRIPT_REQUIRE_ISOLATION` | `false` | Refuse scripts unless non-root with no network. |
| `VIREONET_FLOW_SCRIPT_ALLOWED_IMPORTS` | safe list | Modules scripts may import. |
| `VIREONET_FLOW_SCRIPT_MAX_SECONDS` / `_MEMORY_MB` | `300` / `512` | Script limits. |
| `VIREONET_FLOW_EVENT_TOKEN` | empty (→ webhook secret) | Token / HMAC secret for `/webhooks/flows/source/<source>`. |
| `VIREONET_FLOW_SECRET_<NAME>` | — | Secrets for `{{ secrets.NAME }}` in flows. |
| `VIREONET_LOG_BACKENDS` | `[]` | Log platforms (JSON list, §52). Fake mode adds `fake`. |
| `VIREONET_LOG_EXPORT_TO` | `[]` | Log platforms that receive VireoNet events. |
| `VIREONET_GITEA_UI_URL` / `_API_URL` / `_EMBED_URL` | derived | The Gitea tab (§54). |
| `VIREONET_ADAPTER_CONFIG_FROM_DB` | `true` | Apply settings saved in Adapters → Configure (§53). |
| `VIREONET_CONTROLLERS` | `[]` | Vendor controllers (JSON list, §51.3). Fake mode adds a simulated controller per vendor. |
| `VIREONET_CONTROLLER_POLL_SECONDS` | `15` | How often a flow checks a controller job while it waits. |
| `VIREONET_KAFKA_BOOTSTRAP` | empty (off) | Kafka brokers for the Kafka trigger; plus `_GROUP_ID`, `_AUTO_OFFSET_RESET`, `_SECURITY_PROTOCOL`, `_SASL_MECHANISM`, `_USERNAME`, `_PASSWORD`. |
| `VIREONET_POST_APPLY_SOAK_SECONDS` | `0` | Hold after post-check for manual rollback; 0 = off. |
| `VIREONET_ROLLBACK_SOURCE` | `git` | `git` (Git commit recorded at pre-check, backup fallback) or `backup`. |

### Git

| Variable | Default | Meaning |
|---|---|---|
| `VIREONET_GIT_PROVIDER` | `gitlab` | `gitlab`, `github`, `gitea`, `bitbucket`. |
| `VIREONET_GIT_BASE_URL` | empty | Provider URL (required for Gitea). |
| `VIREONET_GIT_REPO` | empty | Intent repo (`owner/repo`). |
| `VIREONET_GIT_TOKEN_SECRET` | `git/token` | Vault path of the Git token. |
| `VIREONET_GIT_DEFAULT_BRANCH` | `main` | `main` or `master`: holds the approved running config. |
| `VIREONET_GIT_LAYOUT` | `per_device` | `per_device` or `monorepo` (§29). |
| `VIREONET_GIT_CONFIG_REPO` | empty | Monorepo name (defaults to `VIREONET_GIT_REPO`). |
| `VIREONET_GIT_CONFIG_PATH` | `{device}/config` | Monorepo file path per device. |
| `VIREONET_GITLAB_BASE_URL` / `_TOKEN` / `_PROJECT_ID` | empty | GitLab adapter. |
| `VIREONET_GITLAB_WEBHOOK_TOKEN` | empty | Expected `X-Gitlab-Token`. |

### ServiceNow, NetBox, AWX, Vault, Batfish

| Variable | Default | Meaning |
|---|---|---|
| `VIREONET_SERVICENOW_BASE_URL` | empty | Instance URL. |
| `VIREONET_SERVICENOW_AUTH_MODE` | `oauth` | `oauth` or `basic`. |
| `VIREONET_SERVICENOW_CLIENT_ID` / `_CLIENT_SECRET` / `_USERNAME` / `_PASSWORD` | empty | Credentials. |
| `VIREONET_NETBOX_BASE_URL` / `_TOKEN` | empty | NetBox. |
| `VIREONET_AWX_BASE_URL` / `_TOKEN` | empty | AWX. |
| `VIREONET_AWX_EXECUTION_ENVIRONMENT` | `VireoNet EE` | EE the job templates run in. |
| `VIREONET_VAULT_ADDR` / `_ROLE_ID` / `_SECRET_ID` | empty | Vault AppRole. |
| `VIREONET_VAULT_KV_MOUNT` | `secret` | KV mount. |
| `VIREONET_VAULT_BOOTSTRAP_POLICY` | `bootstrap` | Policy name for bootstrap. |
| `VIREONET_BATFISH_HOST` / `_PORT` | `localhost` / `9996` | Batfish server. |
| `VIREONET_BATFISH_SNAPSHOT_DIR` | `/tmp/vireonet/batfish` | Snapshot working dir. |

### Labs and scripts

| Variable | Default | Meaning |
|---|---|---|
| `VIREONET_LAB_BACKEND` | `eveng` | `eveng`, `containerlab` or `both`. |
| `VIREONET_EVENG_BASE_URL` / `_USERNAME` / `_PASSWORD` | empty | EVE-NG. |
| `VIREONET_CONTAINERLAB_HOST` / `_USER` / `_KEY_PATH` / `_PASSWORD` | empty | Containerlab over SSH. |
| `VIREONET_SCRIPT_REPO` | `netops/scripts` | Repo of custom scripts. |
| `VIREONET_SCRIPT_MANIFEST` | `policies/scripts.yaml` | Allowed scripts and SHAs. |

### ZTP and DHCP

| Variable | Default | Meaning |
|---|---|---|
| `VIREONET_DHCP_SERVER` | `isc` | `isc` or `kea`. |
| `VIREONET_DHCP_CONFIG_PATH` | `dhcp/dhcpd.conf` | Config path in the DHCP Git repo. |
| `VIREONET_DHCP_TFTP_SERVER` | `10.0.0.5` | Option 66. |
| `VIREONET_DHCP_HTTP_BASE_URL` | `http://10.0.0.5/ztp` | Day-0 file server. |
| `VIREONET_DHCP_PNP_SERVER_URL` | `https://pnp.example.com` | Option 43 (IOS-XE PnP). |
| `VIREONET_DHCP_GIT_REPO` | empty | Repo holding the DHCP config. |
| `VIREONET_DHCP_RELOAD_TEMPLATE` | `dhcp-reload` | AWX template that reloads DHCP. |
| `VIREONET_DHCP_VERIFY_RETRIES` / `_INTERVAL_SECONDS` | `12` / `5.0` | Reservation verification. |
| `VIREONET_ZTP_TOKEN_TTL_SECONDS` | `900` | One-time callback token lifetime. |

### Notifications and monitoring (alert silencing)

| Variable | Default | Meaning |
|---|---|---|
| `VIREONET_NOTIFICATION_DEFAULT_CHANNEL` | `slack` | `email`, `teams` or `slack`. |
| `VIREONET_SLACK_WEBHOOK_URL` / `VIREONET_TEAMS_WEBHOOK_URL` | empty | Chat webhooks. |
| `VIREONET_NOTIFICATION_EMAIL_TO` / `_EMAIL_FROM` | empty / `vireonet@example.com` | Email. |
| `VIREONET_SMTP_HOST` / `_PORT` / `_USERNAME` / `_PASSWORD` | empty / `587` | SMTP. |
| `VIREONET_NOTIFICATION_WEBHOOK_URL` | empty | Generic webhook. |
| `VIREONET_MONITORING_BACKEND` | `none` | `none`, `alertmanager`, `librenms`: silences alerts during changes. |
| `VIREONET_MONITORING_BASE_URL` / `_TOKEN` | empty | Monitoring tool. |
| `VIREONET_MONITORING_SILENCE_MINUTES` | `60` | Silence length. |

### Telemetry (traffic for topology and ML)

| Variable | Default | Meaning |
|---|---|---|
| `VIREONET_TELEMETRY_BACKEND` | `none` | `none`, `fake`, `zabbix`, `prometheus`, `librenms`, `prtg`, `solarwinds` (§30). |
| `VIREONET_TELEMETRY_BASE_URL` | empty | Tool URL. |
| `VIREONET_TELEMETRY_TOKEN` | empty | API token (Zabbix, Prometheus, LibreNMS, PRTG). |
| `VIREONET_TELEMETRY_USERNAME` / `_PASSWORD` | empty | SolarWinds. |
| `VIREONET_TELEMETRY_VERIFY_TLS` | `true` | Verify TLS certificates. |
| `VIREONET_TELEMETRY_PROMETHEUS_PRESET` | `snmp_exporter` | `snmp_exporter` (SNMP) or `gnmic` (gNMI/gRPC). |

### ITSM, Excel push and TextFSM

| Variable | Default | Meaning |
|---|---|---|
| `VIREONET_ITSM_PROVIDER` | `auto` | `auto`, `none`, `fake`, `servicenow`, `jira`, `freshservice`, `bmc_helix` (§44). |
| `VIREONET_ITSM_VERIFY_TLS` | `true` | Verify the ITSM tool's certificate. |
| `VIREONET_JIRA_BASE_URL` / `_EMAIL` / `_API_TOKEN` / `_PAT` | empty | Jira Cloud (email + token) or Data Center (PAT). |
| `VIREONET_JIRA_CLOSE_TRANSITION` | `Done` | Transition used to close. |
| `VIREONET_FRESHSERVICE_BASE_URL` / `_API_KEY` | empty | Freshservice. |
| `VIREONET_BMC_BASE_URL` / `_USERNAME` / `_PASSWORD` | empty | BMC Helix ITSM / Remedy. |
| `VIREONET_ITSM_TRIGGER_STATES` | `["implement", "-1"]` | Ticket states that start a fully automatic push (name or raw value). |
| `VIREONET_ITSM_TRIGGER_APPROVES` | `true` | The trigger state is the approval; `false` = still approve in VireoNet. |
| `VIREONET_ITSM_REQUIRE_PLANNED_WINDOW` | `true` | Fully automatic pushes need a planned start and end. |
| `VIREONET_ITSM_DEFAULT_SITE` | `dc1` | Site when the ticket names none. |
| `VIREONET_JIRA_PLANNED_START_FIELD` / `_END_FIELD` | empty | Jira custom fields holding the planned window. |
| `VIREONET_EXCEL_MAX_BYTES` | `2000000` | Largest workbook. |
| `VIREONET_EXCEL_MAX_DEVICE_CONFIG_BYTES` | `200000` | Largest config block per device. |
| `VIREONET_TEXTFSM_USER_DIR` | `policies/textfsm` | Your TextFSM templates (tried before ntc-templates). |

### Adapter resilience

| Variable | Default | Meaning |
|---|---|---|
| `VIREONET_ADAPTER_TIMEOUT_SECONDS` | `10.0` | Per-call timeout. |
| `VIREONET_ADAPTER_MAX_RETRIES` | `3` | Retries for transient errors. |
| `VIREONET_ADAPTER_FAILURE_THRESHOLD` | `5` | Failures before the circuit breaker opens. |
| `VIREONET_ADAPTER_RESET_TIMEOUT_SECONDS` | `30.0` | Breaker reset time. |

### Drift

| Variable | Default | Meaning |
|---|---|---|
| `VIREONET_DRIFT_SITES` | `["dc1"]` | Sites scanned on schedule. |
| `VIREONET_DRIFT_CRON` | `0 2 * * *` | Scan schedule (5-field cron). |
| `VIREONET_DRIFT_EVENT_SECRET` | empty | Secret for `/drift/events` (empty = refused). |
| `VIREONET_DRIFT_EXCEPTION_MAX_DAYS` | `30` | Longest exception. |
| `VIREONET_DRIFT_EMERGENCY_DEADLINE_HOURS` | `24` | Emergency-accept deadline. |
| `VIREONET_DRIFT_INCIDENT_SEVERITY_THRESHOLD` | `high` | Severity that opens an incident. |
| `VIREONET_DRIFT_MAX_CONCURRENT` | `5` | Parallel device scans (1–50). |
| `VIREONET_DRIFT_IGNORE_PATH` | `policies/drift_ignore.yaml` | Ignore rules. |
| `VIREONET_DRIFT_SEVERITY_POLICY` | `policies/drift.yaml` | Severity policy. |
| `VIREONET_DRIFT_COMPLIANCE_RULES` | empty | Optional compliance rules file. |

UI build variable: `VITE_API_URL` (default `http://localhost:8000`; `/api` behind nginx).

## 39. API reference (every route)

Interactive docs with request/response schemas: `http://<api>:8000/docs`. "Auth" is the
minimum role, `webhook` (§32.1) or `none`.

| Method | Route | Auth |
|---|---|---|
| GET | `/healthz` | none |
| GET | `/metrics` | none |
| GET | `/system/health` | none |
| GET | `/adapters/health` | none |
| GET | `/adapters/settings` | viewer |
| POST | `/auth/login` | none |
| GET | `/auth/me` | signed in |
| POST | `/changes` | webhook |
| GET | `/changes` | viewer |
| GET | `/changes/{change_id}` | viewer |
| GET | `/changes/{change_id}/detail` | viewer |
| POST | `/changes/{change_id}/signals/approval` | approver |
| POST | `/changes/{change_id}/signals/abort` | operator |
| POST | `/changes/{change_id}/signals/rollback` | operator |
| POST | `/changes/{change_id}/signals/ci` | webhook |
| POST | `/changes/{change_id}/signals/merge` | webhook |
| POST | `/changes/{change_id}/reconcile-override` | approver |
| GET | `/change-windows` | viewer |
| GET | `/locks` | viewer |
| POST | `/locks/{device}/release` | operator |
| GET | `/checks/templates` | viewer |
| GET | `/checks/sample-intents` | viewer |
| POST | `/checks/templates/validate` | operator |
| POST | `/checks/templates/preview` | viewer |
| GET | `/render/templates` | viewer |
| POST | `/render/preview` | viewer |
| GET | `/config/repos` | viewer |
| GET | `/config/repos/{hostname}` | viewer |
| GET | `/config/repos/{hostname}/history` | viewer |
| POST | `/config/repos/{hostname}/render` | operator |
| GET | `/config/devices/{hostname}/history` | viewer |
| GET | `/config/devices/{hostname}/history/compare` | viewer |
| GET | `/config/devices/{hostname}/commits` | viewer |
| GET | `/drift` | viewer |
| GET | `/drift/summary` | viewer |
| GET | `/drift/{record_id}` | viewer |
| POST | `/drift/scan` | operator |
| POST | `/drift/events` | drift event secret |
| POST | `/drift/{record_id}/rollback` | approver (body may include `target`) |
| POST | `/drift/{record_id}/merge` | approver |
| POST | `/drift/{record_id}/exception` | approver |
| POST | `/drift/{record_id}/emergency-accept` | operator |
| POST | `/drift/{record_id}/mr-merged` | operator |
| GET / PUT | `/drift/ignore-rules` | admin |
| GET | `/config-push/template.xlsx` | viewer |
| POST | `/config-push/preview` | viewer |
| POST | `/config-push` | operator |
| POST | `/config-push/from-ticket` | operator |
| POST | `/webhooks/itsm/config-push` | webhook |
| POST | `/webhooks/servicenow` | webhook (state trigger) |
| POST | `/webhooks/itsm/state` | webhook (state trigger) |
| GET | `/changes/{change_id}/workbook` | viewer |
| GET | `/itsm/health` | viewer |
| GET | `/itsm/tickets/{key}` | viewer |
| GET | `/textfsm/templates` | viewer |
| GET | `/textfsm/templates/{source}/{name}` | viewer |
| POST | `/textfsm/parse` | viewer |
| POST | `/textfsm/validate` | viewer |
| POST | `/ml/log-anomalies` | viewer (operator to write to NetBox) |
| GET | `/topology` | viewer |
| GET | `/telemetry/health` | viewer |
| GET | `/telemetry/interfaces?device=…` | viewer |
| GET | `/telemetry/interfaces/{device}/history?interface=…` | viewer |
| GET | `/ml/overview` | viewer |
| POST | `/ml/anomalies` | viewer |
| POST | `/ml/clusters` | viewer |
| POST | `/ml/classification` | viewer |
| POST | `/ml/risk` | viewer |
| POST | `/ml/regression` | viewer |
| POST | `/ml/reinforcement` | viewer |
| GET | `/ztp/devices` | viewer |
| POST | `/ztp/devices` | none (see §36) |
| GET | `/ztp/devices/{serial}` | none (see §36) |
| POST | `/ztp/callback` | one-time token |
| POST / DELETE | `/ztp/devices/{serial}/reservation` | operator |
| POST | `/ztp/devices/{serial}/retry` | operator |
| POST | `/ztp/devices/{serial}/reset` | operator |
| POST | `/webhooks/gitlab` | GitLab token |
| POST | `/webhooks/github` / `gitea` / `bitbucket` | webhook |
| GET | `/iam/users` | admin |
| POST | `/iam/users` | admin |
| PATCH | `/iam/users/{user_id}` | admin |
| GET | `/iam/roles` | admin |
| GET | `/scripts` | admin |
| GET | `/setup/gitea/manual` | admin |
| POST | `/setup/gitea/install` | admin |
| GET | `/setup/gitea/status/{job_id}` | admin |

## 40. Policy and template files

| File | What it controls | Edited by |
|---|---|---|
| `policies/default.yaml` | Max devices per change (10), protected devices and VLANs, change windows, emergency bypass. | Merge request |
| `policies/execution.yaml` | Which AWX job template runs each step, per change type and platform. | Merge request |
| `policies/checks.yaml` | Per-intent pre/post checks (§26). | Merge request (draft in UI) |
| `policies/render_templates.yaml` | Vendor render templates (§27). | Merge request (draft in UI) |
| `policies/textfsm/` | Your TextFSM templates and their `index` (§46). | Merge request (test on the TextFSM page) |
| `policies/scripts.yaml` | Allowed custom scripts and their SHAs. | Merge request |
| `policies/drift.yaml` | Drift severity policy. | Merge request |
| `policies/drift_ignore.yaml` | Drift ignore rules. | Admin via API, or merge request |
| `policies/config_store.yaml` | Separation rules for templates/vars/device repos. | Merge request |
| `ansible/playbooks/*.yml` | What AWX runs on devices (§33). | Merge request |

All files load when the API and worker start; restart after a change. An invalid
check or render template file stops the worker (in every mode) and the API (in real mode).

## 41. Glossary A–Z

| Term | Meaning |
|---|---|
| **Abort** | Stop a change before apply; ends `rejected`. |
| **Action** | One step of a flow (Call API, Run show commands, Ask for approval…). |
| **Adapter** | VireoNet's connector to one external system. |
| **Advisory** | ML output that recommends but never acts. |
| **Anomaly** | A traffic point that breaks an interface's own normal pattern. |
| **Approver** | Role that can approve changes and resolve drift. |
| **Audit trail** | Append-only record of every state change. |
| **AWX** | The Ansible server; the only thing that touches devices. |
| **Backup** | Running config saved by AWX before a change. |
| **Baseline** | The approved config drift is measured against. |
| **Batfish** | Offline network analysis run in CI. |
| **Blast radius** | Devices affected if a wave fails. |
| **Breeze** | The KDE Plasma visual style the UI follows. |
| **Built-in check / renderer** | Provided in code, not in a YAML file. |
| **Change** | One intent being carried out, keyed by ticket number. |
| **Change window** | Allowed time for changes. |
| **CI** | Continuous integration: lint, render, Batfish, lab test. |
| **Classification** | ML that predicts a category (here: will the change fail). |
| **Cluster** | A group of interfaces with similar traffic (k-means). |
| **Command check** | A check that runs a read-only show command and tests the output. |
| **Contamination** | Expected share of anomalous points (Isolation Forest, LOF). |
| **Dedup key** | What makes two trigger events "the same", so a flow runs once per ticket + state, alert, offset… |
| **Drift** | Device config differs from Git/NetBox outside VireoNet. |
| **Dry-run** | Apply in check mode; nothing is changed. |
| **Dynamic content** | The panel of values (trigger, inputs, vars, steps) you click to insert `{{ … }}` into a field. |
| **Emergency** | A change flag that bypasses the change window only. |
| **Enable (trigger)** | Switch a flow's trigger on; an approver does it after publishing. |
| **Exception** | A time-boxed decision to keep a drift. |
| **Fake mode** | Every external system simulated. |
| **Flow** | A stored automation: a trigger and a list of actions (§50). |
| **gNMI** | gRPC Network Management Interface; streaming telemetry, collected via gnmic. |
| **Gradient boosting** | Ensemble of small decision trees, each correcting the last. |
| **IAM** | Users, roles and permissions. |
| **Intent** | What you want changed, independent of vendor. |
| **Isolation Forest** | Anomaly detector that isolates unusual points with random splits. |
| **k-means** | Clustering into k groups around centres. |
| **Lock** | Per-device guard so only one change touches a device. |
| **LOF** | Local Outlier Factor, a density-based anomaly detector. |
| **Monorepo** | One Git repo for all devices, `<device>/config` each. |
| **NetBox** | Source of truth for devices, cables, platforms, VLANs. |
| **NeXt UI** | The topology visualisation toolkit used on the Topology page. |
| **Operator** | Role that runs day-to-day actions (abort, rollback, locks, ZTP). |
| **Platform** | Device OS from NetBox; picks the renderer and check commands. |
| **Post-check / pre-check** | Checks after / before apply. |
| **Publish** | Choose the flow version the trigger runs (approver). |
| **Q-learning** | Reinforcement learning that learns action values by trial in a simulator. |
| **Reconcile** | Update Git and NetBox to match devices after a change. |
| **Regression** | ML that predicts a number (duration, utilisation). |
| **Render** | Turn an intent into a device's own config. |
| **RITM** | ServiceNow request item; also the workflow ID. |
| **ROC AUC** | Classifier ranking quality: 0.5 chance, 1.0 perfect. |
| **Rollback** | Restore devices (Git commit, else AWX backup). |
| **Rollback failed** | The restore did not succeed; manual action needed, devices stay locked. |
| **Run (flow)** | One execution of a flow, with its step log, variables and outputs. |
| **Sandbox (scripts)** | The layers that keep flow Python scripts harmless (§50.7). |
| **Script runner** | Separate container that runs flow scripts with no secrets and no outside network. |
| **Silhouette** | Cluster separation score, −1 to 1. |
| **SNMP** | Polling protocol for interface counters (via snmp_exporter or your monitoring tool). |
| **Soak** | Optional wait after post-check before closing. |
| **Step id** | A step's short name; its result is `steps.<id>`. |
| **Supervised / unsupervised** | Learning from labelled outcomes / finding structure without labels. |
| **Telemetry** | Interface traffic data from a monitoring tool. |
| **Temporal** | Durable workflow engine running each change. |
| **Test run** | Runs a flow's logic with every action simulated; nothing real happens. |
| **Topology** | Map of devices and links with live traffic. |
| **Trigger** | What starts a flow: a ticket, alert, NetBox change, schedule, webhook, Kafka message… |
| **Vault** | Secret store. |
| **Viewer** | Read-only role. |
| **Wave** | Batch of devices applied together. |
| **Zabbix** | Monitoring tool; one of the telemetry backends. |
| **ZTP** | Zero-touch provisioning of new devices. |

## 42. Index A–Z

| Topic | Section |
|---|---|
| Abort a change | §9, §25.5, §34.3 |
| Adapter integration | §43, §44 |
| BMC Helix / Remedy | §44 |
| End-to-end flow | §43 |
| Excel config push | §45 |
| Fully automatic / manual modes | §45.4 |
| Planned window | §45.6 |
| ServiceNow state trigger (Implement) | §45.5 |
| Freshservice | §44 |
| ITSM tools | §43, §44 |
| Jira | §44 |
| Log analytics / show logging | §47 |
| ntc-templates | §46 |
| Pre/post-check how-to and cookbook | §48 |
| TextFSM | §46, §48.4 |
| Try it in fake mode | §49 |
| ACL changes per vendor | §28, §32.2 |
| Adapters page | §21 |
| Alert silencing during changes | §30 |
| Anomaly detection | §19, §31 |
| API routes | §39 |
| Approvals | §10, §34.2 |
| Audit trail | §9 |
| AWX job templates | §33 |
| Backups | §25.4, §25.6, §34.5 |
| Batfish | §9, §25.3 |
| Built-in checks | §26.2 |
| Change detail | §9 |
| Change lifecycle and states | §25 |
| Change types and intent fields | §2, §32.2 |
| Change windows | §11, §25.7 |
| Check templates | §16, §26 |
| Classification (ML) | §19, §31 |
| Config repos | §15 |
| Config templates (rendering) | §17, §27 |
| CORS and remote browsing | §3, §37 |
| Dark mode | §6 |
| Drift | §12, §29, §34.7 |
| Dry-run | §25.4 |
| Exceptions (drift) | §12 |
| Fake mode | §3 |
| Filters, Jinja | §27.3 |
| First login | §4 |
| Gitea setup | §23 |
| Git layout and rollback | §29 |
| gNMI / gRPC telemetry | §30 |
| Health and metrics | §20, §35 |
| IAM, users, roles | §5, §22, §34.11 |
| Installation | §3 |
| Interface names | §27.5 |
| k-means clustering | §19, §31 |
| Known issues | §25.7, §36, §37 |
| Locks | §11, §34.5 |
| Login | §7 |
| Machine learning | §19, §31 |
| Monitoring tools (Zabbix, Prometheus, LibreNMS, PRTG, SolarWinds) | §30 |
| NeXt UI topology | §13 |
| Permissions | §5 |
| Platforms and NetBox slugs | §27.4 |
| Policy files | §40 |
| Post-check / pre-check | §25.4, §26 |
| Regression (ML) | §19, §31 |
| Reinforcement learning | §19, §31 |
| Rollback | §9, §25.6, §29, §34.4 |
| Rollback failed | §25.6, §34.5 |
| Security | §36 |
| Settings | §38 |
| Soak window | §25.4 |
| SNMP telemetry | §30 |
| Topology | §13, §30 |
| Troubleshooting | §37 |
| Vendor support matrix | §28 |
| Waves | §25.4, §31 |
| Webhooks | §32 |
| Zabbix | §30 |
| ZTP | §14, §32.5, §34.10 |

---

*VireoNet User Manual — generated reference tables match this build. Companion
documents: `docs/ADAPTERS.md`, `docs/DRIFT.md`, `docs/SCRIPTS.md`, `docs/LAB.md`,
`docs/runbook.md`, `docs/SDLC.md`.*
