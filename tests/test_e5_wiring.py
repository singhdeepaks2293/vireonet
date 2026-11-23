"""E5: fake-mode end-to-end wiring.

One VLAN add through a playbook job and one VLAN delete through a python
job, both through the Fake engine and the ReconciliationService (the same
fakes test_e3_reconciliation.py uses): each ends reconciled with a Git
commit and a NetBox journal entry. A forced post-check failure in each
path triggers rollback and leaves no Git/NetBox write. Plus the /scripts
and /system/health API checks and the change-detail derivation.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tests.test_e3_reconciliation import FakeGit, FakeNetBox
from vireonet.adapters.fakes import FakeNetBoxAdapter, FakeServiceNowAdapter
from vireonet.api.security import CurrentUser, get_current_user
from vireonet.config import Settings
from vireonet.config_store.device_repos import DeviceRepoManager
from vireonet.config_store.store import ConfigStore
from vireonet.execution.fake_engine import FakeExecutionEngine
from vireonet.execution.types import DeclaredChange, DeviceRef, StepRequest, StepResult, StepStatus
from vireonet.main import create_app
from vireonet.models import AuditLog, Change, DriftRecord
from vireonet.policy import PolicyEngine
from vireonet.reconciliation.drift import DriftStore
from vireonet.reconciliation.service import ReconciliationService
from vireonet.services.changes import ChangeService
from vireonet.state_machine import ChangeState

RUNNING = "hostname leaf-01\n!\nvlan 100\n name web\n"


def _vlan_add() -> DeclaredChange:
    return DeclaredChange(
        object_type="vlan",
        identifier="100",
        field="name",
        old_value="",
        new_value="web",
        config_lines=["vlan 100", " name web"],
    )


def _vlan_delete() -> DeclaredChange:
    return DeclaredChange(
        object_type="vlan",
        identifier="100",
        field="name",
        old_value="web",
        new_value="",
        config_lines=["no vlan 100"],
    )


def _collect_result() -> StepResult:
    return StepResult(
        status=StepStatus.SUCCESSFUL,
        step="collect_state",
        device="leaf-01",
        job_id=7,
        output={"running_config": RUNNING},
    )


class DeclaringFakeEngine(FakeExecutionEngine):
    """Fake engine whose playbook steps declare the given changes.

    The shipped Fake playbook engine declares nothing (like the real AWX
    playbook path, whose StepResult has declared_changes=[]); this
    subclass simulates a playbook that reports what it changed, so the
    test can drive the production data flow — declared changes carried
    from the apply/collect result into reconcile — end to end.
    """

    def __init__(self, *, declared: list[DeclaredChange], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._declared = list(declared)

    def _result(self, req: StepRequest) -> StepResult:
        result = super()._result(req)
        if req.job_type == "playbook":
            result.declared_changes = list(self._declared)
        return result


def _request(job_type: str, *, extra_vars: dict[str, str] | None = None) -> StepRequest:
    return StepRequest(
        ticket_number="RITM1",
        device=DeviceRef(name="leaf-01", platform="iosxe"),
        step="backup",
        change_type="vlan_add",
        job_type=job_type,  # type: ignore[arg-type]
        dry_run=False,
        extra_vars=extra_vars or {},
        correlation_id="RITM1",
    )


async def _run_engine_steps(engine: FakeExecutionEngine, req: StepRequest) -> dict[str, StepResult]:
    """backup -> pre_check -> dry_run -> apply -> post_check, all via the engine."""
    results: dict[str, StepResult] = {}
    for step in ("backup", "pre_check", "dry_run", "apply", "post_check"):
        results[step] = await getattr(engine, step)(
            req.model_copy(update={"step": step, "dry_run": step != "apply"})
        )
    return results


@pytest.fixture()
def recon(
    engine: AsyncEngine,
) -> tuple[ReconciliationService, FakeGit, FakeNetBox]:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    git = FakeGit()
    repos = DeviceRepoManager(git_factory=lambda repo: git, config_store=ConfigStore(factory))
    netbox = FakeNetBox()
    svc = ReconciliationService(ConfigStore(factory), repos, netbox, DriftStore(factory))
    return svc, git, netbox


async def test_playbook_vlan_add_ends_reconciled(
    recon: tuple[ReconciliationService, FakeGit, FakeNetBox],
) -> None:
    svc, git, netbox = recon
    engine = DeclaringFakeEngine(declared=[_vlan_add()])
    results = await _run_engine_steps(engine, _request("playbook"))
    assert all(r.ok for r in results.values()), results
    collect = await engine.collect_state(
        _request("playbook").model_copy(update={"step": "collect_state"})
    )
    assert collect.declared_changes == [_vlan_add()]
    outcome = await svc.reconcile(
        ticket="RITM1",
        device="leaf-01",
        job_type="playbook",
        script_name=None,
        script_sha=None,
        awx_job_id=collect.job_id or 0,
        declared=list(collect.declared_changes),
        collect_result=_collect_result(),
        manifest_writes=["vlan"],
    )
    assert outcome.status == "reconciled", outcome.failures
    assert outcome.git_commit is not None
    assert len(git.commits) == 1
    assert "RITM1: reconcile after playbook job" in git.commits[0]["message"]
    assert netbox.vlan_ensures == [{"vlan_id": 100, "name": "web"}]
    assert any(c["operation"] == "add_journal_entry" for c in netbox.calls)


async def test_python_vlan_delete_ends_reconciled(
    recon: tuple[ReconciliationService, FakeGit, FakeNetBox],
) -> None:
    svc, git, netbox = recon
    engine = FakeExecutionEngine(python_ok=True, python_declared_changes=[_vlan_delete()])
    req = _request(
        "python",
        extra_vars={"script_name": "add_vlan_nornir", "script_sha": "1a2b3c4d5e6f7a8b"},
    )
    results = await _run_engine_steps(engine, req)
    assert all(r.ok for r in results.values()), results
    collect = await engine.collect_state(req.model_copy(update={"step": "collect_state"}))
    assert collect.declared_changes == [_vlan_delete()]
    assert collect.output["script_name"] == "add_vlan_nornir"
    outcome = await svc.reconcile(
        ticket="RITM1",
        device="leaf-01",
        job_type="python",
        script_name="add_vlan_nornir",
        script_sha="1a2b3c4d5e6f7a8b",
        awx_job_id=collect.job_id or 0,
        declared=list(collect.declared_changes),
        collect_result=_collect_result(),
        manifest_writes=["vlan"],
    )
    assert outcome.status == "reconciled", outcome.failures
    assert len(git.commits) == 1
    assert "add_vlan_nornir@1a2b3c4d5e6f7a8b" in git.commits[0]["message"]
    # A delete declares an empty new value: ensure_vlan records it with no name.
    assert netbox.vlan_ensures == [{"vlan_id": 100, "name": None}]
    assert any(c["operation"] == "add_journal_entry" for c in netbox.calls)


async def test_playbook_post_check_failure_rolls_back_with_no_git_or_netbox_write(
    recon: tuple[ReconciliationService, FakeGit, FakeNetBox],
) -> None:
    _, git, netbox = recon
    engine = DeclaringFakeEngine(declared=[_vlan_add()], fail_steps={"post_check"})
    results = await _run_engine_steps(engine, _request("playbook"))
    assert results["post_check"].ok is False
    # The workflow rolls back instead of reconciling: no collect, no
    # reconcile, and the rollback step itself is an AWX job, not a write.
    rolled = await engine.rollback(_request("playbook").model_copy(update={"step": "rollback"}))
    assert rolled.ok
    assert git.commits == []
    assert netbox.vlan_ensures == []
    assert netbox.interface_updates == []
    assert not any(c["operation"] == "add_journal_entry" for c in netbox.calls)


async def test_python_post_check_failure_rolls_back_with_no_git_or_netbox_write(
    recon: tuple[ReconciliationService, FakeGit, FakeNetBox],
) -> None:
    _, git, netbox = recon
    engine = FakeExecutionEngine(
        fail_steps={"post_check"},
        python_ok=True,
        python_declared_changes=[_vlan_delete()],
    )
    results = await _run_engine_steps(engine, _request("python"))
    assert results["post_check"].ok is False
    rolled = await engine.rollback(_request("python").model_copy(update={"step": "rollback"}))
    assert rolled.ok
    assert git.commits == []
    assert netbox.vlan_ensures == []
    assert not any(c["operation"] == "add_journal_entry" for c in netbox.calls)


# -- /scripts and /system/health API ---------------------------------------


def _client(settings: Settings | None = None) -> TestClient:
    settings = settings or Settings(
        adapter_mode="fake", dev=True, database_url="sqlite+aiosqlite:///:memory:"
    )
    return TestClient(create_app(settings))


def _as_role(client: TestClient, role: str) -> TestClient:
    app = client.app  # type: ignore[attr-defined]
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="u1", username="test", role=role
    )
    return TestClient(app)


def test_scripts_requires_auth() -> None:
    assert _client().get("/scripts").status_code == 401


def test_scripts_forbids_non_admin() -> None:
    client = _as_role(_client(), "viewer")
    assert client.get("/scripts").status_code == 403


def test_scripts_lists_manifest_entries_for_admin() -> None:
    client = _as_role(_client(), "admin")
    resp = client.get("/scripts")
    assert resp.status_code == 200, resp.text
    entries = resp.json()
    names = [e["name"] for e in entries]
    assert "add_vlan_nornir" in names
    assert "set_interface_description" in names
    vlan = next(e for e in entries if e["name"] == "add_vlan_nornir")
    assert vlan["sha"] == "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b"
    assert vlan["allowed_change_types"] == ["vlan_add"]
    assert vlan["allowed_platforms"] == ["iosxe", "nxos"]
    assert vlan["writes"] == ["vlan"]
    assert vlan["last_run"] is None  # no audit rows in this app's DB


def test_system_health_includes_awx_ee_and_lab_backends() -> None:
    client = _as_role(_client(), "viewer")
    resp = client.get("/system/health")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["awx"]["status"] == "ok"
    assert body["execution_environment"]["name"] == "VireoNet EE"
    # EE registration cannot be verified via the AWX API: honestly unknown.
    assert body["execution_environment"]["status"] == "unknown"
    backends = {b["name"]: b for b in body["lab_backends"]}
    assert "EVE-NG" in backends
    assert backends["EVE-NG"]["status"] == "ok"
    assert backends["EVE-NG"]["capabilities"]


def test_system_health_both_lab_backends() -> None:
    settings = Settings(
        adapter_mode="fake",
        dev=True,
        database_url="sqlite+aiosqlite:///:memory:",
        lab_backend="both",
    )
    client = _as_role(_client(settings), "viewer")
    body = client.get("/system/health").json()
    assert {b["name"] for b in body["lab_backends"]} == {"EVE-NG", "Containerlab"}
    assert all(b["status"] == "ok" for b in body["lab_backends"])


async def test_scripts_last_run_from_audit_log(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    change_id = uuid.uuid4()
    async with factory() as session:
        session.add(
            Change(
                id=change_id,
                ticket_number="RITM1",
                change_type="vlan_add",
                state=ChangeState.APPLIED,
                requester="alice",
                intent={},
                devices=["leaf-01"],
            )
        )
        session.add(
            AuditLog(
                change_id=change_id,
                from_state="applied",
                to_state="post_checked",
                actor="temporal",
                reason="post-check passed",
                evidence={
                    "template": "run-python",
                    "script_name": "add_vlan_nornir",
                    "script_sha": "1a2b3c4d5e6f7a8b",
                    "job_id": 9,
                },
            )
        )
        await session.commit()
    settings = Settings(adapter_mode="fake", dev=True, database_url="sqlite+aiosqlite:///:memory:")
    client = _as_role(TestClient(create_app(settings, session_factory=factory)), "admin")
    resp = client.get("/scripts")
    assert resp.status_code == 200, resp.text
    vlan = next(e for e in resp.json() if e["name"] == "add_vlan_nornir")
    assert vlan["last_run"] is not None
    other = next(e for e in resp.json() if e["name"] == "set_interface_description")
    assert other["last_run"] is None


# -- change detail derivation ----------------------------------------------


async def test_change_detail_derives_execution_fields(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    change_id = uuid.uuid4()
    declared = [
        {
            "object_type": "vlan",
            "identifier": "100",
            "field": "name",
            "old_value": "",
            "new_value": "web",
        }
    ]
    async with factory() as session:
        session.add(
            Change(
                id=change_id,
                ticket_number="RITM1",
                change_type="vlan_add",
                state=ChangeState.RECONCILED,
                requester="alice",
                intent={},
                devices=["leaf-01"],
            )
        )
        session.add(
            AuditLog(
                change_id=change_id,
                from_state="applied",
                to_state="post_checked",
                actor="temporal",
                reason="post-check passed",
                evidence={
                    "dry_run": {"job_id": 9, "status": "successful"},
                    "template": "run-python",
                    "script_name": "add_vlan_nornir",
                    "script_sha": "1a2b3c4d5e6f7a8b",
                    "declared_changes": declared,
                },
            )
        )
        session.add(
            DriftRecord(
                device="leaf-01",
                kind="out_of_scope",
                severity="medium",
                status="detected",
                diff="out of scope: vlan.name",
                baseline_hash="a",
                running_hash="b",
                linked_ticket="RITM1",
            )
        )
        await session.commit()
    svc = ChangeService(
        factory,
        servicenow=FakeServiceNowAdapter(),
        netbox=FakeNetBoxAdapter(),
        policy=PolicyEngine.default(),
    )
    detail = await svc.get_detail(str(change_id))
    assert detail is not None
    assert detail["job_type"] == "python"
    assert detail["script_name"] == "add_vlan_nornir"
    assert detail["script_sha"] == "1a2b3c4d5e6f7a8b"
    assert detail["declared_changes"] == declared
    assert detail["reconciliation_status"] == "reconciled"
    assert detail["drift_count"] == 1
    assert detail["awx_jobs"] == [{"job_id": 9, "status": "successful", "to_state": "post_checked"}]


async def test_change_detail_defaults_when_nothing_stored(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    change_id = uuid.uuid4()
    async with factory() as session:
        session.add(
            Change(
                id=change_id,
                ticket_number="RITM2",
                change_type="vlan_add",
                state=ChangeState.VALIDATED,
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
    detail = await svc.get_detail(str(change_id))
    assert detail is not None
    assert detail["job_type"] == "playbook"  # default; nothing stored says otherwise
    assert detail["script_name"] is None
    assert detail["script_sha"] is None
    assert detail["declared_changes"] == []
    assert detail["reconciliation_status"] is None
    assert detail["drift_count"] == 0


# -- startup validation ------------------------------------------------------


def test_fake_mode_starts_with_missing_real_mode_settings() -> None:
    settings = Settings(
        adapter_mode="fake",
        dev=True,
        database_url="sqlite+aiosqlite:///:memory:",
        script_manifest="policies/does-not-exist.yaml",
        lab_backend="containerlab",
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/healthz").status_code == 200


def test_real_mode_refuses_unloadable_script_manifest() -> None:
    settings = Settings(
        adapter_mode="real",
        dev=True,
        database_url="postgresql+asyncpg://vireonet:real-password@localhost:5432/vireonet",
        jwt_secret="not-the-dev-default",
        script_manifest="policies/does-not-exist.yaml",
    )
    app = create_app(settings)
    with pytest.raises(RuntimeError, match="script manifest .* does not load"), TestClient(app):
        pass
