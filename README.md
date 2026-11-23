# VireoNet

> Formerly NetOrch — renamed to VireoNet. The old `NETORCH_` environment-variable prefix is gone; use `VIREONET_` instead.

Thin, network-aware change orchestrator. VireoNet never touches devices
directly — AWX + Ansible is the only component that does.

## Architecture

```
ServiceNow ──webhook──> VireoNet API ──start──> Temporal (Workflow ID = ticket)
  RITM/CHG                │  validate + policy      │
                          │  (NetBox + YAML)        ▼
NetBox ◄──── adapters ────┤                    ChangeWorkflow
GitLab ◄──── (httpx,      │                      ├─ validate → plan
  retry, breaker,         │                      ├─ wait CI signal
  correlation ID)         │                      ├─ wait approval + merge
AWX ◄─────────────────────┤                      ├─ lock → backup → pre-check
Batfish ◄─────────────────┤                      ├─ dry-run → apply in waves
Vault ◄───────────────────┤                      ├─ post-check → rollback on fail
EVE-NG ◄──────────────────┘                      └─ close (NetBox active, SN closed)
DHCP / ZTP ──────────────> ZtpWorkflow (separate workflow, same adapters)
```

State machine (exact, enforced in `state_machine.py`):
`received → validated → planned → ci_passed → approved → pre_checked → applied → post_checked → closed`
Failure exits: `rejected, ci_failed, pre_check_failed, rolled_back, failed`.
Every transition is an append-only audit row with actor, reason and evidence.

## Quick start (local dev, Fake adapters, no external systems)

```bash
docker compose up --build
curl localhost:8000/healthz
curl localhost:8000/adapters/health
curl localhost:8000/metrics
# Temporal UI: http://localhost:8233
```

Or without Docker:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
uvicorn vireonet.main:app --port 8000
python -m vireonet.worker   # needs a Temporal server; see docs/runbook.md
```

## Configuration

All config is env (`VIREONET_*`, see `.env.example`). Secrets come from
Vault in production, never from code, Git or logs. `VIREONET_ADAPTER_MODE`
is `fake` (in-memory, deterministic) or `real`.

Key settings: `VIREONET_DATABASE_URL`, `VIREONET_TEMPORAL_ADDRESS`,
`VIREONET_MONITORING_BACKEND` (`none` | `alertmanager` | `librenms`),
notification webhooks / SMTP, `VIREONET_WAVE_SIZE`, `VIREONET_ABORT_THRESHOLD`.

## Runbook

See `docs/runbook.md` — start/stop, deploy a change, approve, roll back,
ZTP, failure modes, and what to do when Temporal / Postgres / AWX is down.
