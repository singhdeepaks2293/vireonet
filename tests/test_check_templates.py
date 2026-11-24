"""Per-intent, user-defined pre/post check templates (policies/checks.yaml)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jinja2 import TemplateError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from vireonet.adapters.fakes import FakeAwxAdapter, FakeNetBoxAdapter, FakeServiceNowAdapter
from vireonet.adapters.protocols import NetBoxDevice, NetBoxVlan
from vireonet.api.security import CurrentUser, get_current_user
from vireonet.checks.base import CheckContext
from vireonet.checks.templates import (
    CheckTemplates,
    TemplateCommandCheck,
    TemplateValidationError,
)
from vireonet.config import Settings
from vireonet.db import Base
from vireonet.execution.awx_engine import parse_command_outputs
from vireonet.execution.fake_engine import FakeExecutionEngine
from vireonet.execution.fake_network import FakeNetwork
from vireonet.intent import parse_intent
from vireonet.main import create_app
from vireonet.models import Change
from vireonet.policy import PolicyEngine
from vireonet.state_machine import ChangeState
from vireonet.temporal.activities import ApplyInput, ChangeActivities, CheckInput

REPO = Path(__file__).resolve().parents[1]
VLAN = {
    "change_type": "vlan_add",
    "vlan_id": 120,
    "vlan_name": "app-tier",
    "devices": ["leaf-01"],
    "site": "dc1",
    "ticket_number": "RITM0000120",
    "requested_by": "alice",
}


def _yaml(body: str) -> str:
    return "version: 1\n" + body


def _errors(text: str) -> list[str]:
    with pytest.raises(TemplateValidationError) as exc:
        CheckTemplates.from_yaml_text(text)
    return exc.value.errors


# --- Loading and validation --------------------------------------------------


def test_shipped_policy_file_is_valid_and_covers_every_change_type() -> None:
    templates = CheckTemplates.from_file(REPO / "policies/checks.yaml")
    summary = templates.summary()
    assert set(summary["change_types"]) >= {"vlan_add", "static_route_add", "interface_set"}
    names = [c["name"] for c in summary["change_types"]["vlan_add"]["post"]]
    assert "vlan_active" in names and "config_matches_intent" in names


def test_default_templates_keep_the_old_check_lists() -> None:
    intent = parse_intent(VLAN)
    t = CheckTemplates.default()
    assert [c.name for c in t.checks_for(intent, "pre")] == [
        "reachability",
        "no_active_lock",
        "interface_state",
        "backup_taken",
        "neighbour_baseline",
    ]
    assert [c.name for c in t.checks_for(intent, "post")] == [
        "reachability",
        "config_matches_intent",
        "no_unexpected_diff",
        "neighbour_baseline",
    ]


def test_missing_file_falls_back_to_defaults(tmp_path) -> None:
    t = CheckTemplates.load(tmp_path / "nope.yaml")
    assert t.source == "<builtin>"


def test_change_type_checks_extend_or_replace_defaults() -> None:
    t = CheckTemplates.from_yaml_text(
        _yaml(
            """
defaults:
  pre: [reachability]
change_types:
  vlan_add:
    pre:
      checks: [backup_taken]
  vlan_remove:
    pre:
      extends_defaults: false
      checks: [no_active_lock]
