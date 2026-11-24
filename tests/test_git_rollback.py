"""Git-based rollback: <device>/config on main/master is the running config.

- A change pushed from Git rolls back to the commit recorded at pre-check
  (the last commit before the change).
- A drift rollback restores the main/master head by default, or the commit
  before it ("previous"), or any commit in the device's config history.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from vireonet.adapters.fake_git import fake_git_factory
from vireonet.adapters.fakes import FakeAwxAdapter, FakeNetBoxAdapter, FakeServiceNowAdapter
from vireonet.api.security import CurrentUser, get_current_user
from vireonet.config import Settings
from vireonet.config_store.device_repos import DeviceRepoManager
from vireonet.config_store.layout import GitLayout, git_layout_from_settings
from vireonet.db import Base
from vireonet.drift.resolve import ResolutionError, resolve_rollback_source
from vireonet.execution.fake_engine import FakeExecutionEngine
from vireonet.main import create_app
from vireonet.models import Change
from vireonet.policy import PolicyEngine
from vireonet.state_machine import ChangeState
from vireonet.temporal.activities import ChangeActivities, CheckInput, RollbackInput

MONO = GitLayout(mode="monorepo", repo="netops/configs", branch="main")
V1 = "hostname leaf-01\nvlan 10\n"
V2 = "hostname leaf-01\nvlan 10\nvlan 20\n"
V3 = "hostname leaf-01\nvlan 10\nvlan 20\nvlan 30\n"
INTENT = {
    "change_type": "vlan_add",
    "vlan_id": 120,
    "vlan_name": "app",
    "devices": ["leaf-01"],
    "site": "dc1",
    "ticket_number": "RITM7",
    "requested_by": "alice",
}


def _repos(layout: GitLayout = MONO):  # type: ignore[no-untyped-def]
    factory, repos = fake_git_factory()
    manager = DeviceRepoManager(factory, layout=layout)
    repo = factory(layout.repo_for("leaf-01"))
    path = layout.running_path("leaf-01")
    shas = [
        repo.seed(path, V1, "RITM1: vlan 10"),
        repo.seed(path, V2, "RITM2: vlan 20"),
        repo.seed("leaf-02/config", "hostname leaf-02\n", "RITM3: other device"),
    ]
    return manager, repo, shas


# --- Layout ------------------------------------------------------------------


def test_monorepo_layout_uses_device_slash_config() -> None:
    assert MONO.repo_for("leaf-01") == "netops/configs"
    assert MONO.running_path("leaf-01") == "leaf-01/config"
    with pytest.raises(ValueError):
        MONO.running_path("../etc")


def test_per_device_layout_unchanged() -> None:
    layout = GitLayout()
    assert layout.repo_for("Leaf-01") == "netops/config-leaf-01"
    assert layout.running_path("leaf-01") == "backups/running-config.txt"


def test_layout_from_settings_supports_master() -> None:
    s = Settings(git_layout="monorepo", git_repo="netops/net", git_default_branch="master")
    layout = git_layout_from_settings(s)
    assert (layout.mode, layout.repo, layout.branch) == ("monorepo", "netops/net", "master")


# --- Rollback targets --------------------------------------------------------------


async def test_resolve_targets() -> None:
    manager, _repo, shas = _repos()
    assert (await manager.resolve_target("leaf-01", "branch")).sha == shas[1]
    assert (await manager.resolve_target("leaf-01", "main")).sha == shas[1]
    assert (await manager.resolve_target("leaf-01", "previous")).sha == shas[0]
    assert (await manager.resolve_target("leaf-01", shas[0][:10])).sha == shas[0]
    # Another device's commit is not in leaf-01's history.
    with pytest.raises(ValueError, match="not in this device"):
        await manager.resolve_target("leaf-01", shas[2])
    with pytest.raises(ValueError, match="unknown rollback target"):
        await manager.resolve_target("leaf-01", "yesterday")
    assert await manager.running_config_at("leaf-01", shas[0]) == V1


async def test_history_only_lists_this_device() -> None:
    manager, _repo, shas = _repos()
    commits = await manager.commits("leaf-01")
    assert [c.sha for c in commits] == [shas[1], shas[0]]


# --- Change rollback: restore the last commit before the change ---------------------


async def _change_acts(tmp_path, manager, engine, rollback_source: str = "git"):  # type: ignore[no-untyped-def]
    db = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(db, expire_on_commit=False)
    change_id = str(uuid.uuid4())
    async with factory() as s:
        s.add(
            Change(
                id=uuid.UUID(change_id),
                ticket_number="RITM7",
                change_type="vlan_add",
                state=ChangeState.APPROVED,
                requester="alice",
                intent=INTENT,
                devices=["leaf-01"],
            )
        )
        await s.commit()
    acts = ChangeActivities(
        factory,
        servicenow=FakeServiceNowAdapter(),
        netbox=FakeNetBoxAdapter(),
        awx=FakeAwxAdapter(),
        policy=PolicyEngine.default(),
        engine=engine,
        device_repos=manager,
        rollback_source=rollback_source,
    )
    return acts, change_id, db


async def test_change_rollback_restores_the_commit_recorded_at_pre_check(tmp_path) -> None:
    manager, repo, shas = _repos()
    engine = FakeExecutionEngine()
    acts, change_id, db = await _change_acts(tmp_path, manager, engine)
    pre = await acts.pre_check_change(CheckInput(change_id=change_id, intent=INTENT, kind="pre"))
    assert pre["ok"], pre
    assert pre["evidence"]["git_refs"] == {"leaf-01": shas[1]}
    # main moves on after pre-check (e.g. the change's own reconcile commit).
    repo.seed("leaf-01/config", V3, "RITM7: vlan 120")
    rb = await acts.rollback_change(
        RollbackInput(
            change_id=change_id,
            intent=INTENT,
            backups=pre["evidence"]["backups"],
            git_refs=pre["evidence"]["git_refs"],
        )
    )
    assert rb["ok"], rb
    assert rb["sources"]["leaf-01"] == {"source": "git", "ref": shas[1]}
    call = [c for c in engine.calls if c.step == "rollback"][-1]
    assert call.extra_vars["restore_config"] == V2  # the pre-change version, not V3
    assert call.extra_vars["git_ref"] == shas[1]
    await db.dispose()


async def test_change_rollback_falls_back_to_backup_and_says_so(tmp_path) -> None:
    manager, _repo, _shas = _repos()
    engine = FakeExecutionEngine()
    acts, change_id, db = await _change_acts(tmp_path, manager, engine)
    pre = await acts.pre_check_change(CheckInput(change_id=change_id, intent=INTENT, kind="pre"))
    rb = await acts.rollback_change(
        RollbackInput(
            change_id=change_id,
            intent=INTENT,
            backups=pre["evidence"]["backups"],
            git_refs={"leaf-01": "deadbeefdeadbeef"},  # unknown commit
        )
    )
    assert rb["ok"]
    assert rb["sources"]["leaf-01"]["source"] == "backup"
    assert "git_error" in rb["sources"]["leaf-01"]
    assert "restore_config" not in [c for c in engine.calls if c.step == "rollback"][-1].extra_vars
    await db.dispose()


async def test_backup_mode_never_reads_git(tmp_path) -> None:
    manager, _repo, _shas = _repos()
    acts, change_id, db = await _change_acts(tmp_path, manager, FakeExecutionEngine(), "backup")
    pre = await acts.pre_check_change(CheckInput(change_id=change_id, intent=INTENT, kind="pre"))
    assert pre["evidence"]["git_refs"] == {}
    await db.dispose()


# --- Drift rollback: main/master head, previous commit, or a chosen commit ----------


async def test_drift_rollback_source_targets() -> None:
    manager, _repo, shas = _repos()
    content, digest, src = await resolve_rollback_source("leaf-01", None, device_repos=manager)
    assert content == V2 and src["source"] == "git" and src["ref"] == shas[1]
    assert src["target"] == "branch" and src["branch"] == "main"
    content, _, src = await resolve_rollback_source("leaf-01", "previous", device_repos=manager)
    assert content == V1 and src["ref"] == shas[0]
    content, _, src = await resolve_rollback_source("leaf-01", shas[0], device_repos=manager)
    assert content == V1
    assert digest  # hash of what is restored, used to confirm the rollback


async def test_explicit_target_never_falls_back() -> None:
    with pytest.raises(ResolutionError, match="needs the Git device repos"):
        await resolve_rollback_source("leaf-01", "previous", device_repos=None)
    manager, _repo, _shas = _repos()
    with pytest.raises(ResolutionError, match="cannot restore leaf-01 from Git"):
        await resolve_rollback_source("leaf-01", "0000000", device_repos=manager)


# --- API --------------------------------------------------------------------------


def test_api_lists_device_config_commits() -> None:
    manager, _repo, shas = _repos()
    app = create_app(
        Settings(adapter_mode="fake", dev=True, database_url="sqlite+aiosqlite:///:memory:")
    )
    app.state.device_repo_manager = manager
    app.dependency_overrides[get_current_user] = lambda: CurrentUser("u", "v", "viewer")
    body = TestClient(app).get("/config/devices/leaf-01/commits").json()
    assert body["path"] == "leaf-01/config" and body["branch"] == "main"
    assert [c["sha"] for c in body["commits"]] == [shas[1], shas[0]]


async def test_workflow_carries_git_ref_into_rollback(tmp_path) -> None:
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from vireonet.temporal.activities import all_activities
    from vireonet.temporal.workflow import ChangeWorkflow, ChangeWorkflowInput

    manager, _repo, shas = _repos()
    engine = FakeExecutionEngine(fail_steps={"apply"})
    acts, change_id, db = await _change_acts(tmp_path, manager, engine)
    async with acts._sessions() as s:
        change = await s.get(Change, uuid.UUID(change_id))
        assert change is not None
        change.state = ChangeState.VALIDATED
        await s.commit()
    async with (
        await WorkflowEnvironment.start_time_skipping() as env,
        Worker(
            env.client,
            task_queue="git-q",
            workflows=[ChangeWorkflow],
            activities=all_activities(acts),
        ),
    ):
        handle = await env.client.start_workflow(
            ChangeWorkflow.run,
            ChangeWorkflowInput(
                change_id=change_id,
                ticket_number="RITM7",
                intent=INTENT,
                requester="alice",
                ci_timeout_seconds=60,
                approval_timeout_seconds=60,
            ),
            id=f"RITM7-{uuid.uuid4().hex[:6]}",
            task_queue="git-q",
        )
        await handle.signal(ChangeWorkflow.ci_passed)
        await handle.signal(ChangeWorkflow.merge_completed)
        await handle.signal(ChangeWorkflow.approve, "ok")
        assert await handle.result() == "rolled_back"
    rollback = [c for c in engine.calls if c.step == "rollback"][-1]
    assert rollback.extra_vars["git_ref"] == shas[1]
    assert rollback.extra_vars["restore_config"] == V2
    await db.dispose()
