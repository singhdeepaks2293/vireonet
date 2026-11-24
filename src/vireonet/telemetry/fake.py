"""Synthetic interface traffic for fake/demo mode (and ML training data).

Deterministic per (device, interface): a daily cycle plus noise, with a few
injected anomalies (spikes and drops) so anomaly detection has something
real to find. Same inputs and time -> same numbers.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from vireonet.telemetry.base import InterfaceRate, MetricPoint

DEFAULT_INTERFACES = ["Ethernet1", "Ethernet2", "Ethernet3", "Ethernet4"]


def _seed(*parts: str) -> int:
    return int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:12], 16)


class FakeTelemetry:
    name = "fake"

    def __init__(
        self,
        interfaces_for: Callable[[str], list[str]] | None = None,
        speed_bps: float = 10e9,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._interfaces_for = interfaces_for or (lambda _d: list(DEFAULT_INTERFACES))
        self.speed_bps = speed_bps
        self._now = now or (lambda: datetime.now(UTC))

    def profile(self, device: str, interface: str) -> dict[str, float]:
        rnd = random.Random(_seed(device, interface))
        return {
            "base": rnd.uniform(0.05, 0.45),  # mean utilisation
            "swing": rnd.uniform(0.05, 0.25),  # daily amplitude
            "ratio": rnd.uniform(0.4, 1.6),  # out / in
            "phase": rnd.uniform(0, 2 * math.pi),
            "anomalous": 1.0 if rnd.random() < 0.15 else 0.0,
        }

    def sample(self, device: str, interface: str, at: datetime) -> tuple[float, float]:
        p = self.profile(device, interface)
        hours = at.timestamp() / 3600
        rnd = random.Random(_seed(device, interface, str(int(at.timestamp()) // 60)))
        util = p["base"] + p["swing"] * math.sin(2 * math.pi * hours / 24 + p["phase"])
        util += rnd.gauss(0, 0.02)
        # Anomalous interfaces burst for 20 minutes in every 6 hours.
        if p["anomalous"] and int(hours * 3) % 18 == 0:
            util = min(0.98, util * 2.8 + 0.3)
        util = max(0.001, min(0.99, util))
        in_bps = util * self.speed_bps
        out_bps = max(0.0, min(self.speed_bps, in_bps * p["ratio"] * rnd.uniform(0.9, 1.1)))
        return in_bps, out_bps

    async def health(self) -> dict[str, Any]:
        return {"backend": self.name, "status": "ok", "detail": "synthetic traffic"}

    async def interface_rates(self, devices: list[str]) -> list[InterfaceRate]:
        now = self._now()
        out = []
        for device in devices:
            for iface in self._interfaces_for(device):
                in_bps, out_bps = self.sample(device, iface, now)
                out.append(
                    InterfaceRate(
                        device,
                        iface,
                        in_bps,
                        out_bps,
                        speed_bps=self.speed_bps,
                        in_errors_ps=0.0,
                        out_errors_ps=0.0,
                        oper_status="up",
                        timestamp=now.isoformat(),
                        source=self.name,
                    )
                )
        return out

    async def interface_history(
        self, device: str, interface: str, minutes: int = 60, step_seconds: int = 300
    ) -> list[MetricPoint]:
        end = self._now()
        steps = max(1, minutes * 60 // step_seconds)
        points = []
        for n in range(steps, -1, -1):
            at = end - timedelta(seconds=n * step_seconds)
            i, o = self.sample(device, interface, at)
            points.append(MetricPoint(at.isoformat(), i, o))
        return points
