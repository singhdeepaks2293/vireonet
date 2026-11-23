"""Drift scan scheduling (D3).

One schedule spec per site, built from Settings (drift_sites,
drift_cron). A spec is a plain dataclass — deliberately NOT a Temporal
Schedule object — so schedule building is pure and testable without a
Temporal server. create_temporal_schedule wraps the real Temporal
schedule creation; schedules are created by the worker at startup (see
worker.py), never by the API.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from vireonet.config import Settings

_CRON_FIELD = re.compile(r"^(\*|\d+|\d+-\d+|\*/\d+|\d+(,\d+)*)$")


def _valid_cron(cron: str) -> bool:
    parts = cron.strip().split()
    return len(parts) == 5 and all(bool(_CRON_FIELD.match(p)) for p in parts)


@dataclass(frozen=True)
class DriftScheduleSpec:
    name: str
    site: str
    cron: str
    jitter_seconds: int
    max_parallel: int = 5


def build_drift_schedules(config: Settings, *, max_parallel: int = 5) -> list[DriftScheduleSpec]:
    """One schedule spec per site in settings.drift_sites."""
    if not _valid_cron(config.drift_cron):
        raise ValueError(f"invalid drift cron expression: {config.drift_cron!r}")
    specs: list[DriftScheduleSpec] = []
    for site in config.drift_sites:
        # Deterministic jitter from the site name (0–299s) so sites do
        # not all scan at the same second, without any randomness.
        digest = hashlib.sha256(site.encode()).hexdigest()
        jitter = int(digest[:4], 16) % 300
        specs.append(
            DriftScheduleSpec(
                name=f"drift-scan-{site}",
                site=site,
                cron=config.drift_cron,
                jitter_seconds=jitter,
                max_parallel=max_parallel,
            )
        )
    return specs


async def create_temporal_schedule(client: Any, spec: DriftScheduleSpec) -> str:
    """Create (or update) the Temporal schedule for one spec.

    Uses temporalio.client.Schedule when the library is available; the
    import is guarded so importing this module never requires Temporal.
    Returns the schedule id. Raises RuntimeError when the Temporal
    schedule API is unavailable in the installed temporalio version.
    """
    try:
        from temporalio.client import (
            Schedule,
            ScheduleActionStartWorkflow,
            ScheduleSpec,
        )
    except ImportError as exc:
        raise RuntimeError(
            "temporalio schedule API is unavailable; drift schedules must be created manually"
        ) from exc
    from vireonet.drift.workflows import DriftScanInput, DriftScanWorkflow

    try:
        schedule = Schedule(
            action=ScheduleActionStartWorkflow(
                DriftScanWorkflow.run,
                DriftScanInput(site=spec.site, date=""),
                id=f"drift-scan-{spec.site}",
                task_queue="vireonet-changes",
            ),
            spec=ScheduleSpec(cron_expressions=[spec.cron]),
        )
        handle = await client.create_schedule(
            spec.name,
            schedule,
            id=spec.name,
        )
    except (AttributeError, TypeError) as exc:
        raise RuntimeError(
            "temporalio schedule API differs from the expected shape; "
            "drift schedules must be created manually"
        ) from exc
    return str(handle.id)
