"""D4: drift resolution actions (rollback / merge / exception / emergency)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vireonet.adapters.fakes import FakeNetBoxAdapter, FakeServiceNowAdapter
from vireonet.adapters.git.base import GitCommitResult, GitFile, GitMR
from vireonet.config_store.store import ConfigStore
from vireonet.diff import config_hash, normalize_config
from vireonet.drift.models import DriftDecision
from vireonet.drift.resolve import (
    EmergencyAction,
    ExceptionAction,
    MergeAction,
    ResolutionError,
    RollbackAction,
    SelfApprovalError,
    check_emergency_deadlines,
    ensure_incident,
    expire_exceptions,
    handle_mr_merged,
    is_excepted,
    touches_shared_template,
)
from vireonet.execution.fake_engine import FakeExecutionEngine
from vireonet.models import Change, DriftRecord
from vireonet.reconciliation.drift import DriftStore
from vireonet.safety import DeviceLockService
from vireonet.services.changes import ChangeService

BASELINE = "hostname leaf-01\ninterface GigabitEthernet0/1\n description uplink\n"
CHANGED = "hostname leaf-01\ninterface GigabitEthernet0/1\n description CHANGED\n"
DIFF = "--- baseline\n+++ running\n@@\n-interface GigabitEthernet0/1\n- description uplink\n+ description CHANGED\n"


def _hash(text: str) -> str:
    return config_hash(normalize_config(text))


@pytest.fixture()
def sessions(engine) -> async_sessionmaker[AsyncSession]:  # type: ignore[no-untyped-def]
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture()
def store(sessions) -> DriftStore:  # type: ignore[no-untyped-def]
    return DriftStore(sessions)


@pytest.fixture()
def config_store(sessions) -> ConfigStore:  # type: ignore[no-untyped-def]
    return ConfigStore(sessions)


@pytest.fixture()
def change_service(sessions) -> ChangeService:  # type: ignore[no-untyped-def]
    from vireonet.policy import PolicyEngine

    return ChangeService(
        sessions,
        servicenow=FakeServiceNowAdapter(),  # type: ignore[arg-type]
        netbox=FakeNetBoxAdapter(),  # type: ignore[arg-type]
        policy=PolicyEngine.default(),
    )


async def _seed_record(
    store: DriftStore,
    config_store: ConfigStore,
    *,
    device: str = "leaf-01",
    severity: str = "high",
    attributed_actor: str | None = None,
    diff: str = DIFF,
    linked_ticket: str | None = None,
) -> str:
    await config_store.store_running(device, BASELINE, ticket_number="RITM-SEED")
    return await store.create(
        device,
        "unmanaged",
        severity,
        diff,
        _hash(BASELINE),
        _hash(CHANGED),
        linked_ticket,
        attributed_actor=attributed_actor,
    )


async def _get_record(sessions, record_id: str) -> DriftRecord:  # type: ignore[no-untyped-def]
    async with sessions() as session:
        row = await session.get(DriftRecord, uuid.UUID(record_id))
        assert row is not None
        return row


async def _decisions(sessions, record_id: str) -> list[DriftDecision]:  # type: ignore[no-untyped-def]
    async with sessions() as session:
        return list(
            (
                await session.execute(
                    select(DriftDecision).where(DriftDecision.record_id == uuid.UUID(record_id))
                )
            )
            .scalars()
            .all()
        )


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    async def send(self, channel: str, title: str, body: str, ticket: str) -> bool:
        self.sent.append({"channel": channel, "title": title, "body": body, "ticket": ticket})
        return True


class FakeGit:
    """GitProvider stand-in (create_branch / commit_file / open_mr)."""

    def __init__(self) -> None:
        self.branches: list[str] = []
        self.commits: list[dict[str, str]] = []
        self.mrs: list[GitMR] = []
        self.mr_descriptions: list[str] = []

    @property
    def name(self) -> str:
        return "fake-git"

    async def health_check(self):  # type: ignore[no-untyped-def]
        from vireonet.adapters.base import AdapterHealth

        return AdapterHealth(name="fake-git", status="ok", capabilities=[])

    def capabilities(self) -> list[str]:
        return ["create_branch", "commit_file", "open_mr"]

    async def get_file(self, path: str, ref: str) -> GitFile:
        raise FileNotFoundError(path)

    async def create_branch(self, branch: str, from_ref: str) -> str:
        self.branches.append(branch)
        return branch

    async def commit_file(
        self, branch: str, path: str, content: str, message: str
    ) -> GitCommitResult:
        self.commits.append(
            {"branch": branch, "path": path, "content": content, "message": message}
        )
        return GitCommitResult(sha="abc123", branch=branch, path=path)

    async def open_mr(
        self, *, source_branch: str, target_branch: str, title: str, description: str = ""
    ) -> GitMR:
        mr = GitMR(
            id=len(self.mrs) + 1,
            iid=len(self.mrs) + 1,
            title=title,
            source_branch=source_branch,
            target_branch=target_branch,
            url=f"https://git.example/mr/{len(self.mrs) + 1}",
            state="opened",
        )
        self.mrs.append(mr)
        self.mr_descriptions.append(description)
        return mr

    async def get_mr(self, mr_id: int) -> GitMR:
        return self.mrs[mr_id - 1]

    async def get_pipeline(self, mr_id: int):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    async def merge_mr(self, mr_id: int) -> GitMR:
        mr = self.mrs[mr_id - 1]
        mr.state = "merged"
        return mr

    async def add_comment(self, mr_id: int, body: str) -> None:
        return None

    async def get_mr_diff(self, mr_id: int) -> str:
        return ""


class FakeDeviceRepos:
    def __init__(self) -> None:
        self.commits: list[dict[str, str]] = []

    async def commit_config(
        self, *, hostname: str, ticket_number: str, content: str, message: str
    ) -> str:
        self.commits.append({"hostname": hostname, "ticket": ticket_number, "content": content})
        return "sha-merge"


class FakeCollectorStore:
    """ConfigStore stand-in that returns the running config as latest."""

    def __init__(self, content: str) -> None:
        self._content = content

    async def store_running(
        self, device: str, content: str, ticket_number: str | None = None, source: str = "baseline"
    ):  # type: ignore[no-untyped-def]
        from vireonet.models import DeviceConfigVersion

        return DeviceConfigVersion(
            device=device, version=1, content=content, content_hash=_hash(content)
        )

    async def latest(self, device: str):  # type: ignore[no-untyped-def]
        from vireonet.models import DeviceConfigVersion

        return DeviceConfigVersion(
            device=device,
            version=1,
            content=self._content,
            content_hash=_hash(self._content),
        )


def _async_collector(text: str):  # type: ignore[no-untyped-def]
    async def collect(device: str) -> str:
        return text

    return collect


def _rollback(
    sessions, *, engine=None, change_service=None, config_store=None, collector=None, **kw: Any
) -> RollbackAction:  # type: ignore[no-untyped-def]
    locks = kw.pop("locks", None) or DeviceLockService(sessions)
    return RollbackAction(
        sessions,
        change_service=change_service,
        engine=engine or FakeExecutionEngine(),
        config_store=config_store,
        locks=locks,
        collector=collector,
        **kw,
    )


# -- rollback -----------------------------------------------------------------


async def test_rollback_end_to_end_resolves(sessions, store, config_store, change_service) -> None:
    record_id = await _seed_record(store, config_store)
    running = {"text": CHANGED}

    async def collector(device: str) -> str:
        return running["text"]

    action = _rollback(
        sessions,
        change_service=change_service,
        config_store=config_store,
        collector=collector,
    )
    # The fake engine "applies" nothing; simulate the device now running
    # the baseline by flipping what the collector returns after apply.
    orig_apply = action._engine.apply  # type: ignore[attr-defined]

    async def apply_and_restore(req):  # type: ignore[no-untyped-def]
        result = await orig_apply(req)
        running["text"] = BASELINE
        return result

    action._engine.apply = apply_and_restore  # type: ignore[attr-defined]
    outcome = await action.execute(
        record_id, actor="approver1", reason="restore baseline", ticket_ref="RITM0001234"
    )
    assert outcome["status"] == "resolved", outcome
    record = await _get_record(sessions, record_id)
    assert record.status == "resolved"
    assert record.linked_ticket == outcome["change_ticket"]
    assert outcome["change_ticket"].startswith("DRIFT-RB-")
    decisions = await _decisions(sessions, record_id)
    assert [d.action for d in decisions] == ["rollback"]
    assert decisions[0].actor == "approver1"
    async with sessions() as session:
        changes = list((await session.execute(select(Change))).scalars().all())
    assert len(changes) == 1
    assert changes[0].change_type == "config_restore"
    assert str(changes[0].state) == "post_checked"


async def test_rollback_post_check_failure_auto_rolls_back_and_stays_open(
    sessions, store, config_store, change_service
) -> None:
    record_id = await _seed_record(store, config_store)
    engine = FakeExecutionEngine(fail_steps={"post_check"})
    action = _rollback(
        sessions,
        engine=engine,
        change_service=change_service,
        config_store=config_store,
        collector=_async_collector(BASELINE),
    )
    outcome = await action.execute(
        record_id, actor="approver1", reason="restore", ticket_ref="RITM0001234"
    )
    assert outcome["status"] == "failed", outcome
    assert outcome["stage"] == "post_check"
    assert any(c.step == "rollback" for c in engine.calls), "auto-rollback must run"
    record = await _get_record(sessions, record_id)
    assert record.status == "under_review", "record stays open after failed rollback"
    async with sessions() as session:
        changes = list((await session.execute(select(Change))).scalars().all())
    assert len(changes) == 1
    assert str(changes[0].state) == "rolled_back"


async def test_rollback_idempotent_when_already_reverting(
    sessions, store, config_store, change_service
) -> None:
    record_id = await _seed_record(store, config_store)
    async with sessions() as session:
        row = await session.get(DriftRecord, uuid.UUID(record_id))
        assert row is not None
        row.status = "reverting"
        row.linked_ticket = "DRIFT-RB-abcdef01"
        await session.commit()
    action = _rollback(sessions, change_service=change_service, config_store=config_store)
    outcome = await action.execute(
        record_id, actor="approver1", reason="again", ticket_ref="RITM0001234"
    )
    assert outcome["status"] == "already_reverting"
    assert outcome["change_ticket"] == "DRIFT-RB-abcdef01"
    async with sessions() as session:
        changes = list((await session.execute(select(Change))).scalars().all())
    assert changes == [], "no second change must be started"


async def test_rollback_lock_conflict_raises(sessions, store, config_store, change_service) -> None:
    record_id = await _seed_record(store, config_store)
    locks = DeviceLockService(sessions)
    await locks.acquire("leaf-01", "RITM-OTHER")
    action = _rollback(
        sessions, change_service=change_service, config_store=config_store, locks=locks
    )
    with pytest.raises(Exception, match="locked"):
        await action.execute(record_id, actor="approver1", reason="r", ticket_ref="RITM0001234")


# -- merge --------------------------------------------------------------------


async def test_merge_creates_branch_override_and_mr_then_resolves_on_merged(
    sessions, store, config_store
) -> None:
    record_id = await _seed_record(store, config_store)
    git = FakeGit()
    action = MergeAction(sessions, git=git)
    outcome = await action.execute(
        record_id, actor="approver1", reason="keep it", ticket_ref="RITM0001234"
    )
    assert outcome["status"] == "merge_pending"
    assert outcome["branch"].startswith("drift/leaf-01/")
    assert outcome["mr_id"] == 1
    assert len(git.commits) == 1
    assert git.commits[0]["path"] == "netops/vars/device/leaf-01.yaml"
    assert "templates/" not in git.commits[0]["path"]
    assert "RITM0001234" in git.mr_descriptions[0]
    assert record_id in git.mr_descriptions[0]
    record = await _get_record(sessions, record_id)
    assert record.status == "merge_pending"

    repos = FakeDeviceRepos()
    fake_store = FakeCollectorStore(CHANGED)
    result = await handle_mr_merged(
        sessions,
        branch=outcome["branch"],
        merged=True,
        config_store=fake_store,
        device_repos=repos,
        collector=_async_collector(CHANGED),
    )
    assert result is not None and result["status"] == "resolved"
    assert len(repos.commits) == 1, "baseline must be updated on merge"
    record = await _get_record(sessions, record_id)
    assert record.status == "resolved"


async def test_merge_closed_unmerged_returns_to_under_review(sessions, store, config_store) -> None:
    record_id = await _seed_record(store, config_store)
    git = FakeGit()
    action = MergeAction(sessions, git=git)
    outcome = await action.execute(
        record_id, actor="approver1", reason="keep it", ticket_ref="RITM0001234"
    )
    result = await handle_mr_merged(sessions, branch=outcome["branch"], merged=False)
    assert result is not None and result["status"] == "under_review"
    record = await _get_record(sessions, record_id)
    assert record.status == "under_review"


def test_merge_refuses_shared_template_diff() -> None:
    diff = "+++ b/templates/base.j2\n+hostname x\n"
    assert touches_shared_template(diff) is True
    assert touches_shared_template(DIFF) is False


async def test_merge_refuses_shared_template_record(sessions, store, config_store) -> None:
    record_id = await _seed_record(
        store,
        config_store,
        diff="+++ b/templates/base.j2\n+hostname x\n",
    )
    git = FakeGit()
    with pytest.raises(ResolutionError, match="shared template"):
        await MergeAction(sessions, git=git).execute(
            record_id, actor="approver1", reason="r", ticket_ref="RITM0001234"
        )
    assert git.branches == []


# -- self-approval ------------------------------------------------------------


async def test_self_approval_blocked_for_exception_and_merge(sessions, store, config_store) -> None:
    record_id = await _seed_record(store, config_store, attributed_actor="alice")
    with pytest.raises(SelfApprovalError):
        await ExceptionAction(sessions).execute(
            record_id,
            actor="alice",
            reason="mine",
            ticket_ref="RITM1",
            owner="alice",
            expiry=datetime.now(UTC) + timedelta(days=7),
        )
    with pytest.raises(SelfApprovalError):
        await MergeAction(sessions, git=FakeGit()).execute(
            record_id, actor="alice", reason="mine", ticket_ref="RITM1"
        )
    record = await _get_record(sessions, record_id)
    assert record.status == "detected", "blocked decision must not move the record"


# -- exception / expiry -------------------------------------------------------


async def test_exception_ignores_exact_hash_until_expiry(sessions, store, config_store) -> None:
    record_id = await _seed_record(store, config_store)
    notifier = FakeNotifier()
    action = ExceptionAction(sessions, notifier=notifier, max_days=30)
    expiry = datetime.now(UTC) + timedelta(days=7)
    outcome = await action.execute(
        record_id,
        actor="approver1",
        reason="known good",
        ticket_ref="RITM0001234",
        owner="netops",
        expiry=expiry,
    )
    assert outcome["status"] == "exception_active"
    assert await is_excepted(sessions, "leaf-01", _hash(CHANGED)) is True
    assert await is_excepted(sessions, "leaf-01", _hash("hostname other\n")) is False
    assert await is_excepted(sessions, "leaf-02", _hash(CHANGED)) is False

    moved = await expire_exceptions(
        sessions, notifier=notifier, now=datetime.now(UTC) + timedelta(days=8)
    )
    assert moved == [record_id]
    record = await _get_record(sessions, record_id)
    assert record.status == "under_review"
    assert await is_excepted(sessions, "leaf-01", _hash(CHANGED)) is False
    assert any("expired" in n["title"] for n in notifier.sent)


async def test_exception_beyond_max_days_refused(sessions, store, config_store) -> None:
    record_id = await _seed_record(store, config_store)
    with pytest.raises(ResolutionError, match="maximum of 30 days"):
        await ExceptionAction(sessions, max_days=30).execute(
            record_id,
            actor="approver1",
            reason="r",
            ticket_ref="RITM1",
            owner="netops",
            expiry=datetime.now(UTC) + timedelta(days=31),
        )


# -- emergency ----------------------------------------------------------------


async def test_emergency_deadline_missed_raises_severity_and_notifies(
    sessions, store, config_store
) -> None:
    record_id = await _seed_record(store, config_store, severity="medium")
    notifier = FakeNotifier()
    action = EmergencyAction(sessions, notifier=notifier, deadline_hours=24)
    outcome = await action.execute(record_id, actor="op1", reason="outage", ticket_ref="INC0001")
    assert outcome["status"] == "under_review"
    record = await _get_record(sessions, record_id)
    assert record.severity == "medium"

    escalated = await check_emergency_deadlines(
        sessions, notifier=notifier, now=datetime.now(UTC) + timedelta(hours=25)
    )
    assert escalated == [record_id]
    record = await _get_record(sessions, record_id)
    assert record.severity == "critical"
    assert any("deadline missed" in n["title"] for n in notifier.sent)


# -- ServiceNow incident ------------------------------------------------------


async def test_ensure_incident_work_note_when_linked_and_none_without_ticket(
    sessions, store, config_store
) -> None:
    snow = FakeServiceNowAdapter()
    notifier = FakeNotifier()
    record_id = await _seed_record(
        store, config_store, severity="critical", linked_ticket="RITM0001234"
    )
    record = await _get_record(sessions, record_id)
    assert await ensure_incident(record, servicenow=snow, notifier=notifier) == "RITM0001234"
    notes = [c for c in snow.calls if c.get("operation") == "add_work_note"]
    assert len(notes) == 1, "one work note on the existing incident"
    assert await ensure_incident(record, servicenow=snow, notifier=notifier) == "RITM0001234"
    notes = [c for c in snow.calls if c.get("operation") == "add_work_note"]
    assert len(notes) == 2, "idempotent per record means update, not a second incident"

    record_id2 = await _seed_record(store, config_store, severity="critical")
    record2 = await _get_record(sessions, record_id2)
    assert await ensure_incident(record2, servicenow=snow, notifier=notifier) is None
    assert any("needs a ServiceNow incident" in n["title"] for n in notifier.sent)
