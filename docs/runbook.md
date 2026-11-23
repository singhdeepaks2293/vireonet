# VireoNet Runbook

## Start (local dev)
```bash
docker compose up --build
docker compose logs -f api worker
```
API: http://localhost:8000/healthz, Temporal UI: http://localhost:8233.

## Start (production, outline)
- Postgres (managed or HA), Temporal with its own Postgres persistence,
  API + worker as separate deployments, same image, different command.
- Set `VIREONET_ADAPTER_MODE=real` and all adapter base URLs + Vault AppRole.
- Run `alembic upgrade head` once before starting the API/worker.

## Submit a change
1. Create the ServiceNow request. The webhook `POST /changes` validates
   against NetBox + policy and starts the Temporal workflow (ID = ticket).
2. GitLab MR is opened by the workflow's CI step. CI runs lint, render,
   Batfish, optional EVE-NG. Merge only when the pipeline is green.
3. Approve: `POST /changes/{id}/signals/approval` with role `approver`.
4. Watch the workflow in the Temporal UI or `GET /changes/{id}`.

## Roll back
- Automatic: a failed post-check or a wave abort triggers rollback from
  the pre-change backup (AWX `rollback` playbook) and the state becomes
  `rolled_back`. No human action is needed.
- Manual: `POST /changes/{id}/signals/approval` with `approved: false`
  before apply, or cancel the AWX job and signal reject.

## ZTP
1. Register the device in NetBox, then `POST /ztp/devices` (returns a
   one-time token once).
2. The device boots via DHCP (option 66/67, option 43 for PnP), calls
   `POST /ztp/callback` with the token. A bad token returns 401.
3. Identity is checked (serial/model/platform vs NetBox) before day-1.
4. Day-1 renders, Batfish-gates, AWX-pushes, then NetBox becomes `active`.

## Failure modes
- **Temporal down**: `POST /changes` returns 202 and persists the change
  as `validated`, but the workflow does not start. The API logs the error.
  Restart Temporal; re-posting the same ticket is a no-op (idempotent start).
- **Postgres down**: API returns 503 on writes. No state is lost — all
  state is in Postgres + Temporal.
- **AWX job failed**: the workflow rolls back and notifies. Check the AWX
  job stdout via `GET /adapters/health` and the AWX UI; fix the playbook
  or the intent, then submit a new change (do not re-run the same workflow).
- **Lock held**: `pre_check_failed` with `device X is locked by RITM...`.
  Wait for the holder to finish, or check `device_locks` for an expired
  lock (expired locks are stealable).
- **Batfish gate failed**: the MR pipeline fails. Fix the intent, push a
  new commit; do not merge a failing pipeline.
- **Monitoring silence leaked**: silences expire after
  `VIREONET_MONITORING_SILENCE_MINUTES` (default 60). Check Alertmanager
  `/api/v2/silences` for leftovers after a crashed worker.

## Observability
- Logs: JSON via structlog, ticket number as correlation ID, no secrets.
- Metrics: `GET /metrics` — `vireonet_changes_total`, `vireonet_adapter_calls_total`,
  `vireonet_check_failures_total`, `vireonet_device_locks_held`, `vireonet_ztp_total`.
- Health: `GET /healthz`, `GET /adapters/health` (per-adapter status + capabilities).
