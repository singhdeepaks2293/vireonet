"""Excel config push: workbook rules, policy, API, workflow end to end."""

from __future__ import annotations

import base64
import io
import uuid

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from vireonet.adapters.fakes import FakeAwxAdapter, FakeNetBoxAdapter, FakeServiceNowAdapter
from vireonet.api.security import CurrentUser, get_current_user
from vireonet.config import Settings
from vireonet.db import Base
from vireonet.excel_push.service import ExcelPushService
from vireonet.excel_push.workbook import (
    WorkbookError,
    build_workbook,
    demo_workbook,
    parse_workbook,
)
from vireonet.execution.fake_engine import FakeExecutionEngine
from vireonet.execution.fake_network import FakeNetwork
from vireonet.intent import parse_intent
from vireonet.itsm.fake import FakeItsm
from vireonet.main import create_app
from vireonet.models import Change
from vireonet.policy import PolicyEngine
from vireonet.rendering import Renderer
from vireonet.state_machine import ChangeState
from vireonet.temporal.activities import ChangeActivities, CheckInput, RollbackInput
from vireonet.topology.demo import seed_demo_netbox


def _wb(
    config_rows: list[tuple[str | None, str]],
    backup_rows: list[tuple[str, str]] | None = None,
    sheet: str = "config",
) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append(["Device", "Config"])
    for row in config_rows:
        ws.append(list(row))
    if backup_rows is not None:
        bk = wb.create_sheet("Backup")
        bk.append(["Device", "Rollback config"])
        for row in backup_rows:
            bk.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# --- Workbook rules ------------------------------------------------------------------


def test_parse_one_row_per_device_with_continuation_rows() -> None:
    wb = parse_workbook(
        _wb(
            [("leaf-01", "vlan 5"), (None, " name five"), ("leaf-02", "vlan 6\n name six")],
            [("leaf-01", "no vlan 5")],
        )
    )
    assert wb.configs == {"leaf-01": "vlan 5\n name five", "leaf-02": "vlan 6\n name six"}
    assert wb.rollback_configs == {"leaf-01": "no vlan 5"}
    assert any("leaf-02: no rollback config" in w for w in wb.warnings)


@pytest.mark.parametrize(
    "content,needle",
    [
        (_wb([("leaf-01", "x")], sheet="settings"), 'no sheet named "config"'),
        (_wb([("leaf-01", "a"), ("leaf-01", "b")]), "appears twice"),
        (_wb([("bad name!", "a")]), "invalid device name"),
        (_wb([(None, "orphan line")]), "no device above it"),
        (_wb([("leaf-01", "")]), "config is empty"),
        (_wb([("leaf-01", "a")], [("ghost", "no a")]), 'not in "config"'),
        (b"not a workbook", "not a readable .xlsx"),
    ],
)
def test_parse_errors_are_specific(content: bytes, needle: str) -> None:
    with pytest.raises(WorkbookError) as exc:
        parse_workbook(content)
    assert any(needle in e for e in exc.value.errors), exc.value.errors


def test_size_limits() -> None:
    with pytest.raises(WorkbookError, match="larger than"):
        parse_workbook(demo_workbook(), max_bytes=100)
    with pytest.raises(WorkbookError, match="larger than 10 bytes"):
        parse_workbook(_wb([("leaf-01", "x" * 50)]), max_device_bytes=10)


def test_policy_denies_dangerous_raw_lines() -> None:
    p = PolicyEngine.default()
    denials = p.raw_config_denials(
        {
            "r1": "vlan 5\nreload\nusername bob privilege 15\nwrite erase",
            "fw": "execute factoryreset",
            "j": "set system login user x",
        }
    )
    assert len(denials) == 5
    assert (
        p.raw_config_denials(
            {"r1": "vlan 5\n name reload-test\ninterface Gi1\n description no username"}
        )
        == []
    )


def test_passthrough_render_keeps_the_config_and_detects_set_format() -> None:
    cli = parse_intent(
        {
            "change_type": "config_push",
            "devices": ["r1"],
            "site": "dc1",
            "ticket_number": "T",
            "requested_by": "a",
            "configs": {"r1": "vlan 5\n name five\n\n"},
        }
    )
    rc = Renderer().render(cli, "r1", "ios")
    assert (rc.lines, rc.format, rc.source) == (["vlan 5", " name five"], "cli", "excel")
    junos = parse_intent(
        {
            "change_type": "config_push",
            "devices": ["j1"],
            "site": "dc1",
            "ticket_number": "T",
            "requested_by": "a",
            "configs": {"j1": "set vlans a vlan-id 5\ndelete vlans b"},
        }
    )
    assert Renderer().render(junos, "j1", "junos").format == "set"


def test_control_characters_are_refused_by_the_intent() -> None:
    # Excel cannot store them (openpyxl refuses), but other callers could send them.
    with pytest.raises(ValueError, match="control characters"):
        parse_intent(
            {
                "change_type": "config_push",
                "devices": ["r1"],
                "site": "s",
                "ticket_number": "T",
                "requested_by": "a",
                "configs": {"r1": "vlan 1\x07"},
            }
        )


