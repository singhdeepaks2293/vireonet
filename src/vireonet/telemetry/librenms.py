"""LibreNMS (API token, /api/v0). Port rates are octets/s -> bits/s.

LibreNMS exposes history as graphs (PNG/RRD), not JSON, so
interface_history is not supported for this backend.
"""

from __future__ import annotations

from typing import Any

from vireonet.telemetry.base import HttpBackend, InterfaceRate, MetricPoint, TelemetryError

_COLUMNS = "ifName,ifInOctets_rate,ifOutOctets_rate,ifSpeed,ifOperStatus,ifInErrors_rate,ifOutErrors_rate,poll_time"


def _num_getter(port: dict[str, Any]) -> Any:
    def f(key: str) -> float | None:
        v = port.get(key)
        return float(v) if v not in (None, "") else None

    return f


class LibreNMSBackend(HttpBackend):
    name = "librenms"

    def __init__(self, base_url: str, *, token: str, **kw: Any) -> None:
        super().__init__(base_url, headers={"X-Auth-Token": token}, **kw)

    async def health(self) -> dict[str, Any]:
        data = await self._call("GET", "/api/v0/system")
        return {"backend": self.name, "status": "ok", "detail": data.get("status")}

    async def interface_rates(self, devices: list[str]) -> list[InterfaceRate]:
        out: list[InterfaceRate] = []
        for device in devices:
            data = await self._call(
                "GET", f"/api/v0/devices/{device}/ports", params={"columns": _COLUMNS}
            )
            for p in data.get("ports", []):
                f = _num_getter(p)
                out.append(
                    InterfaceRate(
                        device=device,
                        interface=str(p.get("ifName", "")),
                        in_bps=(f("ifInOctets_rate") or 0.0) * 8,
                        out_bps=(f("ifOutOctets_rate") or 0.0) * 8,
                        speed_bps=f("ifSpeed"),
                        in_errors_ps=f("ifInErrors_rate"),
                        out_errors_ps=f("ifOutErrors_rate"),
                        oper_status=p.get("ifOperStatus"),
                        source=self.name,
                    )
                )
        return out

    async def interface_history(
        self, device: str, interface: str, minutes: int = 60, step_seconds: int = 300
    ) -> list[MetricPoint]:
        raise TelemetryError("librenms: interface history is only available as graphs, not JSON")
