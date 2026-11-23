# VireoNet User Manual

> For operators. This manual tells you how to run VireoNet day to day and how to use
> every screen and button in the web UI. It is written to be usable on its own — you do
> not need to read the README first.
>
> **About the screenshots:** this manual was written from a live, read-only walkthrough
> of the running UI on 2026-11-23 (signed in as `admin`, all data in fake mode, so every
> list was empty). The screenshots from that walkthrough live in the browser session
> and are not files in this repository, so **no images are embedded in this manual**.
> Each screen section instead describes exactly what you will see on that screen —
> the heading, the fields, the columns, the buttons, and the empty-state text — so you
> can match the manual to the screen in front of you. Where a screen could not be
> seen during the walkthrough, the manual says so and describes it from the frontend
> code instead. Nothing in this manual is a placeholder.
>
> Companion documents: `README.md` (detailed setup, architecture, troubleshooting for
> the live host), `docs/ADAPTERS.md` (per-adapter integration: ServiceNow, NetBox, AWX,
> Git, Batfish, Vault, EVE-NG, DHCP, notifications, monitoring), `docs/runbook.md`
> (short operations runbook), `docs/SDLC.md` (requirements traceability).

## Contents

1. [What VireoNet is](#1-what-vireonet-is)
2. [Getting started](#2-getting-started)
3. [First login](#3-first-login)
4. [Roles — what each role can do](#4-roles--what-each-role-can-do)
5. [The screen layout](#5-the-screen-layout)
6. [Login](#6-login)
7. [Changes](#7-changes)
8. [Change detail](#8-change-detail)
9. [ZTP devices](#9-ztp-devices)
10. [Approvals](#10-approvals)
11. [Locks and change windows](#11-locks-and-change-windows)
12. [System health](#12-system-health)
13. [Adapters](#13-adapters)
14. [IAM](#14-iam)
15. [Scripts](#15-scripts)
16. [Config version control](#16-config-version-control)
17. [Drift](#17-drift)
18. [Gitea setup](#18-gitea-setup)
19. [Style reference](#19-style-reference)
20. [Daily workflows](#20-daily-workflows)
21. [Checking health](#21-checking-health)
22. [Troubleshooting and FAQ](#22-troubleshooting-and-faq)
23. [Glossary](#23-glossary)

---

## 1. What VireoNet is

VireoNet is a thin, network-aware change orchestrator. It sits between the systems you
already use and the network devices, and it makes sure every change is validated,
reviewed, tested, applied in a controlled way, and recorded.

The flow for a targeted change is:

```
ServiceNow request → validate vs NetBox + policy → GitLab/Gitea MR → CI
(lint, render, Batfish, optional EVE-NG) → approval/merge → AWX workflow
(lock, backup, baseline, dry-run diff, apply, post-check, auto-rollback
on failure) → update NetBox + Git → ServiceNow result
```

The flow for a new device (ZTP — zero-touch provisioning) is:

```
NetBox registration → DHCP reservation → day-0 bootstrap → callback →
identity check (serial/model/version vs NetBox) → day-1 rendered from
NetBox + templates → Batfish gate → AWX push → post-check → NetBox active
→ ServiceNow closed
```

Three rules shape everything in this manual:

- **AWX + Ansible is the only component that touches network devices.** VireoNet
  never SSHes to a device itself.
- **Dry-run is the default for every apply step.** A real apply only happens from an
  explicitly approved state.
- **Every change has a state machine**, and every transition is recorded with a
  timestamp, actor, reason, and evidence in an append-only audit log:

  `received → validated → planned → ci_passed → approved → pre_checked → applied
  → post_checked → closed`

  Failure exits: `rejected`, `ci_failed`, `pre_check_failed`, `rolled_back`, `failed`.

## 2. Getting started

### What you need

- A Linux host with Docker and Docker Compose (the supported path), or Python 3.12,
  Node 20, PostgreSQL, and Temporal if you run it without Docker.
- The VireoNet directory (the `vireonet-2026-11-23-fix11.zip` extract, or the repo).
- A browser. The UI is a web app; there is nothing to install on your workstation.

### Start the stack (Docker)

```bash
cp .env.example .env        # if present; set VIREONET_JWT_SECRET to a long random value
docker compose up --build -d
sleep 15
docker compose ps
curl http://localhost:8000/healthz
```

Ports on the host:

| Service | Host port | URL |
|---|---|---|
| Frontend (UI) | 5173 | http://localhost:5173 |
| API | 8000 | http://localhost:8000 |
| Temporal UI | 8233 | http://localhost:8233 |
| Postgres | 5433 | (database only, no web UI) |

> **Localhost vs LAN.** The frontend is built to call the API at the address baked in
> at build time (`VITE_API_URL`, default `http://localhost:8000`). `localhost` only
> works when your browser is on the same machine as Docker. If you browse from
> another machine, rebuild with the host's LAN address, for example
> `VITE_API_URL=http://10.228.197.24:8000 docker compose up --build -d`, and open
> the UI at `http://10.228.197.24:5173`. See §22 for the CORS and
> connection-refused errors this causes when it is wrong.

### Start without Docker (outline)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn vireonet.main:app --host 0.0.0.0 --port 8000   # terminal 1
python -m vireonet.worker                               # terminal 2
cd frontend && npm install && npm run dev               # terminal 3, UI on :5173
```

You still need PostgreSQL and Temporal running. See `README.md` for the full
non-Docker procedure.

### Fake mode vs real mode

Out of the box the stack runs with `VIREONET_ADAPTER_MODE=fake`. The fake adapters
return fixed, deterministic demo data — a NetBox with `leaf-01`/`leaf-02`, a ServiceNow
request `RITM0001234`, and so on — so you can click through every screen and the whole
workflow without any real system connected. **Fake data is not real data.** If a screen
shows devices or changes in fake mode, nothing exists behind them.

To use real systems, set `VIREONET_ADAPTER_MODE=real` and configure each adapter
(see `docs/ADAPTERS.md`). In real mode the lists start empty and only fill up when a
real system creates something. System Health will then show `down` for any adapter it
cannot actually reach.

## 3. First login

There is **no default username or password**. The first admin is created by the seed
script, which only runs when the users table is empty:

```bash
docker compose exec -e VIREONET_ADMIN_USER=admin \
  -e VIREONET_ADMIN_PASSWORD='YourPassword123' \
  api python scripts/seed_admin.py
```

It must print `Admin user admin created.` If it prints `Users already exist`, an admin
was already created and the password is whatever was used then. There is no
forgot-password screen; a forgotten admin password has to be reset directly in the
database by someone with database access.

Then open the UI (`http://localhost:5173`, or the LAN address) and sign in — see §6.
The password must be at least 12 characters. Do not paste your password into chat,
tickets, or logs.

## 4. Roles — what each role can do

| Role | Rank | Can do |
|---|---|---|
| viewer | 1 | Read changes, health, adapters, locks, ZTP devices. Cannot approve, create, signal, or release anything. |
| operator | 2 | Everything a viewer can, plus create changes, send signals, abort and roll back changes, retry/reset ZTP devices, release device locks, and emergency-accept drift. |
| approver | 3 | Everything an operator can, plus approve and reject changes waiting for approval, and decide drift (roll back, merge, grant exception). |
| admin | 4 | Everything, plus manage users and roles in IAM (§14), view the Scripts manifest (§15), and manage drift ignore rules (§17). |

Your role is set by an admin and is shown in the top bar as `Role: admin` (or your
role). The UI hides or disables buttons you are not allowed to use — for example,
**Approve** and **Reject** are disabled unless you are at least an approver, and the
whole IAM page shows "Admin role is required to manage users and roles." unless you
are an admin. Your session lasts 8 hours (480 minutes) by default; after that, sign
in again.

## 5. The screen layout

Every screen after login has the same layout:

- **Left navigation** (fixed, text labels, no icons): Changes, Drift,
  Exceptions, ZTP devices, Approvals, Locks and change windows, System health,
  Adapters, IAM, Scripts (admin only — hidden for other roles), Config repos,
  Style reference. Clicking an item opens that screen. The current screen is
  highlighted. **Gitea setup has no left-nav entry** — it is reached from a link on
  the Adapters page (§13, §18).
- **Top bar**: the screen title on the left; on the right, adapter status
  (`Adapters: ok`, or `Adapters down: N`, or `Adapter health: unavailable`),
  your role (`Role: admin`), a **Theme: dark** / **Theme: light** button, and a
  **Sign out** button.
- **Tables**: full width, sortable column headers, a density toggle, and
  **Previous** / **Next** pagination buttons. Filters stay in the URL, so you can
  bookmark or share a filtered view. Most list screens also have **Export CSV**
  (Changes) or a **Refresh** button (System health).
- **Density: comfortable** — the button at the top right of most tables. Clicking it
  switches the table to compact rows; clicking again switches back. Your choice is
  remembered in the browser.
- **Retry** — appears when a list fails to load. Clicking it re-fetches the data.
  It does not change anything.

> **A quirk you will see:** some filter fields are restored from stored state on
> every visit. On the Changes list the Device filter repopulates with `leaf-01`, and
> on ZTP Devices the Site filter repopulates with `dc1`, even after you click
> **Clear filters** (the URL goes back to the clean route, but the field fills in
> again). This is a known UI bug, not data — the lists still show 0 items in a fresh
> system. See §22.

## 6. Login

**Route:** the login page is shown whenever you have no session; the browser address
may show `/approvals` (the page it was trying to reach) while the login form is
displayed.

**What you see:**

- Heading: **VireoNet**
- Text: "Sign in with your username and password. Your role is set by an admin."
- A **Username** field and a **Password** field.
- A **Sign in** button.

**How to use it:**

1. Type your username in **Username**.
2. Type your password in **Password**.
3. Click **Sign in** (or press Enter).

On success you are taken to the Changes list (`/changes`). On failure the page shows
"Invalid username or password." — this message is also shown when the login request
never reaches the API at all (for example a CORS or connection error), so if you are
sure the password is right, check §22 before resetting anything.

**Buttons on this screen:**

| Button | What it does |
|---|---|
| Sign in | Sends your username and password to `POST /auth/login`. On success it stores the token and opens the Changes list. On failure it shows "Invalid username or password." |

There is no "remember me", no password reset, and no sign-up on this page. New users
are created by an admin in IAM (§14).

## 7. Changes

**Route:** `/changes` — the home screen after login. Left-nav label: **Changes**.

This is the main working list: every change in the system, one row per change.

### Filters

| Field | What it does |
|---|---|
| State | Dropdown. `All states` by default; pick one state (for example `ci_passed`) to show only changes in that state. |
| Type | Dropdown. `All types` by default; pick one intent type (for example `vlan_add`). |
| Device | Text. Type a hostname (for example `leaf-01`) to show only changes touching that device. |
| From / To | Dates. Limit the list to changes started in that range. |
| Clear filters | Resets all filters. See the stored-filter quirk in §5: the Device field may repopulate with `leaf-01` after clearing. |

### The table

Columns: **Ticket, Type, Devices, State, Requester, Started, Duration.**

- Click a column header's sort button to sort by that column.
- Click a row to open that change's detail page (§8).
- Footer shows the count and the page, for example `0 changes` and `Page 1 of 1`.

### Empty state

On a fresh system you will see:

> "No changes match these filters." / "0 changes", "Page 1 of 1"

That is normal. A change only appears here after a ServiceNow request creates one
(in real mode) or after one is created through the API / fake data (in fake mode).
An empty list is not an error.

### Buttons on this screen

| Button | Who can use it | What it does |
|---|---|---|
| Density: comfortable | Anyone signed in | Toggles the table between comfortable and compact row spacing. Remembered in the browser. |
| Export CSV | Anyone signed in | Downloads the current (filtered) list as a CSV file. |
| Clear filters | Anyone signed in | Clears State, Type, Device, and From/To filters. |
| Sort buttons (per column) | Anyone signed in | Sort the table by that column. Click again to reverse. |
| Previous / Next | Anyone signed in | Move between pages. Disabled when there is only one page (or no results). |
| Retry | Anyone signed in | Shown only if loading the list fails. Re-fetches the list. |

### A failure to know about

Filtering by some values — observed with Device `leaf-02`, a blank space, or `-` —
fails with:

> "Failed to load changes: Failed to fetch" — plus a **Retry** button.

**Retry** re-runs the same (failing) filter. Clear the Device filter and the list
loads again. This is a known bug in the filter handling, not a sign that the API is
down: if the API were down, every filter would fail, including `leaf-01`.

## 8. Change detail

**Route:** `/changes/<id>` — opened by clicking a row on the Changes list, or from
the **Open full detail** link on the Approvals screen.

> **Not screenshotted during the walkthrough.** There were no changes in the system
> (0 changes, 0 approvals waiting), so there was no detail page to open. Everything
> in this section is verified from the frontend code (`frontend/src/pages/ChangeDetail.tsx`),
> not from a screenshot. The layout below is what you will see once a change exists.
> To get one, submit a change — see §20.1.

**What you see:**

- Heading: the ticket number (for example `RITM0001234`) and the change type, with a
  coloured state badge showing the current state.
- A line under it: Requester, Devices, Started.
- A **state timeline** showing the states the change has passed through.
- Action buttons (only the ones that are legal for the current state — see the table
  below). If no action is legal, the page says "No actions are legal for state …".
- Five tabs: **Checks**, **Diff**, **Batfish results**, **AWX job output**,
  **Audit trail**.

### Execution section

Above the tabs, an **Execution** section shows how the change actually ran,
derived from stored evidence only (nothing is invented when the evidence is
silent):

| Row | What it shows |
|---|---|
| Job type | `playbook` or `python`. Defaults to `playbook` when the change record stores no job type. |
| AWX job | The AWX job ID; a link opening the job in AWX in a new tab when an AWX base URL is configured. |
| Script | Script name and SHA (first 12 chars) when the job was a script job. |
| Reconciliation | `reconciled`, `reconciliation_pending`, or `—`. When pending, the drift record count is shown and the note says an approver override is required before close. |

Under it, a **Declared changes** table (Object, Identifier, Field, Old, New)
lists what the job declared it changed; when nothing was recorded the page
says "No declared changes recorded for this change."

### Action buttons

| Button | When it appears | Who can use it | What it does |
|---|---|---|---|
| Approve | State is `ci_passed` only | approver or above | Opens a confirmation dialog: "Approve RITM…. This moves the change to approved and allows pre-check and apply to run. It does not apply config yet." Confirming sends the approval signal. Success shows "Approval signal sent." |
| Abort | States `received`, `validated`, `planned`, `approved`, `pre_checked` — and `ci_passed` | operator or above | Opens a confirmation dialog: "Abort RITM…. This rejects the change and stops the workflow. No config will be applied." Confirming sends the abort signal. Success shows "Abort signal sent." |
| Roll back | States `applied`, `post_checked` | operator or above | Opens a confirmation dialog: "Roll back RITM…. This restores the pre-change backup via the AWX rollback playbook and moves the change to rolled_back. Applied config on completed waves will be reverted." Confirming requests the rollback. Success shows "Rollback requested." |

Every action button asks for confirmation first — nothing is applied by a single
click. If the action fails, the error is shown on the page.

### Tabs

| Tab | What it shows |
|---|---|
| Checks | Pre-check and post-check results: Check name, Status (pass/fail/skip), Message. Empty text: "No checks recorded yet. Pre-checks run after approval; post-checks run after apply." |
| Diff | The rendered config diff for the change (added/removed lines). |
| Batfish results | The Batfish gate output as JSON. Empty text: "No Batfish results yet. The Batfish gate runs in CI before approval." |
| AWX job output | AWX jobs for this change: Job ID, Status, Step. Empty text: "No AWX jobs recorded yet." A note under the table says full stdout is in AWX — the job IDs here link the change to the AWX job. |
| Audit trail | Every state transition with timestamp, actor, reason, and evidence. |

## 9. ZTP devices

**Route:** `/ztp`. Left-nav label: **ZTP devices**.

Devices being provisioned by zero-touch provisioning, one row per device.

### Filters

| Field | What it does |
|---|---|
| Hostname | Text. Filter by device hostname. |
| Serial | Text. Filter by serial number. |
| Stage | Dropdown. `All stages` by default. |
| Site | Dropdown. `dc1` is restored from stored state on every visit (see §5). |
| Platform | Dropdown. `All platforms` by default. |
| Clear filters | Resets all filters (subject to the stored-filter quirk in §5). |

### The table

Columns: **Serial, Hostname, Model, Site, NetBox status, ZTP stage, Last callback,
DHCP reservation, Actions.**

### Empty state

> "No ZTP devices match these filters." / "0 devices"

Normal on a fresh system: a device only appears here after it is registered
(see §20.4).

### Buttons on this screen

| Button | Who can use it | What it does |
|---|---|---|
| Density: comfortable | Anyone signed in | Toggles comfortable/compact row spacing. |
| Clear filters | Anyone signed in | Clears Hostname, Serial, Stage, Site, and Platform filters. |
| Sort buttons (per column) | Anyone signed in | Sort the table by that column. |
| Previous / Next | Anyone signed in | Move between pages. Disabled on a single page. |
| Retry | Anyone signed in | Shown only if loading fails. Re-fetches the list. |
| Retry (per device, in Actions) | operator or above | Re-runs the ZTP flow for that device. Disabled for viewers. Asks for confirmation first. |
| Reset (per device, in Actions) | operator or above | Resets that device's ZTP state so it can be provisioned again. Disabled for viewers. Asks for confirmation first. |

## 10. Approvals

**Route:** `/approvals`. Left-nav label: **Approvals**.

The queue of changes waiting for a human decision. This is the screen approvers
should check first each day.

### Tabs

| Tab | What it shows |
|---|---|
| Awaiting approval | Changes in state `ci_passed` — CI is green and they are waiting for approval. |
| Processed | Changes you (or others) have already approved or rejected. |

Footer: `0 changes waiting · 0 processed` on a fresh system.

### The table

Columns: **Ticket, Type, Devices, Requester, Waiting since, Evidence, Actions.**

- **Evidence** is an expandable row: click **Show** to see the change's evidence
  (checks, diff summary, Batfish result), **Hide** to close it again.
- Each row also has an **Open full detail** link to the change detail page (§8).

### Empty state

> "No changes waiting for approval."

Normal when nothing is in `ci_passed`. A change only lands here after it passes CI.

### Buttons on this screen

| Button | Who can use it | What it does |
|---|---|---|
| Density: comfortable | Anyone signed in | Toggles comfortable/compact row spacing. |
| Sort buttons (per column) | Anyone signed in | Sort the table by that column. |
| Previous / Next | Anyone signed in | Move between pages. Disabled on a single page. |
| Retry | Anyone signed in | Shown only if loading fails. Re-fetches the list. |
| Show / Hide (Evidence) | Anyone signed in | Expands/collapses the evidence row for that change. |
| Approve (per row) | approver or above | Opens a confirmation dialog (with a reason field). Confirming sends the approval signal and moves the change out of the queue. Disabled for viewers/operators. |
| Reject (per row) | approver or above | Opens a confirmation dialog (with a reason field). Confirming rejects the change. Disabled for viewers/operators. |

## 11. Locks and change windows

**Route:** `/locks`. Left-nav label: **Locks and change windows**.

> One page, two sections. The left nav lists this as a single item, and the page
> shows both the device locks and the change windows together.

### Device locks section

A lock stops two changes touching the same device at the same time. Locks are taken
automatically by the workflow before apply and released afterwards; a lock that is
still held means a change is in progress on that device, or a change crashed while
holding it.

Columns: **Device, Ticket, Holder, Acquired, Expires, TTL remaining, Actions.**

Text under the heading: "Refreshed every 30 seconds." — the list updates itself; you
do not need to refresh the page.

### Empty state

> "No device locks held." / "0 locks held."

### Change windows section

Change windows are the allowed times for changes (from `policies/default.yaml` —
by default Monday–Friday 01:00–05:00 UTC, with an emergency bypass for emergencies
only). The table columns are **Days, Start, End, Timezone, In window now, Next
window, Emergency bypass.**

### Empty state

> "No change windows configured."

This means no windows are returned by the API on this system — check the policy
file and the API if you expected windows to be listed.

### Buttons on this screen

| Button | Who can use it | What it does |
|---|---|---|
| Density: comfortable | Anyone signed in | Toggles comfortable/compact row spacing (both sections). |
| Sort buttons (per column) | Anyone signed in | Sort that section's table by that column. |
| Retry (locks) | Anyone signed in | Shown only if loading locks fails. Re-fetches the locks. |
| Retry (change windows) | Anyone signed in | Shown only if loading windows fails. Re-fetches the windows. |
| Release (per lock, in Actions) | operator or above | Releases that device's lock immediately. Disabled for viewers. Asks for confirmation first. Only use this if you are sure the change that held the lock is finished or dead — releasing a lock while a change is applying can let two changes touch the same device at once. |

There are **no buttons in the Change windows section** — windows are configured in
the policy file, not from the UI.

## 12. System health

**Route:** `/health`. Left-nav label: **System health**.

Four tables: VireoNet's own components, AWX and its Execution Environment,
the lab backends, and the adapters. Check this screen first
whenever anything looks wrong.

### Components table

Columns: **Component, Status, Detail.**

Observed on the live walkthrough (fake mode):

| Component | Status | Detail |
|---|---|---|
| api | ok | "FastAPI responding" |
| database | unknown | "session factory not configured" |
| temporal | ok | "client configured" |

Overall: **"Overall: ok"**.

> **Read this carefully:** the database row says `unknown` while the overall status
> says `ok`. `unknown` means the health endpoint could not determine the database
> state from this code path — it does **not** mean the database is down (changes and
> users were being read and written fine on the same system). The overall status is
> `ok` because the API and Temporal are up. If the database really were down, writes
> would return 503 and the Changes list would fail to load. Treat `database: unknown`
> as "not verified by this check", not as a fault — but if you also see failed writes,
> check Postgres first.

### AWX and Execution Environment table

Columns: **Item, Status, Detail.**

| Item | Status | Detail |
|---|---|---|
| awx | `ok` / `down` / `not_configured` | AWX reachable, or why not. |
| execution environment: VireoNet EE | `unknown` | The EE name comes from `VIREONET_AWX_EXECUTION_ENVIRONMENT` (default `VireoNet EE`). The status is honestly `unknown`: the AWX API exposes no call that confirms the EE is registered on the job templates, so VireoNet never claims it is registered. Verify in AWX (Job Templates → Execution Environment). Device sessions run only inside this Execution Environment. |

### Lab backends table

Columns: **Backend, Status, Capabilities, Detail.** One row per lab backend
enabled by `VIREONET_LAB_BACKEND` (`eveng` | `containerlab` | `both`,
default `eveng`): **EVE-NG** and/or **Containerlab**, each with its
`health_check` status and capabilities. To change which backends are enabled,
set `VIREONET_LAB_BACKEND` and restart the API; in real mode the API refuses
to start with an unknown backend, or with `containerlab` selected but
`VIREONET_CONTAINERLAB_HOST` / `VIREONET_CONTAINERLAB_USER` unset. In fake
mode both backends report `ok` with fake capabilities.

### Adapters table

Columns: **Adapter, Status, Latency, Last success, Capabilities** (headings vary
slightly by build; the observed rows are below).

Observed on the live walkthrough (fake mode): all 11 adapters — servicenow, netbox,
awx, gitlab, batfish, vault, eveng, containerlab, dhcp, notification, monitoring — status **ok**,
latency **—**, last success **never**.

In fake mode every adapter reports `ok` because there is nothing real to fail, and
`last success: never` because no real call has ever been made. In real mode, a
`down` adapter here is the first sign that a system is unreachable — check its base
URL and credentials (see `docs/ADAPTERS.md`).

### Drift scans table

Columns: **Site, Status, Started, Scanned, With drift, Failed** — one
row per site with the last drift scan. Above the table: the count of
open drift records. Below it: the never-scanned devices (best-effort —
see §17). When no scan has run yet the page says "No drift scan has run
yet." rather than showing an empty table.

### Buttons on this screen

| Button | Who can use it | What it does |
|---|---|---|
| Density: comfortable | Anyone signed in | Toggles comfortable/compact row spacing. |
| Refresh | Anyone signed in | Re-fetches both the components and the adapters tables now. |
| Sort buttons (per column) | Anyone signed in | Sort that table by that column. |
| Retry (per section) | Anyone signed in | Shown only if that section fails to load. Re-fetches it. |

## 13. Adapters

**Route:** `/adapters`. Left-nav label: **Adapters**.

A read-only view of how every adapter is configured. You cannot change anything
from this screen — to change an adapter, update its config or Vault entry and
restart the API and worker.

Intro text on the page: "To change an adapter, update its config or Vault entry and
restart the API and worker. Using Gitea? Set it up here: **Gitea setup**." — the
**Gitea setup** text is a link to `/setup/gitea` (§18).

### The table

> "11 adapters configured."

Columns: **Adapter, Enabled, Mode, Endpoint, Timeout, Retries, Rate limit,
Capabilities.**

Observed on the live walkthrough (fake mode), for all 11 adapters (servicenow,
netbox, awx, gitlab, batfish, vault, eveng, containerlab, dhcp, notification, monitoring):

| Field | Value |
|---|---|
| Enabled | yes |
| Mode | fake |
| Endpoint | fake://localhost |
| Timeout | 10s |
| Retries | — |
| Rate limit | 10/s |

In real mode the Endpoint column shows the real base URL (for example the NetBox
URL) and Mode shows `real`. If an adapter you configured still shows `fake`, the
API was started with `VIREONET_ADAPTER_MODE=fake` — change the setting and restart.

### Buttons on this screen

| Button | Who can use it | What it does |
|---|---|---|
| Density: comfortable | Anyone signed in | Toggles comfortable/compact row spacing. |
| Sort buttons (per column) | Anyone signed in | Sort the table by that column. |
| Retry | Anyone signed in | Shown only if loading fails. Re-fetches the list. |
| Gitea setup (link) | Anyone signed in | Opens the Gitea setup page (§18). |

For what each adapter connects to, what credentials it needs, and how to verify it,
see `docs/ADAPTERS.md`.

## 14. IAM

**Route:** `/iam`. Left-nav label: **IAM**. Admin only.

If you are not an admin, the page shows only:

> "Admin role is required to manage users and roles."

### Tabs

| Tab | What it shows |
|---|---|
| Users | Create-user form and the users table. |
| Roles | The permissions matrix: which roles hold which permissions. |

### Users tab

**Create user form** — fields: **Username**, **Display name**, **Password**,
**Role** (dropdown, default `viewer`). The password must be at least 12 characters.

**Users table** — columns: **Username, Display name, Role, Active, Actions.**

Observed: one user, `admin` — role `admin`, active `yes`.

**Buttons on this tab:**

| Button | What it does |
|---|---|
| Create user | Creates the user with the form's values. On success the page shows "Created user `<username>` with role `<role>`." and the form clears. On failure the error is shown on the page. |
| Role dropdown (per user, in the table) | Changes that user's role immediately. The change applies to the next sign-in; an existing token keeps working until it expires, but deactivation (below) is checked on every request. |
| Deactivate / Activate (per user, in Actions) | Toggles the user active. `Deactivate` on an active user deactivates them immediately — they cannot use the API even with a token issued before. The button then reads `Activate`. You cannot deactivate yourself, and you cannot deactivate the last active admin — the API blocks both. |

### Roles tab

A read-only matrix. Rows are permissions, columns are the four roles
(viewer, operator, approver, admin), cells say `yes` or `—`. The permissions are:

`adapters:read`, `approvals:approve`, `approvals:read`, `changes:create`,
`changes:read`, `changes:rollback`, `changes:signal`, `health:read`, `iam:manage`,
`locks:read`, `locks:release`, `ztp:read`, `ztp:reset`, `ztp:retry`.

There are **no action buttons on the Roles tab** — roles and their permissions are
fixed in code. If you need a permission a role does not have, give the user a
higher role.

## 15. Scripts

**Route:** `/scripts`. Left-nav label: **Scripts**. Admin only — the nav entry
is hidden for non-admins, and the page itself says "Admin role is required to
view the script manifest" if you open the route directly.

**What you see:** a read-only table of the script manifest
(`policies/scripts.yaml`) — the custom scripts that may run, at exactly the
listed SHA, for the listed change types and platforms.

Columns: **Name, SHA, Allowed change types, Platforms, Writes scope, Last run.**

- **SHA** shows the first 12 characters; hover to see the full SHA.
- **Last run** is the most recent run recorded in the audit log, or `never`
  when no run is recorded there.
- There are **no edit buttons** on this page. The manifest is edited by merge
  request only — see `docs/SCRIPTS.md` for the contract a script must meet
  (stdin/stdout shape, dry-run hash approval, declared changes).

If the table fails to load, a **Retry** button re-fetches it.

## 16. Config version control

**Route:** `/config`. Left-nav label: **Config repos**.

How VireoNet stores configuration in Git — three stores, never mixed, plus the
NetBox Jinja export:

| Store | Repo | Contents |
|---|---|---|
| Templates | `netops/templates` | Jinja2 templates only. No device values, IPs, or secrets. |
| Variables | `netops/vars` | YAML variables only. No Jinja delimiters. |
| Per-device config | `netops/config-<hostname>` | Rendered config, candidate config, backup, a `vars.yaml` snapshot, and `metadata.yaml`. One repo per device. |
| NetBox Jinja export | `netops/netbox-export` | Jinja exported read-only from NetBox. Never a render source. |

### What you see

- Heading: **Config version control**.
- A short explanation of the three stores above.
- A **Filter by hostname or repo** text box (observed pre-filled with `leaf-01` —
  the same stored-state behaviour as §5).
- A table: **Hostname, Repo**. Observed: 2 repos listed (`leaf-01`, `leaf-02`),
  both showing **"No commits yet"**.

### Buttons on this screen

There are **no action buttons** on this screen — it is a read-only view of the
repos and their history. The only control is the filter text box, and **Retry** if
loading fails.

Device repos must already exist in Git — VireoNet does not create them. History
(`history()`) returns an empty list in the current build, which is why every repo
shows "No commits yet" even if it has commits. See `docs/ADAPTERS.md` and
`README.md` for the known limitations.

## 17. Drift

**Route:** `/drift` (list), `/drift/:id` (detail), `/exceptions` (active
exceptions). Left-nav labels: **Drift**, **Exceptions**. Full reference:
[`docs/DRIFT.md`](docs/DRIFT.md).

Drift is the difference between a device's running config and what the
system of record says it should be. Four kinds: **unmanaged** (running
hash vs golden baseline), **intent** (rendered intent vs running),
**state** (NetBox vs collected facts), **compliance** (rules). States:
`detected → under_review → reverting | merge_pending | exception_active
| resolved`; an expired exception goes
`exception_active → exception_expired → under_review`.

### Drift list

- Heading: **Drift**. A table: **Device, Kind, Severity, Status, First
  seen, Last seen, Owner, Age**. Severity is a coloured badge
  (critical / high / medium / low).
- Filters (URL-synced): **Device** (text), **Site** (text), **Kind**,
  **Severity**, **Status** dropdowns. **Start scan** starts a drift scan
  for the filtered site and reports the workflow id (or queued scan id).
- **Export CSV** downloads the filtered records. **Previous** / **Next**
  page through them. Click a row (or press Enter on it) to open the
  record. Empty state: "No drift records match these filters."

### Drift detail

- Heading: **Drift \<device\>**, with the severity badge. A **Status
  timeline** lists every state move (from → to, actor, when, reason).
- Tabs: **Diff vs baseline** (the stored diff, with baseline and running
  hashes), **Diff vs intent**, **State findings**, **Compliance**,
  **Batfish impact**, **Attribution** (who made the change, when —
  best-effort; "unknown" when the scan could not prove it), and
  **Decisions / Audit**. Tabs with no stored data say so honestly
  instead of inventing content — for example, Batfish impact is only
  shown when the scan attached it.
- **Resolve this drift** (approver/admin only): fill in **Reason
  (required)** and **ServiceNow reference (required)** — the buttons
  stay disabled until both are filled — then:
  - **Roll back**: starts a normal change restoring the baseline
    config (backup, pre-check, dry-run, apply, post-check; automatic
    rollback on failure). Resolves only when a re-collect confirms the
    running config matches the baseline.
  - **Merge**: creates a `drift/<device>/<record>` branch and a merge
    request with a per-device vars override. Never touches a shared
    template. Stays merge-pending until the MR is merged (baseline
    updated) or closed unmerged (back to under review).
  - **Grant exception**: keeps the config and ignores that exact diff
    until the expiry. Needs **Exception owner** and **Exception
    expiry** as well; the button stays disabled until those are filled.
  Each action opens a confirm dialog stating exactly what will happen.
  Whoever made the manual change cannot approve their own exception or
  merge (the API returns 403). Operators can also **emergency-accept**
  a drift from the API (not a UI button) with a retrospective deadline.
- **Exceptions** (`/exceptions`): active exceptions only, with an
  expiry countdown ("2d 3h left", or "expired"). **Open** jumps to the
  drift record.

### System health — drift scans

System health (§12) has a **Drift scans** section: one row per site
with the last scan's status, start time, devices scanned / with drift /
failed, the count of open drift records, and the never-scanned devices
(best-effort: scans are per site, so "scanned" means "has at least one
drift record"; when NetBox is unavailable the list is empty and the page
says so).

## 18. Gitea setup

**Route:** `/setup/gitea`. **There is no left-nav entry.** Reach it from the
**Gitea setup** link on the Adapters page (§13).

**What you see:**

- Heading: **Gitea setup**.
- Intro: "Install Gitea on the same host as the VireoNet backend, or use the automatic
  installer below." (The page text reads "Install Gitea and connect it to VireoNet.
  Choose how: follow the manual steps yourself, or let VireoNet install and integrate
  it automatically.")
- Two cards side by side: **Manual install** and **Automatic install**.

### Buttons on this screen

| Button | What it does |
|---|---|
| Show manual steps | Opens the manual install view: a numbered list of exact steps with copyable commands. You run Docker Compose, create the admin and repo, and store the token in Vault yourself. A **Back** button returns to the two cards. |
| Install automatically | Opens the automatic install form (see below). |
| Back | Returns from either the manual or the automatic view to the two cards. |
| Install Gitea automatically | (In the automatic form.) Opens a confirmation dialog: "Install Gitea with Docker Compose at `<url>`, create admin `<user>` and repo `<repo>`, create an API token and store it in Vault. This starts containers on this host." Confirming starts the installer. |

### Automatic install form

Fields: **Admin username**, **Repo (owner/repo)**, **Gitea URL**.

Note under the form: "Requires Docker on this host, and GITEA_ADMIN_PASSWORD +
GITEA_DB_PASSWORD set in the VireoNet environment (12+ chars each). The token is
stored in Vault, never shown here."

After starting, the page shows `Status: <status>` and a per-step list
(`<step>: <status> — <detail>`). When it finishes it says `Done. Restart the
VireoNet API + worker with VIREONET_GIT_PROVIDER=gitea, then check System health
for the git adapter.` If it fails, it shows `Failed: <error>`.

Neither button was clicked during the walkthrough, so the installer itself was not
run — the descriptions above are verified from the page code.

After Gitea is installed, point VireoNet at it with:

```bash
VIREONET_GIT_PROVIDER=gitea
VIREONET_GIT_BASE_URL=http://localhost:3000
VIREONET_GIT_REPO=netops/intent
```

then restart the API and worker. See `docs/ADAPTERS.md` (Git — Gitea) for the full
procedure, and the manual/auto choice in `README.md`.

## 19. Style reference

**Route:** `/style`. Left-nav label: **Style reference**.

A reference page showing the UI's design tokens — colours, type sizes, buttons, and
badges — so that new screens match the existing ones. It is a reference for
developers, not something operators need day to day. It was not captured in the
walkthrough; its contents are the design tokens only and it has no actions that
change anything.

## 20. Daily workflows

### 20.1 Submit a change

1. Create the request in ServiceNow (a RITM). The ServiceNow webhook calls
   `POST /changes` on VireoNet.
2. VireoNet validates the request against NetBox and the policy
   (`policies/default.yaml`: at most 10 devices, protected devices and VLANs, change
   window). An invalid request is rejected with a reason; nothing is created.
3. The Temporal workflow starts (Workflow ID = the ticket number, so re-posting the
   same ticket is a no-op). The change appears on the Changes list (§7) in state
   `received`, then `validated`, then `planned`.
4. The workflow opens a Git MR (GitLab/Gitea/GitHub/Bitbucket, per
   `VIREONET_GIT_PROVIDER`). CI runs lint, render, Batfish, and optionally EVE-NG.
   Merge only when the pipeline is green. A failed pipeline puts the change in
   `ci_failed` — fix the intent, push a new commit, do not merge a failing pipeline.
5. When CI passes the change is `ci_passed` and appears on the Approvals screen
   (§10).

### 20.2 Approve a change

1. Open **Approvals**. Find the change in **Awaiting approval**.
2. Click **Show** under Evidence to review the checks, diff, and Batfish result.
   Click **Open full detail** to see all five tabs (§8) before deciding.
3. Click **Approve** (or **Reject**). A confirmation dialog asks for a reason.
4. The change moves to `approved`. The workflow then runs pre-checks, and — if they
   pass — applies the change in waves (default wave size 2) with a dry-run diff
   first. Only an explicitly approved change is ever applied for real.

### 20.3 Handle a failed check

| Failure | What it means | What to do |
|---|---|---|
| `ci_failed` | CI (lint, render, Batfish, or EVE-NG) failed. | Fix the intent, push a new commit. Do not merge. |
| `pre_check_failed` | A pre-check failed (device unreachable, lock held, no backup, neighbour baseline changed). | Read the Checks tab. If it says `device X is locked by RITM…`, wait or check Locks (§11). Fix the cause, then submit a new change. |
| Post-check failed / wave abort | A post-check failed after apply. | The workflow rolls back automatically from the pre-change backup (AWX `rollback` playbook) and the state becomes `rolled_back`. No human action is needed. Check the AWX job output on the detail page, fix the cause, submit a new change. |

### 20.4 Roll back a change manually

On the change detail page (§8), while the change is `applied` or `post_checked`,
click **Roll back** and confirm. This restores the pre-change backup via the AWX
rollback playbook and moves the change to `rolled_back`. Applied config on
completed waves is reverted. You can also **Abort** a change before apply
(`received` through `pre_checked`, or `ci_passed`) — that rejects it and stops the
workflow, and no config is applied.

### 20.5 Register and bootstrap a ZTP device

1. Register the device in NetBox (serial, model, platform, site).
2. Call `POST /ztp/devices` (API — there is no register button in the UI). It
   returns a one-time token, shown once. The token is stored hashed only and
   expires after 15 minutes; it is single-use.
3. The device boots via DHCP (option 66/67, option 43 for PnP) and calls
   `POST /ztp/callback` with the token. A bad token returns 401.
4. Identity is checked (serial/model/platform vs NetBox) before day-1. Day-1 is
   rendered, Batfish-gated, and pushed by AWX; the device appears on the ZTP
   Devices screen (§9) and ends up `active` in NetBox.
5. If a device is stuck, use **Retry** or **Reset** in its row on the ZTP Devices
   screen (operator or above).

### 20.6 Manage users and roles

Admin only, in **IAM** (§14): fill in the Create user form and click **Create user**;
change a role with the dropdown in the users table; deactivate a user with
**Deactivate**. You cannot deactivate yourself or the last active admin.

### 20.7 Set up Gitea

See §18. Either follow **Show manual steps** or fill in the automatic form and click
**Install Gitea automatically**, then restart the API and worker with
`VIREONET_GIT_PROVIDER=gitea`.

### 20.8 Check config repos

Open **Config repos** (§16) to see which per-device repos exist and filter by
hostname. Remember: this screen is read-only and currently shows "No commits yet"
for every repo — that is the unimplemented history function, not proof that the
repos are empty.

## 21. Checking health

Every day, or whenever something looks wrong:

1. **System health** (§12): components should be `ok`; adapters should be `ok`.
   In real mode, a `down` adapter is the first sign a system is unreachable.
   Remember `database: unknown` with `Overall: ok` means "not verified by this
   check", not "the database is down" (§12).
2. **Top bar**: `Adapters: ok` on every screen. `Adapters down: N` means N adapters
   are failing — open System health to see which.
3. **Temporal UI** (`http://localhost:8233`): find the workflow by ticket number
   (the Workflow ID) and see exactly which step it is on or where it failed.
4. **Logs**: `docker compose logs -f api worker` — JSON logs, ticket number as the
   correlation ID, no secrets.
5. **Metrics**: `GET /metrics` — `vireonet_changes_total`,
   `vireonet_adapter_calls_total`, `vireonet_check_failures_total`,
   `vireonet_device_locks_held`, `vireonet_ztp_total`.

## 22. Troubleshooting and FAQ

### I cannot log in — "Invalid username or password."

- Check the password first: the seed script only creates the admin once. If it said
  "Users already exist", the password is the one used in the first successful seed.
- **The UI and curl can disagree.** If `curl -X POST http://localhost:8000/auth/login`
  returns a token but the UI still fails, the browser cannot reach the API — see the
  next two items. The login page shows the same "Invalid username or password."
  message for a request that never reached the API, so do not reset the password
  until you have checked them.

### The browser shows a CORS error / the login request fails with status 400

The UI (port 5173) and the API (port 8000) are different origins, so the browser
blocks the login unless the API allows that origin. The API's allowed origins come
from `VIREONET_CORS_ORIGINS`, passed into the API container by `docker-compose.yml`
(default: `http://localhost:5173,http://127.0.0.1:5173,http://10.228.197.24:5173`).
If you open the UI from another address, add that origin and rebuild:
`docker compose up --build -d`. Verify with a preflight check from the host:

```bash
curl -i -X OPTIONS http://localhost:8000/auth/login \
  -H "Origin: http://10.228.197.24:5173" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: content-type" | grep -i access-control-allow-origin
```

It must print `access-control-allow-origin: http://10.228.197.24:5173`.

### The login request fails with `ERR_CONNECTION_REFUSED`

The frontend is calling the API at `http://localhost:8000`, but your browser is on a
different machine from Docker — `localhost` on your machine points at your machine,
where nothing listens. Rebuild the frontend with the host's LAN address
(`VITE_API_URL=http://10.228.197.24:8000 docker compose up --build -d`) and open
the UI at `http://10.228.197.24:5173`. Check the API is reachable from your browser
first: open `http://10.228.197.24:8000/healthz` — it should return `{"status":"ok"}`.
If that also refuses, the host firewall is blocking port 8000.

### The screen is blank white

A cached or broken frontend bundle. Hard refresh (Ctrl+Shift+R). If it persists,
open the browser console (F12) and look for the red error — the known cause in this
build was the production bundle importing the test-only `test-setup` module; that
is fixed in the current build, so a blank screen on the current build means the
browser is still serving the old bundle.

### The lists show data but I have connected nothing / System health says ok with nothing present

You are in fake mode (`VIREONET_ADAPTER_MODE=fake`, the compose default). The data
is deterministic demo data and every fake adapter reports `ok`. Switch to real mode
and configure the adapters (`docs/ADAPTERS.md`); then the lists start empty and
health reflects the real systems.

### The lists are empty in real mode

That is expected. Changes appear only when ServiceNow creates one, ZTP devices only
when a device registers, approvals only when a change reaches `ci_passed`. An empty
list is not an error.

### Filtering by leaf-02, a blank space, or "-" fails with "Failed to load changes: Failed to fetch"

Known bug in the Changes filter handling. Click **Retry** only re-runs the same
failing filter — clear the Device filter instead and the list loads again.

### "Clear filters" does not clear the Device / Site field

Known stored-state quirk (§5). The URL returns to the clean route but the field
repopulates (`leaf-01` on Changes, `dc1` on ZTP Devices). The lists still show 0
items; it is a display bug, not data.

### System health shows database "unknown" but Overall "ok"

See §12. `unknown` means the health endpoint could not determine the database state
from that code path. It is not a fault on its own. If writes also fail (503), check
Postgres.

### Config repos all say "No commits yet"

Known limitation: the history function is not implemented and returns an empty
list. It does not mean the repos are empty.

### A device lock will not clear

Check **Locks and change windows** (§11). A lock held by a finished or crashed
change can be released with **Release** (operator or above, with confirmation).
Expired locks are stealable by the next change. Do not release a lock while a
change is actively applying on that device.

### The API/worker container keeps exiting

Check `docker compose logs api --tail 40`. The known causes in this build were:
the Alembic files not being in the image, the `alembic.ini` logging sections being
missing, and the Temporal image tag / DB driver settings. All are fixed in the
current build; a new exit means a new error — paste the log.

## 23. Glossary

| Term | Meaning |
|---|---|
| AWX | The Ansible AWX server that runs the playbooks. The only component that touches network devices. |
| Batfish | Offline network analysis run in GitLab CI. The Batfish gate must pass before a change can be approved. |
| Change | One requested network change, tracked by its ServiceNow ticket number. |
| Change window | The allowed time for changes (default Mon–Fri 01:00–05:00 UTC). |
| CI | Continuous integration in GitLab/Gitea: lint, render, Batfish, optional EVE-NG. |
| Dry-run | Rendering and diffing a change without applying it. The default for every apply step. |
| EVE-NG | Lab environment used for optional CI testing of a change. |
| Fake mode | Adapter mode where every external system is simulated with fixed demo data. |
| Git provider | The Git system used for intent and config repos: GitLab, GitHub, Bitbucket, or Gitea. |
| IAM | Identity and access management — users, roles, and permissions (§14). |
| Intent | A declarative description of the desired change (for example `vlan_add`). |
| Lock | A per-device lock that stops two changes touching the same device at once. |
| MR | Merge request (GitLab/Gitea) / pull request (GitHub/Bitbucket). The approved change. |
| NetBox | Source of truth for devices, IPs, and VLANs. |
| PnP / POAP | Cisco IOS-XE Plug and Play / Nexus Power-On Auto Provisioning — day-0 bootstrap methods. |
| Post-check | Checks run after apply (reachability, config matches intent, no unexpected diff, neighbour baseline). |
| Pre-check | Checks run before apply (reachability, no active lock, interface state, backup exists, neighbour baseline). |
| RITM | ServiceNow request item number (for example `RITM0001234`). Also the Temporal Workflow ID. |
| Rollback | Restoring the pre-change backup via the AWX rollback playbook. Automatic on post-check failure. |
| ServiceNow | The request/approval system (RITM/CHG). |
| Temporal | The durable workflow engine. Workflow ID = ticket number, so duplicate webhooks are harmless. |
| Vault | Where all secrets live. No credentials in code, Git, or logs. |
| Wave | A batch of devices applied together (default size 2). A failed wave aborts the change. |
| ZTP | Zero-touch provisioning — a new device bootstrapping itself with no manual login. |

---

*VireoNet — User Manual. Companion documents: `README.md`, `docs/ADAPTERS.md`,
`docs/runbook.md`, `docs/SDLC.md`.*
