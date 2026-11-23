"""Netmiko runner (E2): old-IOS-style devices with no NAPALM driver.

Dry-run sends nothing: it only reports the lines it *would* send. A real
apply optionally arms a ``reload in 5`` safety net first (only when
``ios_reload_safety`` is "true"), then send_config_set + save_config.
"""

from __future__ import annotations

import os
import re
import time
from contextlib import suppress
from typing import Any

from vireonet_runners.common import redact

DEVICE_TYPES = {"ios": "cisco_ios", "iosxe": "cisco_xe", "nxos": "cisco_nxos"}

_VLAN_RE = re.compile(r"^\s*vlan\s+(\d+)")


def _failed(req: dict[str, Any], error: str, duration: float = 0.0) -> dict[str, Any]:
    return {
        "status": "failed",
        "step": str(req.get("step", "")),
        "device": str(req.get("device", "")),
        "output": {},
        "declared_changes": [],
        "error": redact(error),
        "duration_seconds": duration,
    }


def _ok(
    req: dict[str, Any], output: dict[str, Any], declared: list[dict[str, Any]], duration: float
) -> dict[str, Any]:
    return {
        "status": "successful",
        "step": str(req.get("step", "")),
        "device": str(req.get("device", "")),
        "output": output,
        "declared_changes": declared,
        "error": None,
        "duration_seconds": duration,
    }


def _declared_from_lines(lines: list[str]) -> list[dict[str, Any]]:
    declared: list[dict[str, Any]] = []
    for line in lines:
        match = _VLAN_RE.match(line.strip())
        if match is None:
            continue
        declared.append(
            {
                "object_type": "vlan",
                "identifier": match.group(1),
                "field": "exists",
                "old_value": "absent",
                "new_value": "present",
                "config_lines": [line.strip()],
            }
        )
    return declared


def run_netmiko_step(req: dict[str, Any]) -> dict[str, Any]:
    """Run one step against the device with Netmiko. Returns a dict."""
    started = time.monotonic()
    device = str(req.get("device", ""))
    platform = str(req.get("platform", ""))
    step = str(req.get("step", ""))
    device_type = DEVICE_TYPES.get(platform)
    if device_type is None:
        return _failed(req, f"platform {platform} has no netmiko driver")
    conn = None
    try:
        from netmiko import ConnectHandler

        conn = ConnectHandler(
            device_type=device_type,
            host=str(req.get("mgmt_ip") or device),
            username=os.environ.get("VIREONET_DEVICE_USERNAME", ""),
            password=os.environ.get("VIREONET_DEVICE_PASSWORD", ""),
            secret=os.environ.get("VIREONET_DEVICE_ENABLE", ""),
            timeout=int(req.get("timeout_seconds") or 600),
        )

        def _duration() -> float:
            return time.monotonic() - started

        if step == "backup":
            out = conn.send_command("show running-config")
            return _ok(req, {"running_config": out}, [], _duration())
        if step == "collect_state":
            interfaces = conn.send_command("show interfaces status", use_textfsm=True)
            lldp = conn.send_command("show lldp neighbors", use_textfsm=True)
            return _ok(
                req,
                {"interfaces_status": interfaces, "lldp_neighbors": lldp},
                [],
                _duration(),
            )
        if step in {"dry_run", "apply"}:
            lines = [
                line for line in str(req.get("rendered_config", "")).splitlines() if line.strip()
            ]
            is_dry = bool(req.get("dry_run")) or step == "dry_run"
            if is_dry:
                # A dry-run sends nothing.
                return _ok(req, {"would_send": lines}, _declared_from_lines(lines), _duration())
            extra_vars = req.get("extra_vars") or {}
            output: dict[str, Any] = {}
            if extra_vars.get("ios_reload_safety") == "true":
                # UNCERTAIN: "reload in 5" as a dead-man's switch on old IOS
                # is a common safety net, but whether the reload actually
                # fires (and is cancelled on success) is not verified here.
                output["reload_safety"] = conn.send_command("reload in 5")
            output["config_result"] = conn.send_config_set(lines)
            output["saved"] = conn.save_config()
            return _ok(req, output, _declared_from_lines(lines), _duration())
        if step == "rollback":
            lines = [
                line for line in str(req.get("rendered_config", "")).splitlines() if line.strip()
            ]
            output = {
                "config_result": conn.send_config_set(lines),
                "saved": conn.save_config(),
            }
            return _ok(req, output, _declared_from_lines(lines), _duration())
        return _failed(req, f"unsupported step {step}", _duration())
    except Exception as exc:  # noqa: BLE001 — a runner failure is a failed result, not a crash
        return _failed(req, str(exc), time.monotonic() - started)
    finally:
        if conn is not None:
            with suppress(Exception):
                conn.disconnect()
