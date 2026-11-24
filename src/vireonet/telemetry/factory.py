from __future__ import annotations

from typing import Any

from vireonet.telemetry.base import TelemetryBackend, TelemetryError


def build_telemetry(settings: Any) -> TelemetryBackend | None:
    """The telemetry backend from settings, or None when disabled.

    Fake adapter mode with telemetry_backend "none" uses synthetic traffic,
    so demos and the ML tab have data.
    """
    backend = str(getattr(settings, "telemetry_backend", "none"))
    if backend == "none" and getattr(settings, "adapter_mode", "") == "fake":
        backend = "fake"
    if backend == "none":
        return None
    kw: dict[str, Any] = {
        "timeout": float(getattr(settings, "adapter_timeout_seconds", 10.0)),
        "verify_tls": bool(getattr(settings, "telemetry_verify_tls", True)),
    }
    url = str(getattr(settings, "telemetry_base_url", ""))
    token = str(getattr(settings, "telemetry_token", ""))
    if backend == "fake":
        from vireonet.telemetry.fake import FakeTelemetry

        return FakeTelemetry()
    if backend == "zabbix":
        from vireonet.telemetry.zabbix import ZabbixBackend

        return ZabbixBackend(url, token=token, **kw)
    if backend == "prometheus":
        from vireonet.telemetry.prometheus import PrometheusBackend

        preset = str(getattr(settings, "telemetry_prometheus_preset", "snmp_exporter"))
        return PrometheusBackend(
            url,
            preset=preset,
            token=token,
            metric_prefix=str(getattr(settings, "telemetry_metric_prefix", "") or ""),
            **kw,
        )
    if backend == "librenms":
        from vireonet.telemetry.librenms import LibreNMSBackend

        return LibreNMSBackend(url, token=token, **kw)
    if backend == "prtg":
        from vireonet.telemetry.prtg import PRTGBackend

        return PRTGBackend(url, token=token, **kw)
    if backend == "solarwinds":
        from vireonet.telemetry.solarwinds import SolarWindsBackend

        return SolarWindsBackend(
            url,
            username=str(getattr(settings, "telemetry_username", "")),
            password=str(getattr(settings, "telemetry_password", "")),
            **kw,
        )
    raise TelemetryError(f"unknown telemetry backend {backend!r}")
