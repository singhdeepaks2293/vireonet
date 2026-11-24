"""FlowWorkflow in Temporal: the OS-upgrade example end to end (ITSM event ->
flow -> child change pipeline -> device -> ticket closed), approvals by
signal, sub-flows, cancellation and a failing change caught by Try."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from vireonet.adapters.fakes import FakeAwxAdapter, FakeNetBoxAdapter, FakeServiceNowAdapter
from vireonet.db import Base
from vireonet.execution.fake_engine import FakeExecutionEngine
from vireonet.execution.fake_network import FakeNetwork
from vireonet.flows.activities import FlowActivities, all_flow_activities
from vireonet.flows.dispatch import FlowDispatcher
from vireonet.flows.examples import example
from vireonet.flows.store import FlowStore
from vireonet.itsm.fake import FakeItsm
from vireonet.policy import PolicyEngine
from vireonet.temporal.activities import ChangeActivities, all_activities
from vireonet.topology.demo import seed_demo_netbox


class Rig:
    def __init__(
        self, env: Any, store: FlowStore, network: FakeNetwork, itsm: FakeItsm, queue: str
    ) -> None:
        self.env, self.store, self.network, self.itsm, self.queue = env, store, network, itsm, queue
        self.handles: dict[str, Any] = {}

    async def start(self, run_id: str) -> None:
        from vireonet.flows.workflow import FlowWorkflow, FlowWorkflowInput, flow_workflow_id

        self.handles[run_id] = await self.env.client.start_workflow(
            FlowWorkflow.run,
            FlowWorkflowInput(run_id=run_id),
            id=flow_workflow_id(run_id),
            task_queue=self.queue,
        )

    async def flow(self, data: dict[str, Any], name: str | None = None) -> dict[str, Any]:
        flow, _ = await self.store.create(
            name=name or data["name"],
            trigger=data["trigger"],
            definition=data["definition"],
            actor="test",
            allow_scripts=True,
            publish=True,
            enabled=True,
        )
        return flow


@pytest.fixture
async def rig(tmp_path):  # type: ignore[no-untyped-def]
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from vireonet.flows.workflow import FlowScheduleWorkflow, FlowWorkflow
    from vireonet.temporal.workflow import ChangeWorkflow
    from vireonet.worker import workflow_runner

    db = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/wf.db")
    async with db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(db, expire_on_commit=False)
    network = FakeNetwork()
    engine = FakeExecutionEngine(network=network)
    netbox = FakeNetBoxAdapter()
    seed_demo_netbox(netbox)
    itsm = FakeItsm()
    snow = FakeServiceNowAdapter()
    policy = PolicyEngine.default()
    changes = ChangeActivities(
        sessions,
        servicenow=snow,
        netbox=netbox,
        awx=FakeAwxAdapter(),
        policy=policy,
        engine=engine,
        itsm=itsm,
    )
    from vireonet.config import Settings
    from vireonet.controllers import build_controllers

    flows = FlowActivities(
        sessions,
        settings=_Settings(),
        engine=engine,
        netbox=netbox,
        itsm=itsm,
        policy=policy,
        servicenow=snow,
        controllers=build_controllers(Settings(adapter_mode="fake"), network=network),
    )
    queue = f"flows-{uuid.uuid4().hex[:6]}"
    async with (
        await WorkflowEnvironment.start_time_skipping() as env,
        Worker(
            env.client,
            task_queue=queue,
            workflows=[ChangeWorkflow, FlowWorkflow, FlowScheduleWorkflow],
            activities=all_activities(changes) + all_flow_activities(flows),
            workflow_runner=workflow_runner(),
        ),
    ):
        yield Rig(env, FlowStore(sessions), network, itsm, queue)
    await db.dispose()


class _Settings:
    flow_http_allowed_hosts = ["*"]
    flow_http_denied_hosts: list[str] = []
    flow_http_verify_tls = True
    flow_scripts_enabled = True
    flow_script_max_seconds = 30
    flow_script_memory_mb = 512
    textfsm_user_dir = "policies/textfsm"
    post_apply_soak_seconds = 0
    controller_poll_seconds = 1
    notification_channel = ""


async def _wait_for(store: FlowStore, run_id: str, status: str, tries: int = 200) -> dict[str, Any]:
    for _ in range(tries):
        run = await store.get_run(run_id)
        if run["status"] == status:
            return run
        await asyncio.sleep(0.1)
    raise AssertionError(f"run never reached {status}: {run['status']}")


async def test_os_upgrade_example_end_to_end(rig: Rig) -> None:
    await rig.flow(example("os-upgrade-itsm"))
    ticket = await rig.itsm.get_ticket("CHG0030005")
    d = FlowDispatcher(rig.store, rig.start)
    (res,) = await d.dispatch(
        {"source": "itsm", "event": "state_changed", "ticket": ticket.to_dict()}
    )
    assert res["status"] == "started"
    result = await rig.handles[res["run_id"]].result()
    assert result["status"] == "succeeded", result
    assert result["outputs"] == {
        "device": "leaf-01",
        "from": "17.6.5",
        "to": "17.9.4",
        "image": "flash:cat9k_iosxe.17.09.04.SPA.bin",
    }
    assert rig.network.device("leaf-01").boot_image == "flash:cat9k_iosxe.17.09.04.SPA.bin"
    assert (await rig.itsm.get_ticket("CHG0030005")).status == "closed"
    notes = await rig.itsm.comments("CHG0030005")
    assert (
        notes[0].startswith("VireoNet flow: OS upgrade of leaf-01")
        and "boot image set" in notes[-1]
    )
    run = await rig.store.get_run(res["run_id"])
    done = {
        e["id"]: e["status"]
        for e in run["log"]
        if e.get("status") in ("succeeded", "failed", "skipped")
    }
    assert done["push_boot"] == "succeeded" and done["close_ok"] == "succeeded"
    assert run["variables"]["change"]["result"] == "closed"


async def test_already_on_target_version_stops_early(rig: Rig) -> None:
    rig.network.device("leaf-01").version = "17.9.4"
    await rig.flow(example("os-upgrade-itsm"))
    ticket = await rig.itsm.get_ticket("CHG0030005")
    (res,) = await FlowDispatcher(rig.store, rig.start).dispatch(
        {"source": "itsm", "event": "created", "ticket": ticket.to_dict()}
    )
    result = await rig.handles[res["run_id"]].result()
    assert result["status"] == "succeeded" and result["message"] == "already on the target version"
    assert "already runs 17.9.4" in (await rig.itsm.comments("CHG0030005"))[-1]


async def test_failed_change_is_caught_reported_and_fails_the_run(rig: Rig) -> None:
    data = example("os-upgrade-itsm")
    # A config the change pipeline refuses at intake (raw-config denylist).
    push = data["definition"]["steps"][-1]["steps"][0]
    push["intent"]["configs"] = {"{{ trigger.match.device }}": "reload"}
    await rig.flow(data)
    ticket = await rig.itsm.get_ticket("CHG0030005")
    (res,) = await FlowDispatcher(rig.store, rig.start).dispatch(
        {"source": "itsm", "event": "created", "ticket": ticket.to_dict()}
    )
    result = await rig.handles[res["run_id"]].result()
    assert result["status"] == "failed" and "rejected" in result["error"]
    assert "failed" in (await rig.itsm.comments("CHG0030005"))[-1]
    assert (await rig.itsm.get_ticket("CHG0030005")).status != "closed"


async def test_approval_signal_and_rejection(rig: Rig) -> None:
    from vireonet.flows.workflow import FlowWorkflow

    flow = await rig.flow(
        {
            "name": "Ask",
            "trigger": {"type": "manual"},
            "definition": {
                "steps": [
                    {
                        "id": "ask",
                        "type": "approval",
                        "message": "go?",
                        "timeout_minutes": 5,
                        "save_as": "d",
                    },
                    {
                        "id": "after",
                        "type": "set_variable",
                        "name": "by",
                        "value": "{{ vars.d.by }}",
                    },
                ]
            },
        }
    )
    d = FlowDispatcher(rig.store, rig.start)
    run = await d.run_manual(flow["id"], inputs={}, user="alice")
    waiting = await _wait_for(rig.store, run["id"], "waiting")
    assert waiting["waiting"]["ask"]["role"] == "approver"
    await rig.handles[run["id"]].signal(FlowWorkflow.decide, args=["ask", True, "bob", "ok"])
    result = await rig.handles[run["id"]].result()
    assert result["status"] == "succeeded" and result["vars"]["by"] == "bob"
    assert (await rig.store.get_run(run["id"]))["waiting"] == {}
    run2 = await d.run_manual(flow["id"], inputs={}, user="alice")
    await _wait_for(rig.store, run2["id"], "waiting")
    await rig.handles[run2["id"]].signal(FlowWorkflow.decide, args=["ask", False, "bob", "no"])
    r2 = await rig.handles[run2["id"]].result()
    assert r2["status"] == "failed" and "not approved by bob: no" in r2["error"]


async def test_sub_flow_and_cancel(rig: Rig) -> None:
    from vireonet.flows.workflow import FlowWorkflow

    await rig.flow(
        {
            "name": "child",
            "trigger": {"type": "manual"},
            "definition": {
                "inputs": [{"name": "n", "type": "number"}],
                "steps": [
                    {"type": "set_variable", "name": "double", "value": "{{ inputs.n * 2 }}"}
                ],
                "outputs": {"double": "{{ vars.double }}"},
            },
        }
    )
    parent = await rig.flow(
        {
            "name": "parent",
            "trigger": {"type": "manual"},
            "definition": {
                "steps": [
                    {
                        "id": "sub",
                        "type": "run_flow",
                        "flow": "child",
                        "inputs": {"n": 21},
                        "save_as": "sub",
                    },
                    {"id": "ask", "type": "approval", "message": "wait here", "timeout_minutes": 5},
                ]
            },
        }
    )
    d = FlowDispatcher(rig.store, rig.start)
    run = await d.run_manual(parent["id"], inputs={}, user="a")
    await _wait_for(rig.store, run["id"], "waiting")
    mid = await rig.store.get_run(run["id"])
    sub = next(e for e in mid["log"] if e["id"] == "sub" and e["status"] == "succeeded")
    assert sub["output"]["outputs"] == {"double": 42}
    await rig.handles[run["id"]].signal(FlowWorkflow.cancel, "ops")
    result = await rig.handles[run["id"]].result()
    assert result["status"] == "cancelled"
    assert (await rig.store.get_run(run["id"]))["status"] == "cancelled"
    child_runs = [r for r in await rig.store.list_runs() if r["parent_run_id"] == run["id"]]
    assert child_runs and child_runs[0]["status"] == "succeeded"


@pytest.mark.parametrize(
    ("ticket", "device", "platform", "version", "controller"),
    [
        ("CHG0030006", "spine-01", "eos", "4.32.1F", "cloudvision"),
        ("CHG0030007", "fw-02", "panos", "11.1.2", "panorama"),
    ],
)
async def test_os_upgrade_via_controller_end_to_end(
    rig: Rig, ticket: str, device: str, platform: str, version: str, controller: str
) -> None:
    await rig.flow(example("os-upgrade-controller"))
    t = await rig.itsm.get_ticket(ticket)
    (res,) = await FlowDispatcher(rig.store, rig.start).dispatch(
        {"source": "itsm", "event": "state_changed", "ticket": t.to_dict()}
    )
    result = await rig.handles[res["run_id"]].result()
    assert result["status"] == "succeeded", result
    assert result["outputs"]["to"] == version and result["outputs"]["controller"] == controller
    assert version in rig.network.respond(device, platform, "show version")
    assert (await rig.itsm.get_ticket(ticket)).status == "closed"
    assert "upgraded from" in (await rig.itsm.comments(ticket))[-1]


async def test_controller_failure_rolls_back_to_the_previous_version(rig: Rig) -> None:
    data = example("os-upgrade-controller")
    # Verification fails after a good upgrade -> On error -> activate the old version.
    verify = data["definition"]["steps"][-1]["steps"][2]
    verify["branches"][0]["condition"] = {"expr": "true"}
    await rig.flow(data)
    t = await rig.itsm.get_ticket("CHG0030006")
    (res,) = await FlowDispatcher(rig.store, rig.start).dispatch(
        {"source": "itsm", "event": "state_changed", "ticket": t.to_dict()}
    )
    result = await rig.handles[res["run_id"]].result()
    assert result["status"] == "failed"
    assert "4.30.2F" in rig.network.respond("spine-01", "eos", "show version")  # rolled back
    notes = await rig.itsm.comments("CHG0030006")
    assert "Activating the previous version 4.30.2F" in notes[-1]
