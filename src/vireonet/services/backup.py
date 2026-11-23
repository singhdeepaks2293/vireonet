"""Backup collection: run the backup step through the execution engine,
extract the running config from its stdout, store it, and diff it against
the candidate config (F1).

Contract with ansible/playbooks/backup.yml: the playbook prints the config
between the VIREONET-BACKUP-BEGIN / VIREONET-BACKUP-END markers, and this
module extracts exactly that span.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, cast

from vireonet.adapters.protocols import AwxProtocol
from vireonet.config_store.store import ConfigStore
from vireonet.diff import apply_intent_to_config
from vireonet.execution.awx_engine import AwxEngine
from vireonet.execution.engine import ExecutionEngine
from vireonet.execution.mapping import ExecutionMapping
from vireonet.execution.types import DeviceRef, StepRequest
from vireonet.models import DeviceConfigVersion

BEGIN_MARKER = "VIREONET-BACKUP-BEGIN"
END_MARKER = "VIREONET-BACKUP-END"

_MARKER_RE = re.compile(r"VIREONET-BACKUP-BEGIN\s*(.*?)\s*VIREONET-BACKUP-END", re.DOTALL)


def extract_running_config(stdout: str) -> str:
    """Text between the backup markers. Raises ValueError if the markers are
    missing or the body between them is empty."""
    match = _MARKER_RE.search(stdout)
    if match is None:
        raise ValueError(
            f"running config markers {BEGIN_MARKER}/{END_MARKER} not found in AWX stdout"
        )
    body = match.group(1).strip()
    if not body:
        raise ValueError("running config between backup markers is empty")
    return body


class BackupService:
    """Collects backups through an ExecutionEngine.

    The constructor accepts an engine directly, or — as a migration shim
    for existing call sites and tests — a raw AwxProtocol adapter, which is
    wrapped in an AwxEngine with the default execution mapping.
    """

    def __init__(self, engine: ExecutionEngine | AwxProtocol, store: ConfigStore) -> None:
        backup = getattr(engine, "backup", None)
        if callable(backup):
            self._engine: ExecutionEngine = cast(ExecutionEngine, engine)
        else:
            try:
                mapping = ExecutionMapping.from_yaml("policies/execution.yaml")
            except (OSError, ValueError):
                mapping = ExecutionMapping.default()
            self._engine = AwxEngine(engine, mapping)  # type: ignore[arg-type]
        self._store = store

    async def collect(self, device: str, ticket_number: str) -> DeviceConfigVersion:
        result = await self._engine.backup(
            StepRequest(
                ticket_number=ticket_number,
                device=DeviceRef(name=device),
                step="backup",
                dry_run=True,
                correlation_id=ticket_number,
            )
        )
        if not result.ok:
            raise ValueError(
                f"backup job {result.job_id} for {device} ended with status {result.status.value}"
            )
        stdout = str(result.output.get("stdout") or result.output.get("stdout_tail") or "")
        if not stdout:
            raise ValueError("backup produced no stdout; cannot read backup output")
        config = extract_running_config(stdout)
        return await self._store.store_running(device, config, ticket_number=ticket_number)

    async def collect_and_diff(
        self, change_id: uuid.UUID | str, device: str, ticket_number: str, intent: dict[str, Any]
    ) -> dict[str, Any]:
        version = await self.collect(device, ticket_number)
        candidate = apply_intent_to_config(version.content, intent)
        diff_row = await self._store.store_change_diff(
            change_id, device, version.content, candidate
        )
        return {
            "device": device,
            "running_version": version.version,
            "added": diff_row.added,
            "removed": diff_row.removed,
            "is_empty": diff_row.is_empty,
        }
