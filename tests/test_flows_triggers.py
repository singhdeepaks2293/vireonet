"""Flow triggers: adapter sources, matching, store, dispatcher, Kafka, activities."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from vireonet.db import Base
from vireonet.flows.activities import FlowActionInput, FlowActivities, _host_allowed
from vireonet.flows.catalog import TRIGGERS
from vireonet.flows.dispatch import FlowDispatcher
from vireonet.flows.kafka import KafkaTriggerConsumer, message_event
from vireonet.flows.schema import FlowValidationError
from vireonet.flows.sources import normalise
from vireonet.flows.store import FlowStore
from vireonet.flows.triggers import match

OS_UPGRADE = {
    "type": "itsm",
    "events": ["created", "state_changed"],
    "states": ["Implement"],
    "conditions": {
        "all": [
            {
                "field": "ticket.summary",
                "op": "matches",
                "value": r"OS UPGRADE (?P<device>\S+) to (?P<version>[\d.]+)",
            },
        ]
    },
}


def itsm_event(
    summary: str, status: str = "Implement", key: str = "CHG0030005", event: str = "state_changed"
) -> dict[str, Any]:
    return {
        "source": "itsm",
        "event": event,
        "ticket": {"key": key, "summary": summary, "status": status, "fields": {}},
    }


# --- adapter sources ---------------------------------------------------------------------


def test_netbox_webhook_is_normalised_with_before_and_after() -> None:
    body = {
        "event": "updated",
        "model": "device",
        "username": "netops",
        "data": {"name": "leaf-04", "status": {"value": "active"}},
        "snapshots": {"prechange": {"status": "planned"}, "postchange": {"status": "active"}},
    }
    (e,) = normalise("netbox", body)
    assert (e["source"], e["event"], e["model"], e["name"]) == (
        "netbox",
        "updated",
        "device",
        "leaf-04",
    )
    assert e["changed_fields"] == ["status"] and e["previous"]["object"]["status"] == "planned"
    trig = {
        "type": "netbox",
        "events": ["updated"],
        "conditions": {
            "all": [
                {"field": "model", "op": "equals", "value": "device"},
                {"field": "changed_fields", "op": "contains", "value": "status"},
                {"field": "object.status.value", "op": "equals", "value": "active"},
            ]
        },
    }
    assert match(trig, e).matched


def test_zabbix_problem_recovery_and_tags() -> None:
    (p,) = normalise(
        "zabbix",
        {
            "event_id": "88",
            "event_value": "1",
            "severity": "4",
            "host": "leaf-02",
            "trigger_name": "Link down on Ethernet1/49",
            "tags": "interface:Ethernet1/49,scope:availability",
        },
    )
    assert (p["event"], p["severity"], p["severity_level"], p["tags"]["interface"]) == (
        "problem",
        "high",
        4,
        "Ethernet1/49",
    )
    (r,) = normalise("zabbix", {"event_id": "88", "event_value": "0", "severity": "4"})
    assert r["event"] == "recovery"
    trig = {
        "type": "zabbix",
        "events": ["problem"],
        "conditions": {
            "all": [
                {"field": "severity_level", "op": "gte", "value": 4},
                {"field": "trigger", "op": "matches", "value": r"Link down on (?P<interface>\S+)"},
            ]
        },
    }
    m = match(trig, p)
    assert m.matched and m.captures["interface"] == "Ethernet1/49" and m.dedup_key == "88:problem"
    assert not match(trig, r).matched


def test_alertmanager_batches_become_one_event_per_alert() -> None:
    body = {
        "receiver": "vireonet",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "IfDown",
                    "instance": "spine-01:9116",
                    "ifName": "Ethernet1/1",
                },
                "fingerprint": "f1",
                "startsAt": "t",
            },
            {
                "status": "resolved",
                "labels": {"alertname": "IfDown", "instance": "spine-02:9116"},
                "fingerprint": "f2",
            },
        ],
    }
    events = normalise("alertmanager", body)
    assert [(e["source"], e["event"], e["device"]) for e in events] == [
        ("prometheus", "firing", "spine-01"),
        ("prometheus", "resolved", "spine-02"),
    ]
    assert events[0]["interface"] == "Ethernet1/1"
    assert match({"type": "prometheus", "events": ["firing"]}, events[0]).matched


@pytest.mark.parametrize(
    ("source", "body", "headers", "expect"),
    [
        (
            "librenms",
            {"state": 1, "sysName": "border-01", "title": "BGP down", "severity": "Critical"},
            {},
            ("alert", "border-01"),
        ),
        (
            "prtg",
            {"device": "fw-01", "status": "Down (Ping)", "sensor": "Ping"},
            {},
            ("down", "fw-01"),
        ),
        (
            "solarwinds",
            {"NodeName": "leaf-03", "AlertName": "Node down", "event": "Triggered"},
            {},
            ("triggered", "leaf-03"),
        ),
        ("awx", {"id": 7, "name": "vireonet-backup", "status": "failed"}, {}, ("failed", None)),
        (
            "gitea",
            {
                "ref": "refs/heads/main",
                "commits": [{"modified": ["leaf-01/config"]}],
                "repository": {"full_name": "netops/configs"},
            },
            {"X-Gitea-Event": "push"},
            ("push", None),
        ),
    ],
)
def test_other_sources(
    source: str, body: dict[str, Any], headers: dict[str, str], expect: tuple[str, Any]
) -> None:
    (e,) = normalise(source, body, headers)
    assert e["event"] == expect[0]
    if expect[1]:
        assert e["host"] == expect[1]
    if source == "gitea":
        assert e["branch"] == "main" and e["devices"] == ["leaf-01"] and e["provider"] == "gitea"


def test_every_adapter_trigger_has_a_matching_sample() -> None:
    for ttype, spec in TRIGGERS.items():
        if spec.get("adapter"):
            assert match({"type": ttype}, spec["event"]).matched, ttype
    with pytest.raises(KeyError):
        normalise("carrier-pigeon", {})


# --- matching ------------------------------------------------------------------------------


def test_itsm_trigger_filters_type_event_state_and_regex() -> None:
    ok = match(OS_UPGRADE, itsm_event("OS UPGRADE leaf-01 to 17.9.4"))
    assert ok.matched and ok.captures == {
        "device": "leaf-01",
        "version": "17.9.4",
        "match": "OS UPGRADE leaf-01 to 17.9.4",
        "group1": "leaf-01",
        "group2": "17.9.4",
    }
    assert ok.dedup_key == "CHG0030005:Implement"
    assert match(OS_UPGRADE, itsm_event("VLAN add")).reason == "conditions did not match"
    assert (
        "not in" in match(OS_UPGRADE, itsm_event("OS UPGRADE x to 1.2.3", status="Assess")).reason
    )
    assert (
        "event 'updated'"
        in match(OS_UPGRADE, itsm_event("OS UPGRADE x to 1.2.3", event="updated")).reason
    )
    assert "trigger type is itsm" in match(OS_UPGRADE, {"source": "zabbix"}).reason
    custom = match(
        {**OS_UPGRADE, "dedup_key": "{{ ticket.key }}-{{ match.version }}"},
        itsm_event("OS UPGRADE a to 2.0.1"),
    )
    assert custom.dedup_key == "CHG0030005-2.0.1"
    assert (
        match({**OS_UPGRADE, "dedup_key": "none"}, itsm_event("OS UPGRADE a to 2.0.1")).dedup_key
        == ""
    )


# --- store + dispatcher ------------------------------------------------------------------------


@pytest.fixture
async def store(tmp_path):  # type: ignore[no-untyped-def]
    db = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/f.db")
    async with db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield FlowStore(async_sessionmaker(db, expire_on_commit=False))
    await db.dispose()


DEF = {"steps": [{"type": "log", "message": "upgrading {{ trigger.match.device }}"}]}


async def test_versions_publish_enable_and_export(store: FlowStore) -> None:
    flow, token = await store.create(
        name="OS upgrade", trigger=OS_UPGRADE, definition=DEF, actor="alice", allow_scripts=False
    )
    assert flow["version"] == 1 and flow["published_version"] is None and len(token) > 20
    assert await store.check_token(flow["id"], token) and not await store.check_token(
        flow["id"], "x"
    )
    with pytest.raises(FlowValidationError, match="publish a version"):
        await store.set_enabled(flow["id"], True)
    same = await store.update(flow["id"], actor="bob", allow_scripts=False, definition=DEF)
    assert same["version"] == 1  # unchanged definition: no new version
    v2 = await store.update(
        flow["id"],
        actor="bob",
        allow_scripts=False,
        definition={"steps": [{"type": "log", "message": "v2"}]},
        comment="new message",
    )
    assert v2["version"] == 2
    await store.publish(flow["id"], 1)
    await store.set_enabled(flow["id"], True)
    live = await store.triggered_flows({"itsm"})
    assert [(f["name"], f["version"]) for f in live] == [("OS upgrade", 1)]  # published, not latest
    assert [v["version"] for v in await store.versions(flow["id"])] == [2, 1]
    exported = store.export(await store.get(flow["id"]))
    assert exported["format"] == "vireonet-flow/1" and exported["trigger"]["type"] == "itsm"
    with pytest.raises(FlowValidationError, match="already exists"):
        await store.create(name="OS upgrade", definition=DEF, actor="a", allow_scripts=False)
    with pytest.raises(FlowValidationError, match="admin"):
        await store.create(
            name="s",
            definition={"steps": [{"type": "script", "code": "result=1"}]},
            actor="a",
            allow_scripts=False,
        )


async def test_dispatch_starts_matching_flows_once(store: FlowStore) -> None:
    started: list[str] = []

    async def starter(run_id: str) -> None:
        started.append(run_id)

    d = FlowDispatcher(store, starter)
    flow, _ = await store.create(
        name="OS upgrade",
        trigger=OS_UPGRADE,
        definition=DEF,
        actor="a",
        allow_scripts=False,
        publish=True,
        enabled=True,
    )
    await store.create(
        name="Other",
        trigger={
            "type": "itsm",
            "conditions": {"field": "ticket.summary", "op": "contains", "value": "VLAN"},
        },
        definition=DEF,
        actor="a",
        allow_scripts=False,
        publish=True,
        enabled=True,
    )
    await store.create(
        name="Disabled",
        trigger=OS_UPGRADE,
        definition=DEF,
        actor="a",
        allow_scripts=False,
        publish=True,
        enabled=False,
    )
    res = {r["flow"]: r for r in await d.dispatch(itsm_event("OS UPGRADE leaf-01 to 17.9.4"))}
    assert (
        res["OS upgrade"]["status"] == "started"
        and res["Other"]["status"] == "not_matched"
        and "Disabled" not in res
    )
    again = await d.dispatch(itsm_event("OS UPGRADE leaf-01 to 17.9.4"))
    assert [r["status"] for r in again if r["flow"] == "OS upgrade"] == ["duplicate"]
    run = await store.get_run(res["OS upgrade"]["run_id"])
    assert run["trigger_event"]["match"]["device"] == "leaf-01" and run["status"] == "queued"
    assert started == [res["OS upgrade"]["run_id"]]
    manual = await d.run_manual(flow["id"], inputs={"device": "leaf-02"}, user="alice")
    assert manual["trigger_type"] == "manual" and len(started) == 2
    no_temporal = FlowDispatcher(store, None)
    failed = await no_temporal.run_manual(flow["id"], inputs={}, user="a")
    assert (await store.get_run(failed["id"]))["status"] == "failed"


async def test_flow_webhook_and_run_log(store: FlowStore) -> None:
    d = FlowDispatcher(store, lambda run_id: _noop())
    flow, _ = await store.create(
        name="Hook",
        trigger={
            "type": "webhook",
            "conditions": {"field": "body.alert", "op": "equals", "value": "InterfaceDown"},
        },
        definition=DEF,
        actor="a",
        allow_scripts=False,
    )
    assert (await d.run_webhook(flow["id"], {"alert": "InterfaceDown"}, {}))["status"] == "ignored"
    await store.publish(flow["id"])
    await store.set_enabled(flow["id"], True)
    assert (await d.run_webhook(flow["id"], {"alert": "CPU"}, {}))[
        "reason"
    ] == "conditions did not match"
    started = await d.run_webhook(flow["id"], {"alert": "InterfaceDown"}, {})
    rid = started["run"]["id"]
    await store.append_log(rid, [{"id": "s1", "status": "succeeded"}], status="running")
    await store.finish(rid, {"status": "succeeded", "vars": {"x": 1}, "outputs": {"y": 2}})
    run = await store.get_run(rid)
    assert (run["status"], run["variables"], run["outputs"], len(run["log"])) == (
        "succeeded",
        {"x": 1},
        {"y": 2},
        1,
    )
    assert (await store.list_runs(flow["id"]))[0]["id"] == rid
    assert (await store.list_flows())[0]["runs"] == 1


async def _noop() -> None:
    return None


# --- Kafka ---------------------------------------------------------------------------------------


class FakeConsumer:
    def __init__(self, messages: list[Any], stop: Any) -> None:
        self.messages, self.subscribed, self.commits, self._stop = messages, [], 0, stop

    async def start(self) -> None: ...
    async def stop(self) -> None: ...

    def subscribe(self, topics: list[str]) -> None:
        self.subscribed = topics

    def unsubscribe(self) -> None:
        self.subscribed = []

    async def getmany(self, timeout_ms: int, max_records: int) -> dict[Any, list[Any]]:
        if not self.messages:
            self._stop()
            return {}
        batch, self.messages = self.messages, []
        return {("t", 0): batch}

    async def commit(self) -> None:
        self.commits += 1


async def test_kafka_messages_trigger_flows_once(store: FlowStore) -> None:
    started: list[str] = []

    async def starter(run_id: str) -> None:
        started.append(run_id)

    d = FlowDispatcher(store, starter)
    await store.create(
        name="K",
        trigger={
            "type": "kafka",
            "topic": "network-events",
            "conditions": {"field": "value.type", "op": "equals", "value": "link_down"},
        },
        definition=DEF,
        actor="a",
        allow_scripts=False,
        publish=True,
        enabled=True,
    )
    msg = SimpleNamespace(
        topic="network-events",
        partition=0,
        offset=41,
        key=b"leaf-02",
        value=json.dumps({"type": "link_down", "device": "leaf-02"}).encode(),
        headers=[("h", b"v")],
    )
    other = SimpleNamespace(
        topic="network-events", partition=0, offset=42, key=None, value=b"not json", headers=[]
    )
    holder: dict[str, Any] = {}
    k = KafkaTriggerConsumer(
        d,
        lambda: holder.setdefault("c", FakeConsumer([msg, other, msg], lambda: k.stop())),
        refresh_seconds=60,
    )
    await k.run()
    assert holder["c"].subscribed == ["network-events"] and holder["c"].commits == 1
    assert len(started) == 1  # the redelivered offset 41 is a duplicate
    assert message_event(other)["value"] == "not json" and message_event(msg)["headers"] == {
        "h": "v"
    }


# --- activities ----------------------------------------------------------------------------------


def test_http_host_rules() -> None:
    deny = ["169.254.0.0/16", "metadata.google.internal"]
    assert _host_allowed("https://api.example.com/x", ["*"], deny) is None
    assert "blocked range" in str(_host_allowed("http://169.254.169.254/latest", ["*"], deny))
    assert "not in" in str(_host_allowed("https://evil.test", ["*.example.com"], deny))
    assert "only http" in str(_host_allowed("file:///etc/passwd", ["*"], deny))


async def test_http_action_secrets_status_and_errors(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers.get("Authorization")
        seen["body"] = json.loads(req.content) if req.content else None
        if req.url.path == "/fail":
            return httpx.Response(503, text="busy")
        return httpx.Response(201, json={"id": 7})

    monkeypatch.setenv("VIREONET_FLOW_SECRET_TOKEN", "s3cr3t")
    settings = SimpleNamespace(
        flow_http_allowed_hosts=["*"], flow_http_denied_hosts=[], flow_http_verify_tls=True
    )
    acts = FlowActivities(None, settings=settings, http_transport=httpx.MockTransport(handler))
    from vireonet.flows.expr import base_context, render

    params = render(
        {
            "method": "POST",
            "url": "https://api.example.com/items",
            "headers": {"Authorization": "Bearer {{ secrets.TOKEN }}"},
            "body": {"a": 1},
            "expect_status": ["201"],
        },
        base_context(),
    )
    out = await acts.flow_action(
        FlowActionInput(run_id="r1", step_id="s1", step_type="http_request", params=params)
    )
    assert (
        out["status"] == 201
        and out["json"] == {"id": 7}
        and seen["auth"] == "Bearer s3cr3t"
        and seen["body"] == {"a": 1}
    )
    from temporalio.exceptions import ApplicationError

    with pytest.raises(ApplicationError, match="HTTP 503"):
        await acts.flow_action(
            FlowActionInput(
                run_id="r1",
                step_id="s1",
                step_type="http_request",
                params={"url": "https://api.example.com/fail"},
            )
        )
    with pytest.raises(ApplicationError, match="secret 'NOPE' is not set"):
        await acts.flow_action(
            FlowActionInput(
                run_id="r1",
                step_id="s1",
                step_type="http_request",
                params=render({"url": "https://x/{{ secrets.NOPE }}"}, base_context()),
            )
        )


async def test_script_action_errors_timeout_and_disabled() -> None:
    from temporalio.exceptions import ApplicationError

    settings = SimpleNamespace(
        flow_scripts_enabled=True, flow_script_max_seconds=10, flow_script_memory_mb=512
    )
    acts = FlowActivities(None, settings=settings)
    with pytest.raises(ApplicationError, match="ZeroDivisionError"):
        await acts.flow_action(
            FlowActionInput(run_id="r", step_id="s", step_type="script", params={"code": "1/0"})
        )
    with pytest.raises(ApplicationError, match="timed out"):
        await acts.flow_action(
            FlowActionInput(
                run_id="r",
                step_id="s",
                step_type="script",
                params={"code": "while True: pass", "timeout_seconds": 1},
            )
        )
    off = FlowActivities(None, settings=SimpleNamespace(flow_scripts_enabled=False))
    with pytest.raises(ApplicationError, match="disabled"):
        await off.flow_action(
            FlowActionInput(
                run_id="r", step_id="s", step_type="script", params={"code": "result=1"}
            )
        )


async def test_run_commands_and_checks_actions() -> None:
    from temporalio.exceptions import ApplicationError

    from vireonet.adapters.fakes import FakeNetBoxAdapter
    from vireonet.execution.fake_engine import FakeExecutionEngine
    from vireonet.execution.fake_network import FakeNetwork
    from vireonet.topology.demo import seed_demo_netbox

    nb = FakeNetBoxAdapter()
    seed_demo_netbox(nb)
    acts = FlowActivities(
        None,
        settings=SimpleNamespace(textfsm_user_dir="policies/textfsm"),
        engine=FakeExecutionEngine(network=FakeNetwork()),
        netbox=nb,
    )
    out = await acts.flow_action(
        FlowActionInput(
            run_id="run12345",
            step_id="s",
            step_type="run_commands",
            params={"devices": ["leaf-01"], "commands": ["show vlan brief"], "parse": True},
        )
    )
    assert isinstance(out["leaf-01"]["show vlan brief"], list)
    with pytest.raises(ApplicationError, match="not a read-only command"):
        await acts.flow_action(
            FlowActionInput(
                run_id="r",
                step_id="s",
                step_type="run_commands",
                params={"devices": ["leaf-01"], "commands": ["reload"]},
            )
        )
    checks = await acts.flow_action(
        FlowActionInput(
            run_id="run12345",
            step_id="s",
            step_type="run_checks",
            params={
                "devices": ["leaf-01"],
                "change_type": "vlan_add",
                "phase": "post",
                "intent": {"vlan_id": 1, "vlan_name": "default", "site": "dc1"},
            },
        )
    )
    assert "results" in checks and isinstance(checks["ok"], bool)
    dev = await acts.flow_action(
        FlowActionInput(
            run_id="r", step_id="s", step_type="get_device", params={"device": "leaf-01"}
        )
    )
    assert dev["name"] == "leaf-01" and dev["platform"]


async def test_notify_uses_the_configured_default_channel() -> None:
    from vireonet.adapters.fakes import FakeNotificationAdapter

    acts = FlowActivities(
        None,
        settings=SimpleNamespace(notification_default_channel="slack"),
        notifier=FakeNotificationAdapter(),
    )
    out = await acts.flow_action(
        FlowActionInput(run_id="r", step_id="s", step_type="notify", params={"message": "hi"})
    )
    assert out == {"sent": True}
