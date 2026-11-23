"""Nornir/NAPALM runner (E2).

Inventory is built per run, in memory, via InitNornir with the
SimpleInventory plugin — there is no static hosts file; ``host_file`` is
"/dev/null" and the single host comes from the request.

UNCERTAIN: whether SimpleInventory accepts an inline ``hosts`` mapping in
its options in Nornir 3.5.0 (the documented options are the ``*_file``
paths). The runner tests run against a mocked InitNornir, so this shape
is not verified here — confirm on a real host before trusting it, and
fall back to a generated temp inventory file if SimpleInventory rejects
the inline hosts.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any

from vireonet_runners.capabilities import CAPABILITIES
from vireonet_runners.common import redact

_VLAN_RE = re.compile(r"^\s*[+-]?\s*vlan\s+(\d+)")


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


def _declared_from_diff(diff: str) -> list[dict[str, Any]]:
    """VLAN lines in a diff -> DeclaredChange dicts."""
    declared: list[dict[str, Any]] = []
    for line in diff.splitlines():
        match = _VLAN_RE.match(line)
        if match is None:
            continue
        vlan_id = match.group(1)
        added = line.lstrip().startswith("+")
        declared.append(
            {
                "object_type": "vlan",
                "identifier": vlan_id,
                "field": "exists",
                "old_value": "absent" if added else "present",
                "new_value": "present" if added else "absent",
                "config_lines": [line.strip()],
            }
        )
    return declared


def _host_result(res: Any, device: str) -> Any:
    try:
        host = res[device]
    except Exception:
        host = res
    return getattr(host, "result", host)


def run_nornir_step(req: dict[str, Any]) -> dict[str, Any]:
    """Run one step against the device with Nornir/NAPALM. Returns a dict."""
    started = time.monotonic()
    device = str(req.get("device", ""))
    platform = str(req.get("platform", ""))
    step = str(req.get("step", ""))
    caps = CAPABILITIES.get(platform)
    if caps is None or caps.get("driver") != "napalm":
        return _failed(req, f"platform {platform} has no napalm driver")
    try:
        from nornir import InitNornir
        from nornir_napalm.plugins.tasks import napalm_configure, napalm_get

        def _duration() -> float:
            return time.monotonic() - started

        nr = InitNornir(
            inventory={
                "plugin": "SimpleInventory",
                "options": {
                    "host_file": "/dev/null",
                    "hosts": {
                        device: {
                            "hostname": str(req.get("mgmt_ip") or device),
                            "platform": platform,
                            "username": os.environ.get("VIREONET_DEVICE_USERNAME", ""),
                            "password": os.environ.get("VIREONET_DEVICE_PASSWORD", ""),
                        }
                    },
                },
            },
            runner={"plugin": "threaded", "options": {"num_workers": 4}},
        )
        if step == "collect_state":
            res = nr.run(task=napalm_get, getters=["facts", "interfaces", "lldp_neighbors"])
            got = _host_result(res, device) or {}
            return _ok(
                req,
                {
                    "facts": got.get("facts", {}),
                    "interfaces": got.get("interfaces", {}),
                    "lldp_neighbors": got.get("lldp_neighbors", {}),
                },
                [],
                _duration(),
            )
        if step == "backup":
            res = nr.run(task=napalm_get, getters=["config"])
            got = _host_result(res, device) or {}
            config = got.get("config", {}) if isinstance(got, dict) else {}
            running = config.get("running", "") if isinstance(config, dict) else ""
            return _ok(req, {"running_config": running}, [], _duration())
        if step in {"dry_run", "apply"}:
            rendered = str(req.get("rendered_config", ""))
            res = nr.run(task=napalm_configure, configuration=rendered, dry_run=True)
            diff = str(_host_result(res, device) or "")
            declared = _declared_from_diff(diff)
            is_dry = bool(req.get("dry_run")) or step == "dry_run"
            if is_dry:
                return _ok(
                    req,
                    {"diff": diff, "would_change": bool(diff.strip())},
                    declared,
                    _duration(),
                )
            res = nr.run(task=napalm_configure, configuration=rendered, dry_run=False)
            committed = str(_host_result(res, device) or "")
            return _ok(
                req,
                {"diff": committed or diff, "committed": True},
                declared,
                _duration(),
            )
        if step == "rollback":
            rendered = str(req.get("rendered_config", ""))
            nr.run(task=napalm_configure, configuration=rendered, dry_run=False)
            return _ok(req, {"restored": True}, [], _duration())
        return _failed(req, f"unsupported step {step}", _duration())
    except Exception as exc:  # noqa: BLE001 — a runner failure is a failed result, not a crash
        return _failed(req, str(exc), time.monotonic() - started)
