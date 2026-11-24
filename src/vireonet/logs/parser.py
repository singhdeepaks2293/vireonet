"""Parse `show logging` output into structured events with TextFSM."""

from __future__ import annotations

import io
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import textfsm

_TEMPLATES = Path(__file__).parent / "templates"
CISCO_STYLE = {"ios", "ios-xe", "nxos", "eos"}

# Junos events carry no severity in the line; map the common ones.
JUNOS_SEVERITY = {
    "SNMP_TRAP_LINK_DOWN": 3,
    "SNMP_TRAP_LINK_UP": 5,
    "BGP_IO_ERROR_CLOSE_SESSION": 3,
    "RPD_BGP_NEIGHBOR_STATE_CHANGED": 4,
    "SSHD_LOGIN_FAILED": 4,
    "UI_AUTH_EVENT": 5,
    "UI_COMMIT": 5,
    "CHASSISD_PSU_FAILURE": 1,
    "CHASSISD_FRU_OFFLINE_NOTICE": 2,
}
_IFACE = re.compile(r"(?:Interface|ifName)\s+([A-Za-z][\w/.:-]*\d)", re.IGNORECASE)


@dataclass
class LogEvent:
    device: str
    timestamp: str  # ISO 8601 when parseable, else as printed
    severity: int  # syslog 0 (emergency) .. 7 (debug)
    facility: str
    mnemonic: str  # FACILITY-MNEMONIC (Cisco) or EVENT (Junos)
    message: str
    interface: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_time(raw: str, year: int) -> str:
    raw = raw.strip().lstrip("*")
    raw = re.sub(r"\s+[A-Z]{3,4}$", "", raw)
    for fmt in ("%Y %b %d %H:%M:%S", "%b %d %Y %H:%M:%S", "%b %d %H:%M:%S"):
        for value in (raw.split(".")[0],):
            try:
                dt = datetime.strptime(value, fmt)
                if "%Y" not in fmt:
                    dt = dt.replace(year=year)
                return dt.replace(tzinfo=UTC).isoformat()
            except ValueError:
                continue
    return raw


def _fsm(name: str) -> textfsm.TextFSM:
    return textfsm.TextFSM(io.StringIO((_TEMPLATES / name).read_text()))


def parse_show_logging(
    device: str, platform: str, text: str, year: int | None = None
) -> tuple[list[LogEvent], int]:
    """(events, unparsed_line_count). Unknown platforms are tried with the
    Cisco-style template (most vendors' syslog uses %FAC-SEV-MNEMONIC)."""
    year = year or datetime.now(UTC).year
    lines = [line for line in text.splitlines() if line.strip()]
    events: list[LogEvent] = []
    if platform == "junos":
        fsm = _fsm("junos_show_log.textfsm")
        for ts, _host, process, event, msg in fsm.ParseText(text):
            m = _IFACE.search(msg)
            events.append(
                LogEvent(
                    device,
                    _parse_time(ts, year),
                    JUNOS_SEVERITY.get(event, 5),
                    process,
                    event,
                    msg,
                    m.group(1) if m else "",
                )
            )
    else:
        fsm = _fsm("cisco_show_logging.textfsm")
        for ts, _host, fac, sev, mnem, msg in fsm.ParseText(text):
            m = _IFACE.search(msg)
            events.append(
                LogEvent(
                    device,
                    _parse_time(ts, year),
                    int(sev),
                    fac,
                    f"{fac}-{mnem}",
                    msg,
                    m.group(1).rstrip(",") if m else "",
                )
            )
    return events, max(0, len(lines) - len(events))
