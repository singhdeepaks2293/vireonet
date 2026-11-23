# VireoNet Lab Guide — End-to-End Test (F5)

`scripts/lab_e2e.py` runs the two lab flows against **real** systems and
prints one `PASS`/`FAIL` line per stage. It is the only script that drives a
change and a ZTP device end to end; `scripts/eveng_smoke.py` only starts an
EVE-NG lab and wipes it.

> **Never run this against production.** Flow A creates a real change
> (ticket `RITM-LAB-001`) and a real VLAN 100 on `leaf-01`/`leaf-02`.
> Flow B registers a real ZTP device (`leaf-99`). The test VLAN, device,
> and change remain afterwards as evidence — only the EVE-NG lab is wiped.

## What it proves

- **Flow A — VLAN add:** intent → validation → backup + rendered diff →
  Batfish CI gate → human approval → AWX apply → post-check → closed.
- **Flow B — ZTP:** device registration → DHCP reservation → one-time-token
  callback → identity check → day-1 config push → active in NetBox.

## Prerequisites

| Requirement | Detail |
|---|---|
| VireoNet in real mode | `VIREONET_ADAPTER_MODE=real`; API, worker, Temporal, and Postgres up. Fake mode is refused (exit 2). |
| EVE-NG | Reachable at `VIREONET_EVENG_BASE_URL`, with IOSvL2 images for `leaf-01` and `leaf-02`. Import `topologies/two-leaf.unl` (see note below). |
| NetBox | Devices `leaf-01` and `leaf-02` exist at site `dc1`, with `GigabitEthernet0/1` interfaces. `leaf-99` (serial `FOC9999A`, model `C9300-48P`) must exist or be creatable for Flow B. |
| AWX | Job templates imported from `ansible/awx/job_templates.yaml`: `backup`, `pre-check`, `dry-run`, `apply`, `post-check`, `rollback`, `dhcp-reload`, `ztp-day1`. Inventory `NetBox`, project `VireoNet`. |
| Gitea | Repo `netops/intent` (`VIREONET_GIT_REPO=netops/intent`) reachable; intent configs land here. |
| Batfish | Reachable at `VIREONET_BATFISH_HOST:VIREONET_BATFISH_PORT` (defaults `localhost:9996`) for the CI gate. |
| API token | A bearer token with `viewer` + `approver` + `operator` roles — approval and ZTP reset require them. |

### EVE-NG topology import (uncertain)

EVE-NG has **no stable single REST call to import a `.unl` file**
(`src/vireonet/adapters/eveng.py: load_topology` records the topology; it
does not invent an import endpoint). Import `topologies/two-leaf.unl` via
the EVE-NG UI (Lab → Import) before running the test, and check the
`image` attribute in the file matches the IOSvL2 image installed on your
EVE-NG host — image names differ between installs. The lab the script
starts must already contain the `leaf-01`/`leaf-02` nodes.

## Environment

Secrets come from Vault or the environment. **Never print a token or
password** — the script sends the API token only as an `Authorization:
Bearer` header and never logs it.

| Variable | Purpose |
|---|---|
| `VIREONET_API_URL` | VireoNet API base URL, e.g. `http://localhost:8000` (required; script-only, not a server setting) |
| `VIREONET_API_TOKEN` | Bearer token for the API (required) |
| `VIREONET_ADAPTER_MODE` | Must be `real` (server setting; anything else → exit 2) |
| `VIREONET_EVENG_BASE_URL` | EVE-NG base URL |
| `VIREONET_EVENG_USERNAME` / `VIREONET_EVENG_PASSWORD` | EVE-NG login |
| `VIREONET_NETBOX_BASE_URL` / `VIREONET_NETBOX_TOKEN` | NetBox (server setting) |
| `VIREONET_AWX_BASE_URL` / `VIREONET_AWX_TOKEN` | AWX (server setting) |
| `VIREONET_GIT_BASE_URL` / `VIREONET_GIT_REPO` | Git provider URL and repo, e.g. `netops/intent` |
| `VIREONET_BATFISH_HOST` / `VIREONET_BATFISH_PORT` | Batfish (server settings) |
| `VIREONET_VAULT_ADDR` / `VIREONET_VAULT_ROLE_ID` / `VIREONET_VAULT_SECRET_ID` | Vault, if secrets are fetched from Vault instead of env |

## Running it

