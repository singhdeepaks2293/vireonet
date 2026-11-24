"""Zabbix (6.4+ API token auth, JSON-RPC at /api_jsonrpc.php).

Works with the standard SNMP and agent templates: traffic items have keys
net.if.in[...] / net.if.out[...] and names like
"Interface Gi0/1(uplink): Bits received". Values are already bits/s.

Only traffic items are used. Same-prefix items that are not bits/s are
skipped: agent keys with a non-traffic mode (net.if.in["eth0",errors],
...,dropped / packets) and SNMP keys with a suffix (net.if.in.errors[...],
net.if.in.discards[...]).
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from typing import Any

from vireonet.telemetry.base import HttpBackend, InterfaceRate, MetricPoint, TelemetryError

_NAME_RE = re.compile(
    r"Interface\s+(?P<iface>[^:(]+?)\s*(\([^)]*\))?\s*:\s*Bits (received|sent)", re.I
)
_KEY_RE = re.compile(r'net\.if\.(in|out)\[\s*"?(?P<iface>[^",\]]+)"?')
# Traffic keys only: net.if.in[<iface>] or net.if.in[<iface>,bytes] (agent),
# net.if.in[ifHCInOctets.N] (SNMP). No ".errors"/".discards" suffix and no
# mode other than bytes.
_TRAFFIC_KEY_RE = re.compile(
    r'^net\.if\.(?P<dir>in|out)\[\s*("[^"]*"|[^,\]]*)\s*(,\s*"?(?P<mode>[^",\]]*)"?\s*)?\]$'
)
_ITEM_NAME_DIR_RE = re.compile(r"Bits (?P<dir>received|sent)\s*$", re.I)


def _direction(item: dict[str, Any]) -> str | None:
    """'in' / 'out' for a traffic (bits/s) item, None for anything else."""
    m = _TRAFFIC_KEY_RE.match(str(item.get("key_", "")).strip())
    if not m or (m.group("mode") or "bytes").lower() != "bytes":
        return None
    name = str(item.get("name", ""))
    n = _ITEM_NAME_DIR_RE.search(name)
    if name and "bits" in name.lower() and n is None:
        return None
    return m.group("dir")


def _iface(item: dict[str, Any]) -> str:
    m = _NAME_RE.search(str(item.get("name", "")))
    if m:
        return m.group("iface").strip()
    m = _KEY_RE.search(str(item.get("key_", "")))
    return m.group("iface").strip() if m else str(item.get("key_", ""))


def _ts(clock: Any) -> str | None:
    try:
        return datetime.fromtimestamp(int(clock), UTC).isoformat()
    except (TypeError, ValueError):
        return None


class ZabbixBackend(HttpBackend):
    name = "zabbix"

    def __init__(self, base_url: str, *, token: str, **kw: Any) -> None:
        super().__init__(base_url, headers={"Authorization": f"Bearer {token}"}, **kw)
        self._id = 0

    async def _rpc(self, method: str, params: dict[str, Any]) -> Any:
        self._id += 1
        body = {"jsonrpc": "2.0", "method": method, "params": params, "id": self._id}
        data = await self._call("POST", "/api_jsonrpc.php", json=body)
        if isinstance(data, dict) and "error" in data:
            err = data["error"]
            raise TelemetryError(f"zabbix {method}: {err.get('message')} {err.get('data', '')}")
        return data.get("result") if isinstance(data, dict) else None

    async def health(self) -> dict[str, Any]:
        version = await self._rpc("apiinfo.version", {})
        return {"backend": self.name, "status": "ok", "version": version}

    async def _hosts(self, devices: list[str]) -> dict[str, str]:
        hosts = await self._rpc(
            "host.get", {"output": ["hostid", "host"], "filter": {"host": devices}}
        )
        return {str(h["hostid"]): str(h["host"]) for h in hosts or []}

    async def _items(self, hostids: list[str]) -> list[dict[str, Any]]:
        return list(
            await self._rpc(
                "item.get",
                {
                    "output": ["itemid", "hostid", "name", "key_", "lastvalue", "lastclock"],
                    "hostids": hostids,
                    # Plain search is a LIKE '%net.if.%' match. Do NOT set
                    # searchWildcardsEnabled: with it on the implicit %...%
                    # is dropped and "net.if." matches nothing.
                    "search": {"key_": "net.if."},
                },
            )
            or []
        )

    async def interface_rates(self, devices: list[str]) -> list[InterfaceRate]:
        hosts = await self._hosts(devices)
        if not hosts:
            return []
        rows: dict[tuple[str, str], InterfaceRate] = {}
        for item in await self._items(list(hosts)):
            direction = _direction(item)
            if direction is None:
                continue
            device, iface = hosts[str(item["hostid"])], _iface(item)
            row = rows.setdefault(
                (device, iface), InterfaceRate(device, iface, 0.0, 0.0, source=self.name)
            )
            value = float(item.get("lastvalue") or 0)
            if direction == "in":
                row.in_bps = value
            else:
                row.out_bps = value
            row.timestamp = _ts(item.get("lastclock")) or row.timestamp
        return list(rows.values())

    async def interface_history(
        self, device: str, interface: str, minutes: int = 60, step_seconds: int = 300
    ) -> list[MetricPoint]:
        hosts = await self._hosts([device])
        if not hosts:
            return []
        ids: dict[str, str] = {}
        for i in await self._items(list(hosts)):
            direction = _direction(i)
            if direction is not None and _iface(i) == interface:
                ids[direction] = str(i["itemid"])
        if not ids:
            return []
        since = int(time.time()) - minutes * 60
        series: dict[str, dict[int, float]] = {}
        for direction, itemid in ids.items():
            hist = await self._rpc(
                "history.get",
                {
                    "itemids": [itemid],
                    "history": 3,
                    "time_from": since,
                    "sortfield": "clock",
                    "sortorder": "ASC",
                },
            )
            bucket: dict[int, float] = {}
            for h in hist or []:
                bucket[int(h["clock"]) // step_seconds * step_seconds] = float(h["value"])
            series[direction] = bucket
        clocks = sorted(set(series.get("in", {})) | set(series.get("out", {})))
        return [
            MetricPoint(
                _ts(c) or "", series.get("in", {}).get(c, 0.0), series.get("out", {}).get(c, 0.0)
            )
            for c in clocks
        ]
