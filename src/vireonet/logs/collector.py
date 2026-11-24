"""Collect `show logging` from devices.

Fake mode: the deterministic fake syslog. Real mode: the read-only
run-commands step (AWX), one job per device. For continuous analysis at
scale, send syslog to a collector (Loki, Elastic, Graylog, Splunk) and
query it instead; this collector is for on-demand analysis.
"""

from __future__ import annotations

import json
from typing import Any

from vireonet.logs.fake_syslog import fake_show_logging
from vireonet.logs.parser import CISCO_STYLE, LogEvent, parse_show_logging

SHOW_LOGGING = {"junos": "show log messages | last 2000"}
DEFAULT_SHOW = "show logging"


async def collect(
    devices: list[tuple[str, str, list[str]]],
    *,
    fake: bool,
    engine: Any | None = None,
    hours: int = 24,
) -> tuple[list[LogEvent], dict[str, dict[str, Any]]]:
    """(events, per-device stats) for [(device, platform, interfaces)]."""
    events: list[LogEvent] = []
    stats: dict[str, dict[str, Any]] = {}
    for device, platform, interfaces in devices:
        try:
            if fake:
                text = fake_show_logging(device, platform, interfaces, hours=hours)
            else:
                text = await _run(engine, device, platform)
        except Exception as exc:  # noqa: BLE001 — one device failing must not hide the others
            stats[device] = {"platform": platform, "error": str(exc)}
            continue
        evs, unparsed = parse_show_logging(device, platform, text)
        events.extend(evs)
        stats[device] = {
            "platform": platform,
            "lines": len(text.splitlines()),
            "parsed": len(evs),
            "unparsed": unparsed,
            "template": "junos_show_log" if platform == "junos" else "cisco_show_logging",
            "note": ""
            if platform in CISCO_STYLE | {"junos"}
            else "parsed with the Cisco-style template; add a TextFSM template for this vendor's log format",
        }
    return events, stats


async def _run(engine: Any, device: str, platform: str) -> str:
    from vireonet.execution.types import DeviceRef, StepRequest

    if engine is None or not hasattr(engine, "run_commands"):
        raise RuntimeError("no execution engine with run_commands")
    command = SHOW_LOGGING.get(platform, DEFAULT_SHOW)
    req = StepRequest(
        ticket_number="LOG-ANALYTICS",
        device=DeviceRef(name=device, platform=platform),
        step="run_commands",
        extra_vars={"commands": json.dumps([command])},
        dry_run=True,
    )
    result = await engine.run_commands(req)
    if not result.ok:
        raise RuntimeError(f"run-commands failed (job {result.job_id})")
    return str((result.output.get("command_outputs") or [""])[0])
