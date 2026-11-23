from __future__ import annotations

import contextvars

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

# Correlation ID: the ticket number, carried in a contextvar so every log
# line and metric for a change can be joined without passing it explicitly.
ticket_var: contextvars.ContextVar[str] = contextvars.ContextVar("vireonet_ticket", default="")

CHANGES_TOTAL = Counter("vireonet_changes_total", "Changes by final state", ["state"])
WORKFLOW_DURATION = Histogram("vireonet_workflow_duration_seconds", "Workflow duration", ["workflow"])
ADAPTER_CALLS = Counter("vireonet_adapter_calls_total", "Adapter calls by adapter/operation/status", ["adapter", "operation", "status"])
CHECK_FAILURES = Counter("vireonet_check_failures_total", "Check failures by check name", ["check"])
LOCKS_HELD = Gauge("vireonet_device_locks_held", "Device locks currently held")
ZTP_TOTAL = Counter("vireonet_ztp_total", "ZTP devices by stage", ["stage"])


def set_ticket(ticket: str) -> None:
    ticket_var.set(ticket)


def get_ticket() -> str:
    return ticket_var.get()


def record_change(state: str) -> None:
    CHANGES_TOTAL.labels(state=state).inc()


def record_adapter_call(adapter: str, operation: str, status: str) -> None:
    ADAPTER_CALLS.labels(adapter=adapter, operation=operation, status=status).inc()


def record_check_failure(check: str) -> None:
    CHECK_FAILURES.labels(check=check).inc()


def metrics_bytes() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