"""
        )
    )
    add = parse_intent(VLAN)
    rm = parse_intent(
        {k: v for k, v in VLAN.items() if k != "vlan_name"} | {"change_type": "vlan_remove"}
    )
    assert [c.name for c in t.checks_for(add, "pre")] == ["reachability", "backup_taken"]
    assert [c.name for c in t.checks_for(rm, "pre")] == ["no_active_lock"]


@pytest.mark.parametrize(
    "body,needle",
    [
        ("change_types:\n  vlan_frobnicate: {}\n", "unknown change type"),
        ("defaults:\n  pre: [no_such_check]\n", "unknown built-in"),
        (
            "defaults:\n  pre:\n    - name: bad\n      command: {default: 'configure terminal'}\n"
            "      expect: {contains: x}\n",
            "read-only",
        ),
        (
            "defaults:\n  pre:\n    - name: bad\n      command: {default: 'show {{ intent.vlan_id '}\n"
            "      expect: {contains: x}\n",
            "does not parse",
        ),
        (
            "defaults:\n  pre:\n    - name: bad\n      command: {default: '{{ intent.cmd }}'}\n"
            "      expect: {contains: x}\n",
            "literal text",
        ),
        ("defaults:\n  pre: [reachability, reachability]\n", "listed twice"),
        (
            "defaults:\n  pre:\n    - name: reachability\n      command: {default: 'show x'}\n"
            "      expect: {contains: x}\n",
            "clashes",
        ),
        (
            "defaults:\n  pre:\n    - name: noexp\n      command: {default: 'show x'}\n"
            "      expect: {}\n",
            "expect",
        ),
        ("bogus_key: 1\n", "bogus_key"),
    ],
)
def test_invalid_templates_are_rejected_with_a_clear_error(body: str, needle: str) -> None:
    errors = _errors(_yaml(body))
    assert any(needle in e for e in errors), errors


# --- Rendering safety --------------------------------------------------------


def _command_check(command: str, expect: dict, **extra) -> TemplateCommandCheck:  # type: ignore[type-arg]
    t = CheckTemplates.from_yaml_text(
        _yaml(
            "defaults:\n  pre:\n"
            + "    - "
            + json.dumps({"name": "c1", "command": {"default": command}, "expect": expect, **extra})
            + "\n"
        )
    )
    check = t.checks_for(parse_intent(VLAN), "pre")[0]
    assert isinstance(check, TemplateCommandCheck)
    return check


def test_rendered_command_with_injected_newline_is_refused() -> None:
    check = _command_check("show vlan name {{ intent.vlan_name }}", {"contains": "x"})
    evil = parse_intent({**VLAN, "vlan_name": "x\nconf t"})
    with pytest.raises(TemplateValidationError, match="single line"):
        check.render_for(evil, "leaf-01", "ios")


def test_sandbox_blocks_python_attribute_tricks() -> None:
    check = _command_check("show {{ intent.__class__.__mro__ }}", {"contains": "x"})
    with pytest.raises(TemplateError):
        check.render_for(parse_intent(VLAN), "leaf-01", "ios")


async def test_typo_in_template_variable_fails_the_check_not_silently() -> None:
    check = _command_check("show vlan id {{ intent.vlan_idd }}", {"contains": "x"})
    ctx = _ctx(lambda *_: ["anything"])
    result = await check.run(ctx)
    assert result.status == "fail"
    assert "template error" in result.message


# --- Running command checks --------------------------------------------------


def _ctx(responder, platform: str = "ios") -> CheckContext:  # type: ignore[no-untyped-def]
    netbox = FakeNetBoxAdapter()
    netbox.devices["leaf-01"] = NetBoxDevice(
        "leaf-01", "S1", "m", "dc1", "active", platform, "10.0.0.11"
    )

    async def run(device: str, plat: str, commands: list[str]) -> list[str]:
        return list(responder(device, plat, commands))

    return CheckContext(
        intent=parse_intent(VLAN),
        ticket_number="RITM0000120",
        netbox=netbox,
        awx=FakeAwxAdapter(),
        run_commands=run,
    )


async def test_command_check_passes_and_records_evidence() -> None:
    check = _command_check("show vlan id {{ intent.vlan_id }}", {"regex": r"120\s+app-tier"})
    seen: list[list[str]] = []

    def responder(_d, _p, cmds):  # type: ignore[no-untyped-def]
        seen.append(cmds)
        return ["120  app-tier   active"]

    result = await check.run(_ctx(responder))
    assert result.status == "pass", result.message
    assert seen == [["show vlan id 120"]]
    assert result.evidence["leaf-01"]["command"] == "show vlan id 120"  # type: ignore[index]


async def test_command_check_fails_when_output_does_not_match() -> None:
    check = _command_check(
        "show vlan id {{ intent.vlan_id }}", {"contains": "{{ intent.vlan_name }}"}
    )
    result = await check.run(_ctx(lambda *_: ["VLAN id 120 not found"]))
    assert result.status == "fail"
    assert "app-tier" in result.message


async def test_warn_checks_never_block() -> None:
    check = _command_check("show x", {"contains": "nope"}, on_fail="warn")
    result = await check.run(_ctx(lambda *_: ["something else"]))
    assert result.status == "skip" and result.message.startswith("WARN")
    assert result.ok


async def test_per_platform_command_and_expectation() -> None:
    t = CheckTemplates.from_yaml_text(
        _yaml(
            """
defaults:
  post:
    - name: vlan_up
      command:
        ios: "show vlan id {{ intent.vlan_id }}"
        junos: "show vlans {{ intent.vlan_name }}"
      expect:
        ios: {regex: "{{ intent.vlan_id }}\\\\s+{{ intent.vlan_name }}"}
        junos: {regex: "{{ intent.vlan_name }}\\\\s+{{ intent.vlan_id }}"}
