# VireoNet — SDLC

How VireoNet is built, tested and shipped. This file is the process record;
the code is the implementation. If they disagree, the code wins and this
file must be updated in the same change.

## 1. Requirements (traceability)

Requirements come from the original VireoNet spec (M1–M12). Each requirement
maps to the module that implements it and the test file that proves it.

| ID | Requirement | Implements | Proves it |
|----|-------------|------------|-----------|
| R1 | State machine `received -> validated -> planned -> ci_passed -> approved -> pre_checked -> applied -> post_checked -> closed`, failure exits `rejected, ci_failed, pre_check_failed, rolled_back, failed`; every transition append-only audited | `src/vireonet/state_machine.py`, `change_state.py`, `models.py` (AuditLog) | `tests/test_sdlc_state_machine.py`, `tests/test_state_machine.py` |
| R2 | 8 discriminated intents (vlan/acl/route/interface/ztp), invalid intents rejected | `src/vireonet/intent.py` | `tests/test_sdlc_intent.py`, `tests/test_intent.py` |
| R3 | Validation vs NetBox + policy: protected devices/VLANs, change window Mon–Fri 01:00–05:00 UTC, emergency bypasses window only, max 10 devices, uplink deletes denied | `src/vireonet/validation.py`, `policy.py`, `policies/default.yaml` | `tests/test_sdlc_policy_validation.py`, `tests/test_policy.py`, `tests/test_validation.py` |
| R4 | Adapter framework: Protocol/ABC, Real + Fake (deterministic), Recorded optional; shared httpx factory (timeout, retry with jitter on idempotent calls only, circuit breaker, rate limit, correlation ID); typed errors; `health_check()` + `capabilities()`; secrets never logged | `src/vireonet/adapters/base.py`, `errors.py`, `protocols.py`, `fakes.py`, `structlog_config.py` | `tests/test_sdlc_adapters.py`, `tests/test_adapter_framework.py`, `tests/test_contract.py` |
| R5 | Change service: POST /changes returns 202, duplicate ticket returns existing, list filters/pagination, manual rollback only from applied/post_checked | `src/vireonet/services/changes.py`, `api/changes.py` | `tests/test_sdlc_services.py`, `tests/test_api_changes.py` |
| R6 | ZTP: NetBox registration, DHCP reservation, one-time token (hash only, single use, 15m), identity check serial/model/platform vs NetBox, day-1 via Batfish+AWX; retry/reset legality | `src/vireonet/ztp/service.py`, `temporal/ztp_workflow.py`, `api/ztp.py` | `tests/test_sdlc_services.py`, `tests/test_ztp_service.py` |
| R7 | Temporal: Workflow ID = ServiceNow ticket (idempotent starts), signals for approval/CI/merge; dry-run default for every apply; auto-rollback on failure; device locks (DB, TTL, sorted acquire) + waves/abort | `src/vireonet/temporal/`, `safety.py`, `checks/` | `tests/test_sdlc_services.py`, `tests/test_temporal_workflow.py`, `tests/test_safety_waves.py` |
| R8 | AWX is the ONLY component that touches devices; Vault holds all secrets; Ansible playbooks (backup/pre-check/dry-run/apply/post-check/rollback/ztp-day1) | `src/vireonet/adapters/awx.py`, `vault.py`, `ansible/` | `tests/test_awx_real.py`, `tests/test_vault_real.py`, `tests/test_ansible_syntax.py`, `tests/test_apply_rollback.py` |
| R9 | GitLab + Batfish + EVE-NG in CI (lint, render, Batfish gate must-pass, EVE-NG manual); Git provider option: Gitea/GitLab/GitHub/Bitbucket selected by `GIT_PROVIDER` | `src/vireonet/adapters/git/`, `batfish_gate.py`, `.gitlab-ci.yml`, `.github/workflows/ci.yml`, `.gitea/workflows/ci.yml` | `tests/test_sdlc_git_providers.py`, `tests/test_git_providers.py`, `tests/test_batfish_gate.py` |
| R10 | IAM (M9): users, fixed roles viewer<operator<approver<admin, permission map, JWT auth, DB role wins, last-admin guard, first admin seeded once | `src/vireonet/iam/`, `api/auth.py`, `api/iam.py`, `api/security.py` | `tests/test_sdlc_iam.py` |
| R11 | Gitea setup (M11): manual 10 steps OR automatic install (Docker Compose, admin+repo+token, token to Vault only, never shown) | `src/vireonet/setup/gitea_setup.py`, `api/setup_gitea.py`, `scripts/install_gitea.sh`, `scripts/setup_gitea.py`, `deployments/gitea/` | `tests/test_sdlc_gitea_setup.py` |
| R12 | Config version control (M12): templates repo (Jinja only) ≠ vars repo (YAML only) ≠ NetBox Jinja (NetBox only, read-only export) ≠ per-device repos `netops/config-<hostname>` (one repo per device) | `src/vireonet/config_store/`, `policies/config_store.yaml`, `api/config_repos.py` | `tests/test_sdlc_config_store.py` |
| R13 | Notifications + monitoring + observability; UI (M8): tokens, shell/nav, Changes list/detail, ZTP, Approvals, Locks, Health, Adapters, IAM, Git setup, Config repos | `src/vireonet/notifications.py`, `observability.py`, `frontend/` | `frontend/src/pages/*.test.tsx`, `frontend/tests/e2e/*.spec.ts` |
| E1 | AWX execution engine: one StepRequest in, one StepResult out; mapping (most-specific wins) picks the job template; playbook jobs return empty `declared_changes` | `src/vireonet/execution/awx_engine.py`, `mapping.py`, `types.py` | `tests/test_e1_engine.py`, `tests/test_e1_mapping.py`, `tests/contract_engine.py` |
| E2 | Python runners through AWX (`run-python` template, `VIREONET-RESULT` stdout markers, `set_stats` fallback — UNCERTAIN how AWX exposes `set_stats`) | `src/vireonet/execution/awx_engine.py`, `set_stats.py`, `ansible/playbooks/run_python.yml` | `tests/test_e2_engine_python.py` |
| E3 | Custom scripts (manifest SHA gate, dry-run hash approval) + reconciliation: Git commit + NetBox writes from declared changes only, out-of-scope → drift + `reconciliation_pending`, approver override | `src/vireonet/scripts/`, `src/vireonet/reconciliation/` | `tests/test_e3_scripts.py`, `tests/test_e3_reconciliation.py` |
| E4 | LabAdapter protocol; EVE-NG implements it (old methods kept); Containerlab adapter drives the CLI over SSH (no stable REST API known); backend selected by `VIREONET_LAB_BACKEND` (`eveng` \| `containerlab` \| `both`) | `src/vireonet/adapters/lab.py`, `eveng.py`, `containerlab.py`, `registry.py`, `topologies/two-leaf.clab.yml`, `scripts/lab_smoke.py` | `tests/test_e4_lab.py`, `tests/contract_lab.py` |
| E5 | Wiring/UI/docs: startup validation (manifest + lab settings, real mode only), `/system/health` gains AWX + Execution Environment (status honestly `unknown`) + lab backends, change detail gains execution provenance from stored evidence, read-only admin-only `GET /scripts`, Scripts page + System Health + Change Detail UI | `src/vireonet/main.py`, `src/vireonet/api/health.py`, `api/changes.py`, `api/scripts.py`, `src/vireonet/services/changes.py`, `frontend/src/pages/Scripts.tsx`, `SystemHealth.tsx`, `ChangeDetail.tsx` | `tests/test_e5_wiring.py`, `frontend/src/pages/Scripts.test.tsx`, `SystemHealth.test.tsx`, `ChangeDetail.test.tsx` |
| D1 | Drift data model: `DriftScan` / `DriftDecision` / `DriftIgnoreRule` / `DriftAuditLog` tables, normaliser (volatile-line ignore rules, interface-block sort, secret redaction), idempotent `commit_baseline`, drift state machine (`detected -> under_review -> reverting / merge_pending / exception_active / resolved`) with audit on every move | `src/vireonet/drift/models.py`, `normalise.py`, `baseline.py`, `state_machine.py`, `src/vireonet/reconciliation/drift.py` | `tests/test_d1_drift_model.py` |
| D2 | Drift detection engine: four kinds (unmanaged hash vs golden baseline, intent render vs running, NetBox state vs collected facts, compliance rules), dedup key (device\|kind\|running hash), severity policy, section classification, Batfish enrichment that never breaks detection | `src/vireonet/drift/detect.py`, `severity.py`, `sections.py`, `compliance.py`, `attribution.py` | `tests/test_d2_detection.py` |
| D3 | Drift scheduling: one Temporal schedule spec per site (deterministic jitter), debounced event triggers (`POST /drift/events`, HMAC/token, fail-closed), change-in-progress suppression, scan activities/workflows, `POST /drift/scan`, drift metrics | `src/vireonet/drift/schedules.py`, `events.py`, `workflows.py`, `activities.py`, `collector.py`, `metrics.py`, `src/vireonet/api/drift.py` | `tests/test_d3_scheduling.py` |
| D4 | Drift resolution: rollback through a normal change (`config_restore`, engine steps + auto-rollback, confirm by re-collect), merge via Git branch + MR (per-device vars override, shared templates refused, MR-merged webhook/endpoint, baseline update on merge), time-boxed exceptions (`is_excepted`, expiry job), emergency accept with retrospective deadline, ServiceNow work notes (no incident-creation method exists — logged, not invented), self-approval block, device locks, audit | `src/vireonet/drift/resolve.py`, `src/vireonet/api/drift.py`, `src/vireonet/api/webhooks.py`, `src/vireonet/services/changes.py` (`create_config_restore`, `transition_change`) | `tests/test_d4_resolution.py` |
| D5 | Drift API + UI: list (filters device/kind/severity/status/site, pagination), detail (record + decisions + audit), summary (counts, last scan per site, never-scanned best-effort), ignore-rules (admin, replace whole set), Drift list/detail/Exceptions pages, scan summary on System health; OpenAPI client NOT regenerated (`npm run generate:api` corrupts `api-types.ts` — drift types added by hand) | `src/vireonet/api/drift.py`, `frontend/src/api/drift.ts`, `frontend/src/lib/drift.ts`, `frontend/src/lib/api-types.ts`, `frontend/src/pages/DriftList.tsx`, `DriftDetail.tsx`, `Exceptions.tsx`, `SystemHealth.tsx`, `frontend/src/App.tsx`, `frontend/src/components/layout/Nav.tsx` | `tests/test_d5_drift_api.py`, `frontend/src/pages/DriftList.test.tsx`, `DriftDetail.test.tsx`, `SystemHealth.test.tsx`, `frontend/tests/e2e/drift.spec.ts` (written, not run — no system Chromium in sandbox) |
| D6 | Drift hardening/docs/lab: drift settings + startup validation (real mode refuses, fake warns), 500-device scale test under the concurrency limit, no lock starvation for a mid-scan change, `docs/DRIFT.md`, lab Flow C in `scripts/lab_e2e.py` (backend via `VIREONET_LAB_BACKEND` / `--backend`, drift rollback/merge/exception stages), `docs/LAB.md`, `README.md`, `docs/SDLC.md`, `docs/USER_MANUAL.md` | `src/vireonet/config.py` (`validate_drift_settings`), `src/vireonet/main.py`, `scripts/lab_e2e.py`, `docs/DRIFT.md`, `docs/LAB.md` | `tests/test_d6_scale.py` |
| S1 | Hardening: health/ZTP/metrics auth, webhook HMAC needs a secret, login lockout, lock renewal per wave, per-wave health gate, policy window re-check before apply, drift schedules created, in-app Help, shared demo ITSM state | `api/security.py`, `api/ztp.py`, `api/auth.py`, `safety.py`, `temporal/activities.py`, `temporal/workflow.py`, `drift/schedules.py`, `api/help.py`, `itsm/fake.py` | `tests/test_stage1_hardening.py`, `tests/test_itsm.py`, `frontend/src/pages/Help.test.tsx` |
| W1 | Flow model: stored flows with versions, publish (approver) / enable, export / import, typed inputs, variables, outputs; validation against the action catalog | `flows/catalog.py`, `flows/schema.py`, `flows/store.py`, `models.py` (Flow, FlowVersion, FlowRun), `alembic/versions/0011_flows.py` | `tests/test_flows_engine.py`, `tests/test_flows_triggers.py`, `tests/test_flows_api.py` |
| W2 | Interpreter: variables, if/elif/else, switch, for-each (concurrency), repeat, while (max), try/catch/finally, parallel, break/continue, stop/fail, run-if, run-after, retries with backoff, continue-on-error, step caps; sandboxed Jinja with typed values and secret markers | `flows/engine.py`, `flows/expr.py` | `tests/test_flows_engine.py` |
| W3 | Durable execution in Temporal: activities for every action, timers, approvals by signal, cancel, child ChangeWorkflow for config push, sub-flows, schedule workflow; action failures fail the step | `flows/workflow.py`, `flows/activities.py`, `worker.py` | `tests/test_flows_workflow.py` (real Temporal in Docker) |
| W4 | Triggers: manual, ITSM (any provider, regex captures, changed from→to), flow webhook (token), schedule (cron/interval), Kafka (optional, at-least-once + dedup), VireoNet events (change.*, drift.detected), adapter sources (NetBox, Zabbix, Prometheus, LibreNMS, PRTG, SolarWinds, Git, AWX); dedup keys | `flows/triggers.py`, `flows/sources.py`, `flows/dispatch.py`, `flows/kafka.py`, `api/flows.py`, `api/excel_push.py` | `tests/test_flows_triggers.py`, `tests/test_flows_api.py` |
| W5 | Secure API and Python steps: host allow/deny, DNS-rebinding guard, no redirects, secrets only at call time; scripts admin-only, AST allow-list, restricted builtins, rlimits, empty env, nobody + net namespace when root, isolation report/require, signed sidecar script-runner | `flows/activities.py`, `flows/sandbox.py`, `flows/script_runner.py`, `docker-compose.yml` | `tests/test_flows_sandbox.py`, `tests/test_flows_triggers.py` |
| W6 | Flow builder UI: Visual (drag-and-drop, palette, nested blocks) ⇄ YAML toggle, condition builder, dynamic content, trigger test with sample data, simulation test run, runs with approvals/cancel, versions, templates, import/export | `frontend/src/pages/Flows.tsx`, `FlowEditor.tsx`, `FlowRun.tsx`, `components/flows/*`, `api/flows.ts` | `frontend/src/pages/Flows.test.tsx`, `frontend/src/api/flows.test.ts` |
| C1 | Vendor controllers behind one interface (devices, images, distribute, activate, upgrade, jobs, raw API): Catalyst Center, CloudVision, Mist, Apstra, FortiManager, Panorama, Nokia NSP, ExtremeCloud IQ, Meraki, Aruba Central; fakes backed by the simulated network; flow actions with auto controller selection and durable job waits; OS rollback = activate the previous version; read-only Controllers page | `controllers/*`, `flows/activities.py`, `flows/engine.py`, `flows/examples.py`, `api/controllers.py`, `frontend/src/pages/Controllers.tsx` | `tests/test_controllers.py`, `tests/test_flows_workflow.py`, `tests/test_flows_api.py`, `frontend/src/pages/Controllers.test.tsx` |
| A1 | Adapters verified against official vendor docs (ITSM, NetBox, AWX, Git x4, Vault, Batfish, EVE-NG, containerlab, Kea, Teams, Zabbix, Prometheus, LibreNMS, PRTG, SolarWinds, webhook sources, 10 controllers) and fixed; log platforms (Elasticsearch, OpenSearch, Splunk, Loki, Graylog) for flows and event export; every adapter family on the Adapters tab; adapter settings editable in the UI (encrypted secrets, test connection); Gitea tab; documentation space | `adapters/*`, `itsm/*`, `telemetry/*`, `flows/sources.py`, `controllers/vendors.py`, `logsearch/`, `adapter_config.py`, `api/adapters.py`, `api/adapter_config.py`, `api/gitea_view.py`, `api/help.py`, `frontend/src/pages/{Adapters,Gitea,Help}.tsx` | `tests/test_*_docs.py`, `tests/test_logsearch.py`, `tests/test_adapters_tab.py`, `tests/test_adapter_config.py`, UI tests |
| N1 | Intelligence tab (renamed from Machine learning) with neural networks: DeepLog-style log sequences (held-out baseline), change-based traffic forecast with time-to-saturation, change-risk MLP with permutation importance, simplified-graph-convolution root cause on the topology, TF-IDF+MLP ticket classifier with device extraction; flow actions Predict change risk / Classify ticket text | `ml/neural.py`, `api/ml.py`, `flows/catalog.py`, `flows/activities.py`, `frontend/src/components/NeuralTabs.tsx` | `tests/test_neural.py`, `frontend/src/components/NeuralTabs.test.tsx` |
| P1 | Pre/Post checks: saved profiles (versioned), snapshots of raw + TextFSM output, tests on parsed rows only (row_exists, no_rows, all_rows, count, field_range, no_rows_removed/added, unchanged, delta), intelligent diff (keyed rows, ignored fields, numeric tolerances, raw diff with ignore regexes); usable on changes (check_profile: pre blocks, post fails -> rollback), in flows (Run Pre/Post check profile) and by hand; shared simulated network between API and worker | `prepost/engine.py`, `prepost/service.py`, `api/prepost.py`, `temporal/activities.py`, `execution/fake_network.py`, `frontend/src/pages/PrePost.tsx` | `tests/test_prepost.py`, `frontend/src/pages/PrePost.test.tsx` |

