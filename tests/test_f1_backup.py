"""F1 backup: ConfigStore on sqlite+aiosqlite, marker extraction, BackupService."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from vireonet.adapters.fakes import FakeAwxAdapter
from vireonet.adapters.protocols import AwxJob
from vireonet.config_store.store import ConfigStore
from vireonet.diff import compute_diff
from vireonet.services.backup import BackupService, extract_running_config

RUNNING_V1 = "hostname leaf-01\n!\nvlan 1\n name default\n!\n"
RUNNING_V2 = "hostname leaf-01\n!\nvlan 1\n name default\nvlan 100\n name web\n!\n"


@pytest_asyncio.fixture()
async def store(engine: AsyncEngine) -> ConfigStore:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return ConfigStore(factory)


class StubAwx(FakeAwxAdapter):
    """Fake AWX that also answers poll_job / get_job_stdout like the real adapter."""

    def __init__(self, stdout: str) -> None:
        super().__init__()
        self._stdout = stdout

    async def poll_job(
        self, job_id: int, *, timeout_seconds: int = 600, interval_seconds: float = 5.0
    ) -> AwxJob:
        return await self.get_job(job_id)

    async def get_job_stdout(self, job_id: int) -> str:
        return self._stdout


def _stdout(config: str) -> str:
    return f"PLAY [backup] ***\nVIREONET-BACKUP-BEGIN\n{config}\nVIREONET-BACKUP-END\n"


async def test_store_running_dedupes_identical_config(store: ConfigStore) -> None:
    first = await store.store_running("leaf-01", RUNNING_V1, ticket_number="RITM1")
    second = await store.store_running("leaf-01", RUNNING_V1 + "\n", ticket_number="RITM1")
    assert first.id == second.id
    assert first.version == 1 and second.version == 1
    assert first.content_hash == second.content_hash
    assert (await store.latest("leaf-01")) is not None
    assert len(await store.history("leaf-01")) == 1


async def test_store_running_new_version_and_history_newest_first(store: ConfigStore) -> None:
    v1 = await store.store_running("leaf-01", RUNNING_V1, ticket_number="RITM1")
    v2 = await store.store_running("leaf-01", RUNNING_V2, ticket_number="RITM2")
    assert v1.version == 1 and v2.version == 2
    assert v1.id != v2.id
    history = await store.history("leaf-01")
    assert [h["version"] for h in history] == [2, 1]
    assert history[0]["content_hash"] == v2.content_hash
    assert history[0]["ticket_number"] == "RITM2"
    assert history[0]["lines"] == len(RUNNING_V2.strip().split("\n"))
    assert history[0]["source"] == "awx-backup"
    assert history[0]["created_at"] is not None
    assert (await store.get_version("leaf-01", 1)).content == RUNNING_V1  # type: ignore[union-attr]
    assert await store.get_version("leaf-01", 99) is None
    assert await store.latest("no-such-device") is None


async def test_compare_versions_and_missing_version(store: ConfigStore) -> None:
    await store.store_running("leaf-01", RUNNING_V1)
    await store.store_running("leaf-01", RUNNING_V2)
    diff = await store.compare_versions("leaf-01", 1, 2)
    assert diff == compute_diff("leaf-01", RUNNING_V1, RUNNING_V2)
    assert diff["added"] == 2 and diff["removed"] == 0
    with pytest.raises(ValueError, match="no config version 99"):
        await store.compare_versions("leaf-01", 1, 99)


async def test_store_change_diff_is_idempotent(store: ConfigStore) -> None:
    change_id = uuid.uuid4()
    await store.store_running("leaf-01", RUNNING_V1)
    candidate = RUNNING_V2
    first = await store.store_change_diff(change_id, "leaf-01", RUNNING_V1, candidate)
    second = await store.store_change_diff(change_id, "leaf-01", RUNNING_V1, candidate)
    assert first.id == second.id
    assert first.added == 2 and first.removed == 0 and first.is_empty is False
    assert first.candidate_hash == compute_diff("leaf-01", RUNNING_V1, candidate)["candidate_hash"]
    assert first.running_version_id is not None
    rows = await store.diffs_for_change(change_id)
    assert [r.device for r in rows] == ["leaf-01"]
    assert await store.diffs_for_change(uuid.uuid4()) == []


def test_extract_running_config_markers() -> None:
    assert extract_running_config(_stdout(RUNNING_V1)) == RUNNING_V1.strip()
    assert (
        extract_running_config("noise\n" + _stdout(RUNNING_V1) + "more noise") == RUNNING_V1.strip()
    )
    with pytest.raises(ValueError, match="markers"):
        extract_running_config("no markers here")
    with pytest.raises(ValueError, match="empty"):
        extract_running_config("VIREONET-BACKUP-BEGIN\n\nVIREONET-BACKUP-END\n")


async def test_backup_collect_and_diff(store: ConfigStore) -> None:
    awx = StubAwx(_stdout(RUNNING_V1))
    service = BackupService(awx, store)
    change_id = uuid.uuid4()
    result = await service.collect_and_diff(
        change_id,
        "leaf-01",
        "RITM1",
        {
            "change_type": "vlan_add",
            "vlan_id": 100,
            "vlan_name": "web",
            "interfaces": [],
        },
    )
    assert result == {
        "device": "leaf-01",
        "running_version": 1,
        "added": 2,
        "removed": 0,
        "is_empty": False,
    }
    version = await store.latest("leaf-01")
    assert version is not None and version.version == 1 and version.ticket_number == "RITM1"
    rows = await store.diffs_for_change(change_id)
    assert len(rows) == 1 and rows[0].added == 2
    # Collecting again with the same running config dedupes the version.
    again = await service.collect("leaf-01", "RITM1")
    assert again.version == 1 and again.id == version.id


async def test_backup_collect_fails_on_failed_job(store: ConfigStore) -> None:
    class FailingAwx(StubAwx):
        async def launch_job(
            self, template: str, extra_vars: dict[str, str], limit: str | None = None
        ) -> AwxJob:
            job = AwxJob(self._next_id, template, "failed", dict(extra_vars))
            self.jobs[job.id] = job
            self._next_id += 1
            return job

    with pytest.raises(ValueError, match="ended with status failed"):
        await BackupService(FailingAwx(_stdout(RUNNING_V1)), store).collect("leaf-01", "RITM1")
    assert await store.latest("leaf-01") is None