```bash
# Both flows (default)
VIREONET_API_URL=http://localhost:8000 VIREONET_API_TOKEN=<token> \
  python scripts/lab_e2e.py --lab f5-lab

# Flow A only
python scripts/lab_e2e.py --lab f5-lab --only vlan

# Flow B only
python scripts/lab_e2e.py --lab f5-lab --only ztp

# Leave the EVE-NG lab running for inspection (default: wiped in a finally)
python scripts/lab_e2e.py --lab f5-lab --no-cleanup

# Different topology file (default: topologies/two-leaf.unl)
python scripts/lab_e2e.py --lab f5-lab --topology topologies/two-leaf.unl
```

Exit codes: `0` all stages passed, `1` some stage failed, `2` refused
(non-real adapter mode, or missing `VIREONET_API_URL` /
`VIREONET_API_TOKEN` — raised as `KeyError` with a clear message).

## Expected output — Flow A (VLAN add)

| Stage | What happens | Pass condition |
|---|---|---|
| `prereq` | `GET /adapters/settings` — every adapter `mode` is `real`; `GET /adapters/health` — every adapter `status` is `ok` | All real and ok |
| `change-create` | `POST /changes` with the `vlan_add` 100 `web` intent for `leaf-01`/`leaf-02` (ticket `RITM-LAB-001`) | HTTP 202, change id returned |
| `ci-batfish` | Poll `GET /changes/{id}` until `ci_passed` (600 s) | State reaches `ci_passed` |
| `backup-diff` | `GET /changes/{id}/detail`, count diff lines of type `add` | At least one diff add (rendered config + backup evidence) |
| `approve` | `POST /changes/{id}/signals/approval` `{"approved": true}` | HTTP 202 |
| `apply` | Poll until `applied` / `post_checked` / `closed` (900 s) | AWX apply ran |
| `post-check` | Poll until `post_checked` / `closed` (600 s) | Post-checks passed |
| `close` | `GET /changes/{id}` | State is `closed` |

Success tail:

```text
PASS prereq: all 10 adapters real and ok
PASS change-create: RITM-LAB-001 -> <change-id>
PASS ci-batfish: state=ci_passed
PASS backup-diff: 4 diff adds
PASS approve: POST approval -> 202
PASS apply: state=applied
PASS post-check: state=post_checked
PASS close: state=closed
EVE-NG lab /f5-lab wiped
ALL STAGES PASSED (8/8)
```

## Expected output — Flow B (ZTP)

| Stage | What happens | Pass condition |
|---|---|---|
| `ztp-register` | `POST /ztp/devices` — `leaf-99`, serial `FOC9999A`, MAC `aa:bb:cc:dd:ee:99`, `C9300-48P`, `ios-xe`, site `dc1` | HTTP 202, one-time token returned |
| `ztp-reservation` | `POST /ztp/devices/FOC9999A/reservation` (F2 endpoint; idempotent — returns `already_created: true` if the ZTP workflow already created it) | HTTP 202, `ip_address` returned |
| `ztp-callback` | `POST /ztp/callback` with the token from registration (simulated device) | HTTP 202 |
| `ztp-identity` | Poll until stage `identity_verified` (300 s) | Serial/model/platform match NetBox |
| `ztp-day1` | Poll until stage `active` (600 s) | Day-1 config applied, device activated |
| `ztp-cleanup` | `DELETE /ztp/devices/FOC9999A/reservation` (F2 endpoint; legal only once the device is active) | Response reports `removed: true` |

Success tail:

```text
PASS ztp-register: leaf-99 FOC9999A ticket ZTP-FOC9999A
PASS ztp-reservation: ip=10.0.1.101
PASS ztp-callback: POST /ztp/callback -> 202
PASS ztp-identity: stage=identity_verified
PASS ztp-day1: stage=active
PASS ztp-cleanup: removed=True
ALL STAGES PASSED (6/6)
```

## Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `FAIL prereq: adapter_mode is 'fake'` / exit 2 | Server (or local env) not in real mode | Set `VIREONET_ADAPTER_MODE=real` and restart the API |
| `FAIL prereq: adapters not all real: {...}` | One adapter still configured fake/recorded | Check that adapter's settings; see `GET /adapters/settings` |
| `FAIL prereq: adapters down: [...]` | That system is unreachable or its credentials are wrong | Check the adapter's base URL / token; see `GET /adapters/health` |
| `FAIL change-create: POST /changes -> 422` | Intent rejected (unknown device, policy) | Verify `leaf-01`/`leaf-02` exist in NetBox at site `dc1` |
| `FAIL ci-batfish: state=ci_failed` | Batfish gate failed on the rendered config | Read the gate evidence in `GET /changes/{id}/detail` (`batfish`) |
| `FAIL ci-batfish: state=timeout` | CI never reported back | Check GitLab CI / the `ci` signal webhook; Batfish host reachable? |
| `FAIL backup-diff: 0 diff adds` | Render produced no config lines | Check the intent renders: `python scripts/render_intent.py examples/intent-vlan-100.yaml` |
| `FAIL approve: POST approval -> 403` | Token lacks the `approver` role | Use a token with `approver` (and `operator`) roles |
| `FAIL apply: state=failed` / `state=rolled_back` | AWX apply failed, or post-check failed and the workflow rolled back | Check the AWX job linked in the change detail (`awx_jobs`) |
| `FAIL ztp-register: POST /ztp/devices -> 409/422` | Serial already registered with different data, or NetBox lookup failed | Use a fresh serial or `POST /ztp/devices/{serial}/reset` first |
| `FAIL ztp-reservation: POST reservation -> 409` | Device stage is not `registered`/`reservation_created` (e.g. a previous run already moved it on) | Check the device stage with `GET /ztp/devices/FOC9999A`; reset if stuck |
| `FAIL ztp-callback: ... -> 401` | Token expired (15 min TTL) or already used | Re-run; tokens are single-use |
| `FAIL ztp-identity: stage=identity_failed` | Serial/model/platform mismatch vs NetBox | Fix the NetBox device record for `leaf-99` |
| `FAIL ztp-day1: stage=day1_failed` | `ztp-day1` AWX template failed or Batfish gate failed | Check the AWX `ztp-day1` job |
| `FAIL ztp-cleanup: DELETE reservation -> 409` | Cleanup ran before the device was active (DELETE is legal only from `active`) | Let the device reach `active` first; do not delete mid-flight |
| `FAIL ztp-cleanup: removed=False` | No reservation for `leaf-99` in the DHCP config (already removed, or never created) | Check the DHCP config; the reservation step must have succeeded first |
| `FAIL eveng-lab: could not start lab ...` | EVE-NG unreachable or bad credentials | Check `VIREONET_EVENG_*`; the lab is not created, nothing to wipe |

Polling stops early (no waiting for the timeout) on terminal states:
changes `rejected`, `ci_failed`, `pre_check_failed`, `failed`,
`rolled_back`; ZTP `failed`, `identity_failed`, `day1_failed`,
`callback_timeout`.

## Cleanup

- The EVE-NG lab is stopped and wiped in a `finally` (same pattern as
  `scripts/eveng_smoke.py`), even if a stage fails — unless `--no-cleanup`
  is given, in which case the lab is left running for inspection.
- The test VLAN, the `leaf-99` device, and the `RITM-LAB-001` change are
  **not** removed; they are the evidence the lab ran. The DHCP reservation
  *is* removed by the `ztp-cleanup` stage. To fully reset before re-running
  Flow B with the same serial, delete VLAN 100 from the leaves and
  `POST /ztp/devices/FOC9999A/reset` — note reset is not legal while the
  device is `active`, so an already-`active` device cannot be re-run
  through Flow B with the same serial; use a fresh serial instead.

## Known gaps / adaptations (F5 brief vs the repo)

- **Reservation endpoints are F2's and exist:** the script uses
  `POST /ztp/devices/{serial}/reservation` (202, idempotent) and
  `DELETE /ztp/devices/{serial}/reservation` (requires `removed: true`,
  legal only from `active`). Note the race the script tolerates: the ZTP
  workflow's `ztp_create_reservation` activity may create the reservation
  first, in which case the POST returns `already_created: true`.
- **No `POST verify-identity` endpoint and no separate token endpoint.**
  `POST /ztp/devices` returns the one-time token, and identity is checked
  by the workflow's `ztp_verify_identity` activity, so the script polls
  the device stage (`identity_verified`, then `active`).
- **`GET /adapters/health` has no `mode` field** (see `AdapterHealth` in
  `src/vireonet/adapters/base.py`), so the mode check uses
  `GET /adapters/settings` and health uses `GET /adapters/health`.
- **`.unl` import may need the EVE-NG UI** — see the note above.
- The script is **not** run live in the sandbox (no EVE-NG, NetBox, AWX,
  or Gitea there). Its stage logic is unit-tested with mocked API calls
  in `tests/test_f5_lab_smoke.py`.
