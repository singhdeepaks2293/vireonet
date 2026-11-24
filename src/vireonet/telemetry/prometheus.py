"""Prometheus: SNMP via snmp_exporter, or gNMI/gRPC streaming via gnmic.

Presets build the PromQL; override with custom queries that keep the
device and interface labels. Rates are bits/s (octet counters x 8).

PromQL regex matchers are fully anchored, and the device label usually
carries a port (gnmic ``source`` is "routerIP:port"; snmp_exporter
``instance`` may be "host:port"), so devices are matched as
``(dev1|dev2)(:\\d+)?``.

gnmic metric names assume the prometheus output defaults
``metric-prefix: ""`` and ``append-subscription-name: false``. If your gnmic
config sets a prefix, pass ``metric_prefix`` (e.g. "gnmic") and it is
prepended as "<prefix>_" to the gnmic metric names.
See https://gnmic.openconfig.net/user_guide/outputs/prometheus_output/
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from typing import Any

from vireonet.telemetry.base import HttpBackend, InterfaceRate, MetricPoint

PRESETS: dict[str, dict[str, str]] = {
    # snmp_exporter if_mib module: counters labelled instance + ifName.
    "snmp_exporter": {
        "in": 'rate(ifHCInOctets{{instance=~"{devices}"}}[5m]) * 8',
        "out": 'rate(ifHCOutOctets{{instance=~"{devices}"}}[5m]) * 8',
        "device_label": "instance",
        "interface_label": "ifName",
    },
    # gnmic prometheus output, OpenConfig interfaces model.
    "gnmic": {
        "in": 'rate({prefix}interfaces_interface_state_counters_in_octets{{source=~"{devices}"}}[5m]) * 8',
        "out": 'rate({prefix}interfaces_interface_state_counters_out_octets{{source=~"{devices}"}}[5m]) * 8',
        "device_label": "source",
        "interface_label": "interface_name",
    },
}


def _promql_regex(devices: list[str]) -> str:
    """Anchored-safe device regex: (dev1|dev2)(:\\d+)? inside a PromQL string.

    Regex metacharacters are escaped, then backslashes and quotes are escaped
    again for the double-quoted PromQL string literal.
    """
    alts = "|".join(re.escape(d) for d in devices)
    regex = f"({alts})(:\\d+)?"
    return regex.replace("\\", "\\\\").replace('"', '\\"')


class PrometheusBackend(HttpBackend):
    name = "prometheus"

    def __init__(
        self,
        base_url: str,
        *,
        preset: str = "snmp_exporter",
        token: str = "",
        metric_prefix: str = "",
        **kw: Any,
    ) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        super().__init__(base_url, headers=headers, **kw)
        if preset not in PRESETS:
            raise ValueError(f"unknown prometheus preset {preset!r} (want {sorted(PRESETS)})")
        self.preset = PRESETS[preset]
        self.name = f"prometheus/{preset}"
        self._prefix = f"{metric_prefix.rstrip('_')}_" if metric_prefix else ""

    def _query(self, direction: str, devices: list[str]) -> str:
        return self.preset[direction].format(devices=_promql_regex(devices), prefix=self._prefix)

    async def health(self) -> dict[str, Any]:
        data = await self._call("GET", "/api/v1/status/buildinfo")
        return {
            "backend": self.name,
            "status": "ok",
            "version": data.get("data", {}).get("version"),
        }

    def _labels(self, metric: dict[str, str]) -> tuple[str, str]:
        dev = metric.get(self.preset["device_label"], "")
        return dev.split(":")[0], metric.get(self.preset["interface_label"], "")

    async def interface_rates(self, devices: list[str]) -> list[InterfaceRate]:
        if not devices:
            return []
        rows: dict[tuple[str, str], InterfaceRate] = {}
        for direction in ("in", "out"):
            q = self._query(direction, devices)
            data = await self._call("GET", "/api/v1/query", params={"query": q})
            for r in data.get("data", {}).get("result", []):
                dev, iface = self._labels(r.get("metric", {}))
                ts, value = r.get("value", [None, "0"])
                row = rows.setdefault(
                    (dev, iface), InterfaceRate(dev, iface, 0.0, 0.0, source=self.name)
                )
                setattr(row, f"{direction}_bps", float(value))
                row.timestamp = datetime.fromtimestamp(float(ts), UTC).isoformat() if ts else None
        return list(rows.values())

    async def interface_history(
        self, device: str, interface: str, minutes: int = 60, step_seconds: int = 300
    ) -> list[MetricPoint]:
        end = time.time()
        series: dict[str, dict[float, float]] = {}
        for direction in ("in", "out"):
            q = self._query(direction, [device])
            data = await self._call(
                "GET",
                "/api/v1/query_range",
                params={"query": q, "start": end - minutes * 60, "end": end, "step": step_seconds},
            )
            for r in data.get("data", {}).get("result", []):
                if self._labels(r.get("metric", {}))[1] != interface:
                    continue
                series[direction] = {float(t): float(v) for t, v in r.get("values", [])}
        stamps = sorted(set(series.get("in", {})) | set(series.get("out", {})))
        return [
            MetricPoint(
                datetime.fromtimestamp(t, UTC).isoformat(),
                series.get("in", {}).get(t, 0.0),
                series.get("out", {}).get(t, 0.0),
            )
            for t in stamps
        ]