## 2. Design

- **Backend:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 + Alembic, PostgreSQL (SQLite in-memory for unit tests), httpx, structlog, Temporal (Python SDK).
- **Workflow:** Temporal is the durable workflow; Workflow ID = ticket number (`ZTP-<serial>` for ZTP). All state lives in PostgreSQL + Temporal; the API is stateless.
- **Adapters:** every external system behind a Protocol; Real (httpx, respx-mocked in tests) and Fake (in-memory, deterministic, seeded data: leaf-01/FOC1234A, RITM0001234, VLAN 100 "web"). Git providers behind a second protocol (`GitProvider`) selected by config.
- **IAM:** JWT (HS256, 8h) from `POST /auth/login`; role comes from the signed token and is re-checked against the DB user (DB role wins; deactivation takes effect immediately). The old `X-VireoNet-Role` header is not trusted.
- **Config version control:** three separate stores + one repo per device (R12). Templates never contain IPs/secrets/device values; vars never contain Jinja; NetBox Jinja is never a render source.

## 3. Implementation

Small readable modules, type hints everywhere, docstrings explain *why*.
Uncertain vendor behaviour is isolated behind an interface and marked
`UNCERTAIN` in code (ServiceNow field names, Batfish question/columns,
EVE-NG `.unl` load, DHCP option 43, Bitbucket multipart commit, Gitea
pipeline latest-run, Gitea merge `Do` field). No placeholders, no invented
library APIs, no credentials in code/Git/logs (Vault only).

