"""Training data: VireoNet's own change history and interface telemetry.

When the database has too few finished changes to learn from, a synthetic
change set is generated instead and every result says so (source).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np
from sqlalchemy import select

CHANGE_TYPES = [
    "vlan_add",
    "vlan_remove",
    "acl_entry_add",
    "acl_entry_remove",
    "static_route_add",
    "static_route_remove",
    "interface_set",
]
PLATFORMS = ["ios", "ios-xe", "nxos", "eos", "junos", "fortios", "panos", "sros", "exos"]
FAILED_STATES = {"rolled_back", "rollback_failed", "failed", "pre_check_failed"}
FINISHED_STATES = FAILED_STATES | {"closed"}
MIN_REAL_CHANGES = 40

FEATURES = [f"type={t}" for t in CHANGE_TYPES] + [
    "devices",
    "platforms",
    "hour_sin",
    "hour_cos",
    "weekend",
    "emergency",
    "prior_failure_rate",
]


@dataclass
class ChangeDataset:
    X: np.ndarray
    failed: np.ndarray  # 0/1
    duration_min: np.ndarray
    feature_names: list[str]
    source: str  # "database" | "synthetic"
    rows: int
    notes: list[str] = field(default_factory=list)


def change_features(
    change_type: str,
    devices: int,
    platforms: int,
    hour: int,
    weekend: bool,
    emergency: bool,
    prior_failure_rate: float,
) -> list[float]:
    onehot = [1.0 if change_type == t else 0.0 for t in CHANGE_TYPES]
    angle = 2 * math.pi * hour / 24
    return onehot + [
        float(devices),
        float(platforms),
        math.sin(angle),
        math.cos(angle),
        1.0 if weekend else 0.0,
        1.0 if emergency else 0.0,
        float(prior_failure_rate),
    ]


def synthetic_changes(n: int = 600, seed: int = 7) -> ChangeDataset:
    """Plausible change history: risk grows with blast radius, vendor mix,
    off-hours work, removals and emergency changes."""
    rnd = random.Random(seed)
    X, y, dur = [], [], []
    base_risk = {
        "vlan_add": 0.03,
        "vlan_remove": 0.07,
        "acl_entry_add": 0.06,
        "acl_entry_remove": 0.09,
        "static_route_add": 0.08,
        "static_route_remove": 0.12,
        "interface_set": 0.04,
    }
    for _ in range(n):
        ct = rnd.choice(CHANGE_TYPES)
        devices = max(1, int(rnd.expovariate(1 / 6)))
        platforms = min(devices, 1 + int(rnd.random() * 3 * rnd.random()))
        hour = rnd.choice(list(range(24)))
        weekend = rnd.random() < 0.2
        emergency = rnd.random() < 0.08
        prior = rnd.betavariate(1.2, 12)
        night = 1.0 if hour < 6 or hour >= 22 else 0.0
        logit = (
            math.log(base_risk[ct] / (1 - base_risk[ct]))
            + 0.08 * devices
            + 0.45 * (platforms - 1)
            + 0.6 * night
            + 0.9 * emergency
            + 4.0 * prior
            + rnd.gauss(0, 0.2)
        )
        failed = rnd.random() < 1 / (1 + math.exp(-logit))
        minutes = 6 + 2.5 * devices + 4 * (platforms - 1) + (15 if failed else 0) + rnd.gauss(0, 3)
        X.append(change_features(ct, devices, platforms, hour, weekend, emergency, prior))
        y.append(1 if failed else 0)
        dur.append(max(2.0, minutes))
    return ChangeDataset(
        np.array(X),
        np.array(y),
        np.array(dur),
        list(FEATURES),
        "synthetic",
        n,
        ["synthetic change history (not enough finished changes in the database yet)"],
    )


async def load_changes(
    session_factory: Any | None, min_rows: int = MIN_REAL_CHANGES
) -> ChangeDataset:
    """Finished changes from the database, else the synthetic set."""
    if session_factory is None:
        return synthetic_changes()
    from vireonet.models import AuditLog, Change

    try:
        async with session_factory() as s:
            changes = (await s.execute(select(Change))).scalars().all()
            audits = (await s.execute(select(AuditLog))).scalars().all()
    except Exception as exc:  # noqa: BLE001 — no DB: train on synthetic data, say so
        data = synthetic_changes()
        data.notes.append(f"database unavailable: {exc}")
        return data
    by_change: dict[Any, list[Any]] = {}
    for a in audits:
        by_change.setdefault(a.change_id, []).append(a)
    finished = [c for c in changes if str(getattr(c.state, "value", c.state)) in FINISHED_STATES]
    if len(finished) < min_rows:
        data = synthetic_changes()
        data.notes.append(f"{len(finished)} finished changes in the database; need {min_rows}")
        return data
    failures: dict[str, list[int]] = {}
    X, y, dur = [], [], []
    for c in sorted(finished, key=lambda c: c.created_at or datetime.min.replace(tzinfo=UTC)):
        state = str(getattr(c.state, "value", c.state))
        devices = list(c.devices or [])
        intent = dict(c.intent or {})
        created = c.created_at or datetime.now(UTC)
        rows = sorted(by_change.get(c.id, []), key=lambda a: a.created_at or created)
        start = next((a.created_at for a in rows if a.to_state == "approved"), None) or created
        end = rows[-1].created_at if rows else created
        prior = [r for d in devices for r in failures.get(d, [])]
        X.append(
            change_features(
                c.change_type,
                len(devices),
                int(intent.get("platform_count") or 1),
                created.hour,
                created.weekday() >= 5,
                bool(intent.get("emergency")),
                sum(prior) / len(prior) if prior else 0.0,
            )
        )
        failed = 1 if state in FAILED_STATES else 0
        y.append(failed)
        dur.append(max(0.5, ((end or created) - start).total_seconds() / 60))
        for d in devices:
            failures.setdefault(d, []).append(failed)
    return ChangeDataset(
        np.array(X), np.array(y), np.array(dur), list(FEATURES), "database", len(X)
    )


# --- Telemetry --------------------------------------------------------------------


@dataclass
class TrafficSeries:
    device: str
    interface: str
    timestamps: list[str]
    in_util: np.ndarray
    out_util: np.ndarray


async def load_traffic(
    telemetry: Any,
    interfaces: list[tuple[str, str]],
    *,
    minutes: int = 24 * 60,
    step_seconds: int = 600,
    speed_bps: float | None = None,
) -> tuple[list[TrafficSeries], list[str]]:
    """History per interface, as utilisation (0-1) when speed is known."""
    notes: list[str] = []
    series: list[TrafficSeries] = []
    speeds: dict[tuple[str, str], float] = {}
    try:
        for r in await telemetry.interface_rates(sorted({d for d, _ in interfaces})):
            if r.speed_bps:
                speeds[(r.device, r.interface)] = r.speed_bps
    except Exception as exc:  # noqa: BLE001
        notes.append(f"current rates unavailable: {exc}")
    for device, iface in interfaces:
        try:
            points = await telemetry.interface_history(device, iface, minutes, step_seconds)
        except Exception as exc:  # noqa: BLE001 — skip interfaces without history
            notes.append(f"{device} {iface}: {exc}")
            continue
        if len(points) < 6:
            continue
        speed = (
            speeds.get((device, iface))
            or speed_bps
            or max(max(p.in_bps for p in points), max(p.out_bps for p in points), 1.0)
        )
        series.append(
            TrafficSeries(
                device,
                iface,
                [p.timestamp for p in points],
                np.array([p.in_bps / speed for p in points]),
                np.array([p.out_bps / speed for p in points]),
            )
        )
    return series, notes
