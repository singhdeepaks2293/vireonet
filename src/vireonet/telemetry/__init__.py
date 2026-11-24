"""Interface telemetry: in/out traffic rates from monitoring tools.

Backends: Zabbix, Prometheus (SNMP via snmp_exporter, gNMI/gRPC via gnmic),
LibreNMS, PRTG, SolarWinds Orion, and a synthetic fake for demo mode.
All return the same InterfaceRate / MetricPoint shapes, in bits per second.
"""

from vireonet.telemetry.base import InterfaceRate, MetricPoint, TelemetryBackend, TelemetryError

__all__ = ["InterfaceRate", "MetricPoint", "TelemetryBackend", "TelemetryError"]
