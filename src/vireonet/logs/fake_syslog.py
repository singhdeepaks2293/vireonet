"""Deterministic fake `show logging` per device (fake/demo mode).

Background noise every device has (config saves, SSH logins, NTP, an
occasional single link flap), plus injected incidents on a few devices so
the anomaly detector has something real to find:

- leaf-02: interface Ethernet1/49 flapping (link down/up bursts)
- border-01: BGP sessions dropping repeatedly
- fw-02: SSH brute force (many failed logins)
- spine-02: power-supply failure (rare, critical)
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

INCIDENTS = {
    "leaf-02": "link_flap",
    "border-01": "bgp_flap",
    "fw-02": "auth_storm",
    "spine-02": "psu_fail",
}


def _fmt(
    platform: str, device: str, at: datetime, fac: str, sev: int, mnem: str, msg: str, seq: int
) -> str:
    if platform == "junos":
        junos = {
            "LINK-UPDOWN-down": ("mib2d", "SNMP_TRAP_LINK_DOWN"),
            "LINK-UPDOWN-up": ("mib2d", "SNMP_TRAP_LINK_UP"),
            "SEC_LOGIN-LOGIN_FAILED": ("sshd", "SSHD_LOGIN_FAILED"),
            "SYS-CONFIG_I": ("mgd", "UI_COMMIT"),
            "BGP-ADJCHANGE": ("rpd", "RPD_BGP_NEIGHBOR_STATE_CHANGED"),
            "PLATFORM-PS_FAIL": ("chassisd", "CHASSISD_PSU_FAILURE"),
        }
        key = f"{fac}-{mnem}" + (
            "-down" if "down" in msg else "-up" if "changed state to up" in msg else ""
        )
        proc, event = junos.get(key, junos.get(f"{fac}-{mnem}", ("eventd", f"{fac}_{mnem}")))
        return f"{at:%b %d %H:%M:%S}  {device} {proc}[{1000 + seq % 900}]: {event}: {msg}"
    if platform == "nxos":
        return f"{at:%Y %b %d %H:%M:%S} {device} %{fac}-{sev}-{mnem}: {msg}"
    if platform == "eos":
        return f"{at:%b %d %H:%M:%S} {device} Ebra: %{fac}-{sev}-{mnem}: {msg}"
    return f"{seq:06d}: *{at:%b %d %H:%M:%S}.{seq % 1000:03d} UTC: %{fac}-{sev}-{mnem}: {msg}"


def fake_show_logging(
    device: str,
    platform: str,
    interfaces: list[str],
    *,
    hours: int = 24,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(UTC)
    rnd = random.Random(f"{device}|{now:%Y-%m-%d}")
    start = now - timedelta(hours=hours)
    events: list[tuple[datetime, str, int, str, str]] = []
    iface = interfaces[0] if interfaces else "Ethernet1"

    def add(at: datetime, fac: str, sev: int, mnem: str, msg: str) -> None:
        if start <= at <= now:
            events.append((at, fac, sev, mnem, msg))

    t = start
    while t < now:  # background noise
        t += timedelta(minutes=rnd.expovariate(1 / 35))
        kind = rnd.random()
        if kind < 0.45:
            add(
                t,
                "SEC_LOGIN",
                5,
                "LOGIN_SUCCESS",
                f"Login Success [user: netops] [Source: 10.9.{rnd.randint(0, 9)}.{rnd.randint(2, 250)}] [localport: 22]",
            )
        elif kind < 0.7:
            add(t, "SYS", 5, "CONFIG_I", "Configured from console by netops on vty0")
        elif kind < 0.9:
            add(t, "NTP", 5, "PEERSYNC", "NTP synced to peer 10.0.0.123")
        elif kind < 0.97:
            add(
                t,
                "LINK",
                3,
                "UPDOWN",
                f"Interface {rnd.choice(interfaces or [iface])}, changed state to down",
            )
            add(
                t + timedelta(seconds=4),
                "LINK",
                3,
                "UPDOWN",
                f"Interface {rnd.choice(interfaces or [iface])}, changed state to up",
            )
        else:
            add(
                t,
                "SEC_LOGIN",
                4,
                "LOGIN_FAILED",
                f"Login failed [user: admin] [Source: 10.9.1.{rnd.randint(2, 250)}] [Reason: Invalid password]",
            )

    incident = INCIDENTS.get(device)
    burst_at = now - timedelta(hours=3, minutes=rnd.randint(0, 50))
    if incident == "link_flap":
        for n in range(60):
            at = burst_at + timedelta(seconds=n * 21)
            add(
                at,
                "LINK",
                3,
                "UPDOWN",
                f"Interface {iface}, changed state to {'down' if n % 2 == 0 else 'up'}",
            )
            add(
                at + timedelta(seconds=1),
                "LINEPROTO",
                5,
                "UPDOWN",
                f"Line protocol on Interface {iface}, changed state to {'down' if n % 2 == 0 else 'up'}",
            )
    elif incident == "bgp_flap":
        for n in range(24):
            at = burst_at + timedelta(minutes=n * 2)
            peer = f"192.0.2.{1 + n % 3}"
            add(
                at,
                "BGP",
                5,
                "ADJCHANGE",
                f"neighbor {peer} {'Down BGP Notification sent' if n % 2 == 0 else 'Up'}",
            )
            if n % 2 == 0:
                add(
                    at + timedelta(seconds=2),
                    "BGP",
                    3,
                    "NOTIFICATION",
                    f"sent to neighbor {peer} 4/0 (hold time expired) 0 bytes",
                )
    elif incident == "auth_storm":
        for n in range(150):
            add(
                burst_at + timedelta(seconds=n * 9),
                "SEC_LOGIN",
                4,
                "LOGIN_FAILED",
                f"Login failed [user: {rnd.choice(['admin', 'root', 'cisco', 'test'])}] [Source: 203.0.113.{rnd.randint(2, 30)}] [Reason: Invalid password]",
            )
    elif incident == "psu_fail":
        add(burst_at, "PLATFORM", 2, "PS_FAIL", "Power supply 2 failed or shutdown")
        add(
            burst_at + timedelta(seconds=30),
            "ENVMON",
            1,
            "FAN_FAIL",
            "Fan tray 2 failure, airflow reduced",
        )

    events.sort(key=lambda e: e[0])
    return "\n".join(
        _fmt(platform, device, at, fac, sev, mnem, msg, n)
        for n, (at, fac, sev, mnem, msg) in enumerate(events, 1)
    )
