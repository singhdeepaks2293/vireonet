"""Per-platform runner capabilities (E2).

driver: which runner talks to the platform ("netmiko" or "napalm" via Nornir).
compare: the driver can diff candidate vs running before applying.
commit: the driver can commit (vs only staging) a candidate config.
rollback: how to roll back — True when the driver supports it natively,
otherwise a note. "UNCERTAIN" means the mechanism is not verified.
"""

from __future__ import annotations

from typing import Any

CAPABILITIES: dict[str, dict[str, Any]] = {
    "ios": {
        "driver": "netmiko",
        "compare": False,
        "commit": False,
        "rollback": "reload-in-N-minutes (UNCERTAIN)",
    },
    "iosxe": {
        "driver": "napalm",
        "compare": True,
        "commit": True,
        "rollback": True,
    },
    "nxos": {
        "driver": "napalm",
        "compare": True,
        "commit": True,
        "rollback": True,
    },
    "eos": {
        "driver": "napalm",
        "compare": True,
        "commit": True,
        "rollback": "UNCERTAIN",
    },
    "junos": {
        "driver": "napalm",
        "compare": True,
        "commit": True,
        "rollback": "UNCERTAIN",
    },
}
