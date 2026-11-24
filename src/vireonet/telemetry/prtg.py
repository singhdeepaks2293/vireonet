"""PRTG Network Monitor (API token, PRTG 22.1+). Traffic sensors carry the
default tag "bandwidthsensor" and channels named "Traffic In (speed)" /
"Traffic Out (speed)" (names vary by sensor type and language, so channels
are matched by prefix). The channel table is requested with the documented
column "lastvalue_", which PRTG answers with "lastvalue" and "lastvalue_raw"
(bytes/s). The PRTG device name must match the NetBox hostname.

/api/historicdata is limited by PRTG to 5 requests per minute, so history
responses are cached in memory for 60 s per sensor + range.
See https://www.paessler.com/manuals/prtg/live_multiple_object_property_status
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

from vireonet.telemetry.base import HttpBackend, InterfaceRate, MetricPoint

_HISTORY_TTL_SECONDS = 60.0


def _channel_value(values: dict[str, float], prefix: str) -> float:
    return next((v for name, v in values.items() if name.startswith(prefix)), 0.0)


def _hist_value(row: dict[str, Any], prefix: str) -> float:
    # historicdata.json keys look like "Traffic In (speed)(RAW)".
    for key, v in row.items():
        if key.startswith(prefix) and key.endswith("(RAW)"):
            try:
                return float(v or 0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


class PRTGBackend(HttpBackend):
    name = "prtg"

    def __init__(self, base_url: str, *, token: str, **kw: Any) -> None:
        super().__init__(base_url, **kw)
        self._token = token
        self._history_cache: dict[tuple[Any, int, int], tuple[float, list[MetricPoint]]] = {}

    async def _get(self, path: str, **params: Any) -> Any:
        return await self._call("GET", path, params={"apitoken": self._token, **params})

    async def health(self) -> dict[str, Any]:
        data = await self._get("/api/status.json")
        return {"backend": self.name, "status": "ok", "version": data.get("Version")}

    async def _sensors(self, devices: list[str]) -> list[dict[str, Any]]:
        data = await self._get(
            "/api/table.json",
            content="sensors",
            columns="objid,device,sensor,tags",
            filter_tags="@tag(bandwidthsensor)",
        )
        wanted = set(devices)
        return [s for s in data.get("sensors", []) if s.get("device") in wanted]

    async def interface_rates(self, devices: list[str]) -> list[InterfaceRate]:
        out = []
        for s in await self._sensors(devices):
            channels = await self._get(
                "/api/table.json", content="channels", id=s["objid"], columns="name,lastvalue_"
            )
            values = {
                c.get("name", ""): float(c.get("lastvalue_raw") or 0)
                for c in channels.get("channels", [])
            }
            out.append(
                InterfaceRate(
                    device=str(s["device"]),
                    interface=str(s.get("sensor", "")),
                    in_bps=_channel_value(values, "Traffic In") * 8,
                    out_bps=_channel_value(values, "Traffic Out") * 8,
                    source=self.name,
                )
            )
        return out

    async def interface_history(
        self, device: str, interface: str, minutes: int = 60, step_seconds: int = 300
    ) -> list[MetricPoint]:
        sensor = next(
            (s for s in await self._sensors([device]) if s.get("sensor") == interface), None
        )
        if sensor is None:
            return []
        key = (sensor["objid"], minutes, step_seconds)
        cached = self._history_cache.get(key)
        if cached and time.monotonic() - cached[0] < _HISTORY_TTL_SECONDS:
            return list(cached[1])
        end = datetime.now(UTC)
        fmt = "%Y-%m-%d-%H-%M-%S"
        data = await self._get(
            "/api/historicdata.json",
            id=sensor["objid"],
            avg=step_seconds,
            sdate=(end - timedelta(minutes=minutes)).strftime(fmt),
            edate=end.strftime(fmt),
        )
        points = [
            MetricPoint(
                str(h.get("datetime", "")),
                _hist_value(h, "Traffic In") * 8,
                _hist_value(h, "Traffic Out") * 8,
            )
            for h in data.get("histdata", [])
        ]
        self._history_cache[key] = (time.monotonic(), points)
        return list(points)
