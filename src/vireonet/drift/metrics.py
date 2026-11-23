"""Drift Prometheus metrics (D3).

Reuses the existing prometheus_client default registry (the same one
vireonet.observability exposes at /metrics) — do NOT create a second
registry. Counters/gauges are module-level, like the existing ones.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

DRIFT_SCAN_DURATION = Histogram(
    "vireonet_drift_scan_duration_seconds",
    "Drift scan duration by site",
    ["site"],
)
DRIFT_RECORDS = Counter(
    "vireonet_drift_records_total",
    "Drift records created, by severity and kind",
    ["severity", "kind"],
)
DRIFT_SCAN_FAILURES = Counter(
    "vireonet_drift_scan_failures_total",
    "Drift scan failures, by site and reason",
    ["site", "reason"],
)
DRIFT_UNREACHABLE_DEVICES = Gauge(
    "vireonet_drift_unreachable_devices",
    "Devices unreachable during the last drift scan, by site",
    ["site"],
)


def record_drift_record(severity: str, kind: str) -> None:
    DRIFT_RECORDS.labels(severity=severity, kind=kind).inc()


def record_drift_scan_failure(site: str, reason: str) -> None:
    DRIFT_SCAN_FAILURES.labels(site=site, reason=reason).inc()


def set_unreachable_devices(site: str, count: int) -> None:
    DRIFT_UNREACHABLE_DEVICES.labels(site=site).set(count)
