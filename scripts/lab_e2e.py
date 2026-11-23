#!/usr/bin/env python3
"""End-to-end lab test for VireoNet (milestone F5).

Flow A — VLAN add through the real change pipeline (create -> CI/Batfish
-> approve -> apply -> post-check -> closed). Flow B — ZTP register ->
callback -> identity -> day-1 -> active on a simulated device.

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

from vireonet.adapters.base import AdapterConfig  # noqa: E402
from vireonet.adapters.eveng import EveNgAdapter  # noqa: E402
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
    adapter = EveNgAdapter(
        AdapterConfig(name="eveng", base_url=settings.eveng_base_url),
        username=settings.eveng_username,
        password=settings.eveng_password,
    )
    try:
        lab = await adapter.start_lab(args.lab, args.topology)
    except Exception as exc:  # noqa: BLE001 — lab start failure is a stage failure, not a crash
        print(f"FAIL eveng-lab: could not start lab {args.lab}: {exc}")
        return 1
    runner = Runner(
        api_url=api_url, token=token, lab_name=args.lab, adapter_mode=settings.adapter_mode
    )
    try:
        await adapter.load_topology(lab.id, args.topology)
        ready = await adapter.wait_for_nodes(lab.id, timeout_seconds=600)
        print(f"EVE-NG lab {ready.id} ready: {ready.nodes}")
        if args.only in {"all", "vlan"}:
            await runner.run_vlan()
        if args.only in {"all", "ztp"}:
            await runner.run_ztp()
    finally:
        # Always wipe the lab, even on failure — a leaked lab blocks the next run.
        if not args.no_cleanup:
            try:
                await adapter.stop_lab(lab.id)
            finally:
                await adapter.wipe_lab(lab.id)
            print(f"EVE-NG lab {lab.id} wiped")
        else:
            print(f"--no-cleanup: EVE-NG lab {lab.id} left running")
    if runner.total and runner.passed == runner.total:
        print(f"ALL STAGES PASSED ({runner.passed}/{runner.total})")
        return 0
    print(f"SOME STAGES FAILED ({runner.passed}/{runner.total} passed)")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="VireoNet end-to-end lab test (F5). Real systems only."
    )
    parser.add_argument("--lab", required=True, help="EVE-NG lab name")
    parser.add_argument(
        "--topology", default="topologies/two-leaf.unl", help="EVE-NG .unl topology file"
    )
    parser.add_argument(
        "--only", choices=["all", "vlan", "ztp"], default="all", help="which flow(s) to run"
    )
    parser.add_argument(
        "--no-cleanup", action="store_true", help="leave the EVE-NG lab running (default: wipe it)"
    )
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
