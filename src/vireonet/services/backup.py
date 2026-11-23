"""Backup collection: run the AWX backup template, extract the running config
from its stdout, store it, and diff it against the candidate config (F1).

Contract with ansible/playbooks/backup.yml: the playbook prints the config
between the VIREONET-BACKUP-BEGIN / VIREONET-BACKUP-END markers, and this
module extracts exactly that span.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from vireonet.adapters.protocols import AwxProtocol
from vireonet.config_store.store import ConfigStore
from vireonet.diff import apply_intent_to_config
from vireonet.models import DeviceConfigVersion

BEGIN_MARKER = "VIREONET-BACKUP-BEGIN"
END_MARKER = "VIREONET-BACKUP-END"

_MARKER_RE = re.compile(r"VIREONET-BACKUP-BEGIN\s*(.*?)\s*VIREONET-BACKUP-END", re.DOTALL)

_TERMINAL_STATUSES = {"successful", "failed", "canceled"}


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
    def __init__(self, awx: AwxProtocol, store: ConfigStore) -> None:
        self._awx = awx
        self._store = store

    async def collect(self, device: str, ticket_number: str) -> DeviceConfigVersion:
        job = await self._awx.launch_job("backup", {"ticket": ticket_number}, limit=device)
        # poll_job / get_job_stdout exist on the real AWX adapter but are not
        # part of AwxProtocol (and the fake has neither); same getattr pattern
        # as temporal/activities.py.
        poll = getattr(self._awx, "poll_job", None)
        if poll is not None and job.status not in _TERMINAL_STATUSES:
            job = await poll(job.id, timeout_seconds=600)
        if job.status != "successful":
            raise ValueError(f"backup job {job.id} for {device} ended with status {job.status}")
        get_stdout = getattr(self._awx, "get_job_stdout", None)
        if get_stdout is None:
            raise ValueError(
                "awx adapter does not expose get_job_stdout; cannot read backup output"
            )
        stdout = await get_stdout(job.id)
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
