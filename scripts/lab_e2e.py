#!/usr/bin/env python3
"""End-to-end lab test for VireoNet (milestone F5).

Flow A — VLAN add through the real change pipeline (create -> CI/Batfish
-> approve -> apply -> post-check -> closed). Flow B — ZTP register ->
callback -> identity -> day-1 -> active on a simulated device. Flow C
(drift, D6) — manual change on leaf-01 -> scan detects drift ->
rollback resolves it -> change again -> merge (MR created, merged,
baseline updated) -> change a third time -> exception (ignored until
expiry, then a further change creates a new record) -> the Flow A
change reconciles into Git and NetBox.

The lab backend is selected with --backend eveng|containerlab, default
from VIREONET_LAB_BACKEND (default eveng), built through the registry
(lab_adapter_for, the same call scripts/lab_smoke.py uses), so the same
script covers both backends. The topology default follows the backend
(.unl for EVE-NG, .clab.yml for Containerlab).

Real systems only: VIREONET_ADAPTER_MODE must be "real". Fake mode is
refused with exit code 2. The EVE-NG lab is always wiped in a finally
unless --no-cleanup is given. Never run this against production: Flow A
creates a real change (ticket RITM-LAB-001) and a real VLAN 100 on
leaf-01/leaf-02, and Flow B registers a real ZTP device (leaf-99).

Adaptations vs the original F5 brief, verified against the repo:
- GET /adapters/health carries no "mode" field (AdapterHealth in
  src/vireonet/adapters/base.py), so the prereq stage checks the mode via
  GET /adapters/settings (one "mode" per adapter) and liveness via
  GET /adapters/health.
- The ZTP reservation endpoints are F2's, and they exist:
  POST /ztp/devices/{serial}/reservation (202, idempotent — returns
  already_created=True if the workflow already created it) and
  DELETE /ztp/devices/{serial}/reservation (requires removed=True, legal
  only once the device is active).
- There is no separate token endpoint and no POST verify-identity
  endpoint: POST /ztp/devices returns the one-time token, and identity is
  checked by the workflow's ztp_verify_identity activity, so the script
  polls the device stage (identity_verified, then active).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vireonet.adapters.lab import LabAdapter  # noqa: E402
from vireonet.adapters.registry import lab_adapter_for  # noqa: E402
from vireonet.config import get_settings  # noqa: E402

TERMINAL_CHANGE_FAILURES = {"rejected", "ci_failed", "pre_check_failed", "failed", "rolled_back"}
TERMINAL_ZTP_FAILURES = {"failed", "identity_failed", "day1_failed", "callback_timeout"}

VLAN_INTENT = {
    "change_type": "vlan_add",
    "vlan_id": 100,
    "vlan_name": "web",
    "devices": ["leaf-01", "leaf-02"],
    "interfaces": ["GigabitEthernet0/1"],
    "site": "dc1",
    "ticket_number": "RITM-LAB-001",
    "requested_by": "lab-e2e",
}

DRIFT_TICKET = "RITM-LAB-DRIFT"

# Manual change applied straight to the device CLI (Flow C). IOS CLI
# text, sent to the node console on EVE-NG (telnet) and via
# `containerlab exec` on Containerlab. UNCERTAIN on Containerlab:
# whether the exec channel reaches the cisco_iol CLI directly depends
# on the node's exec path; if the stage fails there, run the same
# commands on the node console by hand and re-run with --only drift.
DRIFT_CHANGE_CMD = (
    "configure terminal\ninterface GigabitEthernet0/1\ndescription DRIFT-LAB\nend\nwrite memory\n"
)
DRIFT_CHANGE_CMD_2 = (
    "configure terminal\ninterface GigabitEthernet0/1\ndescription DRIFT-LAB-2\nend\nwrite memory\n"
)


ZTP_DEVICE = {
    "hostname": "leaf-99",
    "serial": "FOC9999A",
    "mac_address": "aa:bb:cc:dd:ee:99",
    "model": "C9300-48P",
    "platform": "ios-xe",
    "site": "dc1",
}


@dataclass
class StageResult:
    name: str
    passed: bool
    detail: str


@dataclass
class Runner:
    api_url: str
    token: str
    lab_name: str
    adapter_mode: str = "real"
    poll_seconds: float = 5.0
    vlan_change_id: str | None = None
    results: list[StageResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def total(self) -> int:
        return len(self.results)

    def stage(self, name: str, passed: bool, detail: str) -> bool:
        """Record a stage result and print it. Returns `passed`."""
        self.results.append(StageResult(name=name, passed=passed, detail=detail))
        print(f"{'PASS' if passed else 'FAIL'} {name}: {detail}", flush=True)
        return passed

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    async def api(self, method: str, path: str, *, json: dict | None = None) -> httpx.Response:
        async with httpx.AsyncClient(
            base_url=self.api_url, headers=self._headers(), timeout=30.0
        ) as client:
            return await client.request(method, path, json=json)

    async def wait_for_state(self, change_id: str, want: set[str], *, timeout: float = 600) -> str:
        """Poll GET /changes/{id} until the state is in `want`.

        Returns the state, or "timeout". Stops early on terminal failure
        states (rejected, ci_failed, pre_check_failed, failed, rolled_back).
        """
        deadline = time.monotonic() + timeout
        while True:
            resp = await self.api("GET", f"/changes/{change_id}")
            state = resp.json().get("state", "") if resp.status_code == 200 else ""
            if state in want or state in TERMINAL_CHANGE_FAILURES:
                return state
            if time.monotonic() > deadline:
                return "timeout"
            await asyncio.sleep(self.poll_seconds)

    async def wait_for_ztp_stage(self, serial: str, want: set[str], *, timeout: float = 600) -> str:
        """Poll GET /ztp/devices/{serial} until the stage is in `want`.

        Returns the stage, or "timeout". Stops early on terminal failure
        stages (failed, identity_failed, day1_failed, callback_timeout).
        """
        deadline = time.monotonic() + timeout
        while True:
            resp = await self.api("GET", f"/ztp/devices/{serial}")
            stage = resp.json().get("stage", "") if resp.status_code == 200 else ""
            if stage in want or stage in TERMINAL_ZTP_FAILURES:
                return stage
            if time.monotonic() > deadline:
                return "timeout"
            await asyncio.sleep(self.poll_seconds)

    async def _prereq(self) -> bool:
        """All adapters real and healthy. Fake/recorded mode is refused."""
        if self.adapter_mode != "real":
            return self.stage("prereq", False, f"adapter_mode is {self.adapter_mode!r}, not 'real'")
        settings_resp = await self.api("GET", "/adapters/settings")
        if settings_resp.status_code != 200:
            return self.stage(
                "prereq", False, f"GET /adapters/settings -> {settings_resp.status_code}"
            )
        modes = {a.get("name", "?"): a.get("mode", "?") for a in settings_resp.json()}
        non_real = {n: m for n, m in modes.items() if m != "real"}
        if not modes or non_real:
            return self.stage("prereq", False, f"adapters not all real: {modes}")
        health_resp = await self.api("GET", "/adapters/health")
        if health_resp.status_code != 200:
            return self.stage("prereq", False, f"GET /adapters/health -> {health_resp.status_code}")
        down = [
            a.get("name", "?")
            for a in health_resp.json().get("adapters", [])
            if a.get("status") != "ok"
        ]
        if down:
            return self.stage("prereq", False, f"adapters down: {down}")
        return self.stage("prereq", True, f"all {len(modes)} adapters real and ok")

    async def run_vlan(self) -> bool:
        """Flow A: VLAN 100 add on leaf-01/leaf-02 through the change pipeline."""
        if not await self._prereq():
            return False
        resp = await self.api("POST", "/changes", json={"intent": VLAN_INTENT})
        if resp.status_code != 202:
            return self.stage(
                "change-create", False, f"POST /changes -> {resp.status_code}: {resp.text[:200]}"
            )
        change_id = str(resp.json().get("id", ""))
        self.vlan_change_id = change_id
        self.stage("change-create", True, f"RITM-LAB-001 -> {change_id}")
        state = await self.wait_for_state(change_id, {"ci_passed"}, timeout=600)
        if not self.stage("ci-batfish", state == "ci_passed", f"state={state}"):
            return False
        detail = await self.api("GET", f"/changes/{change_id}/detail")
        adds = (
            [d for d in detail.json().get("diff", []) if d.get("type") == "add"]
            if detail.status_code == 200
            else []
        )
        if not self.stage("backup-diff", len(adds) > 0, f"{len(adds)} diff adds"):
            return False
        resp = await self.api(
            "POST",
            f"/changes/{change_id}/signals/approval",
            json={"approved": True, "reason": "lab-e2e"},
        )
        if not self.stage(
            "approve", resp.status_code == 202, f"POST approval -> {resp.status_code}"
        ):
            return False
        state = await self.wait_for_state(
            change_id, {"applied", "post_checked", "closed"}, timeout=900
        )
        if not self.stage(
            "apply", state in {"applied", "post_checked", "closed"}, f"state={state}"
        ):
            return False
        state = await self.wait_for_state(change_id, {"post_checked", "closed"}, timeout=600)
        if not self.stage("post-check", state in {"post_checked", "closed"}, f"state={state}"):
            return False
        resp = await self.api("GET", f"/changes/{change_id}")
        final = resp.json().get("state", "") if resp.status_code == 200 else ""
        return self.stage("close", final == "closed", f"state={final}")

    async def _ztp_cleanup(self, serial: str) -> bool:
        """Remove the lab device's DHCP reservation (F2 endpoint).

        DELETE is legal only once the device is active; called earlier it
        returns 409 and the stage fails. The response must report
        removed=True — removed=False means no reservation was in the DHCP
        config, which is a failure, not a clean state. The device itself
        stays active as evidence (see docs/LAB.md).
        """
        resp = await self.api("DELETE", f"/ztp/devices/{serial}/reservation")
        if resp.status_code != 200:
            return self.stage(
                "ztp-cleanup", False, f"DELETE reservation -> {resp.status_code}: {resp.text[:200]}"
            )
        body = resp.json()
        return self.stage(
            "ztp-cleanup", body.get("removed") is True, f"removed={body.get('removed')}"
        )

    async def run_ztp(self) -> bool:
        """Flow B: ZTP leaf-99 from registration to active, then cleanup."""
        serial = ZTP_DEVICE["serial"]
        resp = await self.api("POST", "/ztp/devices", json=ZTP_DEVICE)
        if resp.status_code != 202:
            return self.stage(
                "ztp-register", False, f"POST /ztp/devices -> {resp.status_code}: {resp.text[:200]}"
            )
        body = resp.json()
        token = str(body.get("token", ""))
        self.stage(
            "ztp-register", True, f"leaf-99 {serial} ticket {body.get('ticket_number', '?')}"
        )
        # F2 endpoint: creates the DHCP reservation. Idempotent — the ZTP
        # workflow's ztp_create_reservation activity may already have done
        # it, in which case the response reports already_created=True.
        resp = await self.api("POST", f"/ztp/devices/{serial}/reservation")
        if resp.status_code != 202:
            return self.stage(
                "ztp-reservation",
                False,
                f"POST reservation -> {resp.status_code}: {resp.text[:200]}",
            )
        body = resp.json()
        detail = f"ip={body.get('ip_address', '?')}"
        if body.get("already_created"):
            detail += " (already created by the workflow)"
        self.stage("ztp-reservation", True, detail)
        resp = await self.api(
            "POST",
            "/ztp/callback",
            json={
                "token": token,
                "serial": serial,
                "model": ZTP_DEVICE["model"],
                "platform": ZTP_DEVICE["platform"],
                "version": "",
                "ip_address": "",
            },
        )
        if not self.stage(
            "ztp-callback", resp.status_code == 202, f"POST /ztp/callback -> {resp.status_code}"
        ):
            return False
        # No POST verify-identity endpoint exists: identity is checked by the
        # workflow's ztp_verify_identity activity after the callback.
        stage = await self.wait_for_ztp_stage(serial, {"identity_verified", "active"}, timeout=300)
        if not self.stage(
            "ztp-identity", stage in {"identity_verified", "active"}, f"stage={stage}"
        ):
            return False
        stage = await self.wait_for_ztp_stage(serial, {"active"}, timeout=600)
        if not self.stage("ztp-day1", stage == "active", f"stage={stage}"):
            return False
        return await self._ztp_cleanup(serial)

    async def wait_for_drift_record(
        self, device: str, *, want_status: set[str] | None = None, timeout: float = 300
    ) -> dict | None:
        """Poll GET /drift?device=... until a record appears (optionally in a wanted status)."""
        deadline = time.monotonic() + timeout
        while True:
            resp = await self.api("GET", f"/drift?device={device}&limit=50")
            items = resp.json().get("items", []) if resp.status_code == 200 else []
            for item in items:
                if want_status is None or item.get("status") in want_status:
                    return item
            if time.monotonic() > deadline:
                return None
            await asyncio.sleep(self.poll_seconds)

    async def _scan_and_wait(
        self, device: str, *, want_status: set[str] | None = None, timeout: float = 300
    ) -> dict | None:
        resp = await self.api("POST", "/drift/scan", json={"site": "dc1"})
        if resp.status_code != 202:
            self.stage("drift-scan", False, f"POST /drift/scan -> {resp.status_code}")
            return None
        return await self.wait_for_drift_record(device, want_status=want_status, timeout=timeout)

    async def run_drift(self, adapter: LabAdapter, lab_id: str) -> bool:
        """Flow C (D6): manual change -> detect -> rollback -> merge -> exception.

        Each stage prints PASS/FAIL through self.stage; a failed stage
        aborts the flow (later stages depend on the device state).
        """
        device = "leaf-01"
        # 1. Manual change on the lab device, then a scan must detect it.
        try:
            await adapter.exec(lab_id, device, DRIFT_CHANGE_CMD)
        except Exception as exc:  # noqa: BLE001 - lab exec failure is a stage failure
            return self.stage("drift-manual-change", False, f"lab exec failed: {exc}")
        self.stage("drift-manual-change", True, "description DRIFT-LAB applied on leaf-01")
        record = await self._scan_and_wait(device, timeout=300)
        if not self.stage(
            "drift-detected", record is not None, f"record={record.get('id') if record else None}"
        ):
            return False
        record_id = str(record["id"])

        # 2. Rollback: the record must resolve (running == baseline again).
        resp = await self.api(
            "POST",
            f"/drift/{record_id}/rollback",
            json={"reason": "lab-e2e rollback", "ticket_ref": DRIFT_TICKET},
        )
        if not self.stage(
            "drift-rollback", resp.status_code == 200, f"POST rollback -> {resp.status_code}"
        ):
            return False
        record = await self.wait_for_drift_record(device, want_status={"resolved"}, timeout=600)
        if not self.stage(
            "drift-rollback",
            record is not None,
            "record resolved" if record else "not resolved in 600s",
        ):
            return False

        # 3. Change again, then merge: MR created, merged (via the
        # mr-merged endpoint, which simulates the Git webhook — the real
        # webhook path is exercised by the Git provider, not this script),
        # baseline updated, record resolved.
        try:
            await adapter.exec(lab_id, device, DRIFT_CHANGE_CMD)
        except Exception as exc:  # noqa: BLE001 - lab exec failure is a stage failure
            return self.stage("drift-manual-change-2", False, f"lab exec failed: {exc}")
        record = await self._scan_and_wait(device, timeout=300)
        if not self.stage("drift-detected-2", record is not None, "second drift detected"):
            return False
        record_id = str(record["id"])
        resp = await self.api(
            "POST",
            f"/drift/{record_id}/merge",
            json={"reason": "lab-e2e merge", "ticket_ref": DRIFT_TICKET},
        )
        if resp.status_code != 200:
            return self.stage(
                "drift-merge", False, f"POST merge -> {resp.status_code}: {resp.text[:200]}"
            )
        body = resp.json()
        branch = str(body.get("branch", ""))
        if not self.stage(
            "drift-merge",
            body.get("status") == "merge_pending" and bool(body.get("mr_id")),
            f"MR {body.get('mr_id')} on {branch}",
        ):
            return False
        resp = await self.api(
            "POST",
            f"/drift/{record_id}/mr-merged",
            json={"merged": True, "branch": branch},
        )
        if not self.stage(
            "drift-merge",
            resp.status_code == 200 and resp.json().get("status") == "resolved",
            f"mr-merged -> {resp.status_code}",
        ):
            return False

        # 4. Change a third time, then exception: the exact diff is
        # ignored until expiry, and a *different* change creates a new record.
        try:
            await adapter.exec(lab_id, device, DRIFT_CHANGE_CMD)
        except Exception as exc:  # noqa: BLE001 - lab exec failure is a stage failure
            return self.stage("drift-manual-change-3", False, f"lab exec failed: {exc}")
        record = await self._scan_and_wait(device, timeout=300)
        if not self.stage("drift-detected-3", record is not None, "third drift detected"):
            return False
        record_id = str(record["id"])
        from datetime import UTC, datetime, timedelta

        expiry_iso = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
        resp = await self.api(
            "POST",
            f"/drift/{record_id}/exception",
            json={
                "reason": "lab-e2e exception",
                "ticket_ref": DRIFT_TICKET,
                "owner": "lab-e2e",
                "expiry": expiry_iso,
            },
        )
        if not self.stage(
            "drift-exception", resp.status_code == 200, f"POST exception -> {resp.status_code}"
        ):
            return False
        before = await self.api("GET", f"/drift?device={device}&limit=50")
        count_before = before.json().get("total", 0) if before.status_code == 200 else -1
        await self._scan_and_wait(device, timeout=300)
        after = await self.api("GET", f"/drift?device={device}&limit=50")
        count_after = after.json().get("total", 0) if after.status_code == 200 else -1
        if not self.stage(
            "drift-exception-ignored",
            count_after == count_before,
            f"records {count_before} -> {count_after} after re-scan (excepted diff ignored)",
        ):
            return False
        try:
            await adapter.exec(lab_id, device, DRIFT_CHANGE_CMD_2)
        except Exception as exc:  # noqa: BLE001 - lab exec failure is a stage failure
            return self.stage("drift-manual-change-4", False, f"lab exec failed: {exc}")
        record = await self._scan_and_wait(device, timeout=300)
        if not self.stage(
            "drift-exception-new-diff",
            record is not None and str(record.get("id")) != record_id,
            "a different diff created a new record despite the exception",
        ):
            return False

        # 5. The Flow A change must have reconciled into Git and NetBox.
        # NOTE (honest gap): a *python-job* change cannot be driven from
        # the API — the change intent forbids extra keys (so job_type
        # cannot be set on POST /changes), the workflow hardcodes
        # job_type="playbook", and ScriptRunner is library-only (E3).
        # The python reconciliation path is covered by
        # tests/test_e3_scripts.py, not by this lab script. This stage
        # proves reconciliation for the real Flow A change instead.
        if not getattr(self, "vlan_change_id", None):
            return self.stage(
                "drift-python-reconcile", False, "Flow A did not run; no change to check"
            )
        detail = await self.api("GET", f"/changes/{self.vlan_change_id}/detail")
        if detail.status_code != 200:
            return self.stage(
                "drift-python-reconcile", False, f"GET change detail -> {detail.status_code}"
            )
        status = detail.json().get("reconciliation_status")
        return self.stage(
            "drift-python-reconcile",
            status == "reconciled",
            f"Flow A change reconciliation_status={status} "
            "(python-runner dispatch has no API trigger; see note in run_drift)",
        )


async def _run(args: argparse.Namespace) -> int:
    try:
        api_url = os.environ["VIREONET_API_URL"]
        token = os.environ["VIREONET_API_TOKEN"]
    except KeyError as exc:
        raise KeyError(
            f"missing required environment variable {exc.args[0]}: set VIREONET_API_URL "
            "(e.g. http://localhost:8000) and VIREONET_API_TOKEN before running the lab test"
        ) from exc
    settings = get_settings()
    if settings.adapter_mode != "real":
        print(
            f"FAIL prereq: adapter_mode is {settings.adapter_mode!r}, not 'real' — refusing to run the lab test in fake/recorded mode"
        )
        return 2
    backend = args.backend or os.environ.get("VIREONET_LAB_BACKEND", "eveng")
    if backend not in {"eveng", "containerlab"}:
        print(f"FAIL prereq: unknown lab backend {backend!r} (want eveng|containerlab)")
        return 2
    topology = args.topology or (
        "topologies/two-leaf.unl" if backend == "eveng" else "topologies/two-leaf.clab.yml"
    )
    try:
        adapter = lab_adapter_for(settings, backend)
    except Exception as exc:  # noqa: BLE001 - registry failure is a stage failure, not a crash
        print(f"FAIL lab-backend: could not build {backend} adapter: {exc}")
        return 1
    try:
        lab_id = await adapter.deploy(topology, args.lab)
    except Exception as exc:  # noqa: BLE001 - lab start failure is a stage failure, not a crash
        print(f"FAIL lab-deploy: could not start lab {args.lab} on {backend}: {exc}")
        return 1
    runner = Runner(
        api_url=api_url, token=token, lab_name=args.lab, adapter_mode=settings.adapter_mode
    )
    try:
        nodes = await adapter.wait_ready(lab_id, timeout_seconds=600)
        print(f"{backend} lab {lab_id} ready: {[n.name for n in nodes]}")
        if args.only in {"all", "vlan"}:
            await runner.run_vlan()
        if args.only in {"all", "ztp"}:
            await runner.run_ztp()
        if args.only in {"all", "drift"}:
            await runner.run_drift(adapter, lab_id)
    finally:
        # Always destroy the lab, even on failure — a leaked lab blocks the next run.
        if not args.no_cleanup:
            try:
                await adapter.destroy(lab_id)
            except Exception as exc:  # noqa: BLE001 - destroy is best-effort here
                print(f"lab destroy failed for {lab_id}: {exc}", file=sys.stderr)
            print(f"{backend} lab {lab_id} destroyed")
        else:
            print(f"--no-cleanup: {backend} lab {lab_id} left running")
    if runner.total and runner.passed == runner.total:
        print(f"ALL STAGES PASSED ({runner.passed}/{runner.total})")
        return 0
    print(f"SOME STAGES FAILED ({runner.passed}/{runner.total} passed)")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="VireoNet end-to-end lab test (F5). Real systems only."
    )
    parser.add_argument("--lab", required=True, help="lab name")
    parser.add_argument(
        "--backend",
        choices=["eveng", "containerlab"],
        default=None,
        help="lab backend (default: VIREONET_LAB_BACKEND or eveng)",
    )
    parser.add_argument(
        "--topology",
        default=None,
        help="topology file (default: topologies/two-leaf.unl for eveng, "
        "topologies/two-leaf.clab.yml for containerlab)",
    )
    parser.add_argument(
        "--only",
        choices=["all", "vlan", "ztp", "drift"],
        default="all",
        help="which flow(s) to run",
    )
    parser.add_argument(
        "--no-cleanup", action="store_true", help="leave the EVE-NG lab running (default: wipe it)"
    )
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