## 4. Testing — strategy

- **Unit (this repo, pytest):** fakes + respx mocks, no network, no Docker,
  no real devices. `asyncio_mode = "auto"`. Run: `pip install -e ".[dev]" && pytest -q`.
- **Contract:** one shared suite per adapter interface, run against Fake and
  Real (respx-mocked HTTP) — `tests/contract_suite.py`.
- **Frontend:** Vitest component tests + Playwright flows (`npm test`, `npm run test:e2e`).
- **CI gate:** lint, render, Batfish gate must pass; EVE-NG manual only.
- **What is NOT tested here:** live ServiceNow/NetBox/AWX/Git/device
  connections, Docker Compose up, real Temporal/Postgres, Playwright e2e
  (see §6).

## 5. Deployment

```bash
pip install -e ".[dev]"
alembic upgrade head
VIREONET_ADMIN_USER=admin VIREONET_ADMIN_PASSWORD='<12+ chars>' python scripts/seed_admin.py
docker compose up --build -d   # postgres, temporal, api :8000, worker; fake adapters by default
# or: docker compose config    # validate compose files without starting anything
```

See `README.md` and `docs/runbook.md` for the change / rollback / ZTP runbooks.

## 6. Maintenance — known debt and limits

- **Ruff / mypy:** run `ruff check` and `mypy --strict`; record counts per
  run in the test report. Pre-existing strict-typing debt exists across the
  tree; fix behaviour first, typing second.