def test_intent_requires_one_config_per_device() -> None:
    with pytest.raises(ValueError):
        parse_intent(
            {
                "change_type": "config_push",
                "devices": ["r1", "r2"],
                "site": "s",
                "ticket_number": "T",
                "requested_by": "a",
                "configs": {"r1": "x"},
            }
        )


# --- Activities: write-back and rollback source ------------------------------------------


async def _setup(tmp_path, engine):  # type: ignore[no-untyped-def]
    db = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/x.db")
    async with db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(db, expire_on_commit=False)
    itsm = FakeItsm()
    excel = ExcelPushService(factory, itsm=itsm)
    content = build_workbook({"leaf-01": "vlan 300\n name x"}, {"leaf-01": "no vlan 300"})
    wb_id = await excel.store("RITM0005001", "push.xlsx", content)
    intent = {
        "change_type": "config_push",
        "devices": ["leaf-01"],
        "site": "dc1",
        "ticket_number": "RITM0005001",
        "requested_by": "alice",
        "configs": {"leaf-01": "vlan 300\n name x"},
        "rollback_configs": {"leaf-01": "no vlan 300"},
        "workbook_id": wb_id,
    }
    change_id = str(uuid.uuid4())
    async with factory() as s:
        s.add(
            Change(
                id=uuid.UUID(change_id),
                ticket_number="RITM0005001",
                change_type="config_push",
                state=ChangeState.APPROVED,
                requester="alice",
                intent=intent,
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
        excel_push=excel,
    )
    return acts, change_id, intent, excel, itsm, db


async def test_pre_check_writes_running_config_back_and_attaches_it(tmp_path) -> None:
    acts, change_id, intent, excel, itsm, db = await _setup(
        tmp_path, FakeExecutionEngine(network=FakeNetwork())
    )
    pre = await acts.pre_check_change(CheckInput(change_id=change_id, intent=intent, kind="pre"))
    assert pre["ok"], pre
    wb = pre["evidence"]["excel_writeback"]
    assert wb["ok"] and wb["itsm_attachment"]
    latest = await excel.latest("RITM0005001")
    assert (
        latest is not None
        and latest.kind == "updated"
        and latest.filename == "push-with-backup.xlsx"
    )
    sheet = openpyxl.load_workbook(io.BytesIO(latest.content))["backup"]
    header = [c.value for c in sheet[1]]
    row = [c.value for c in sheet[2]]
    assert row[header.index("Rollback config")] == "no vlan 300"  # the user's column is kept
    assert row[header.index("Running config (pre-change)")].startswith("hostname leaf-01")
    names = [a.filename for a in await itsm.list_attachments("RITM0005001")]
    assert "push-with-backup.xlsx" in names
    assert any("pre-change running config" in c for c in itsm.tickets["RITM0005001"].comments)
    await db.dispose()


async def test_rollback_pushes_the_workbook_rollback_config(tmp_path) -> None:
    engine = FakeExecutionEngine()
    acts, change_id, intent, _excel, _itsm, db = await _setup(tmp_path, engine)
    rb = await acts.rollback_change(RollbackInput(change_id=change_id, intent=intent))
    assert rb["ok"], rb
    assert rb["sources"]["leaf-01"] == {"source": "excel"}
    call = [c for c in engine.calls if c.step == "rollback"][-1]
    assert call.extra_vars["restore_lines"] == "no vlan 300"
    await db.dispose()


# --- API and the full workflow ---------------------------------------------------------


def _client(tmp_path):  # type: ignore[no-untyped-def]
    url = f"sqlite+aiosqlite:///{tmp_path}/api.db"
    import asyncio

    async def init() -> None:
        e = create_async_engine(url)
        async with e.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        await e.dispose()

    asyncio.run(init())
    app = create_app(Settings(adapter_mode="fake", dev=True, database_url=url, policy_file="/none"))
    app.dependency_overrides[get_current_user] = lambda: CurrentUser("u", "op1", "operator")
    return TestClient(app), app


def test_api_upload_ticket_and_workbook_download(tmp_path) -> None:
    c, app = _client(tmp_path)
    tpl = c.get("/config-push/template.xlsx")
    b64 = base64.b64encode(tpl.content).decode()
    assert (
        c.post("/config-push/preview", json={"filename": "t.xlsx", "content_b64": b64}).json()[
            "valid"
        ]
        is True
    )
    up = c.post(
        "/config-push",
        json={
            "filename": "t.xlsx",
            "content_b64": b64,
            "ticket_number": "RITM0009001",
            "site": "dc1",
        },
    ).json()
    assert up["change"]["change_type"] == "config_push" and up["change"]["state"] == "validated"
    assert up["workflow_started"] is True
    tk = c.post(
        "/config-push/from-ticket", json={"ticket_number": "RITM0005001", "site": "dc1"}
    ).json()
    assert tk["change"]["requester"] == "alice"
    dl = c.get(f"/changes/{up['change']['id']}/workbook")
    assert dl.status_code == 200 and dl.headers["x-vireonet-workbook"] == "original"
    assert (
        c.post(
            "/config-push/from-ticket", json={"ticket_number": "NET-42", "site": "dc1"}
        ).status_code
        == 422
    )
    assert (
        c.post(
            "/config-push/preview", json={"filename": "t.xlsx", "content_b64": "%%%"}
        ).status_code
        == 422
    )


def test_api_rejects_a_dangerous_push(tmp_path) -> None:
    c, _ = _client(tmp_path)
    content = build_workbook({"leaf-01": "vlan 9\nwrite erase"})
    b64 = base64.b64encode(content).decode()
    prev = c.post("/config-push/preview", json={"filename": "x.xlsx", "content_b64": b64}).json()
    assert prev["valid"] is False and "write erase" in prev["errors"][0]
    sub = c.post(
        "/config-push",
        json={
            "filename": "x.xlsx",
            "content_b64": b64,
            "ticket_number": "RITM0009002",
            "site": "dc1",
        },
    ).json()
    assert sub["change"]["state"] == "rejected" and sub["workflow_started"] is False


def test_webhook_needs_the_secret(tmp_path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path}/w.db"
    app = create_app(
        Settings(adapter_mode="fake", dev=False, webhook_secret="s3cret", database_url=url)
    )
    c = TestClient(app)
    assert (
        c.post(
            "/webhooks/itsm/config-push", json={"ticket_number": "RITM0005001", "site": "dc1"}
        ).status_code
        == 401
    )


async def test_workflow_pushes_excel_config_and_closes(tmp_path) -> None:
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from vireonet.temporal.activities import all_activities
    from vireonet.temporal.workflow import ChangeWorkflow, ChangeWorkflowInput

    network = FakeNetwork()
    engine = FakeExecutionEngine(network=network)
    acts, change_id, intent, excel, itsm, db = await _setup(tmp_path, engine)
    netbox = FakeNetBoxAdapter()
    seed_demo_netbox(netbox)
    acts._netbox = netbox
    async with acts._sessions() as s:
        ch = await s.get(Change, uuid.UUID(change_id))
        assert ch is not None
        ch.state = ChangeState.VALIDATED
        await s.commit()
    async with (
        await WorkflowEnvironment.start_time_skipping() as env,
        Worker(
            env.client,
            task_queue="xl-q",
            workflows=[ChangeWorkflow],
            activities=all_activities(acts),
        ),
    ):
        h = await env.client.start_workflow(
            ChangeWorkflow.run,
            ChangeWorkflowInput(
                change_id=change_id,
                ticket_number="RITM0005001",
                intent=intent,
                requester="alice",
                ci_timeout_seconds=60,
                approval_timeout_seconds=60,
            ),
            id=f"xl-{uuid.uuid4().hex[:6]}",
            task_queue="xl-q",
        )
        await h.signal(ChangeWorkflow.ci_passed)
        await h.signal(ChangeWorkflow.merge_completed)
        await h.signal(ChangeWorkflow.approve, "ok")
        assert await h.result() == "closed"
    applies = [c for c in engine.calls if c.step == "apply"]
    assert applies and applies[0].extra_vars["intended_config"] == "vlan 300\n name x\n"
    assert (await excel.latest("RITM0005001")).kind == "updated"  # type: ignore[union-attr]
    await db.dispose()


async def test_workflow_milestones_are_noted_on_the_ticket_and_it_is_closed(tmp_path) -> None:
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from vireonet.temporal.activities import all_activities
    from vireonet.temporal.workflow import ChangeWorkflow, ChangeWorkflowInput

    acts, change_id, intent, _excel, itsm, db = await _setup(
        tmp_path, FakeExecutionEngine(network=FakeNetwork())
    )
    acts._itsm = itsm
    netbox = FakeNetBoxAdapter()
    seed_demo_netbox(netbox)
    acts._netbox = netbox
    async with acts._sessions() as s:
        ch = await s.get(Change, uuid.UUID(change_id))
        assert ch is not None
        ch.state = ChangeState.VALIDATED
        await s.commit()
    async with (
        await WorkflowEnvironment.start_time_skipping() as env,
        Worker(
            env.client,
            task_queue="it-q",
            workflows=[ChangeWorkflow],
            activities=all_activities(acts),
        ),
    ):
        h = await env.client.start_workflow(
            ChangeWorkflow.run,
            ChangeWorkflowInput(
                change_id=change_id,
                ticket_number="RITM0005001",
                intent=intent,
                requester="alice",
                ci_timeout_seconds=60,
                approval_timeout_seconds=60,
            ),
            id=f"it-{uuid.uuid4().hex[:6]}",
            task_queue="it-q",
        )
        await h.signal(ChangeWorkflow.ci_passed)
        await h.signal(ChangeWorkflow.merge_completed)
        await h.signal(ChangeWorkflow.approve, "ok")
        assert await h.result() == "closed"
    t = itsm.tickets["RITM0005001"]
    notes = "\n".join(t.comments)
    for step in ("-> planned", "-> approved", "-> pre_checked", "-> applied", "-> post_checked"):
        assert step in notes, step
    assert t.ticket.status == "closed"
    await db.dispose()
