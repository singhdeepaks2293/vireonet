# VireoNet

> Formerly NetOrch — renamed to VireoNet. The old `NETORCH_` environment-variable prefix is gone; use `VIREONET_` instead.

**VireoNet is a thin, network-aware change orchestrator with a web UI.** It takes a change request from ServiceNow, validates it against NetBox (the source of truth) and a YAML policy, renders the config from Git templates, gates it through Batfish in CI, gets a human approval, and applies it with AWX + Ansible — with a pre-check and a post-check at every stage, dry-run by default, and automatic rollback on failure. It also does zero-touch provisioning (ZTP) of new devices.

**VireoNet never touches a network device directly.** AWX + Ansible is the only component that does. VireoNet never SSHes to a device.

The full operator manual — every screen and every button, with screenshots — is [`docs/USER_MANUAL.md`](docs/USER_MANUAL.md). Per-adapter integration detail is [`docs/ADAPTERS.md`](docs/ADAPTERS.md). The SDLC record is [`docs/SDLC.md`](docs/SDLC.md); the runbook is [`docs/runbook.md`](docs/runbook.md).

## Contents

- [What it is](#what-it-is)
- [Architecture](#architecture)
- [State machine](#state-machine)
- [Key principles](#key-principles)
- [Prerequisites](#prerequisites)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [First login](#first-login)
- [Adapter integration](#adapter-integration)
- [Git providers](#git-providers)
- [Using it](#using-it)
- [Using the UI — every screen and button](#using-the-ui--every-screen-and-button)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Project layout](#project-layout)
- [Known limitations and debt](#known-limitations-and-debt)

## What it is

VireoNet automates two flows:

1. **Targeted change:** ServiceNow request → validation vs NetBox + policy → GitLab/GitHub/Bitbucket/Gitea MR → CI (lint, render, Batfish, optional EVE-NG) → approval + merge → AWX workflow (lock, backup, baseline, dry-run diff, apply in waves, post-check, auto-rollback on failure) → update NetBox + Git → ServiceNow closed.
2. **ZTP (zero-touch provisioning):** NetBox registration → DHCP reservation → day-0 bootstrap → device callback → identity check (serial/model/version vs NetBox) → day-1 rendered from NetBox + templates → Batfish gate → AWX push → post-check → NetBox `active` → ServiceNow closed.

Who it is for: network operations engineers who already run ServiceNow, NetBox, AWX and Git, and want changes to be reviewed, tested, approved and reversible instead of typed into a device by hand.

What it is **not**: a network device manager, a replacement for ServiceNow/NetBox/AWX/Git, or a real AWS/cloud platform. It orchestrates the systems you already have; it does not replace them.

## Architecture

```
ServiceNow ──webhook──> VireoNet API ──start──> Temporal (Workflow ID = ticket)
  RITM/CHG                │  validate + policy      │
                          │  (NetBox + YAML)        ▼
NetBox ◄──── adapters ────┤                    ChangeWorkflow
Git   ◄──── (httpx,       │                      ├─ validate → plan
  retry, breaker,         │                      ├─ wait CI signal
  correlation ID)         │                      ├─ wait approval + merge
AWX ◄─────────────────────┤                      ├─ lock → backup → pre-check
Batfish ◄─────────────────┤                      ├─ dry-run → apply in waves
Vault ◄───────────────────┤                      ├─ post-check → rollback on fail
EVE-NG ◄──────────────────┘                      └─ close (NetBox active, SN closed)
DHCP / ZTP ──────────────> ZtpWorkflow (separate workflow, same adapters)
```

Components:

| Component | What it is | Port (Docker) |
|---|---|---|
| `api` | FastAPI, stateless; all state in Postgres + Temporal | 8000 |
| `worker` | Temporal worker running `ChangeWorkflow` and `ZtpWorkflow` | — (internal) |
| `frontend` | React 18 + TypeScript + Vite, served by nginx | 5173 |
| `postgres` | PostgreSQL 16; changes, audit log, locks, users, ZTP devices | 5433 on the host, 5432 in the container |
| `temporal` | Temporal server (`temporalio/auto-setup:1.29.7`) | 7233 |
| `temporal-ui` | Temporal Web UI (`temporalio/ui:2.34.0`) | 8233 |

All state lives in PostgreSQL and Temporal. The API is stateless and can be restarted without losing a change — the workflow resumes from Temporal. Every adapter call carries a correlation ID header (`X-Correlation-ID`, the ticket number) and has a timeout; secrets are never logged and never stored in code, Git or logs (Vault only).

## State machine

Enforced in `src/vireonet/state_machine.py`; every transition is an append-only audit row with timestamp, actor, reason and evidence. Illegal transitions are rejected (409).

```
received → validated → planned → ci_passed → approved → pre_checked → applied → post_checked → closed
```

Failure exits: `rejected`, `ci_failed`, `pre_check_failed`, `rolled_back`, `failed`.

The ZTP flow has its own stages on the `ZtpDevice` record: `registered → reservation_created → callback_received → identity_verified → day1_applied → active`, with failure stages `failed`, `identity_failed`, `callback_timeout`, `day1_failed`.

## Key principles

- **AWX-only device access.** VireoNet never SSHes to a device. Only AWX runs Ansible against devices.
- **Dry-run is the default for every apply step.** A real apply requires an explicit approved state; the workflow runs `dry-run` first and only applies after it.
- **Temporal Workflow ID = the ServiceNow ticket number** (`ZTP-<serial>` for ZTP). Starting a workflow is idempotent — a duplicate webhook re-delivery is harmless.
- **Fake / Real adapters.** Every adapter has a deterministic in-memory Fake (used by tests and local dev) and a Real implementation behind the same interface. `VIREONET_ADAPTER_MODE=fake|real` switches the whole set.
- **NetBox is the source of truth.** Validation, rendering and ZTP identity all read NetBox. Git holds intent and templates, not device state.
- **JWT IAM.** Users have fixed roles (`viewer`, `operator`, `approver`, `admin`) with a permission matrix; the API checks the signed JWT and the database role on every request. The old `X-NetOrch-Role` header is no longer trusted.
- **Approvals and CI-merge arrive as Temporal signals** (`approve`, `reject`, `ci_passed`, `ci_failed`, `merge_completed`), not by polling.
- **Config version control keeps three stores separate** (see [Using it](#using-it)): Jinja templates in `netops/templates`, YAML vars in `netops/vars`, one repo per device in `netops/config-<hostname>`. NetBox Jinja is exported read-only to `netops/netbox-export` and is never a render source.

## Prerequisites

- **Docker + Docker Compose** (v2) on Linux, for the full stack. The stack is what most users should run.
- **Python 3.12** and **Node 20** if you run without Docker (API/worker and frontend separately).
- **Ports** on the host: 5173 (frontend), 8000 (API), 7233 (Temporal), 8233 (Temporal UI), 5433 (Postgres — the host port is 5433 because 5432 is commonly already in use; inside Docker Postgres still listens on 5432). Gitea, if you install it, uses 3000 and 2222. Do not reuse ports already taken by another service on the same host (for example CloudForge lite uses 8080/8081).
- **RAM/disk:** the full stack is light — Postgres + Temporal + API + worker + frontend run comfortably on a laptop with 4 GB free. No KVM, no GPU, no cloud account is needed.
- **For real mode** (not fake): a reachable ServiceNow, NetBox, AWX and Git instance, plus Vault — see [`docs/ADAPTERS.md`](docs/ADAPTERS.md). In fake mode none of these are needed.

## Quick start

### With Docker Compose (recommended)

```bash
git clone <your-repo> vireonet
cd vireonet
cp .env.example .env        # edit it: set VIREONET_JWT_SECRET and, for LAN access, VIREONET_CORS_ORIGINS
docker compose up --build -d
curl http://localhost:8000/healthz
# {"status":"ok"}
```

Then seed the first admin and open the UI (see [First login](#first-login)):

```bash
docker compose exec -e VIREONET_ADMIN_USER=admin -e VIREONET_ADMIN_PASSWORD='<12+ characters>' api python scripts/seed_admin.py
# Admin user admin created.
```

Open `http://localhost:5173` and sign in. Temporal UI is at `http://localhost:8233`.

**From another machine on the LAN** (for example your Windows laptop browsing a Fedora host at `10.228.197.24`): the frontend is built with the API address baked in, so rebuild it with the LAN address, and make sure the LAN origin is in `VIREONET_CORS_ORIGINS` (see [Configuration](#configuration)):

```bash
VITE_API_URL=http://10.228.197.24:8000 docker compose up --build -d
# then open http://10.228.197.24:5173 — NOT http://localhost:5173 from the other machine
```

### Without Docker

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
# Start Postgres 16 and a Temporal server (see docs/runbook.md), then:
alembic upgrade head
uvicorn vireonet.main:app --port 8000
python -m vireonet.worker
cd frontend && npm install && npm run dev   # UI at http://localhost:5173
```

The frontend dev server proxies nothing by itself — it calls the API at `VITE_API_URL` (default `http://localhost:8000`). For a production-style preview: `npm run build && npm run preview -- --host 0.0.0.0 --port 4173`.

### Stopping / updating

```bash
docker compose down            # stop; data in the postgres-data volume survives
docker compose down -v         # stop and delete the database volume (all data gone)
docker compose up --build -d   # after pulling new code / changing VITE_API_URL
```

## Configuration

All config is environment variables with the `VIREONET_` prefix (see `src/vireonet/config.py`; the file is the authority — if this table and the code disagree, the code wins). Settings are read from the environment and from `.env` (`extra="ignore"` — unknown vars are ignored). **The API container only receives the variables that `docker-compose.yml` passes explicitly** — setting a variable in `.env` alone does not reach the container unless the compose file forwards it. If you add a new variable, add it to the `api` (and `worker`) service in `docker-compose.yml` and restart.

Copy `.env.example` to `.env` and edit it. Never commit `.env`.

### Full environment variable table

| Variable | Default | What it does |
|---|---|---|
| `VIREONET_DATABASE_URL` | `postgresql+asyncpg://vireonet:vireonet@localhost:5432/vireonet` | Postgres connection string (plain string; tests use sqlite in-memory URLs) |
| `VIREONET_TEMPORAL_ADDRESS` | `localhost:7233` | Temporal server address |
| `VIREONET_TEMPORAL_NAMESPACE` | `default` | Temporal namespace |
| `VIREONET_TEMPORAL_TASK_QUEUE` | `vireonet-changes` | Temporal task queue |
| `VIREONET_ADAPTER_MODE` | `fake` | `fake` \| `real` \| `recorded` (`recorded` raises at startup — do not use) |
| `VIREONET_LOG_LEVEL` | `INFO` | Log level |
| `VIREONET_API_TOKEN` | *(empty)* | Bearer token accepted for inbound webhooks (with `VIREONET_WEBHOOK_SECRET`) |
| `VIREONET_WEBHOOK_SECRET` | *(empty)* | HMAC secret for inbound webhooks. If unset, webhook verification is skipped — dev only |
| `VIREONET_POLICY_FILE` | `policies/default.yaml` | Policy file (device/VLAN limits, change window) |
| `VIREONET_LOCK_TTL_SECONDS` | `1800` | Device lock TTL (30 min) |
| `VIREONET_WAVE_SIZE` | `2` | Devices applied per wave |
| `VIREONET_ABORT_THRESHOLD` | `1` | Failed devices that abort the wave |
| `VIREONET_SERVICENOW_BASE_URL` | *(empty)* | ServiceNow instance URL |
| `VIREONET_SERVICENOW_AUTH_MODE` | `oauth` | `oauth` \| `basic` |
| `VIREONET_SERVICENOW_CLIENT_ID` | *(empty)* | OAuth client id |
| `VIREONET_SERVICENOW_CLIENT_SECRET` | *(empty)* | OAuth client secret |
| `VIREONET_SERVICENOW_USERNAME` | *(empty)* | Basic-auth username |
| `VIREONET_SERVICENOW_PASSWORD` | *(empty)* | Basic-auth password |
| `VIREONET_NETBOX_BASE_URL` | *(empty)* | NetBox URL |
| `VIREONET_NETBOX_TOKEN` | *(empty)* | NetBox API token |
| `VIREONET_AWX_BASE_URL` | *(empty)* | AWX / Ansible Controller URL |
| `VIREONET_AWX_TOKEN` | *(empty)* | AWX Bearer token |
| `VIREONET_GITLAB_BASE_URL` | *(empty)* | GitLab URL (legacy M5 adapter) |
| `VIREONET_GITLAB_TOKEN` | *(empty)* | GitLab token (legacy adapter; also used as the token passed to `get_git_provider()`) |
| `VIREONET_GITLAB_PROJECT_ID` | *(empty)* | GitLab project id |
| `VIREONET_GITLAB_WEBHOOK_TOKEN` | *(empty)* | Shared secret for the GitLab webhook (`X-Gitlab-Token`) |
| `VIREONET_BATFISH_HOST` | `localhost` | Batfish host |
| `VIREONET_BATFISH_PORT` | `9996` | Batfish port |
| `VIREONET_BATFISH_SNAPSHOT_DIR` | `/tmp/vireonet/batfish` | Where candidate snapshots are written for Batfish |
| `VIREONET_VAULT_ADDR` | *(empty)* | Vault address |
| `VIREONET_VAULT_ROLE_ID` | *(empty)* | Vault AppRole role id |
| `VIREONET_VAULT_SECRET_ID` | *(empty)* | Vault AppRole secret id |
| `VIREONET_VAULT_KV_MOUNT` | `secret` | Vault KV v2 mount |
| `VIREONET_VAULT_BOOTSTRAP_POLICY` | `bootstrap` | Vault bootstrap policy name |
| `VIREONET_EVENG_BASE_URL` | *(empty)* | EVE-NG URL |
| `VIREONET_EVENG_USERNAME` | *(empty)* | EVE-NG username |
| `VIREONET_EVENG_PASSWORD` | *(empty)* | EVE-NG password |
| `VIREONET_DHCP_SERVER` | `isc` | `isc` \| `kea` |
| `VIREONET_DHCP_CONFIG_PATH` | `dhcp/dhcpd.conf` | DHCP config path |
| `VIREONET_DHCP_TFTP_SERVER` | `10.0.0.5` | TFTP server (DHCP option 66) |
| `VIREONET_DHCP_HTTP_BASE_URL` | `http://10.0.0.5/ztp` | HTTP base for ZTP bootstrap files |
| `VIREONET_DHCP_PNP_SERVER_URL` | `https://pnp.example.com` | PnP server URL (option 43) |
| `VIREONET_ZTP_TOKEN_TTL_SECONDS` | `900` | ZTP one-time token TTL (15 min) |
| `VIREONET_NOTIFICATION_EMAIL_TO` | *(empty)* | Notification recipient |
| `VIREONET_NOTIFICATION_EMAIL_FROM` | `vireonet@example.com` | Notification sender |
| `VIREONET_SMTP_HOST` | *(empty)* | SMTP host |
| `VIREONET_SMTP_PORT` | `587` | SMTP port (STARTTLS) |
| `VIREONET_SMTP_USERNAME` | *(empty)* | SMTP username |
| `VIREONET_SMTP_PASSWORD` | *(empty)* | SMTP password |
| `VIREONET_TEAMS_WEBHOOK_URL` | *(empty)* | Teams incoming-webhook URL |
| `VIREONET_SLACK_WEBHOOK_URL` | *(empty)* | Slack incoming-webhook URL |
| `VIREONET_NOTIFICATION_DEFAULT_CHANNEL` | `slack` | `email` \| `teams` \| `slack` |
| `VIREONET_MONITORING_BACKEND` | `none` | `none` \| `alertmanager` \| `librenms` |
| `VIREONET_MONITORING_BASE_URL` | *(empty)* | Monitoring base URL |
| `VIREONET_MONITORING_TOKEN` | *(empty)* | Monitoring token |
| `VIREONET_MONITORING_SILENCE_MINUTES` | `60` | Alert silence length during a change |
| `VIREONET_NOTIFICATION_WEBHOOK_URL` | *(empty)* | Legacy generic webhook URL |
| `VIREONET_ADAPTER_TIMEOUT_SECONDS` | `10.0` | Per-adapter HTTP timeout |
| `VIREONET_ADAPTER_MAX_RETRIES` | `3` | Retries (idempotent calls only) |
| `VIREONET_ADAPTER_FAILURE_THRESHOLD` | `5` | Failures before the circuit breaker opens |
| `VIREONET_ADAPTER_RESET_TIMEOUT_SECONDS` | `30.0` | Circuit breaker reset time |
| `VIREONET_GIT_PROVIDER` | `gitlab` | `gitlab` \| `github` \| `bitbucket` \| `gitea` |
| `VIREONET_GIT_BASE_URL` | *(empty)* | Git provider API base URL (required for Gitea) |
| `VIREONET_GIT_REPO` | *(empty)* | Git repo, `owner/repo` |
| `VIREONET_GIT_TOKEN_SECRET` | `git/token` | **Vault KV path** for the Git token — never the token itself |
| `VIREONET_GIT_DEFAULT_BRANCH` | `main` | Default branch |
| `VIREONET_JWT_SECRET` | `dev-only-change-me` | **Must be changed in production** (see warning below) |
| `VIREONET_JWT_ALGORITHM` | `HS256` | JWT algorithm |
| `VIREONET_ACCESS_TOKEN_EXPIRE_MINUTES` | `480` | JWT lifetime (8 hours) |
| `VIREONET_ADMIN_USER` | *(none)* | First-admin username for `scripts/seed_admin.py` |
| `VIREONET_ADMIN_PASSWORD` | *(none)* | First-admin password for `scripts/seed_admin.py` (12+ chars) |
| `VIREONET_CORS_ORIGINS` | `http://localhost:5173,http://localhost:4173,http://localhost:8000,http://127.0.0.1:5173` | Comma-separated browser origins allowed to call the API |

> **JWT secret warning.** `VIREONET_JWT_SECRET` defaults to `dev-only-change-me` and the API logs a startup warning if the default is still in use. Set a long random secret from the environment or Vault before any real deployment — anyone with the default secret can forge an admin token.

### CORS origins

The UI (port 5173) and the API (port 8000) are different browser origins, so the API must list the UI origin in `VIREONET_CORS_ORIGINS` or the browser blocks every fetch and login always fails in the UI even though `curl` works. The compose file already passes the variable into the `api` container with a default that includes `http://localhost:5173`, `http://127.0.0.1:5173` and `http://10.228.197.24:5173`. If you browse from another host, add that host's origin (for example `http://10.228.197.24:5173`) to `VIREONET_CORS_ORIGINS` in `.env` and restart the API. `localhost`, `127.0.0.1` and a LAN IP are three different origins — the one in your browser's address bar must be in the list.

### VITE_API_URL (LAN access)

The frontend is built with the API address baked in (`ARG VITE_API_URL`, default `http://localhost:8000`). If you browse the UI from another machine, `localhost:8000` in the browser points at *that* machine, not the Docker host — you will see `ERR_CONNECTION_REFUSED` on login. Rebuild with the host's LAN address: `VITE_API_URL=http://10.228.197.24:8000 docker compose up --build -d`, and open `http://10.228.197.24:5173`.

## First login

There is no built-in username/password. The first admin is created by the seed script, which only runs when the users table is empty:

```bash
docker compose exec -e VIREONET_ADMIN_USER=admin -e VIREONET_ADMIN_PASSWORD='<12+ characters>' api python scripts/seed_admin.py
# Admin user admin created.
```

The `-e` flags matter: environment variables placed *before* `docker compose` set them for the compose process on the host, not inside the container. After the first admin exists, create further users in the UI (IAM → Create user). Roles:

| Role | Can do |
|---|---|
| `viewer` | Read everything: changes, ZTP, approvals, locks, health, adapters |
| `operator` | Everything a viewer can, plus submit changes, abort, roll back, retry/reset ZTP, release locks |
| `approver` | Everything an operator can, plus approve/reject changes |
| `admin` | Everything, plus manage users and roles, and install Gitea |

Login URL: `http://localhost:5173` (or `http://10.228.197.24:5173` from the LAN). Sign in with the seeded admin. The header shows `Role: <role>` and the page title; sign out is in the header.

## Adapter integration

Summary — the full per-adapter guide (what it does, prerequisites, auth, exact env vars, webhook setup, verification, common failures, fake-mode behaviour) is [`docs/ADAPTERS.md`](docs/ADAPTERS.md). Read it before switching any adapter to real mode.

| Adapter | Required for real mode? | Switch |
|---|---|---|
| ServiceNow | Yes | `VIREONET_ADAPTER_MODE=real` + `VIREONET_SERVICENOW_*` |
| NetBox | Yes | `VIREONET_NETBOX_BASE_URL` + `VIREONET_NETBOX_TOKEN` |
| AWX | Yes | `VIREONET_AWX_BASE_URL` + `VIREONET_AWX_TOKEN` |
| Git (GitLab/GitHub/Bitbucket/Gitea) | Yes | `VIREONET_GIT_PROVIDER` + `VIREONET_GIT_REPO` + Vault token at `VIREONET_GIT_TOKEN_SECRET` |
| Batfish | Yes for the CI gate | `VIREONET_BATFISH_HOST` / `VIREONET_BATFISH_PORT` |
| Vault | Yes in real mode | `VIREONET_VAULT_ADDR` + AppRole |
| EVE-NG | No — CI only, manual | `VIREONET_EVENG_*` |
| DHCP | ZTP only | `VIREONET_DHCP_*` |
| Notification | No | SMTP / Teams / Slack webhook |
| Monitoring | No (default `none`) | `VIREONET_MONITORING_BACKEND=alertmanager` |

Switching to real mode is a single setting plus credentials:

```bash
# in .env, then restart the API and worker
VIREONET_ADAPTER_MODE=real
```

In fake mode the UI shows deterministic seed data (ServiceNow `RITM0001234` "Add VLAN 100" / `alice`; NetBox `leaf-01`/`leaf-02`; VLAN 100 "web"). That is demo data, not your network — in real mode the same screens are empty until a real request creates a change, and a health status of `down` means the real system really is unreachable. Check health at `GET /adapters/health` or on the **System health** screen (`/health`).

## Git providers

VireoNet does not hard-code GitLab. Choose with `VIREONET_GIT_PROVIDER`:

| Provider | `VIREONET_GIT_PROVIDER` | Notes |
|---|---|---|
| GitLab | `gitlab` (default) | Legacy M5 adapter + M10 provider; CI in `.gitlab-ci.yml`; webhook `POST /webhooks/gitlab` |
| GitHub | `github` | CI in `.github/workflows/ci.yml`; pipeline read from check-runs (UNCERTAIN — external CI reports via commit statuses, not check-runs); webhook `POST /webhooks/github` |
| Bitbucket Cloud | `bitbucket` | Repo is `workspace/repo`; ticket parsed from the commit message; `commit_file` is unverified (needs multipart, code posts JSON); webhook `POST /webhooks/bitbucket` |
| Gitea | `gitea` | Self-hosted only; `VIREONET_GIT_BASE_URL` required; CI in `.gitea/workflows/ci.yml`; webhook `POST /webhooks/gitea` |

Common to all: `VIREONET_GIT_REPO=<owner>/<repo>`, `VIREONET_GIT_TOKEN_SECRET=git/token` (a Vault path, never the token), `VIREONET_GIT_DEFAULT_BRANCH=main`.

**Gitea install options** (all end at the same state — Gitea running, admin user, repo, token in Vault):

1. **UI (admin only):** Adapters → Gitea setup (`/setup/gitea`) — *Manual install* shows the 10 steps; *Automatic install* installs Gitea with Docker Compose, creates the admin + repo + token, and stores the token in Vault. Needs Docker on the VireoNet host and `GITEA_ADMIN_PASSWORD` + `GITEA_DB_PASSWORD` (12+ chars) in the environment.
2. **Docker Compose by hand:** `cd deployments/gitea && GITEA_DB_PASSWORD='<strong password>' docker compose -f docker-compose.gitea.yml up -d` (Gitea `1.23` + Postgres 16, ports 3000/2222), then create the admin at `http://localhost:3000/`.
3. **Script:** `python scripts/setup_gitea.py --manual` (prints the steps), `--auto` (installs + creates admin/repo/token/Vault entry + writes `.env.gitea`), `--verify` (checks the connection). The token is never printed.

After any Gitea install, restart the API + worker with `VIREONET_GIT_PROVIDER=gitea` and check System Health for the git adapter. Full detail, including the webhook setup for each provider, is in [`docs/ADAPTERS.md`](docs/ADAPTERS.md) (§4).

## Using it

### Submit a change end to end

1. A ServiceNow request (`RITM…`) is created, or `POST /changes` is called with `{"ticket_number": "RITM0001234"}` (or a rendered `{"intent": {...}}`). The API returns 202 and the change starts in state `received`.
2. VireoNet validates the intent against NetBox + `policies/default.yaml` (max 10 devices per change; protected devices `core-01`, `core-02`, `fw-01`; protected VLANs `1`, `1002`–`1005`; no deletes on uplink interfaces `GigabitEthernet0/0`, `Ethernet1/1`, `mgmt0`; change window Mon–Fri 01:00–05:00 UTC, emergency bypasses the window only). Invalid intent → `rejected`.
3. The workflow renders the config from Git templates, opens a branch `change/<ticket>` and an MR titled `<ticket>: <description>`, and CI runs (lint, render, Batfish — must pass; EVE-NG manual only if `RUN_EVENG=true`). CI failure → `ci_failed`.
4. An approver approves (Approvals queue or Change detail → **Approve**). The workflow then locks the devices, takes a backup, runs pre-checks, dry-runs, and applies in waves of `VIREONET_WAVE_SIZE` (default 2) devices.
5. Post-checks run. Any failure → automatic rollback via the AWX `rollback` playbook and state `rolled_back`. Success → NetBox status `active`, ServiceNow closed, state `closed`.

### Approve / reject

In the UI: **Approvals** (`/approvals`) lists every change in `ci_passed`, oldest first, with an Evidence panel (checks, Batfish gate, diff counts) and **Approve** / **Reject** buttons (approver role only, behind a confirm dialog; an optional reason is recorded in the audit trail). The same actions exist on the Change detail page when the state is `ci_passed`. Rejecting moves the change to `rejected`.

### Roll back

On the Change detail page, when the state is `applied` or `post_checked`, **Roll back** (operator role, confirm dialog) restores the pre-change backup via the AWX `rollback` playbook and moves the change to `rolled_back`. Rollback is also automatic when a post-check fails. There is no "undo" button for a change that is already `closed`.

### ZTP flow

1. Register the device: `POST /ztp/devices` with hostname, serial, MAC, model, site, platform. The response contains a one-time token — it is shown **once** and never stored, so copy it immediately. (The UI's Retry action issues a new token but does not display it.)
2. The DHCP adapter renders a reservation (option 66 = TFTP server, option 67 = `<hostname>.cfg`; option 43 PnP when configured) and deploys it Git-first (commit to Git, then the AWX `dhcp-reload` job).
3. The device boots, fetches its day-0 bootstrap, and calls back (`POST /ztp/callback`). VireoNet checks serial (exact), model (case-insensitive) and platform (exact) against NetBox.
4. Day-1 is rendered from NetBox + templates, gated by Batfish, pushed by AWX, post-checked, and the device becomes `active` in NetBox.
5. In the UI (**ZTP devices**, `/ztp`), **Retry** (operator) re-issues a token and restarts the workflow; **Reset** (operator) returns the device to `registered` and clears its token/IP/version/callback. Reset is refused (409) for an `active` device.

### Config version control

Three stores, never mixed — enforced by `config_store/layout.py` and `policies/config_store.yaml`:

| Store | Repo | Contents | Rules |
|---|---|---|---|
| Templates | `netops/templates` | Jinja2 only | No device values, IPs or secrets |
| Vars | `netops/vars` | YAML only, by site → role → device-type | No Jinja delimiters |
| Per-device | `netops/config-<hostname>` | `rendered/` + `candidate/` + `backup/` + `vars.yaml` snapshot + `metadata.yaml` | One repo per device; MR branch `change/<ticket>` |
| NetBox export | `netops/netbox-export` | NetBox Jinja, read-only | Never a render source |

Browse them in the UI at **Config repos** (`/config`). The backend also exposes `POST /config/repos/<hostname>/render` (operator) and `GET /config/repos/<hostname>/history` — the history endpoint currently returns `[]` (no list-commits call exists in the `GitProvider` protocol yet) and the UI does not expose render/history buttons. Device repos must be pre-created; they are not auto-created.

### Drift

VireoNet scans devices for config drift — running config vs the golden baseline (unmanaged), vs today's rendered intent (intent), NetBox state vs collected facts (state), and compliance rules. See [`docs/DRIFT.md`](docs/DRIFT.md).

- **Scan:** nightly per site (`VIREONET_DRIFT_SITES`, `VIREONET_DRIFT_CRON`, default `0 2 * * *`), on demand via `POST /drift/scan`, or triggered by a device/syslog event via `POST /drift/events` (HMAC/token, fail-closed without `VIREONET_DRIFT_EVENT_SECRET`).
- **Resolve** (approver/admin; the attributed actor cannot decide their own drift): **Roll back** restores the baseline through a normal change and resolves only when a re-collect confirms it; **Merge** opens a Git MR with a per-device vars override (never a shared template) and resolves when the MR merges; **Grant exception** ignores that exact diff hash until an expiry (max `VIREONET_DRIFT_EXCEPTION_MAX_DAYS`, default 30); **Emergency accept** (operator) carries a retrospective deadline (default 24h) after which severity rises to critical.
- **UI:** **Drift** (`/drift`, filterable table + CSV export + Start scan), **Drift detail** (`/drift/:id`, status timeline and tabs for both diffs, state, compliance, Batfish impact, attribution, decisions/audit), **Exceptions** (`/exceptions`, expiry countdowns), and a scan summary on **System health** (`/health`).
- **Honest gaps:** the ServiceNow adapter cannot open incidents (work notes only, and only when the record already has a linked ticket); the per-device vars override path is not yet read by `VarsStore`; the rollback change runs its engine steps in-process rather than through the Temporal `ChangeWorkflow` (which only understands catalog intents). Full list in `docs/DRIFT.md` § Limitations.

## Using the UI — every screen and button

The full manual, with a screenshot of every screen and the button-by-button text written to match what is actually visible, is [`docs/USER_MANUAL.md`](docs/USER_MANUAL.md). This section is the concise summary. **Do not trust a summary over the manual or the code** — the manual is written against `frontend/src/App.tsx`, `frontend/src/pages/*.tsx` and `frontend/src/components/**/*.tsx`.

Screens (fixed left nav, 200 px, text labels only; header shows the adapter-health summary, your role, a theme toggle and **Sign out**):

| Screen | Route | What it is for | Key buttons |
|---|---|---|---|
| Login | any (when logged out) | Sign in with username + password | **Sign in** (primary). On any failure — wrong password *or* a network/CORS failure — it shows the same text: "Invalid username or password." |
| Changes | `/changes` | The change list: filterable, sortable, resizable-column table, URL-synced filters, CSV export | **Density: comfortable/compact**, **Export CSV**, **Clear filters**, **Previous** / **Next**, **Retry** (on load error). Click a row (or Enter) to open it; arrow keys move row focus. |
| Change detail | `/changes/:id` | One change: state timeline, and tabs **Checks**, **Diff**, **Batfish results**, **AWX job output**, **Audit trail** | **Approve** (approver, only in `ci_passed`), **Abort** (operator, before apply), **Roll back** (operator, only `applied`/`post_checked`) — each behind a confirm dialog stating exactly what will happen. In other states the page says "No actions are legal for state \<state\>." |
| Drift | `/drift` | Drift records: device, kind, severity badge, status, first/last seen, owner, age | **Start scan**, **Export CSV**, **Density: comfortable/compact**, **Previous** / **Next**, **Retry** (on load error). Click a row (or Enter) to open it. |
| Drift detail | `/drift/:id` | One drift record: status timeline, tabs **Diff vs baseline**, **Diff vs intent**, **State findings**, **Compliance**, **Batfish impact**, **Attribution**, **Decisions / Audit** | **Roll back** / **Merge** / **Grant exception** (approver, behind confirm dialogs stating exactly what will happen; reason + ServiceNow reference required; owner + expiry required for exceptions). |
| Exceptions | `/exceptions` | Active drift exceptions with expiry countdown | **Open** per row (jumps to the drift record). |
| ZTP devices | `/ztp` | Devices being provisioned, by stage/site/platform | **Retry** / **Reset** per row (operator, behind confirm dialogs). Retry issues a new token that the UI does **not** display. |
| Approvals | `/approvals` | The approval queue (`ci_passed`, oldest first) | **Show/Hide** evidence per row; **Approve** / **Reject** (approver only, behind confirm dialogs; an optional reason field appears below the dialog). |
| Device locks + change windows | `/locks` | Device locks (auto-refresh 30 s) and the read-only change windows from `policies/default.yaml` | **Release** per lock (operator, confirm dialog warns a running change may then apply without the lock). Change windows have no edit controls. |
| System health | `/health` | VireoNet components (api, database, temporal) + every adapter's health, latency, last success, capabilities | **Refresh** (refetches both tables). Auto-refresh 30 s. |
| Adapter settings | `/adapters` | Read-only adapter settings (endpoint shown as scheme+host+port only; no secrets) | **Retry** (on load error). No edit/save controls exist. Contains the link **Gitea setup**. |
| Config repos | `/config` | The four config stores and one row per device repo | A hostname/repo **filter** field; **Retry** (on load error). No render/history buttons in the UI. |
| Gitea setup | `/setup/gitea` | Install and integrate Gitea (admin only; not in the left nav — reach it from the Adapters page or the URL) | **Show manual steps** / **Install automatically** → **Install Gitea automatically** (behind a confirm dialog; job polled every 3 s). Non-admins see only "Admin role is required to install and integrate Gitea." |
| IAM users | `/iam` | Users and the roles × permissions matrix (admin only) | **Create user** (username, display name, password 12+ chars, role); per-row role dropdown (applies immediately); **Deactivate** / **Activate**. |
| Style reference | `/style` | Design-token reference, not an operational screen | Inert example buttons only. |

Common behaviours: every destructive or important action goes through a **ConfirmDialog** with **Cancel** and a confirm button; loading shows a plain "Loading …" line, errors show the real error text plus **Retry**, and empty tables say so ("No changes match these filters.", "No device locks held.", …). Tables are sortable (click the column header), resizable (drag the header edge), and support a **Density** toggle (persisted in `localStorage` as `vireonet-density`; theme as `vireonet-theme`).

**What the UI does not do (do not describe these as working):** the Diff tab renders intent lines as additions — the current config is not stored, so approvers cannot see a real before/after diff; the Batfish tab is raw JSON evidence; the AWX tab is job IDs/status/step only (full stdout is in AWX); the ZTP Retry token is never shown in the UI; Config Repos has no render/history buttons; the Gitea setup job store is in-memory (lost on API restart).

## Testing

```bash
# Backend (pytest, fake adapters, no network needed)
pytest -q
# Frontend
cd frontend && npm run build     # tsc strict + Vite
npm test                          # Vitest
npm run test:e2e                  # Playwright (needs a browser + a running API)
npm run lint
```

**Verified in this repo (2026-11-23):** backend pytest **314 passed, 1 skipped, 5 failed**; frontend build passes; Vitest **25/25, 12 files**. The 5 pytest failures are all Temporal workflow tests (`test_temporal_workflow.py` ×3, `test_ztp_workflow.py` ×2) — the Temporal test-server binary cannot be downloaded in the sandbox; they are environmental, not code failures. Ruff reports 62 style issues and `mypy --strict` reports 103 errors in 27 files (pre-existing debt, not regressions).

**Not verified:** Playwright end-to-end (no system Chromium in the sandbox), `docker compose up` in the sandbox (no Docker there), real Temporal/Postgres integration, real KVM/VMs, and any live ServiceNow/NetBox/AWX/Git/Vault/device connection — all "real" adapter tests use respx-mocked HTTP. The live Fedora host debugging below is the only live evidence, and it covers the stack coming up and `/healthz` responding, not a full change end to end.

## Troubleshooting

Real issues found while bringing VireoNet up on a live Fedora 42 host (username `deepak`, `/home/deepak/vireonet`, LAN `10.228.197.24`). Each one was fixed in the repo; the fix is in the current code.

| Symptom | Cause | Fix |
|---|---|---|
| `docker compose up` fails: `failed to resolve reference "docker.io/temporalio/auto-setup:1.26.0": not found` | Tag `1.26.0` was never published | Pinned `temporalio/auto-setup:1.29.7` and `temporalio/ui:2.34.0` in `docker-compose.yml`. Note: `auto-setup` is deprecated from Temporal 1.30.1 onward |
| `failed to bind host port 0.0.0.0:5432/tcp: address already in use` | Something else on the host (most likely CloudForge) already owns 5432 | Host port moved to `5433:5432` in `docker-compose.yml`. Do **not** stop the other service to free 5432 |
| Temporal container exits (1), API/worker never start | (a) `DB: postgresql` is not a valid driver for auto-setup; (b) `DYNAMIC_CONFIG_FILE_PATH` pointed at a file the image does not ship | `DB: postgres12_pgx`, and the `DYNAMIC_CONFIG_FILE_PATH` line removed |
| API/worker exit: `FAILED: No 'script_location' key found in configuration.` | The Dockerfile never copied `alembic/` or `alembic.ini`, but the compose command starts with `alembic upgrade head` | Dockerfile now copies `alembic.ini`, `alembic/`, `policies/` (and `scripts/`) |
| API/worker exit: `KeyError: 'formatters'` in `alembic/env.py` | `alembic.ini` had only the `[alembic]` section, no logging sections, and `fileConfig` requires them | `alembic.ini` now has standard `[loggers]`/`[handlers]`/`[formatters]` sections; `env.py` only calls `fileConfig` when a `[formatters]` section exists |
| Blank white screen at `http://localhost:5173` | `frontend/src/main.tsx` imported `./test-setup`, which imports `@testing-library/jest-dom/vitest` — a test-only package bundled into production JS, crashing React before it mounted | The import was removed from `main.tsx` (Vitest still loads it via `setupFiles` in `vite.config.ts`). Hard refresh (`Ctrl+Shift+R`) after rebuilding |
| `python: can't open file '/app/scripts/seed_admin.py'` | The Dockerfile never copied `scripts/` into the image; and `seed_admin.py` imported `get_session_factory`, which does not exist | Dockerfile copies `scripts/`; the script now uses `create_engine` + `create_session_factory` |
| Seed admin "does nothing" | Env vars placed before `docker compose exec` set them for the compose process on the host, not inside the container | Use `-e` flags: `docker compose exec -e VIREONET_ADMIN_USER=admin -e VIREONET_ADMIN_PASSWORD='…' api python scripts/seed_admin.py` |
| Login works with `curl` but always fails in the UI ("Invalid username or password.") | No CORS middleware in the backend — the browser blocks the cross-origin fetch from `:5173` to `:8000`, while `curl` does not enforce CORS | `CORSMiddleware` added in `main.py` + `VIREONET_CORS_ORIGINS` setting (see [Configuration](#configuration)) |
| Login fails with `ERR_CONNECTION_REFUSED` / preflight fails | The UI was built with `VITE_API_URL=http://localhost:8000` but the browser runs on another machine, so `localhost:8000` points at the browser's own machine | Rebuild with `VITE_API_URL=http://<host-LAN-IP>:8000` (see [Quick start](#quick-start)) |
| Login fails with `CORS error` / preflight `400` | The browser origin is the LAN IP (`http://10.228.197.24:5173`) but that origin was not in the API's allowed list — `.env` alone does not reach the container | `docker-compose.yml` now passes `VIREONET_CORS_ORIGINS` into the `api` container explicitly; add your LAN origin there |
| System Health shows `ok` and lists exist, but "nothing is present" | `VIREONET_ADAPTER_MODE=fake` is the compose default — the fakes return deterministic seed data and always report `ok` | That is expected. Set `VIREONET_ADAPTER_MODE=real` and real credentials for real data; then lists are empty until real requests exist |

**How to diagnose a login failure, in order:** (1) open `http://<host>:8000/healthz` in the same browser — if it refuses, the API is unreachable or the host firewall is blocking 8000/5173 (do not change firewall rules without explicit approval); (2) check the browser Network tab for `/auth/login` — `200` = credentials, `401` = wrong password, `failed`/`CORS error` = CORS origin or reachability; (3) check `docker compose logs api` for the actual error.

## Project layout

```
.
├── docker-compose.yml          # postgres, temporal, temporal-ui, api, worker, frontend
├── Dockerfile                  # python:3.12-slim; copies src/, alembic/, policies/, scripts/
├── pyproject.toml              # pip install -e ".[dev]"
├── .env.example
├── alembic/ + alembic.ini      # migrations 0001_initial, 0002_device_locks, 0003_ztp_devices, 0004_iam_users
├── policies/
│   ├── default.yaml            # change policy: limits, protected devices/VLANs, change window
│   └── config_store.yaml       # config version control layout
├── ansible/
│   ├── playbooks/              # backup, pre-check, dry-run, apply, post-check, rollback, ztp-day1
│   └── awx/job_templates.yaml  # the AWX template names the adapter looks up
├── scripts/
│   ├── seed_admin.py           # first admin (only when the users table is empty)
│   ├── setup_gitea.py          # --manual | --auto | --verify
│   ├── install_gitea.sh
│   ├── render_intent.py
│   └── eveng_smoke.py
├── deployments/gitea/docker-compose.gitea.yml
├── examples/intent-vlan-100.yaml
├── .gitlab-ci.yml  .github/workflows/ci.yml  .gitea/workflows/ci.yml
├── docs/
│   ├── ADAPTERS.md             # per-adapter integration guide
│   ├── USER_MANUAL.md          # operator manual (screenshots from the live UI)
│   ├── SDLC.md                 # requirements traceability R1–R13, test record
│   └── runbook.md              # start/stop, deploy, approve, roll back, ZTP, failure modes
├── src/vireonet/
│   ├── main.py  worker.py  config.py  db.py  models.py  state_machine.py  change_state.py
│   ├── intent.py  validation.py  policy.py  planner.py  render.py  batfish_gate.py
│   ├── safety.py  checks/  iam/  ztp/  temporal/  notifications.py  observability.py
│   ├── adapters/               # base, errors, protocols, fakes, registry + real adapters
│   │   └── git/                # GitProvider protocol + gitlab/github/gitea/bitbucket
│   ├── api/                    # changes, ztp, locks, health, adapters, auth, iam, webhooks, setup_gitea, config_repos
│   ├── services/               # ChangeService, ZtpService, DeviceLockService
│   └── config_store/           # templates, vars, device repos, NetBox export
├── frontend/                   # React 18 + TS (strict) + Vite; src/pages, src/components, src/lib
└── tests/                      # pytest; contract suite runs per adapter against Fake and Real (respx)
```

## Known limitations and debt

- **No end-to-end test has ever passed for the whole stack.** Best evidence is the unit/contract record in [Testing](#testing) plus the live-host stack coming up and `/healthz` responding. A full change end to end against real ServiceNow/NetBox/AWX/Git has not been run.
- **The diff is not a real diff.** The Diff tab renders intent lines as additions; the current config is not stored, so approvers cannot see what will actually change on a device. Do not present it as a before/after diff.
- **IAM is basic, not enterprise.** HS256 JWTs, 8-hour expiry, no refresh, no revocation, no OIDC, no MFA, no password reset or lockout. A role change does not revoke an existing JWT until it expires (deactivation is immediate via the database check). The last-admin guard prevents demoting/deactivating the only admin.
- **Batfish / EVE-NG are the least proven parts.** Do not make Batfish a hard blocker until it has run against 2–3 real configs; EVE-NG `.unl` loading has no single stable REST call; differential reachability is not implemented.
- **DHCP is not wired in real mode.** The real `DhcpAdapter` exists, but `registry.py` never replaces the `dhcp` fake in real mode — see [`docs/ADAPTERS.md`](docs/ADAPTERS.md) §8 and §"What is NOT wired".
- **Bitbucket `commit_file` is unverified** (posts JSON where Bitbucket needs multipart); **Gitea pipeline = most recent run**, not the run for a specific PR; **config-store `history()` returns `[]`**; device repos must be pre-created.
- **Execution / lab caveats (E1–E4).** E1: playbook jobs return no `declared_changes` — the field is filled only by python runners (E2) and reconciliation (E3), so a playbook-only change shows no declared changes in the change detail. E2: `set_stats` harvesting is **UNCERTAIN** — how AWX exposes `set_stats` via its API is unverified, so the engine falls back to parsing the `VIREONET-RESULT` stdout markers (`src/vireonet/execution/set_stats.py`). E3: the script SHA is verified against the Git provider at the manifest ref (`ScriptRunner.get_file`), not against the AWX project's checkout — if AWX's `VireoNet` project is at a different ref, the script AWX runs may differ from the verified one. E4: Containerlab is CLI-driven over SSH — no stable Containerlab REST API is known, so the adapter shells out to the `containerlab` CLI; `restore_config` is unimplemented (kind-specific) and old IOS on Containerlab is kind-dependent.
- **Drift caveats (D1–D6).** Normaliser `eos`/`junos` is UNCERTAIN; attribution is best-effort (unknown when the scan cannot prove who changed the config); intent drift needs a deterministic render; the ServiceNow adapter has **no incident-creation method** — drift above the incident threshold only gets a work note when the record already has a linked ticket, otherwise a log entry + notification; the per-device vars override path (`netops/vars/device/<device>.yaml`) is not yet read by `VarsStore`; the rollback change runs its engine steps in-process, not through the Temporal `ChangeWorkflow` (catalog intents only); NAPALM rollback on EOS/Junos is UNCERTAIN; Netmiko dry-run reports what *would* be sent; the Playwright drift e2e spec is written but not run in this sandbox (no system Chromium). Full list in [`docs/DRIFT.md`](docs/DRIFT.md).
- **Ruff 62 / mypy strict 103** — pre-existing style/type debt, not regressions.
- **`VITE_API_URL` is baked into the frontend at build time** — changing it requires a rebuild.
- **ServiceNow field names / state values, NetBox device_type/role IDs, Batfish question names, LibreNMS fields, Gitea merge/payload shapes** are instance- or version-specific and marked **UNCERTAIN** in the code and in [`docs/ADAPTERS.md`](docs/ADAPTERS.md). Change only the isolated dicts/constants, not the adapter methods.