"""
        )
    )
    check = t.checks_for(parse_intent(VLAN), "post")[0]
    sent: list[str] = []

    def junos(_d, _p, cmds):  # type: ignore[no-untyped-def]
        sent.extend(cmds)
        return ["default-switch  app-tier  120"]

    result = await check.run(_ctx(junos, platform="junos"))
    assert result.status == "pass", result.message
    assert sent == ["show vlans app-tier"]


async def test_missing_platform_skips_or_fails_as_configured() -> None:
    skip = _command_check("show x", {"contains": "x"})
    skip.spec.command = {"junos": "show x"}
    assert (await skip.run(_ctx(lambda *_: ["x"], platform="eos"))).status == "skip"
    fail = _command_check("show x", {"contains": "x"}, on_missing_platform="fail")
    fail.spec.command = {"junos": "show x"}
    assert (await fail.run(_ctx(lambda *_: ["x"], platform="eos"))).status == "fail"


async def test_command_check_without_runner_fails_closed() -> None:
    check = _command_check("show x", {"contains": "x"})
    ctx = _ctx(lambda *_: ["x"])
    ctx.run_commands = None
    assert (await check.run(ctx)).status == "fail"


# --- AWX output parsing -------------------------------------------------------


def test_parse_command_outputs_raw_and_ansible_escaped() -> None:
    raw = 'x\nVIREONET-COMMANDS-BEGIN\n["a\\nb", "c"]\nVIREONET-COMMANDS-END\n'
    assert parse_command_outputs(raw) == ["a\nb", "c"]
    escaped = '"msg": "VIREONET-COMMANDS-BEGIN\\n[\\"a\\", \\"b\\"]\\nVIREONET-COMMANDS-END"'
    assert parse_command_outputs(escaped) == ["a", "b"]
    assert parse_command_outputs("no markers") is None


# --- End to end with the simulated network ------------------------------------


async def _activities(tmp_path, engine):  # type: ignore[no-untyped-def]
    db = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(db, expire_on_commit=False)
    change_id = str(uuid.uuid4())
    async with factory() as s:
        s.add(
            Change(
                id=uuid.UUID(change_id),
                ticket_number=VLAN["ticket_number"],
                change_type="vlan_add",
                state=ChangeState.APPROVED,
                requester="alice",
                intent=VLAN,
                devices=["leaf-01"],
            )
        )
        await s.commit()
    netbox = FakeNetBoxAdapter()
    netbox.vlans[120] = NetBoxVlan(120, "app-tier", "dc1")  # NetBox is the source of truth
    acts = ChangeActivities(
        factory,
        servicenow=FakeServiceNowAdapter(),
        netbox=netbox,
        awx=FakeAwxAdapter(),
        policy=PolicyEngine.default(),
        engine=engine,
        check_templates=CheckTemplates.from_file(REPO / "policies/checks.yaml"),
    )
    return acts, change_id, db


async def test_shipped_templates_against_simulated_network(tmp_path) -> None:
    network = FakeNetwork()
    engine = FakeExecutionEngine(network=network)
    acts, change_id, db = await _activities(tmp_path, engine)
    pre = await acts.pre_check_change(CheckInput(change_id=change_id, intent=VLAN, kind="pre"))
    assert pre["ok"], pre
    names = {c["name"] for c in pre["evidence"]["checks"]}
    assert "vlan_id_unused" in names
    backups = pre["evidence"]["backups"]

    # Before apply the VLAN is not on the device: the post-check must fail.
    early = await acts.post_check_change(
        CheckInput(change_id=change_id, intent=VLAN, kind="post", backups=backups)
    )
    assert not early["ok"]
    assert any("vlan_active" in f or "120" in f for f in early["failures"])

    applied = await acts.apply_change(ApplyInput(change_id=change_id, intent=VLAN))
    assert applied["ok"], applied
    assert network.device("leaf-01").vlans[120] == "app-tier"
    post = await acts.post_check_change(
        CheckInput(change_id=change_id, intent=VLAN, kind="post", backups=backups)
    )
    assert post["ok"], post
    await db.dispose()


async def test_pre_check_blocks_when_vlan_already_exists(tmp_path) -> None:
    network = FakeNetwork()
    network.device("leaf-01").vlans[120] = "someone-else"
    acts, change_id, db = await _activities(tmp_path, FakeExecutionEngine(network=network))
    pre = await acts.pre_check_change(CheckInput(change_id=change_id, intent=VLAN, kind="pre"))
    assert not pre["ok"]
    assert any(
        "vlan_id_unused" in c["name"] and c["status"] == "fail" for c in pre["evidence"]["checks"]
    )
    await db.dispose()


def test_rollback_restores_simulated_device() -> None:
    network = FakeNetwork()
    network.snapshot("leaf-01", "T1")
    network.apply("leaf-01", {"change_type": "vlan_add", "vlan_id": 7, "vlan_name": "x"})
    assert 7 in network.device("leaf-01").vlans
    assert network.restore("leaf-01", "T1")
    assert 7 not in network.device("leaf-01").vlans


# --- API -------------------------------------------------------------------


def _client(role: str = "operator") -> TestClient:
    app = create_app(
        Settings(
            adapter_mode="fake",
            dev=True,
            database_url="sqlite+aiosqlite:///:memory:",
            check_templates_file=str(REPO / "policies/checks.yaml"),
        )
    )
    app.dependency_overrides[get_current_user] = lambda: CurrentUser("u1", "op1", role)
    return TestClient(app)


def test_api_lists_active_templates() -> None:
    body = _client("viewer").get("/checks/templates").json()
    assert body["valid"] is True
    assert "vlan_add" in body["templates"]["change_types"]


def test_api_validate_reports_errors() -> None:
    client = _client()
    bad = client.post(
        "/checks/templates/validate", json={"yaml": "version: 1\ndefaults: {pre: [nope]}"}
    )
    assert bad.json()["valid"] is False and bad.json()["errors"]
    ok = client.post(
        "/checks/templates/validate", json={"yaml": (REPO / "policies/checks.yaml").read_text()}
    )
    assert ok.json()["valid"] is True


def test_api_validate_needs_operator() -> None:
    assert (
        _client("viewer").post("/checks/templates/validate", json={"yaml": ""}).status_code == 403
    )


def test_api_preview_renders_commands_per_platform() -> None:
    client = _client("viewer")
    samples = client.get("/checks/sample-intents").json()
    for ct, intent in samples.items():
        resp = client.post("/checks/templates/preview", json={"intent": intent, "platform": "nxos"})
        assert resp.status_code == 200, (ct, resp.text)
    resp = client.post(
        "/checks/templates/preview", json={"intent": samples["vlan_add"], "platform": "junos"}
    ).json()
    post = {r["name"]: r for r in resp["preview"]["post"]}
    assert post["vlan_active"]["command"] == "show vlans app-tier"
    assert "error" not in post["vlan_active"]


def test_api_preview_rejects_bad_intent() -> None:
    resp = _client("viewer").post(
        "/checks/templates/preview", json={"intent": {"change_type": "vlan_add"}}
    )
    assert resp.status_code == 422


async def test_workflow_closes_with_shipped_templates_on_simulated_network(tmp_path) -> None:
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from vireonet.temporal.activities import all_activities
    from vireonet.temporal.workflow import ChangeWorkflow, ChangeWorkflowInput

    network = FakeNetwork()
    acts, change_id, db = await _activities(tmp_path, FakeExecutionEngine(network=network))
    async with acts._sessions() as s:  # workflow starts from validated
        change = await s.get(Change, uuid.UUID(change_id))
        assert change is not None
        change.state = ChangeState.VALIDATED
        await s.commit()
    async with (
        await WorkflowEnvironment.start_time_skipping() as env,
        Worker(
            env.client,
            task_queue="tmpl-q",
            workflows=[ChangeWorkflow],
            activities=all_activities(acts),
        ),
    ):
        handle = await env.client.start_workflow(
            ChangeWorkflow.run,
            ChangeWorkflowInput(
                change_id=change_id,
                ticket_number=VLAN["ticket_number"],
                intent=VLAN,
                requester="alice",
                ci_timeout_seconds=60,
                approval_timeout_seconds=60,
            ),
            id=f"{VLAN['ticket_number']}-{uuid.uuid4().hex[:6]}",
            task_queue="tmpl-q",
        )
        await handle.signal(ChangeWorkflow.ci_passed)
        await handle.signal(ChangeWorkflow.merge_completed)
        await handle.signal(ChangeWorkflow.approve, "ok")
        assert await handle.result() == "closed"
    assert network.device("leaf-01").vlans[120] == "app-tier"
    await db.dispose()
