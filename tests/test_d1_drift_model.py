"""D1: drift data model, normalisation, baseline idempotency, locks, state machine."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from vireonet.adapters.base import AdapterHealth
from vireonet.adapters.git.base import GitCommitResult, GitFile, GitMR
from vireonet.config_store.device_repos import DeviceRepoManager
from vireonet.config_store.store import ConfigStore
from vireonet.drift.baseline import _clear_baseline_cache, commit_baseline
from vireonet.drift.collector import DeviceLockedForCollection, DriftCollector
from vireonet.drift.models import DriftAuditLog, DriftDecision, DriftIgnoreRule, DriftScan
from vireonet.drift.normalise import load_ignore_rules, normalize_for_drift
from vireonet.drift.state_machine import (
    DRIFT_TRANSITIONS,
    IllegalDriftTransitionError,
    transition_drift,
)
from vireonet.execution.types import StepResult, StepStatus
from vireonet.models import DriftRecord
from vireonet.safety import DeviceLockService

GOLDEN = Path(__file__).parent / "golden" / "drift"
PLATFORMS = ["ios", "iosxe", "nxos", "eos", "junos"]
RUNNING = "hostname leaf-01\ninterface GigabitEthernet0/1\n description uplink\n"


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    _clear_baseline_cache()


# -- normaliser golden files ----------------------------------------------


@pytest.mark.parametrize("platform", PLATFORMS)
def test_normaliser_golden_files(platform: str) -> None:
    rules = load_ignore_rules("policies/drift_ignore.yaml")
    raw = (GOLDEN / f"{platform}.txt").read_text(encoding="utf-8")
    expected = (GOLDEN / f"{platform}.expected.txt").read_text(encoding="utf-8")
    got = normalize_for_drift(raw, platform, rules)
    assert got == expected, f"{platform}: normalised output differs from golden file"
    # Volatile lines are gone, the real change survives.
    assert "Last configuration change" not in got
    assert "NVRAM config last updated" not in got
    assert "ntp clock-period" not in got
    assert "uptime is" not in got
    assert "# Last commit" not in got
    assert "secret 5 $" not in got and "password 7 1" not in got
    assert "CHANGED" in got


def test_normaliser_sorts_interface_blocks() -> None:
    config = "interface Ethernet2\n no shutdown\n!\ninterface Ethernet1\n no shutdown\n!\n"
    assert normalize_for_drift(config, "eos", {}) == (
        "interface Ethernet1\n no shutdown\n!\ninterface Ethernet2\n no shutdown\n!\n"
    )


def test_load_ignore_rules_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_ignore_rules(tmp_path / "nope.yaml") == {}


# -- baseline idempotency ---------------------------------------------------


class FakeGit:
    """GitProvider stand-in recording every commit (same shape as E3's)."""

    def __init__(self) -> None:
        self.commits: list[dict[str, str]] = []

    @property
    def name(self) -> str:
        return "fake-git"

    async def health_check(self) -> AdapterHealth:
        return AdapterHealth(name="fake-git", status="ok", capabilities=["commit"])

    def capabilities(self) -> list[str]:
        return ["commit"]

    async def get_file(self, path: str, ref: str) -> GitFile:
        return GitFile(path=path, content="")

    async def create_branch(self, branch: str, from_ref: str) -> str:
        return branch

    async def commit_file(
        self, branch: str, path: str, content: str, message: str
    ) -> GitCommitResult:
        self.commits.append(
            {"branch": branch, "path": path, "content": content, "message": message}
        )
        return GitCommitResult(sha=f"sha-{len(self.commits)}", branch=branch, path=path)

    async def open_mr(
        self, *, source_branch: str, target_branch: str, title: str, description: str = ""
    ) -> GitMR:
        return GitMR(
            id=1,
            iid=1,
            title=title,
            source_branch=source_branch,
            target_branch=target_branch,
            url="https://git.example/mr/1",
            state="opened",
        )

    async def get_mr(self, mr_id: int) -> GitMR:
        raise NotImplementedError

    async def get_pipeline(self, mr_id: int) -> object:
        raise NotImplementedError

    async def merge_mr(self, mr_id: int) -> GitMR:
        raise NotImplementedError

    async def add_comment(self, mr_id: int, body: str) -> None:
        pass

    async def get_mr_diff(self, mr_id: int) -> str:
        return ""


async def test_baseline_idempotent_same_config_one_commit(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    git = FakeGit()
    repos = DeviceRepoManager(git_factory=lambda repo: git, config_store=ConfigStore(factory))
    store = ConfigStore(factory)
    sha1 = await commit_baseline(
        device="leaf-01",
        running_config=RUNNING,
        ticket="RITM1",
        config_store=store,
        device_repos=repos,
    )
    sha2 = await commit_baseline(
        device="leaf-01",
        running_config=RUNNING + "\n",
        ticket="RITM1",
        config_store=store,
        device_repos=repos,
    )
    assert sha1 == sha2
    assert len(git.commits) == 1


async def test_baseline_changed_config_commits_again(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    git = FakeGit()
    repos = DeviceRepoManager(git_factory=lambda repo: git, config_store=ConfigStore(factory))
    store = ConfigStore(factory)
    await commit_baseline(
        device="leaf-02",
        running_config=RUNNING,
        ticket="RITM1",
        config_store=store,
        device_repos=repos,
    )
    await commit_baseline(
        device="leaf-02",
        running_config=RUNNING + "interface GigabitEthernet0/2\n",
        ticket="RITM1",
        config_store=store,
        device_repos=repos,
    )
    assert len(git.commits) == 2


# -- collector lock overlap ---------------------------------------------------


class CollectEngine:
    """Minimal engine returning a fixed running config from collect_state."""

    def __init__(self, config: str = RUNNING) -> None:
        self.config = config
        self.calls: list[str] = []

    async def collect_state(self, req: object) -> StepResult:
        self.calls.append("collect_state")
        return StepResult(
            status=StepStatus.SUCCESSFUL,
            step="collect_state",
            device="leaf-01",
            job_id=1,
            output={"running_config": self.config},
        )


async def test_collector_skips_locked_device(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    locks = DeviceLockService(factory)
    await locks.acquire("leaf-01", "RITM9")
    engine_stub = CollectEngine()
    collector = DriftCollector(engine_stub, locks)
    with pytest.raises(DeviceLockedForCollection):
        await collector.collect("leaf-01", "ios", "10.0.0.11", "DRIFT-1")
    assert engine_stub.calls == []  # engine never called for a locked device


async def test_collector_collects_unlocked_device(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    locks = DeviceLockService(factory)
    engine_stub = CollectEngine()
    collector = DriftCollector(engine_stub, locks)
    text = await collector.collect("leaf-01", "ios", "10.0.0.11", "DRIFT-1")
    assert text == RUNNING
    assert engine_stub.calls == ["collect_state"]


# -- state machine --------------------------------------------------------------


def test_drift_transitions_exact() -> None:
    assert {
        "detected": {"under_review", "resolved"},
        "under_review": {"reverting", "merge_pending", "exception_active", "resolved"},
        "reverting": {"resolved", "under_review"},
        "merge_pending": {"resolved", "under_review"},
        "exception_active": {"exception_expired", "resolved"},
        "exception_expired": {"under_review"},
        "resolved": set(),
    } == DRIFT_TRANSITIONS


async def test_drift_state_machine_legal_path_and_audit(session: AsyncSession) -> None:
    record = DriftRecord(device="leaf-01", kind="unmanaged", severity="high", diff="x")
    session.add(record)
    await session.flush()
    await transition_drift(session, record, "under_review", "alice", "triaging")
    await transition_drift(session, record, "reverting", "alice", "rolling back")
    await transition_drift(session, record, "resolved", "alice", "rolled back")
    await session.commit()
    assert record.status == "resolved"
    rows = (await session.execute(select(DriftAuditLog))).scalars().all()
    assert [(r.from_state, r.to_state) for r in rows] == [
        ("detected", "under_review"),
        ("under_review", "reverting"),
        ("reverting", "resolved"),
    ]


async def test_drift_state_machine_illegal_raises(session: AsyncSession) -> None:
    record = DriftRecord(device="leaf-01", kind="unmanaged", severity="high", diff="x")
    session.add(record)
    await session.flush()
    with pytest.raises(IllegalDriftTransitionError):
        await transition_drift(session, record, "reverting", "alice")
    with pytest.raises(IllegalDriftTransitionError):
        await transition_drift(session, record, "exception_expired", "alice")
    # resolved is terminal.
    await transition_drift(session, record, "resolved", "alice")
    with pytest.raises(IllegalDriftTransitionError):
        await transition_drift(session, record, "under_review", "alice")


# -- new D1 models ----------------------------------------------------------------


async def test_drift_scan_decision_ignore_rule_roundtrip(session: AsyncSession) -> None:
    scan = DriftScan(site="dc1")
    session.add(scan)
    await session.flush()
    assert scan.status == "running"
    assert scan.devices_scanned == 0
    record = DriftRecord(
        device="leaf-01",
        kind="unmanaged",
        severity="medium",
        diff="x",
        attributed_actor="bob",
        attributed_at=datetime.now(UTC),
    )
    session.add(record)
    await session.flush()
    decision = DriftDecision(
        record_id=record.id,
        action="exception",
        actor="alice",
        reason="approved change window",
        ticket_ref="RITM2",
    )
    session.add(decision)
    rule = DriftIgnoreRule(pattern="uptime is", platform="ios", created_by="alice")
    session.add(rule)
    await session.commit()
    assert decision.action == "exception"
    assert rule.scope == "global"
    assert rule.target == ""
    assert record.attributed_actor == "bob"
    assert record.attributed_at is not None
    assert str(uuid.UUID(str(scan.id))) == str(scan.id)
