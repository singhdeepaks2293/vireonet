"""Log platforms against their documented request/response shapes, the fake,
flow actions, event export, the API, and the Adapters tab."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from vireonet.config import Settings
from vireonet.logsearch import (
    Elasticsearch,
    FakeLogs,
    Graylog,
    LogBackendConfig,
    LogSearchError,
    Loki,
    OpenSearch,
    Splunk,
    build_log_backends,
    export_event,
)


def mk(cls: Any, handler: Any, **cfg: Any) -> Any:
    return cls(
        LogBackendConfig(**{"name": "b", "type": cls.kind, "url": "https://logs.example", **cfg}),
        transport=httpx.MockTransport(handler),
    )


async def test_elasticsearch_search_and_index() -> None:
    seen: list[Any] = []

    def h(req: httpx.Request) -> httpx.Response:
        assert req.headers["Authorization"] == "ApiKey KEY=="
        body = json.loads(req.content)
        seen.append((req.url.path, body))
        if req.url.path == "/syslog-*/_search":
            # Example response shape from the Elasticsearch search API docs.
            return httpx.Response(
                200,
                json={
                    "took": 3,
                    "hits": {
                        "total": {"value": 1, "relation": "eq"},
                        "hits": [
                            {
                                "_index": "syslog-2026.11.24",
                                "_id": "1",
                                "_source": {
                                    "@timestamp": "2026-11-24T01:00:00Z",
                                    "message": "%LINK-3-UPDOWN: Interface Gi1/0/2, changed state to down",
                                    "host": {"name": "leaf-01"},
                                },
                            }
                        ],
                    },
                },
            )
        if req.url.path == "/vireonet-events/_doc":
            return httpx.Response(201, json={"result": "created"})
        return httpx.Response(404)

    es = mk(Elasticsearch, h, token="KEY==", index="syslog-*")
    hits = await es.search("UPDOWN AND leaf-01", minutes=30, limit=5)
    q = seen[0][1]
    assert (
        q["size"] == 5
        and q["query"]["bool"]["must"][0]["query_string"]["query"] == "UPDOWN AND leaf-01"
    )
    assert q["query"]["bool"]["filter"][0]["range"]["@timestamp"]["gte"] == "now-30m"
    assert (hits[0].host, hits[0].timestamp) == ("leaf-01", "2026-11-24T01:00:00Z")
    await es.send({"event": "change.closed", "message": "CHG1 closed"})
    assert seen[-1][1]["vireonet"]["event"] == "change.closed"


async def test_opensearch_basic_auth() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        assert req.headers["Authorization"] == "Basic " + base64.b64encode(b"admin:pw").decode()
        return httpx.Response(200, json={"hits": {"hits": []}})

    assert await mk(OpenSearch, h, username="admin", password="pw").search("x") == []


async def test_splunk_export_search_and_hec() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/services/search/jobs/export":
            assert req.headers["Authorization"] == "Bearer sp-token"
            form = parse_qs(req.content.decode())
            assert form["search"] == ["search index=network sourcetype=syslog BGP | head 10"]
            assert form["output_mode"] == ["json"] and form["earliest_time"] == ["-60m"]
            # Export streams one JSON object per line ({"preview":false,"result":{...}}).
            lines = [
                json.dumps(
                    {
                        "preview": False,
                        "result": {
                            "_time": "2026-11-24T01:00:00.000+00:00",
                            "_raw": "BGP-5-ADJCHANGE neighbor 10.0.0.2 Down",
                            "host": "border-01",
                        },
                    }
                ),
                json.dumps({"preview": False, "lastrow": True}),
            ]
            return httpx.Response(200, text="\n".join(lines))
        if req.url.path == "/services/collector/event":
            assert req.headers["Authorization"] == "Splunk hec-token" and str(req.url).startswith(
                "https://hec.example:8088"
            )
            b = json.loads(req.content)
            assert b["sourcetype"] == "vireonet:event" and b["event"]["event"] == "flow.message"
            return httpx.Response(200, json={"text": "Success", "code": 0})
        return httpx.Response(404)

    sp = mk(
        Splunk,
        h,
        token="sp-token",
        index="network",
        send_token="hec-token",
        send_url="https://hec.example:8088",
    )
    hits = await sp.search("sourcetype=syslog BGP", limit=10)
    assert hits[0].host == "border-01" and "ADJCHANGE" in hits[0].message
    await sp.send({"event": "flow.message"})
    with pytest.raises(LogSearchError, match="HEC token"):
        await mk(Splunk, h).send({"event": "x"})


async def test_loki_query_range_and_push() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        assert req.headers["X-Scope-OrgID"] == "net"
        if req.url.path == "/loki/api/v1/query_range":
            p = dict(req.url.params)
            assert (
                p["query"] == '{job="syslog"} |= "LINK-3-UPDOWN"'
                and p["direction"] == "backward"
                and p["limit"] == "50"
            )
            # Example from the Loki HTTP API docs (resultType "streams").
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {
                        "resultType": "streams",
                        "result": [
                            {
                                "stream": {"job": "syslog", "host": "leaf-02"},
                                "values": [
                                    [
                                        "1795395600000000000",
                                        "%LINK-3-UPDOWN: Interface Ethernet1/49, changed state to down",
                                    ]
                                ],
                            }
                        ],
                    },
                },
            )
        if req.url.path == "/loki/api/v1/push":
            b = json.loads(req.content)
            assert (
                b["streams"][0]["stream"]["job"] == "vireonet"
                and len(b["streams"][0]["values"][0]) == 2
            )
            return httpx.Response(204)
        return httpx.Response(404)

    lk = mk(Loki, h, tenant="net", index='{job="syslog"}')
    hits = await lk.search("LINK-3-UPDOWN", limit=50)
    assert hits[0].host == "leaf-02" and hits[0].timestamp.startswith("2026-")
    await lk.send({"event": "change.closed"})


async def test_graylog_messages_search_and_gelf() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/search/messages":
            assert req.headers["X-Requested-By"] == "vireonet"
            assert (
                req.headers["Authorization"]
                == "Basic " + base64.b64encode(b"gl-token:token").decode()
            )
            b = json.loads(req.content)
            assert b["timerange"] == {"type": "relative", "range": 900}
            return httpx.Response(
                200,
                json={
                    "schema": [{"field": "timestamp"}, {"field": "source"}, {"field": "message"}],
                    "datarows": [
                        ["2026-11-24T01:00:00.000Z", "fw-02", "sshd: Failed password for root"]
                    ],
                },
            )
        if req.url.path == "/gelf":
            assert json.loads(req.content)["version"] == "1.1"
            return httpx.Response(202)
        return httpx.Response(404)

    gl = mk(Graylog, h, token="gl-token", send_url="http://graylog:12201/gelf")
    hits = await gl.search("sshd AND Failed", minutes=15)
    assert (hits[0].host, hits[0].message) == ("fw-02", "sshd: Failed password for root")
    await gl.send({"event": "x", "message": "hello"})


async def test_errors_factory_fake_and_export(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    bad = mk(Elasticsearch, lambda r: httpx.Response(403, text="forbidden"))
    with pytest.raises(LogSearchError, match="HTTP 403"):
        await bad.search("x")
    assert (await bad.health())["status"] == "down"
    monkeypatch.setenv("ES_KEY", "k1")
    bs = build_log_backends(
        Settings(
            adapter_mode="real",
            log_backends=json.dumps(
                [
                    {
                        "name": "es",
                        "type": "elasticsearch",
                        "url": "https://es",
                        "token_env": "ES_KEY",
                    }
                ]
            ),
        )
    )
    assert list(bs) == ["es"] and bs["es"].cfg.token == "k1"
    with pytest.raises(LogSearchError, match="unknown type"):
        build_log_backends(Settings(log_backends='[{"name": "x", "type": "nope", "url": "u"}]'))
    fake = build_log_backends(Settings(adapter_mode="fake"))["fake"]
    hits = await fake.search("fw-02 login")
    assert hits and all(h.host == "fw-02" for h in hits)
    errors = await export_event(
        {"fake": fake, "bad": bad}, ["fake", "bad", "nope"], {"event": "change.closed"}
    )
    assert fake.sent == [{"event": "change.closed"}] and len(errors) == 2


async def test_log_steps_in_a_flow() -> None:
    from vireonet.flows.activities import FlowActionInput, FlowActivities

    fake = FakeLogs()
    acts = FlowActivities(None, settings=SimpleNamespace(), log_backends={"fake": fake})
    found = await acts.flow_action(
        FlowActionInput(
            run_id="r1",
            step_id="s",
            step_type="log_search",
            params={"backend": "fake", "query": "BGP", "limit": 3},
        )
    )
    assert found["count"] <= 3 and all("bgp" in h["message"].lower() for h in found["hits"])
    await acts.flow_action(
        FlowActionInput(
            run_id="r1",
            step_id="s",
            step_type="log_send",
            params={"backend": "fake", "message": "upgrade done", "fields": {"device": "leaf-01"}},
        )
    )
    assert fake.sent[0]["device"] == "leaf-01" and fake.sent[0]["run_id"] == "r1"


async def test_emitter_exports_events_and_never_raises() -> None:
    from vireonet.flows.dispatch import safe_dispatch

    fake = FakeLogs()
    emit = safe_dispatch(None, log_backends={"fake": fake}, export_to=["fake", "missing"])
    assert await emit({"source": "vireonet", "event": "change.closed"}) == []
    assert fake.sent[0]["event"] == "change.closed"
