# VireoNet — Adapter Integration Guide

> Every adapter in VireoNet, how to connect it to the real system, and how to tell whether it is working. Read this before switching any adapter from fake to real.
>
> Sources: `src/vireonet/adapters/*.py`, `src/vireonet/adapters/git/*.py`, `src/vireonet/config.py`, `src/vireonet/api/webhooks.py`, `policies/default.yaml`, `deployments/gitea/docker-compose.gitea.yml`, `scripts/setup_gitea.py`. Where the code marks something **UNCERTAIN**, this guide says so instead of guessing.

## Contents

- [How adapters work](#how-adapters-work)
- [Checklist](#checklist)
- [The global switch: fake vs real](#the-global-switch-fake-vs-real)
- [Health and capabilities endpoints](#health-and-capabilities-endpoints)
- [Secrets: Vault, never code, Git or logs](#secrets-vault-never-code-git-or-logs)
- [1. ServiceNow](#1-servicenow)
- [2. NetBox](#2-netbox)
- [3. AWX / Ansible Controller](#3-awx--ansible-controller)
- [4. Git — choosing a provider](#4-git--choosing-a-provider)
- [4a. GitLab](#4a-gitlab)
- [4b. GitHub](#4b-github)
- [4c. Bitbucket Cloud](#4c-bitbucket-cloud)
- [4d. Gitea](#4d-gitea)
- [5. Batfish](#5-batfish)
- [6. Vault](#6-vault)
- [7. EVE-NG](#7-eve-ng)
- [7a. Containerlab](#7a-containerlab)
- [8. DHCP (ISC / Kea) and ZTP](#8-dhcp-isc--kea-and-ztp)
- [9. Notification (SMTP / Teams / Slack)](#9-notification-smtp--teams--slack)
- [10. Monitoring (none / Alertmanager / LibreNMS)](#10-monitoring-none--alertmanager--librenms)
- [Webhooks into VireoNet](#webhooks-into-vireonet)
- [What is NOT wired in real mode (known gaps)](#what-is-not-wired-in-real-mode-known-gaps)

## How adapters work

Every adapter is a `Protocol`/ABC with a **Real** implementation and a **Fake** (in-memory, deterministic) implementation used by tests and local development. Shared behaviour lives in `src/vireonet/adapters/base.py`:

- One `httpx` client factory: per-adapter timeout, correlation header `X-Correlation-ID` (the ticket number) on every call, structured logging that never prints secrets or full payloads.
- Retry with exponential back-off + jitter **only on safe/idempotent calls**; non-idempotent calls (launches, commits, merges) are never retried.
- Circuit breaker: opens after `VIREONET_ADAPTER_FAILURE_THRESHOLD` (default 5) consecutive failures, resets after `VIREONET_ADAPTER_RESET_TIMEOUT_SECONDS` (default 30). While open, calls fail fast with `AdapterUnavailable`.
- Rate limiting: token bucket, default 10 requests/second per adapter.
- Typed errors: `AdapterAuthError`, `AdapterNotFound`, `AdapterConflict`, `AdapterRateLimited`, `AdapterTimeout`, `AdapterUnavailable`, `AdapterValidationError`. HTTP status mapping is per-adapter but consistent: 401/403 → auth, 404 → not found, 409 → conflict, 429 → rate limited, 400/422 → validation, 5xx → unavailable.
- Every adapter exposes `health_check()` (returns `AdapterHealth`: `ok | degraded | down`, latency, last success, capabilities, detail) and `capabilities()` (a list of strings).

Adapters are built once at startup by `build_adapters()` in `src/vireonet/adapters/registry.py`. They are **not** re-read at runtime: changing an adapter's URL or token means restarting the API and worker containers.

## Checklist

| Adapter | Required? | Env vars (VIREONET_ prefix) | Webhook into VireoNet? |
|---|---|---|---|
| ServiceNow | Yes, for real changes | `SERVICENOW_BASE_URL`, `SERVICENOW_AUTH_MODE`, `SERVICENOW_CLIENT_ID`, `SERVICENOW_CLIENT_SECRET` or `SERVICENOW_USERNAME`, `SERVICENOW_PASSWORD`; `WEBHOOK_SECRET` / `API_TOKEN` for inbound auth | Yes — ServiceNow calls `POST /changes`; Git webhooks below |
| NetBox | Yes | `NETBOX_BASE_URL`, `NETBOX_TOKEN` | No |
| AWX | Yes — only component that touches devices | `AWX_BASE_URL`, `AWX_TOKEN` | No |
| Git (GitLab) | Yes | `GIT_PROVIDER=gitlab`, `GIT_BASE_URL`, `GIT_REPO`, `GIT_TOKEN_SECRET` (Vault path), `GITLAB_BASE_URL`, `GITLAB_TOKEN`, `GITLAB_PROJECT_ID`, `GITLAB_WEBHOOK_TOKEN` | Yes — `POST /webhooks/gitlab` |
| Git (GitHub) | Alternative to GitLab | `GIT_PROVIDER=github`, `GIT_REPO`, `GIT_TOKEN_SECRET` | Yes — `POST /webhooks/github` |
| Git (Bitbucket) | Alternative to GitLab | `GIT_PROVIDER=bitbucket`, `GIT_REPO`, `GIT_TOKEN_SECRET` | Yes — `POST /webhooks/bitbucket` |
| Git (Gitea) | Alternative to GitLab | `GIT_PROVIDER=gitea`, `GIT_BASE_URL` (required), `GIT_REPO`, `GIT_TOKEN_SECRET` | Yes — `POST /webhooks/gitea` |
| Batfish | Yes for the CI gate | `BATFISH_HOST`, `BATFISH_PORT`, `BATFISH_SNAPSHOT_DIR` | No |
| Vault | Yes in real mode | `VAULT_ADDR`, `VAULT_ROLE_ID`, `VAULT_SECRET_ID`, `VAULT_KV_MOUNT`, `VAULT_BOOTSTRAP_POLICY` | No |
| EVE-NG | No — CI only, manual | `EVENG_BASE_URL`, `EVENG_USERNAME`, `EVENG_PASSWORD` | No |
| DHCP | For ZTP only | `DHCP_SERVER`, `DHCP_CONFIG_PATH`, `DHCP_TFTP_SERVER`, `DHCP_HTTP_BASE_URL`, `DHCP_PNP_SERVER_URL` | No — devices call `POST /ztp/callback` |
| Notification | No | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `NOTIFICATION_EMAIL_TO`, `NOTIFICATION_EMAIL_FROM`, `TEAMS_WEBHOOK_URL`, `SLACK_WEBHOOK_URL`, `NOTIFICATION_DEFAULT_CHANNEL` | No |
| Monitoring | No — default `none` | `MONITORING_BACKEND`, `MONITORING_BASE_URL`, `MONITORING_TOKEN`, `MONITORING_SILENCE_MINUTES` | No |

## The global switch: fake vs real

```bash
VIREONET_ADAPTER_MODE=fake   # default: in-memory deterministic fakes, no external systems
VIREONET_ADAPTER_MODE=real   # real adapters, built from the env vars in this guide
```

In `fake` mode every adapter's `health_check()` returns `ok` unconditionally, and the UI shows seeded fake data (ServiceNow `RITM0001234` "Add VLAN 100" / `alice`; NetBox `leaf-01` `FOC1234A` C9300-48P `dc1` active, `leaf-02` staged; VLAN 100 "web"; AWX templates `backup`, `pre-check`, `dry-run`, `apply`, `post-check`, `rollback`). That is demo/test data, not your network. In `real` mode the same screens are empty until a real request creates a change, and a health status of `down` means the real system really is unreachable.

Note the switch is **global and all-or-nothing**: `registry.py` builds the real set for ServiceNow, NetBox, AWX, Vault, GitLab, Batfish, Notification and Monitoring when the mode is `real`, and the lab backend(s) selected by `VIREONET_LAB_BACKEND` (`eveng` | `containerlab` | `both`, default `eveng`; see §7/§7a). There is no per-adapter fake/real mix in the shipped code. `recorded` is declared as a valid mode in `config.py` but `build_adapters()` raises `AdapterUnavailable` for it — do not use it.

Shared tuning (applies to all real adapters):

```bash
VIREONET_ADAPTER_TIMEOUT_SECONDS=10.0
VIREONET_ADAPTER_MAX_RETRIES=3
VIREONET_ADAPTER_FAILURE_THRESHOLD=5
VIREONET_ADAPTER_RESET_TIMEOUT_SECONDS=30.0
```

## Health and capabilities endpoints

No login is needed for the liveness probe, but adapter health needs a signed-in user (any role, `viewer` or above):

```bash
curl http://localhost:8000/healthz
# {"status":"ok"}

curl http://localhost:8000/adapters/health -H "Authorization: Bearer $TOKEN"
# {"adapters": [{"name": "servicenow", "status": "ok", "latency_ms": 42.1,
#   "last_success": "...", "capabilities": ["read_request", ...], "detail": ""}, ...]}

curl http://localhost:8000/system/health -H "Authorization: Bearer $TOKEN"
# {"status": "ok|degraded|down", "components": [{"name": "api"|"database"|"temporal", ...}]}
```

In the UI the same data is on **System health** (`/health`, read-only table + Refresh) and **Adapters** (`/adapters`, read-only settings, no secrets, endpoint shown as scheme+host+port only). A status of `down` with a `detail` string is the first thing to read when a change stalls: the detail is the adapter's own error (for example `bad NetBox token (401)`).

## Secrets: Vault, never code, Git or logs

- Tokens, passwords, client secrets and webhook secrets live in **Vault** (KV v2) or are passed as environment variables to the container at deploy time. They are never committed, never printed in logs (the shared client redacts them), and never put in AWX `extra_vars` — the AWX adapter rejects any `extra_vars` key containing `secret`, `token`, `password`, `private_key` or `api_key`.
- The Git token is referenced by Vault path only: `VIREONET_GIT_TOKEN_SECRET` defaults to `git/token` — that is a *path*, not the token.
- The webhook secret / API token (`VIREONET_WEBHOOK_SECRET`, `VIREONET_API_TOKEN`) authenticates inbound webhooks. If neither is set, webhook verification is skipped — dev only, never production.

---

## 1. ServiceNow

**What it does in VireoNet:** reads the request (`RITM…` / `CHG…`), updates its state, adds work notes, closes it at the end of a successful change, reads approval state, attaches files. It is the entry point: a ServiceNow request (or a direct `POST /changes` with the ticket) starts the workflow.

**Prerequisites on the external system:** a ServiceNow instance with the Table API enabled; a user (basic auth) or an OAuth application (client-credentials) with read/write access to `sc_req_item`, `change_request` and `sysapproval_approver`; the catalog variables for the request must be readable via `sc_item_option_mtom` / `sc_item_option`.

**Auth:** two modes, selected by `VIREONET_SERVICENOW_AUTH_MODE` (`oauth` default, or `basic`).

- OAuth: the adapter POSTs to `<base>/oauth_token.do` with `grant_type=client_credentials`, the client id/secret, caches the token, and refreshes it 30 seconds before expiry; a 401 on an idempotent call triggers one token refresh and retry.
- Basic: `Authorization: Basic base64(username:password)` on every call.

```bash
VIREONET_SERVICENOW_BASE_URL=https://your-instance.service-now.com
VIREONET_SERVICENOW_AUTH_MODE=oauth
VIREONET_SERVICENOW_CLIENT_ID=<from Vault / env>
VIREONET_SERVICENOW_CLIENT_SECRET=<from Vault / env>
# basic mode instead:
# VIREONET_SERVICENOW_AUTH_MODE=basic
# VIREONET_SERVICENOW_USERNAME=<user>
# VIREONET_SERVICENOW_PASSWORD=<from Vault / env>
```

**How it is used:** `GET /api/now/table/sc_req_item?sysparm_query=number=<ticket>&sysparm_display_value=true&sysparm_limit=1`, variables via `sc_item_option_mtom`; state changes are `PATCH` to `/api/now/table/sc_req_item/<sys_id>` with the mapped state. Health check is `GET /api/now/table/sc_req_item?sysparm_limit=1`.

**Capabilities:** `read_request`, `update_state`, `work_notes`, `close`, `attach_file`, `approval_read`.

**Webhook setup:** ServiceNow (or your integration) calls `POST /changes` on the VireoNet API with either `{"ticket_number": "RITM0001234"}` (VireoNet fetches the request from ServiceNow) or `{"intent": {...}}` (a rendered intent, see `src/vireonet/intent.py` for the 8 intent types). The call must authenticate per [Webhooks into VireoNet](#webhooks-into-vireonet). The workflow ID is the ticket number, so re-posting the same ticket is a no-op (idempotent start).

**Verify:**

```bash
curl http://localhost:8000/adapters/health -H "Authorization: Bearer $TOKEN" | python3 -m json.tool | grep -A 4 servicenow
curl -X POST http://localhost:8000/changes -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"ticket_number": "RITM0001234"}'
# 202 + the created change; a duplicate ticket returns the existing change
```

**Common failures:**

| Symptom | Cause | Fix |
|---|---|---|
| `AdapterAuthError … unauthorised (401)` | Wrong client id/secret, or OAuth app not allowed client-credentials | Check the OAuth app in ServiceNow; re-issue the secret |
| `AdapterNotFound … RITM…` | Ticket number wrong, or the request lives in a different table | Check the ticket exists in `sc_req_item`; see the UNCERTAIN note below |
| Health `down`, `server error 5xx` | Instance down or MID Server down | Check the instance, not VireoNet |
| Change stays `received`, never validates | ServiceNow variables missing (VLAN/device not in the catalog item) | Fix the catalog item variables; validation needs them |

**UNCERTAIN (from the code, `src/vireonet/adapters/servicenow.py`):** ServiceNow Table API field names are instance/catalog-specific. The defaults in `SN_TABLES` (`sc_req_item`, `change_request`, `sysapproval_approver`) and `SN_STATE_MAP` (`open=1`, `in_progress=2`, `closed=3`, `resolved=6`) fit a standard install. If your instance renames tables or state values, change only that dict — not the methods. The variable fetch via `sc_item_option_mtom` / `sc_item_option` (`item_option_new`, `value`) is the standard shape but is also instance-specific.

**Fake-mode behaviour:** one seeded request, `RITM0001234` "Add VLAN 100", state `open`, requester `alice`, variables `{"vlan_id": "100"}`. Work notes are deduplicated in-process by idempotency key.

---

## 2. NetBox

**What it does in VireoNet:** the source of truth. Validation reads devices, interfaces, VLANs and IPs from NetBox; ZTP registers devices there and flips them to `active` at the end; every change writes a journal entry; AWX inventory is exported from it.

**Prerequisites:** NetBox (REST API) reachable from the API container; a device inventory with `device_type`, `role`, `site`, `platform` and `status` populated; VLANs and prefixes for the sites you change.

**Auth:** API token, sent as `Authorization: Token <token>` on every call.

```bash
VIREONET_NETBOX_BASE_URL=https://netbox.example.com
VIREONET_NETBOX_TOKEN=<from Vault / env>
```

Create the token in NetBox under Admin → API Tokens, with write permission on DCIM (devices, interfaces, IPAM, extras/journal). A read-only token will fail validation writes with 403.

**How it is used:** `GET /api/dcim/devices/?limit=1` (health), device list/detail, `POST /api/dcim/devices/` (ZTP registration), `PATCH` for status (with optimistic conflict via `last_updated` — a stale `last_updated` raises `AdapterConflict`), VLAN and IP endpoints, journal entries, inventory export for AWX. Pagination follows NetBox `next` links.

**Capabilities:** `devices`, `interfaces`, `vlans`, `ips`, `journal`, `inventory_export`, `bulk`.

**Verify:**

```bash
curl http://localhost:8000/adapters/health -H "Authorization: Bearer $TOKEN" | python3 -m json.tool | grep -A 4 netbox
# In NetBox itself: a ZTP device should appear as staged, then active after day-1
```

**Common failures:**

| Symptom | Cause | Fix |
|---|---|---|
| `bad NetBox token (401)` | Token wrong or revoked | Re-issue the token in NetBox |
| `NetBox token lacks permission (403)` | Token is read-only | Give it DCIM write permission |
| `AdapterConflict` on status update | Another process updated the device (`last_updated` moved) | Re-read the device and retry; do not force it |
| Validation rejects a device that exists | Device `status` is not `active`/`staged`, or site/platform slug mismatch | Fix the device record in NetBox |

**UNCERTAIN:** `create_device` needs NetBox `device_type_id` and `role_id` as numeric IDs — there is no name-to-ID lookup in the adapter, so the caller must supply them. The `last_updated` conflict check depends on NetBox returning that field.

**Fake-mode behaviour:** `leaf-01` (`FOC1234A`, C9300-48P, `dc1`, active, ios-xe, `10.0.0.11`), `leaf-02` (staged), VLAN 100 "web" in `dc1`; IPs are allocated from `10.0.1.0/24` starting at `.100` in the fake.

---

## 3. AWX / Ansible Controller

**What it does in VireoNet:** the **only** component that touches network devices. VireoNet never SSHes to a device. AWX runs the playbooks in `ansible/playbooks/` via the job templates in `ansible/awx/job_templates.yaml`: `backup`, `pre-check`, `dry-run`, `apply`, `post-check`, `rollback`, `ztp-day1`, plus `dhcp-reload` for DHCP deploys.

**Prerequisites:** AWX or Ansible Controller with the REST API v2 reachable; the job templates above created with exactly those names (the adapter looks templates up by name via `GET /api/v2/job_templates/?name=<name>`); the playbooks and inventory available to AWX; an AWX user/token with permission to launch those templates and read job output.

**Auth:** Bearer token, `Authorization: Bearer <token>`.

```bash
VIREONET_AWX_BASE_URL=https://awx.example.com
VIREONET_AWX_TOKEN=<from Vault / env>
```

**How it is used:** launch is `POST /api/v2/job_templates/<id>/launch/` with `{"extra_vars": {...}, "limit": ...}`; workflows via `/api/v2/workflow_job_templates/<id>/launch/`; polling is `GET /api/v2/jobs/<id>/`, events `/api/v2/jobs/<id>/job_events/`, stdout from the job events. Job statuses are mapped (`new/pending/waiting → pending`, `successful → successful`, `failed/error → failed`, `canceled/cancelled → canceled`); unknown statuses pass through unchanged. Health check is `GET /api/v2/ping/`.

**Execution Environment (E5):** device sessions run only inside the AWX Execution Environment. The EE is built from `ansible/execution-environment/` (`ansible-builder build -t vireonet-ee:latest`, base image `quay.io/ansible/ansible-runner:latest`, ansible-core 2.17.7, `cisco.ios` / `cisco.nxos` / `ansible.netcommon` collections) and registered in AWX as **VireoNet EE** (`VIREONET_AWX_EXECUTION_ENVIRONMENT`, default `VireoNet EE`). In `ansible/awx/job_templates.yaml` only the `run-python` template declares `execution_environment: VireoNet EE`. **Honest limit:** VireoNet cannot verify from the AWX API that the EE is actually registered on the job templates, so `GET /system/health` reports the EE status as `unknown` — verify it in AWX (Job Templates → Execution Environment) rather than trusting the health page.

**Python runners (E2):** job_type `python` runs the `run-python` template (`ansible/playbooks/run_python.yml`), which executes a Python runner from the `vireonet_runners` package inside the EE (`python -m vireonet_runners.runners <step>`, request JSON on stdin, `VIREONET-RESULT` JSON on stdout, result also exposed via `set_stats` as `vireonet_result`). Only scripts listed in `policies/scripts.yaml` at the listed SHA may run (see `docs/SCRIPTS.md`).

**Credential type:** no AWX credential is declared in `ansible/awx/job_templates.yaml` — the templates carry no credential reference. Attach the AWX Machine credential that can log in to the devices to each job template in AWX. Device credentials are never passed in `extra_vars` (the engine rejects any key containing `secret`, `token`, `password`, `private_key` or `api_key`); the API token for AWX itself is a Bearer token (`VIREONET_AWX_TOKEN`), held in Vault or the environment.

**Capabilities:** `launch_job`, `launch_workflow`, `poll_job`, `job_events`, `cancel`, `relaunch`, `templates`, `approvals`, `sync`.

**Verify:**

```bash
curl http://localhost:8000/adapters/health -H "Authorization: Bearer $TOKEN" | python3 -m json.tool | grep -A 4 '"name": "awx"'
# In AWX: run the dry-run template by hand once and confirm it completes
```

**Common failures:**

| Symptom | Cause | Fix |
|---|---|---|
| `bad AWX token (401)` / `lacks permission (403)` | Token wrong or user lacks launch permission on the template | Re-issue / fix the AWX user and template permissions |
| `AdapterNotFound … launch_job … <template>` | Template name mismatch | Rename the AWX template to exactly `backup` / `pre-check` / `dry-run` / `apply` / `post-check` / `rollback` / `ztp-day1` |
| `extra_vars must not contain secrets` | A secret-named key was passed to a job | Move the secret to Vault / AWX credentials; never put secrets in `extra_vars` |
| Job runs but change never advances | AWX job failed or was cancelled | Read the job stdout in AWX; the workflow rolls back on failure |

**Fake-mode behaviour:** templates `backup`, `pre-check`, `dry-run`, `apply`, `post-check`, `rollback`; every launched job immediately returns `successful`. No device is touched.

---

## 4. Git — choosing a provider

VireoNet does not hard-code GitLab. The Git provider is chosen by config, and all four providers implement the same `GitProvider` protocol (`src/vireonet/adapters/git/base.py`): `get_file`, `create_branch`, `commit_file`, `open_mr`, `get_mr`, `get_pipeline`, `merge_mr`, `add_comment`, `get_mr_diff`, plus `health_check` and `capabilities`.

```bash
VIREONET_GIT_PROVIDER=gitlab   # gitlab | github | bitbucket | gitea (default: gitlab)
VIREONET_GIT_BASE_URL=         # required for gitea; optional override for the others
VIREONET_GIT_REPO=netops/intent
VIREONET_GIT_TOKEN_SECRET=git/token   # Vault KV path, never the token itself
VIREONET_GIT_DEFAULT_BRANCH=main
```

`get_git_provider()` (in `src/vireonet/adapters/git/registry.py`) raises a plain error if the provider is unknown, if the token is missing, if the repo is missing, or if Gitea is chosen without a base URL. Default API base URLs when `VIREONET_GIT_BASE_URL` is empty: GitLab `https://gitlab.com/api/v4`, GitHub `https://api.github.com`, Bitbucket `https://api.bitbucket.org/2.0`, Gitea none (no cloud Gitea exists, so a base URL is required).

The token is read from Vault at the `VIREONET_GIT_TOKEN_SECRET` path at runtime (see §6); it is passed into the provider, never read from the repo or logged. The legacy M5 GitLab adapter (`src/vireonet/adapters/gitlab.py`, REST v4, `PRIVATE-TOKEN` auth, bound to one project via `VIREONET_GITLAB_PROJECT_ID`) still exists separately and is what `registry.py` wires as the `gitlab` adapter in real mode; the M10 GitLab *provider* (`src/vireonet/adapters/git/gitlab.py`) is the one used by the config-store / device-repo code via `get_git_provider()`.

**How Git is used:** the workflow opens a branch `change/<ticket>`, commits the rendered intent, opens an MR titled `<ticket>: <description>`, CI runs (lint, render, Batfish, optional EVE-NG), and on merge the workflow proceeds. The ticket is carried in the MR title prefix (`RITM…:`) or the branch name, and the CI/merge webhooks below route on it.

**Config version control (M12)** uses the same provider but with separate repos that must never be mixed — see `docs/USER_MANUAL.md` and `policies/config_store.yaml`: templates only in `netops/templates` (Jinja2 only, no device values/IPs/secrets), vars only in `netops/vars` (YAML only, no Jinja delimiters), one repo per device in `netops/config-<hostname>` (rendered/candidate/backup/`vars.yaml` snapshot/`metadata.yaml`), and NetBox Jinja exported read-only to `netops/netbox-export` (never a render source).

### 4a. GitLab

**Prerequisites:** a GitLab instance (self-hosted or gitlab.com) with a project for the intent repo; a user or group access token with `api` scope (read/write repository, MRs, pipelines).

**Auth:** `PRIVATE-TOKEN: <token>` header (M5 adapter); the M10 provider uses the same token via `get_git_provider()`.

```bash
VIREONET_GIT_PROVIDER=gitlab
VIREONET_GITLAB_BASE_URL=https://gitlab.example.com
VIREONET_GITLAB_TOKEN=<from Vault / env>
VIREONET_GITLAB_PROJECT_ID=<numeric project id, or URL-encoded path>
VIREONET_GITLAB_WEBHOOK_TOKEN=<shared secret for the webhook, see below>
VIREONET_GIT_REPO=netops/intent
```

The project path in API calls is URL-encoded (`/` → `%2F`), as the M5 adapter does.

**Webhook:** in GitLab, project → Settings → Webhooks, URL `https://<vireonet-host>:8000/webhooks/gitlab`, secret token = `VIREONET_GITLAB_WEBHOOK_TOKEN`, trigger on **pipeline events** and **merge request events**. VireoNet verifies the `X-Gitlab-Token` header; if no token is configured it accepts unsigned webhooks (dev only). Pipeline events carry the ticket in the pipeline variables (`ticket`); merge events carry it in the MR title prefix `RITM…:` or the branch `change/RITM…`. A payload with no ticket is answered `{"status": "ignored", "reason": "no ticket in pipeline payload"}`.

**CI:** `.gitlab-ci.yml` in the repo runs lint, render, Batfish (must pass) and EVE-NG (manual, only if `RUN_EVENG=true`).

**Verify:** `GET /adapters/health` → `gitlab` ok; open an MR from the UI workflow and confirm the pipeline appears.

**Common failures:** 401 → token scope/expired; 404 on a project → wrong project id or URL-encoding; webhook ignored → ticket not in the MR title or pipeline variables.

### 4b. GitHub

**Prerequisites:** a GitHub repo, a fine-grained personal access token (or GitHub App token) with contents + pull requests + checks read/write.

**Auth:** `Authorization: Bearer <token>`, `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`.

```bash
VIREONET_GIT_PROVIDER=github
VIREONET_GIT_REPO=<owner>/<repo>     # e.g. your-org/netops-intent
VIREONET_GIT_TOKEN_SECRET=git/token  # Vault path
# VIREONET_GIT_BASE_URL only if you use GitHub Enterprise; default is https://api.github.com
```

**Webhook:** repo → Settings → Webhooks, URL `https://<vireonet-host>:8000/webhooks/github`, content type JSON, secret = `VIREONET_WEBHOOK_SECRET` (verified by `verify_webhook`: HMAC SHA256 over the raw body, header `X-VireoNet-Signature`, optionally prefixed `sha256=`, or Bearer token). Subscribe to **check runs / check suites** and **pull requests**. A merged PR (title `RITM…: …`) signals merge; a check run with conclusion `success` signals CI pass.

**Verify:** `GET /adapters/health` → `github` ok (health is `GET /repos/<owner>/<repo>`).

**UNCERTAIN:** pipeline status is read from **check-runs** (`/repos/<owner>/<repo>/commits/<sha>/check-runs`). A repo whose CI reports via commit statuses (Jenkins, external CI) instead of check-runs will report `unknown`. Merge uses `merge_method: squash`.

**Common failures:** 404 on the repo → token lacks repo access or the `owner/repo` spelling is wrong; webhook 401 → secret mismatch; CI never signals → the repo uses commit statuses, not check-runs (see above).

### 4c. Bitbucket Cloud

**Prerequisites:** a Bitbucket Cloud workspace + repo, an OAuth access token / app password with repository read/write and pull-request write.

**Auth:** `Authorization: Bearer <token>`, base `https://api.bitbucket.org/2.0`.

```bash
VIREONET_GIT_PROVIDER=bitbucket
VIREONET_GIT_REPO=<workspace>/<repo>   # note: workspace, not just repo
VIREONET_GIT_TOKEN_SECRET=git/token
```

**Webhook:** repo → Settings → Webhooks, URL `https://<vireonet-host>:8000/webhooks/bitbucket`, verified by `verify_webhook` (same HMAC/Bearer scheme as GitHub). Subscribe to **commit status created** (`repo:commit_status_created`) and **pull request fulfilled** (`pullrequest:fulfilled`). The ticket for a commit-status event is parsed from the **commit message**, which must start with the ticket number — the webhook carries no PR title.

**Verify:** `GET /adapters/health` → `bitbucket` ok (`GET /repositories/<workspace>/<repo>`).

**UNCERTAIN:** `commit_file` posts JSON to `/repositories/<workspace>/<repo>/src`, but Bitbucket's commit-via-REST endpoint needs a **multipart** POST — this is unverified and may fail on a real workspace. Merge uses `merge_strategy: squash`.

**Common failures:** 404 → workspace/repo spelling; CI never signals → commit message does not start with the ticket number.

### 4d. Gitea

**Prerequisites:** a self-hosted Gitea instance (there is no Gitea cloud). Gitea 1.23 is the version in `deployments/gitea/docker-compose.gitea.yml` (image `gitea/gitea:1.23`, Postgres 16, host ports `3000` and `2222`).

**Auth:** `Authorization: token <token>`, API v1 under `<base>/api/v1`.

```bash
VIREONET_GIT_PROVIDER=gitea
VIREONET_GIT_BASE_URL=http://localhost:3000   # required — no default
VIREONET_GIT_REPO=netops/intent
VIREONET_GIT_TOKEN_SECRET=git/token
```

**Install options** (all three do the same end state — Gitea running, admin user, repo, token in Vault):

1. **From the UI (admin only):** Adapters → Gitea setup (`/setup/gitea`). Two cards: **Manual install** ("Show manual steps" — the 10 steps from `GET /setup/gitea/manual`) or **Automatic install** ("Install automatically" — form: admin username default `vireonet-admin`, repo default `netops/intent`, Gitea URL default `http://localhost:3000`; behind a confirm dialog; the job is polled every 3 s). Automatic needs Docker on the VireoNet host and `GITEA_ADMIN_PASSWORD` + `GITEA_DB_PASSWORD` (12+ chars each) in the VireoNet environment. The token is never shown. The setup job store is in-memory — it is lost if the API restarts.
2. **Docker Compose by hand:**
   ```bash
   cd deployments/gitea
   GITEA_DB_PASSWORD='<strong password>' docker compose -f docker-compose.gitea.yml up -d
   # Open http://localhost:3000/ and create the admin user
   ```
   `GITEA_DB_PASSWORD` is required (`${GITEA_DB_PASSWORD:?…}` fails the stack without it); `GITEA_ROOT_URL` defaults to `http://localhost:3000/`. Registration is disabled (`DISABLE_REGISTRATION: "true"`).
3. **Script:** `python scripts/setup_gitea.py --manual` prints the steps; `--auto` installs, creates the admin + repo + token and stores it in Vault at `git/token` (env: `GITEA_ADMIN_PASSWORD`, `GITEA_DB_PASSWORD`, `VIREONET_GIT_REPO`, `VAULT_ADDR`, `VAULT_TOKEN`); `--verify` checks the connection. The token is never printed. If `hvac` is unavailable or Vault unreachable, the script prints the manual command `vault kv put secret/git token=<token>` instead.

   After any install, restart the API + worker with `VIREONET_GIT_PROVIDER=gitea` and check System Health for the git adapter.

**Webhook:** Gitea repo → Settings → Webhooks, URL `https://<vireonet-host>:8000/webhooks/gitea`, verified by `verify_webhook` (HMAC/Bearer). Subscribe to **workflow runs** and **pull requests**. The ticket is parsed from the PR title (`RITM…: …`) for merges, and from the workflow run's `head_branch` for CI.

**Verify:** `GET /adapters/health` → `gitea` ok (`GET /api/v1/repos/<owner>/<repo>`); Gitea's own health is `GET http://localhost:3000/api/v1/version` (the compose healthcheck).

**UNCERTAIN:** the git-refs response shape (`{object: {sha}}`, GitHub-like) and the workflow-run `conclusion` field are not guaranteed in older Gitea; the pipeline lookup takes the **most recent run** (`GET /repos/<owner>/<repo>/actions/runs`), not the run for a specific PR. Merge uses `Do: "squash"`, which is version-sensitive.

**Common failures:** `GIT_BASE_URL is required for Gitea` → you forgot the base URL; health `down` → Gitea not up yet (wait for its healthcheck) or the token lacks repo scope; install job `failed` → read `job.data.error` on the Gitea setup page, usually Docker missing on the host or the two passwords not set.

---

## 5. Batfish

**What it does in VireoNet:** the offline analysis gate in CI. The workflow renders the candidate config, Batfish analyses it (reachability, ACL, routing, BGP, undefined references, duplicate IPs, interface/VLAN), and a failure blocks the MR from merging.

**Prerequisites:** a Batfish service reachable from the API/worker host (default `localhost:9996`), and `pybatfish` installed (`pip install pybatfish` — it is a lazy import; without it the adapter raises `AdapterUnavailable: pybatfish not installed`).

```bash
VIREONET_BATFISH_HOST=localhost
VIREONET_BATFISH_PORT=9996
VIREONET_BATFISH_SNAPSHOT_DIR=/tmp/vireonet/batfish
```

No auth is configured in the adapter — Batfish is expected to run on a trusted local network. If your Batfish needs auth, that is not implemented.

**How it is used:** `analyse(files)` writes the candidate snapshot to `VIREONET_BATFISH_SNAPSHOT_DIR` (Batfish reads from files, not memory), opens a `pybatfish` `Session(host, port)`, and runs the questions in `BATFISH_QUESTIONS` (`undefinedReferences`, `ipOwners`, `bgpSessionStatus`, `reachability`, `routes`). `analyse_differential(current, candidate)` compares the two snapshots. Health check opens a session; if it cannot connect, health is `down`.

**Capabilities:** `reachability`, `acl`, `routing`, `bgp`, `undefined_refs`, `duplicate_ips`, `interface_vlan`.

**Verify:**

```bash
curl http://localhost:8000/adapters/health -H "Authorization: Bearer $TOKEN" | python3 -m json.tool | grep -A 4 batfish
# Then open an MR: a deliberately broken config (e.g. a reference to an undefined ACL) must fail the gate
```

**Common failures:**

| Symptom | Cause | Fix |
|---|---|---|
| `pybatfish not installed` | Package missing in the worker image | `pip install pybatfish` / add it to the image |
| `cannot connect to Batfish at host:port` | Batfish service not running | Start Batfish; check host/port |
| Gate passes a config that is obviously wrong | Question/column names differ by Batfish version | See UNCERTAIN below |

**UNCERTAIN (from the code):** pybatfish question names and column names vary by Batfish version. Change only the `BATFISH_QUESTIONS` dict in `src/vireonet/adapters/batfish.py` if your version renames a question. The adapter returns raw rows (`.frame().to_dict(orient="records")`) so the gate counts/inspects them without inventing columns. Differential reachability is not implemented as a full diff — the gate is the question set above. Do not make Batfish a hard blocker until it has been run against 2–3 real configs from your network.

**Fake-mode behaviour:** `analyse()` passes unless a file contains the string `UNDEFINED`, in which case it fails with `undefined reference in <file>`.

---

## 6. Vault

**What it does in VireoNet:** the only place secrets live. All adapter tokens/passwords are read from Vault KV v2 at runtime; the Git token is read from the `VIREONET_GIT_TOKEN_SECRET` path; ZTP bootstrap tokens are issued through Vault.

**Prerequisites:** a Vault server (unsealed, initialised), an AppRole (role id + secret id) with read access to the KV mount used for VireoNet secrets.

**Auth:** AppRole login (`client.auth.approle.login(role_id, secret_id)`); the client token is cached and renewed when close to expiry (60 s margin). Secrets are never cached and never logged — only the Vault token and its expiry are held.

```bash
VIREONET_VAULT_ADDR=https://vault.example.com:8200
VIREONET_VAULT_ROLE_ID=<role id>
VIREONET_VAULT_SECRET_ID=<secret id, from Vault>
VIREONET_VAULT_KV_MOUNT=secret
VIREONET_VAULT_BOOTSTRAP_POLICY=bootstrap
```

The bootstrap policy name (`bootstrap` by default) is configurable; the code marks it as the one Vault detail that may differ per install — if your Vault uses a different policy name for the bootstrap token, set this var.

**How it is used:** `read_secret(path)` reads KV v2 (`client.secrets.kv.v2.read_secret_version(path, mount_point=kv_mount)`), `issue_bootstrap_token(device)` issues a ZTP bootstrap token, `renew_token()` renews the client token. Health check is `client.sys.read_health_status()` — `ok` only if Vault is initialised **and** unsealed; sealed or uninitialised Vault reports `down` with detail `vault sealed or not initialised`.

**Capabilities:** `kv_read`, `approle`, `token_renew`, `bootstrap_token`.

**Verify:**

```bash
curl http://localhost:8000/adapters/health -H "Authorization: Bearer $TOKEN" | python3 -m json.tool | grep -A 4 vault
vault kv get secret/git   # from a machine with Vault CLI access; token value must be present
```

**Common failures:**

| Symptom | Cause | Fix |
|---|---|---|
| `Vault role_id/secret_id not configured` | AppRole credentials missing | Set both vars |
| Health `down`, `vault sealed or not initialised` | Vault sealed / not initialised | Unseal / initialise Vault |
| `AdapterNotFound … read_secret … <path>` | Secret path does not exist in the KV mount | Create it: `vault kv put secret/git token=<token>` |
| Git adapter fails with "git token is required" | The `git/token` secret is missing or empty | Store the token in Vault at that path |

**Fake-mode behaviour:** one seeded secret, `secret/vireonet/awx` → `{"token": "fake-awx-token"}`; any other path raises `AdapterNotFound`.

---

## 7. EVE-NG

**What it does in VireoNet:** optional lab validation in CI. When `RUN_EVENG=true`, the pipeline starts a lab, loads the topology, waits for nodes, runs the change against the lab, and wipes the lab afterwards. It is **manual-only in CI** and never on the critical path by default.

**Prerequisites:** an EVE-NG server reachable from the worker, with a user that can create labs; a topology file (`.unl`) for the change being tested.

**Auth:** cookie auth — `POST /api/auth/login` with `{"username": ..., "password": ...}`; the session cookie is kept in-process and re-used.

```bash
VIREONET_EVENG_BASE_URL=https://eveng.example.com
VIREONET_EVENG_USERNAME=<user>
VIREONET_EVENG_PASSWORD=<from Vault / env>
```

**How it is used:** labs are created with `POST /api/labs` (`{"path": "/", "name": ..., "version": "1"}`), topology loaded from a `.unl` file, nodes waited on (default timeout 600 s), stopped with `POST /api/labs/<id>/nodes/stop`, wiped with `DELETE /api/labs/<id>`. Labs are always wiped in a `finally` by the caller (see `scripts/eveng_smoke.py`) — a lab that is not wiped leaks nodes on the EVE-NG host. Console details are returned as `telnet://<base>/<lab_id>/<node>`. Health check is a login — if login fails, health is `down`.

**Capabilities:** `start_lab`, `stop_lab`, `wipe_lab`, `load_topology`, `wait_nodes`, `console`.

**Verify:** run `python scripts/eveng_smoke.py` (it creates a lab, checks it, and wipes it); or `GET /adapters/health` → `eveng` ok.

**Common failures:**

| Symptom | Cause | Fix |
|---|---|---|
| Health `down`, login failure | Wrong credentials or EVE-NG down | Check the user/password and the server |
| Lab starts but nodes never come up | Topology file missing / node images missing on EVE-NG | Fix the `.unl` / install the images on EVE-NG |
| Labs pile up on the server | A run crashed before the `finally` wipe | Wipe stale labs in the EVE-NG UI; check `scripts/eveng_smoke.py` |

**UNCERTAIN:** loading a `.unl` topology has no single stable REST call in the code — the implementation is best-effort and version-sensitive. Treat EVE-NG as a manual verification step, not a hard gate, until it has been run against your EVE-NG version.

**Fake-mode behaviour:** `start_lab` returns a lab `lab-<name>` in state `running` with nodes `leaf-01`, `leaf-02`; nothing is created anywhere.

---

## 7a. Containerlab

**What it does in VireoNet:** a second lab backend alongside EVE-NG (E4), not a replacement. It deploys a containerlab topology on a lab host and runs commands on its nodes, for lab validation where containers are cheaper than EVE-NG VMs. Selected with `VIREONET_LAB_BACKEND=containerlab` (or `both` to expose both backends); see `docs/LAB.md` and `topologies/README.md`.

**Prerequisites:** a lab host with the `containerlab` CLI installed and the node images available (or pullable); SSH access to that host as `VIREONET_CONTAINERLAB_USER`, with a key (`VIREONET_CONTAINERLAB_KEY_PATH`) or password (`VIREONET_CONTAINERLAB_PASSWORD`). No containerlab daemon or API is needed on the host.

**Auth:** SSH, key or password. No secret is ever logged; the SSH command contains only containerlab arguments.

```bash
VIREONET_LAB_BACKEND=containerlab
VIREONET_CONTAINERLAB_HOST=lab.example.com
VIREONET_CONTAINERLAB_USER=lab
VIREONET_CONTAINERLAB_KEY_PATH=~/.ssh/lab_id_ed25519
```

**How it is used:** every operation is one CLI call over SSH (`src/vireonet/adapters/containerlab.py: _run_cli`): `deploy -t <topology>`, `inspect --name <lab> --format json`, `exec --name <lab> --label clab-node-name=<node> --cmd <command>`, `destroy --name <lab> --cleanup`. `save_config` runs `show running-config` via exec. `wait_ready` polls inspect until every node reports `state: running` (default timeout 600 s).

**Capabilities:** `deploy`, `inspect`, `exec`, `save_config`, `destroy`.

**Verify:** run `python scripts/lab_smoke.py --backend containerlab --topology topologies/two-leaf.clab.yml --lab two-leaf` (deploys, waits, runs `show version`, destroys in a `finally`); or `GET /system/health` → `Containerlab` ok.

**Common failures:**

| Symptom | Cause | Fix |
|---|---|---|
| `containerlab backend needs VIREONET_CONTAINERLAB_HOST and VIREONET_CONTAINERLAB_USER` | Host/user not set | Set both vars (startup refuses without them in real mode) |
| `ssh to <host> failed` | Host unreachable, bad key/password, or host key not in known_hosts | Check SSH from the API host with the same key/user |
| `containerlab … cli: <stderr>` | The CLI itself failed (bad topology, missing image, lab name clash) | Read the stderr (first 500 chars are in the error); fix the topology or pull the image on the lab host |

**UNCERTAIN:** no stable Containerlab REST API is known, so the CLI is the interface — `_run_cli` is the one place that knows about it. `inspect --format json` is parsed in both shapes seen in the wild (`{"containers": [...]}` and a top-level list). `restore_config` is deliberately unimplemented: restore is kind-specific (cisco_iol, srl, ceos, crpd all differ) and no generic restore is invented. Old IOS images on Containerlab are kind-dependent — only `cisco_iol` (vrnetlab) is known to run old IOS; see `topologies/README.md`.

**Fake-mode behaviour:** `deploy` returns the lab name with nodes `leaf-01`/`leaf-02` (`cisco_iol`, running, `10.0.1.11`/`10.0.1.12`); `exec` returns fake device output; nothing is created anywhere.

---

## 8. DHCP (ISC / Kea) and ZTP

**What it does in VireoNet:** renders DHCP reservations for zero-touch provisioning and deploys the DHCP config **Git-first** (commit, then AWX reload). It is the DHCP half of the ZTP flow: NetBox registration → DHCP reservation → day-0 bootstrap → callback → identity check → day-1 → NetBox `active`.

**Prerequisites:** a DHCP server (ISC `dhcpd` or Kea) that the AWX `dhcp-reload` job can reload; a TFTP/HTTP server reachable by new devices for the bootstrap file; NetBox for MAC/IP allocation; Git + AWX wired (see §4 and §3) for the deploy path.

```bash
VIREONET_DHCP_SERVER=isc        # isc | kea
VIREONET_DHCP_CONFIG_PATH=dhcp/dhcpd.conf
VIREONET_DHCP_TFTP_SERVER=10.0.0.5
VIREONET_DHCP_HTTP_BASE_URL=http://10.0.0.5/ztp
VIREONET_DHCP_PNP_SERVER_URL=https://pnp.example.com
VIREONET_ZTP_TOKEN_TTL_SECONDS=900
```

**How it is used:** `generate_reservation(hostname, mac, ip)` returns option 66 (TFTP server, `VIREONET_DHCP_TFTP_SERVER`) and option 67 (boot filename `<hostname>.cfg`); `generate_from_netbox` takes the MAC from the registration payload and the IP from the device's NetBox `primary_ip`, or allocates from `10.0.1.0/24` if there is none. `render_config` writes ISC `host { hardware ethernet …; fixed-address …; option tftp-server-name …; filename …; }` blocks, or a Kea JSON host list (`hostname`, `ip-address`, `boot-file-name`, `hw-address`). `validate_config` uses the `dhcpd`/`kea` binary when present, otherwise a structural check. `deploy` validates, commits the rendered config to Git (branch `dhcp/update`, message `Update DHCP reservations (VireoNet)`), then launches the AWX `dhcp-reload` job with `{"config_path": ...}` and returns the commit + job id as evidence. Option 43 (PnP) is rendered when `VIREONET_DHCP_PNP_SERVER_URL` is set.

**Capabilities:** `reservation`, `render_isc`, `render_kea`, `validate`, `deploy`, `reload`, `verify`. Health check always returns `ok` — there is no live DHCP server probe in the adapter; a bad DHCP server shows up as a failed `deploy`/`reload`, not as a `down` health status.

**Verify:**

```bash
# Register a device, then check its reservation
curl -X POST http://localhost:8000/ztp/devices -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"hostname": "leaf-01", "serial": "FOC1234A", "mac": "aa:bb:cc:dd:ee:ff", "model": "C9300-48P", "site": "dc1", "platform": "ios-xe"}'
# 202; the one-time token is returned once, in the response only
```

**Common failures:**

| Symptom | Cause | Fix |
|---|---|---|
| Device never boots / no DHCP offer | Reservation not in the deployed config, or the DHCP server was not reloaded | Check the rendered config in Git and the AWX `dhcp-reload` job |
| `NetBox adapter not configured` | `generate_from_netbox` called without NetBox | Wire NetBox first (§2) |
| `DHCP config failed validation` | Rendered config has a syntax error | Check the config text; run `dhcpd -t` / Kea validation by hand |
| Device boots but callback is 401 | One-time token wrong, expired (900 s default) or already used | Re-issue with `POST /ztp/devices/<serial>/retry` (operator role) |

**UNCERTAIN:** the DHCP option 43 PnP encoding (`encode_pnp_option43` in `src/vireonet/ztp/templates.py`) — the wire encoding is documented in the code as uncertain; `dhcpd` takes the string form, Kea the hex form. NetBox does not store a MAC on the device in all versions; the MAC comes from the registration payload. The ZTP device templates are per-platform: old Cisco IOS (DHCP option 66/67 + TFTP/HTTP), IOS-XE (PnP), Nexus (POAP).

**Fake-mode behaviour:** reservations are generated with TFTP `10.0.0.5` and boot file `bootstrap.cfg`; `generate_from_netbox` returns MAC `aa:bb:cc:dd:ee:ff`, IP `10.0.1.101`.

**Known gap (read the code, not the marketing):** `DhcpAdapter` is a real class, but `registry.py` does **not** wire it in real mode — the real-mode adapter map starts from the fakes and replaces ServiceNow, NetBox, AWX, Vault, GitLab, Batfish, EVE-NG, Notification and Monitoring only. DHCP in a real-mode deployment therefore still uses the fake unless you wire `DhcpAdapter` yourself with the NetBox/GitLab/AWX adapters. This is listed again in [What is NOT wired](#what-is-not-wired-in-real-mode-known-gaps).

---

## 9. Notification (SMTP / Teams / Slack)

**What it does in VireoNet:** tells humans what happened — change approved, applied, failed, rolled back. A notification failure never fails a change.

**Prerequisites:** one of: an SMTP server (with STARTTLS on port 587 by default), a Microsoft Teams incoming-webhook URL, or a Slack incoming-webhook URL.

```bash
VIREONET_NOTIFICATION_DEFAULT_CHANNEL=slack   # email | teams | slack
# Email:
VIREONET_SMTP_HOST=smtp.example.com
VIREONET_SMTP_PORT=587
VIREONET_SMTP_USERNAME=<user>
VIREONET_SMTP_PASSWORD=<from Vault / env>
VIREONET_NOTIFICATION_EMAIL_FROM=vireonet@example.com
VIREONET_NOTIFICATION_EMAIL_TO=netops@example.com
# Chat:
VIREONET_TEAMS_WEBHOOK_URL=https://outlook.office.com/webhook/...
VIREONET_SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
VIREONET_NOTIFICATION_WEBHOOK_URL=   # legacy alias; prefer the per-channel vars above
```

**How it is used:** `send(channel, title, body, ticket)`. Email sends via `smtplib` with STARTTLS (login only if a username is set), subject `[VireoNet <ticket>] <title>`. Teams posts a MessageCard (`@type: MessageCard`, `summary`, `title`, `text` with the ticket appended); Slack posts `{"text": "*<title>*\n<body>\nTicket: <ticket>"}`. **A channel with no URL / no SMTP host configured returns `False` instead of raising** — a missing webhook must not fail a change. Health is `ok` if any channel is configured, otherwise `down` with detail `no notification channel configured`. Unknown channels raise `AdapterValidationError`.

**Capabilities:** `email`, `teams`, `slack`.

**Verify:** trigger a change (or a test notification) and check the inbox / channel; `GET /adapters/health` → `notification` `ok` means at least one channel is configured, not that a message was delivered.

**Common failures:**

| Symptom | Cause | Fix |
|---|---|---|
| No message, no error | Channel not configured — `send()` returned `False` | Set the webhook URL / SMTP host |
| Health `down`, `no notification channel configured` | Nothing configured | Set at least one channel |
| Email fails with an SMTP error | Wrong host/port, or STARTTLS required and not offered | Check the SMTP server; port 587 + STARTTLS is the default |
| Webhook returns 4xx | Webhook URL revoked / wrong workspace | Re-create the incoming webhook and update the URL |

**Fake-mode behaviour:** the fake records the send and returns `True`; nothing is sent anywhere.

---

## 10. Monitoring (none / Alertmanager / LibreNMS)

**What it does in VireoNet:** silences alerts for a device while a change is applying, and re-enables monitoring afterwards. It is behind a flag and defaults to off.

```bash
VIREONET_MONITORING_BACKEND=none   # none | alertmanager | librenms
VIREONET_MONITORING_BASE_URL=https://alertmanager.example.com
VIREONET_MONITORING_TOKEN=<from Vault / env>
VIREONET_MONITORING_SILENCE_MINUTES=60
```

**How it is used:** with `backend=none`, every method is a no-op returning the sentinel `"no-op-monitoring-disabled"`, so the workflow does not branch on the backend, and health is `ok` with detail `monitoring disabled (backend=none)` and no capabilities. With `alertmanager`, `silence(device, minutes)` creates a silence via `POST /api/v2/silences` (matcher `instance = <device>`, `createdBy: vireonet`, comment `Change window for <device>`); health is `GET /api/v2/status`. With `librenms`, health is `GET /api/v0/system` and the token is sent as `X-Auth-Token`.

**Capabilities:** `silence`, `enable`, `disable` — or `[]` when `backend=none`.

**Verify:**

```bash
curl http://localhost:8000/adapters/health -H "Authorization: Bearer $TOKEN" | python3 -m json.tool | grep -A 4 monitoring
# Alertmanager: check /api/v2/silences during a change window; silences expire after VIREONET_MONITORING_SILENCE_MINUTES
```

**Common failures:**

| Symptom | Cause | Fix |
|---|---|---|
| Alerts fire during a change | `backend=none` — silencing is off | Set `VIREONET_MONITORING_BACKEND=alertmanager` and the base URL |
| Health `down` after enabling | Base URL wrong or token wrong (401) | Check the URL and token |
| Silence left over after a crashed worker | The worker died before removing it | Check Alertmanager `/api/v2/silences`; silences expire after the configured minutes |

**UNCERTAIN:** the LibreNMS device enable/disable path (`/api/v0/devices/:hostname`) — the exact field varies by LibreNMS version, and the code marks it UNCERTAIN. Alertmanager silences are the verified path; LibreNMS is best-effort until run against your LibreNMS version.

**Fake-mode behaviour:** backend `none`, all methods no-op.

---

## Webhooks into VireoNet

All inbound webhooks are `POST` to the API (`http://<host>:8000/webhooks/...`). Two verification schemes exist, and they are **not** the same — check which one your route uses:

| Route | Verification | Header / secret |
|---|---|---|
| `POST /changes` (ServiceNow entry point) | `verify_webhook` | HMAC SHA256 over the raw body, header `X-VireoNet-Signature` (optionally `sha256=`-prefixed), secret = `VIREONET_WEBHOOK_SECRET`; or `Authorization: Bearer <VIREONET_WEBHOOK_SECRET or VIREONET_API_TOKEN>` |
| `POST /webhooks/gitlab` | `X-Gitlab-Token` | Header must equal `VIREONET_GITLAB_WEBHOOK_TOKEN`. If no token is configured, unsigned webhooks are accepted (dev only) |
| `POST /webhooks/github` | `verify_webhook` | As `/changes` above |
| `POST /webhooks/bitbucket` | `verify_webhook` | As `/changes` above |
| `POST /webhooks/gitea` | `verify_webhook` | As `/changes` above |

If neither `VIREONET_WEBHOOK_SECRET` nor `VIREONET_API_TOKEN` is set, `verify_webhook` accepts everything — dev only, never production. A failed verification returns 401 and the workflow is never signalled.

What each webhook does: GitLab pipeline events signal CI pass/fail (`signal_ci`) and merged MRs signal merge (`signal_merge`); GitHub check runs/suites and merged PRs do the same; Bitbucket commit-status and fulfilled PRs do the same (ticket from the commit message); Gitea workflow runs and merged PRs do the same (ticket from the PR title or `head_branch`). The ticket format is `RITM…`, `CHG…` or `ZTP-…`, parsed from the MR/PR title prefix (`RITM0001234: Add VLAN 100`) or the branch name (`change/RITM0001234`). A payload with no ticket is answered `{"status": "ignored", ...}` and nothing happens — check the title/branch spelling first when a webhook seems to do nothing.

---

## What is NOT wired in real mode (known gaps)

These are in the code, not oversights in this guide. Do not assume they work until they are fixed and re-tested:

1. **DHCP has no real-mode wiring** — `registry.py` never replaces the `dhcp` adapter in real mode (see §8). The real `DhcpAdapter` class exists, but nothing constructs it with the real NetBox/GitLab/AWX adapters.
2. **Git provider token source** — `get_git_provider()` takes a token as a parameter; the code that calls it in `main.py` passes `settings.gitlab_token or ""` as that token for *every* provider, and the Vault read of `VIREONET_GIT_TOKEN_SECRET` is noted in the code as a follow-up. If you use GitHub/Bitbucket/Gitea, confirm which token actually reaches the provider in your deployment.
3. **Bitbucket `commit_file`** — posts JSON where Bitbucket needs multipart (see §4c). Unverified.
4. **Gitea pipeline = most recent run**, not the run for a specific PR (see §4d). `history()` in the config store returns `[]` — there is no list-commits call in the `GitProvider` protocol yet.
5. **Batfish differential reachability** is not implemented as a full diff; the gate is the question set in §5. EVE-NG `.unl` loading has no single stable REST call.
6. **LibreNMS** enable/disable field varies by version (see §10).
7. **ServiceNow field names / state values** are instance-specific (see §1). NetBox `create_device` needs numeric `device_type_id`/`role_id` (see §2).
8. **`recorded` adapter mode** is accepted by config validation but raises at startup — do not use it.
9. **Notification health** only proves a channel is configured, not that a message was delivered. **DHCP health** always returns `ok` — there is no live DHCP probe.
10. **Gitea setup job store is in-memory** — a restart loses the job; the install itself (containers, repo, Vault token) survives, but the status page will not show it.