- **Temporal workflow tests** (`test_temporal_workflow.py`, `test_ztp_workflow.py`,
  `test_flows_workflow.py`, …) need a Temporal server. In this sandbox they run in
  Docker against the dev server (`-p real_temporal_plugin`); the time-skipping
  test server does not start under the macOS sandbox.
- **Bitbucket `commit_file`** posts JSON; Bitbucket needs multipart `POST /src`.
  Unverified against a real workspace.
- **Gitea `get_pipeline`** returns the most recent run, not necessarily this PR's run.
- **Config history**: `GitProvider.list_commits` exists now; Gitea/GitLab/GitHub implement it.
- **JWT:** role changes take up to the 8h token expiry to apply (deactivation
  is immediate). No refresh tokens, no OIDC, no password reset. Login lockout
  is per API process (put a shared limiter in front with several replicas).
- **Diff** in change detail is intent lines as additions; current config is
  not stored, so there is no true current-vs-candidate diff yet.
- **Gitea setup job store** is in-memory; an API restart loses job history.
- **Flow scripts, local runner**: network isolation needs CAP_SYS_ADMIN, which
  Docker does not grant by default; use the script-runner service in production.
- **Controller adapters**: calls marked UNCERTAIN in `controllers/vendors.py`
  are built from vendor documentation and tested against mocks only; verify on
  a lab controller (stage: adapter verification).
- **Mode matrix not run in this delivery**: the auto / fake / real
  (`temporal_mode` × `adapter_mode`) end-to-end matrix was skipped on request.
  Covered instead: every test against fakes and vendor-documented mocks, real
  Temporal / Postgres / Kafka / Gitea in Docker, fake adapters on the live stack.
  Real-mode runs against live vendor systems remain to be done on a lab.
- **Concurrent for-each** gives each item a copy of the variables; changes made
  inside items are not merged back.
- **Frontend:** full-suite Vitest single-process run can hang at teardown in
  this sandbox (run per-file); Playwright e2e was not run in the SDLC pass.
