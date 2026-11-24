# VireoNet — test report (2026-11-24)

## Automated tests

| Suite | Result | How |
|---|---|---|
| Backend (pytest) | **938 passed, 1 skipped** | Python 3.12 container; real Temporal server (`temporalio/temporal`), SQLite / Postgres |
| Lint / format / types | ruff clean · ruff format clean · mypy clean (198 files) | same container |
| UI (Vitest) | **77 passed** (31 files) · `tsc -b` clean · production build OK | Node 20 container |

Doc-pinned adapter tests: every external call is checked against the request/response shapes in
the vendor's documentation (`tests/test_*_docs.py`, `tests/test_controllers*.py`,
`tests/test_logsearch.py`); see `docs/ADAPTERS.md` §12 for what was verified, fixed, and what
still needs a live system.

## End-to-end (live Docker stack, browser automation with Playwright)

Stack: Postgres 16, Temporal, API, worker, nginx UI, Kafka 3.8, Gitea 1.22. Fake adapters
(simulated network, ITSM, NetBox, controllers).

| Stage | Verified end to end |
|---|---|
| 1 Hardening | Anonymous health / ZTP / help calls refused; login lockout; fully automatic ITSM change closed; shared demo ITSM state |
| 2 Flows | Template → save → publish → enable in the UI; ServiceNow event → OS-upgrade flow → change pipeline → ticket closed; duplicates ignored; Zabbix adapter event; approval in the browser; script sandbox (runs as nobody, `import os` refused); real Kafka message → one run |
| 3 Controllers | CloudVision one-step and Panorama distribute→install→reboot upgrades from tickets; versions changed on the simulated devices and on the Controllers page |
| 4 Adapters | Adapters tab lists every family (52 rows, 7 categories); Configure saves values, never returns secrets, tests the real Gitea; Gitea tab embeds real Gitea; documentation space search; log-search flow |
| 5 Intelligence | All five neural tabs train and run; log-sequence model flags the two real incidents with no false positives; ticket → change type/devices; risk-gated intake flow |
| 6 Pre/Post | VLAN-add change with a check profile closes; VLAN-remove change fails `no-vlan-removed` at post-check and rolls back automatically; pipeline pre/post snapshots compared in the UI; manual snapshot |

## Not run

- **Mode matrix** (`temporal_mode` auto/fake/real × `adapter_mode` fake/real) — skipped on request.
- **Live vendor systems** (real ServiceNow, NetBox, AWX, controllers, log platforms): tested with
  documentation-based mocks only; the remaining `UNCERTAIN` calls are listed in `docs/ADAPTERS.md` §12.
