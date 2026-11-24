"""SolarWinds Orion NPM via the SWIS REST API (basic auth).

Port: 17774 on Orion Platform 2023.1 and later; 17778 on older releases
(deprecated). Put the port in the base URL, e.g. https://orion:17774.
InBps / OutBps are bits/s. Caption must match the NetBox hostname.
"""

from __future__ import annotations

from typing import Any

from vireonet.telemetry.base import HttpBackend, InterfaceRate, MetricPoint

_QUERY = "/SolarWinds/InformationService/v3/Json/Query"


class SolarWindsBackend(HttpBackend):
    name = "solarwinds"

    def __init__(self, base_url: str, *, username: str, password: str, **kw: Any) -> None:
        super().__init__(base_url, auth=(username, password), **kw)

    async def _swql(self, query: str, **params: Any) -> list[dict[str, Any]]:
        data = await self._call("POST", _QUERY, json={"query": query, "parameters": params})
        return list(data.get("results", []))

    async def health(self) -> dict[str, Any]:
        await self._swql("SELECT TOP 1 NodeID FROM Orion.Nodes")
        return {"backend": self.name, "status": "ok"}

    async def interface_rates(self, devices: list[str]) -> list[InterfaceRate]:
        rows = await self._swql(
            "SELECT N.Caption AS Device, I.Name AS Interface, I.InBps, I.OutBps, I.Speed, "
            "I.InErrorsThisHour, I.OutErrorsThisHour, I.StatusDescription, I.LastSync "
            "FROM Orion.NPM.Interfaces I INNER JOIN Orion.Nodes N ON I.NodeID = N.NodeID "
            "WHERE N.Caption IN @devices",
            devices=devices,
        )
        return [
            InterfaceRate(
                device=str(r["Device"]),
                interface=str(r["Interface"]),
                in_bps=float(r.get("InBps") or 0),
                out_bps=float(r.get("OutBps") or 0),
                speed_bps=float(r["Speed"]) if r.get("Speed") else None,
                oper_status=r.get("StatusDescription"),
                timestamp=r.get("LastSync"),
                source=self.name,
            )
            for r in rows
        ]

    async def interface_history(
        self, device: str, interface: str, minutes: int = 60, step_seconds: int = 300
    ) -> list[MetricPoint]:
        rows = await self._swql(
            "SELECT T.DateTime, T.InAveragebps, T.OutAveragebps FROM Orion.NPM.InterfaceTraffic T "
            "INNER JOIN Orion.NPM.Interfaces I ON T.InterfaceID = I.InterfaceID "
            "INNER JOIN Orion.Nodes N ON I.NodeID = N.NodeID "
            "WHERE N.Caption = @device AND I.Name = @iface AND T.DateTime > ADDMINUTE(-@minutes, GETUTCDATE()) "
            "ORDER BY T.DateTime",
            device=device,
            iface=interface,
            minutes=minutes,
        )
        return [
            MetricPoint(
                str(r["DateTime"]),
                float(r.get("InAveragebps") or 0),
                float(r.get("OutAveragebps") or 0),
            )
            for r in rows
        ]
