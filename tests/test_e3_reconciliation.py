"""E3: reconciliation service, state machine, override, and the
no-script-writes-Git/NetBox separation."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from vireonet.adapters.base import AdapterHealth
from vireonet.adapters.fakes import FakeNetBoxAdapter, FakeServiceNowAdapter
from vireonet.adapters.git.base import GitCommitResult, GitFile, GitMR
from vireonet.config_store.device_repos import DeviceRepoManager
from vireonet.config_store.store import ConfigStore
from vireonet.execution.types import DeclaredChange, StepResult, StepStatus
from vireonet.models import Change, DriftRecord
from vireonet.policy import PolicyEngine
from vireonet.reconciliation.drift import DriftStore
from vireonet.reconciliation.service import ReconciliationService
from vireonet.services.changes import ChangeService
from vireonet.state_machine import ChangeState, is_legal

RUNNING = "hostname leaf-01\n!\ninterface GigabitEthernet0/1\n description uplink\n"


class FakeGit:
    """GitProvider stand-in recording every commit."""

    def __init__(self) -> None:
        self.commits: list[dict[str, str]] = []
        self.branches: list[str] = []

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
        self.branches.append(branch)
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

    async def get_pipeline(self, mr_id: int) -> Any:
        raise NotImplementedError

    async def merge_mr(self, mr_id: int) -> GitMR:
        raise NotImplementedError

    async def add_comment(self, mr_id: int, body: str) -> None:
        pass

    async def get_mr_diff(self, mr_id: int) -> str:
        return ""


class FakeNetBox(FakeNetBoxAdapter):
    """Fake NetBox with the E3 write methods the real adapter lacks."""

    def __init__(self, *, fail_update: bool = False) -> None:
        super().__init__()
        self.fail_update = fail_update
        self.interface_updates: list[dict[str, Any]] = []
        self.vlan_ensures: list[dict[str, Any]] = []

    async def update_interface(self, device: str, interface: str, **fields: Any) -> None:
        if self.fail_update:
            raise RuntimeError("netbox update_interface down")
        self.interface_updates.append({"device": device, "interface": interface, **fields})

    async def ensure_vlan(self, vlan_id: int, name: str | None = None) -> None:
        self.vlan_ensures.append({"vlan_id": vlan_id, "name": name})


def _iface_change() -> DeclaredChange:
    return DeclaredChange(
        object_type="interface",
        identifier="GigabitEthernet0/1",
        field="description",
        old_value="",
        new_value="uplink",
        config_lines=["interface GigabitEthernet0/1", " description uplink"],
    )


def _vlan_change() -> DeclaredChange:
    return DeclaredChange(
        object_type="vlan",
        identifier="100",
        field="name",
        old_value="",
        new_value="web",
        config_lines=["vlan 100", " name web"],
    )


def _collect_result() -> StepResult:
    return StepResult(
        status=StepStatus.SUCCESSFUL,
        step="collect_state",
        device="leaf-01",
        job_id=7,
        output={"running_config": RUNNING},
    )


@pytest.fixture()
def service(engine: AsyncEngine) -> tuple[ReconciliationService, FakeGit, FakeNetBox]:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    git = FakeGit()
    repos = DeviceRepoManager(git_factory=lambda repo: git, config_store=ConfigStore(factory))
    netbox = FakeNetBox()
    svc = ReconciliationService(ConfigStore(factory), repos, netbox, DriftStore(factory))
    return svc, git, netbox


async def test_reconcile_correct_changes_commits_git_and_journals_netbox(
    service: tuple[ReconciliationService, FakeGit, FakeNetBox],
) -> None:
    svc, git, netbox = service
    outcome = await svc.reconcile(
        ticket="RITM1",
        device="leaf-01",
        job_type="playbook",
        script_name=None,
        script_sha=None,
        awx_job_id=7,
        declared=[_iface_change()],
        collect_result=_collect_result(),
        manifest_writes=["interface.description", "interface.enabled"],
    )
    assert outcome.status == "reconciled", outcome.failures
    assert outcome.git_commit is not None
    assert len(git.commits) == 1
    assert "RITM1: reconcile after playbook job 7" in git.commits[0]["message"]
    assert git.commits[0]["content"].strip() == RUNNING.strip()
    assert netbox.interface_updates == [
        {"device": "leaf-01", "interface": "GigabitEthernet0/1", "description": "uplink"}
    ]
    assert any(c["operation"] == "add_journal_entry" for c in netbox.calls)
    assert outcome.netbox_applied is True
    assert outcome.drift_records == []
    assert outcome.failures == []


async def test_out_of_scope_declaration_creates_drift_and_pends(
    service: tuple[ReconciliationService, FakeGit, FakeNetBox],
    engine: AsyncEngine,
) -> None:
    svc, git, netbox = service
    outcome = await svc.reconcile(
        ticket="RITM1",
        device="leaf-01",
        job_type="python",
        script_name="add_vlan_nornir",
        script_sha="1a2b3c4d",
        awx_job_id=9,
        declared=[_iface_change(), _vlan_change()],
        collect_result=_collect_result(),
        manifest_writes=["interface.description"],
    )
    # The vlan change is outside the manifest scope: never applied to
    # NetBox, and the change is pending with a drift record — not reconciled.
    assert outcome.status == "reconciliation_pending"
    assert netbox.vlan_ensures == []
    assert len(outcome.drift_records) == 1
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        rows = (await session.execute(select(DriftRecord))).scalars().all()
    assert len(rows) == 1
    assert rows[0].device == "leaf-01"
    assert rows[0].kind == "out_of_scope"
    assert rows[0].linked_ticket == "RITM1"
    assert "vlan.name" in rows[0].diff
    # The in-scope interface change was still applied.
    assert len(netbox.interface_updates) == 1


async def test_netbox_failure_pends_with_drift_record(
    engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    git = FakeGit()
    repos = DeviceRepoManager(git_factory=lambda repo: git, config_store=ConfigStore(factory))
    netbox = FakeNetBox(fail_update=True)
    svc = ReconciliationService(ConfigStore(factory), repos, netbox, DriftStore(factory))
    outcome = await svc.reconcile(
        ticket="RITM1",
        device="leaf-01",
        job_type="playbook",
        script_name=None,
        script_sha=None,
        awx_job_id=7,
        declared=[_iface_change()],
        collect_result=_collect_result(),
        manifest_writes=["interface.description"],
    )
    assert outcome.status == "reconciliation_pending"
    assert outcome.failures
    assert len(outcome.drift_records) == 1
    async with factory() as session:
        rows = (await session.execute(select(DriftRecord))).scalars().all()
    assert len(rows) == 1 and rows[0].kind == "netbox_failure"


async def test_reconcile_works_for_playbook_and_python_jobs(
    service: tuple[ReconciliationService, FakeGit, FakeNetBox],
) -> None:
    svc, git, _ = service
    playbook = await svc.reconcile(
        ticket="RITM1",
        device="leaf-01",
        job_type="playbook",
        script_name=None,
        script_sha=None,
        awx_job_id=7,
        declared=[_iface_change()],
        collect_result=_collect_result(),
        manifest_writes=["interface.description"],
    )
    assert playbook.status == "reconciled"
    python = await svc.reconcile(
        ticket="RITM1",
        device="leaf-01",
        job_type="python",
        script_name="set_interface_description",
        script_sha="9f2c1a4b",
        awx_job_id=9,
        declared=[_iface_change()],
        collect_result=_collect_result(),
        manifest_writes=["interface.description"],
    )
    assert python.status == "reconciled"
    assert "set_interface_description@9f2c1a4b" in git.commits[1]["message"]


def test_failed_post_check_never_reaches_reconcile() -> None:
    """Structural: the workflow rolls back on post_check failure, and the
    reconcile activity is only invoked after post_check succeeds. The
    Temporal test-server tests cannot run in this sandbox, so the ordering
    is asserted on the workflow source instead of a live run."""
    text = Path("src/vireonet/temporal/workflow.py").read_text()
    assert text.index('if not post["ok"]:') < text.index("reconcile_change")
    rollback_block = text[text.index('if not post["ok"]:') : text.index("reconcile_change")]
    assert "reconcile_change" not in rollback_block
    activities = Path("src/vireonet/temporal/activities.py").read_text()
    # reconcile() is only called from the reconcile_change activity.
    assert activities.count("self._reconciliation.reconcile(") == 1


def test_no_script_path_writes_git_or_netbox() -> None:
    """By construction: ScriptRunner and AwxEngine contain no Git/NetBox
    write calls — a script can only ask the engine to run a job."""
    write_calls = (
        "commit_file",
        "open_mr",
        "create_branch",
        "update_interface",
        "ensure_vlan",
        "add_journal_entry",
        "device_repos",
        "netbox.",
    )
    for path in ("src/vireonet/scripts/runner.py", "src/vireonet/execution/awx_engine.py"):
        text = Path(path).read_text()
        for call in write_calls:
            assert call not in text, f"{path} contains {call}"


def test_reconciliation_state_transitions() -> None:
    assert is_legal(ChangeState.POST_CHECKED, ChangeState.RECONCILING)
    assert is_legal(ChangeState.RECONCILING, ChangeState.RECONCILED)
    assert is_legal(ChangeState.RECONCILING, ChangeState.RECONCILIATION_PENDING)
    assert is_legal(ChangeState.RECONCILIATION_PENDING, ChangeState.RECONCILING)
    assert is_legal(ChangeState.RECONCILED, ChangeState.CLOSED)
    assert not is_legal(ChangeState.POST_CHECKED, ChangeState.CLOSED)
    assert not is_legal(ChangeState.RECONCILIATION_PENDING, ChangeState.CLOSED)
    assert not is_legal(ChangeState.RECONCILIATION_PENDING, ChangeState.RECONCILED)


async def test_reconcile_override_moves_pending_to_reconciled(
    engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    change_id = uuid.uuid4()
    async with factory() as session:
        session.add(
            Change(
                id=change_id,
                ticket_number="RITM1",
                change_type="vlan_add",
                state=ChangeState.RECONCILIATION_PENDING,
                requester="alice",
                intent={},
                devices=["leaf-01"],
            )
        )
        await session.commit()
    svc = ChangeService(
        factory,
        servicenow=FakeServiceNowAdapter(),
        netbox=FakeNetBoxAdapter(),
        policy=PolicyEngine.default(),
    )
    change = await svc.reconcile_override(str(change_id), actor="approver", reason="drift accepted")
    assert change is not None
    assert str(change.state) == "reconciled"
    async with factory() as session:
        rows = (
            await session.execute(
                select(Change)
                .options(selectinload(Change.transitions))
                .where(Change.id == change_id)
            )
        ).scalar_one()
        assert len(rows.transitions) == 2
        assert all(t.evidence.get("override") is True for t in rows.transitions)

    with pytest.raises(ValueError, match="only legal from reconciliation_pending"):
        await svc.reconcile_override(str(change_id), actor="approver", reason="again")
    with pytest.raises(ValueError, match="requires a reason"):
        await svc.reconcile_override(str(change_id), actor="approver", reason="  ")
