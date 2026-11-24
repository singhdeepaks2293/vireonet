"""Log platforms: Elasticsearch, OpenSearch, Splunk, Grafana Loki, Graylog.

One interface for what VireoNet needs from a log platform:

* ``search(query, minutes, limit)``: find log lines (flows: "Search logs";
  log analytics can read from it);
* ``send(event)``: write a VireoNet event (change finished, flow run, …) so
  the log platform has the automation audit trail next to device logs.

Configured with VIREONET_LOG_BACKENDS (JSON list); fake mode adds a "fake"
backend filled with the demo network's syslog.
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

MAX_LIMIT = 1000


class LogSearchError(Exception):
    pass


@dataclass
class LogHit:
    timestamp: str
    message: str
    host: str = ""
    fields: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LogBackendConfig:
    name: str
    type: str
    url: str = ""
    username: str = ""
    password: str = ""
    token: str = ""  # Elasticsearch API key, Splunk / Loki bearer, Graylog access token
    send_token: str = ""  # Splunk HEC token (sending uses a different token)
    send_url: str = ""  # Splunk HEC / Graylog GELF HTTP input URL when it differs
    index: str = ""  # ES/OpenSearch search index pattern; Splunk index; Loki base selector
    export_index: str = "vireonet-events"
    tenant: str = ""  # Loki X-Scope-OrgID
    verify_tls: bool = True
    timeout_seconds: float = 20.0
    options: dict[str, Any] = field(default_factory=dict)


def _iso(ts: Any) -> str:
    if isinstance(ts, (int, float)):
        secs = ts / 1e9 if ts > 1e12 else ts
        return datetime.fromtimestamp(secs, UTC).isoformat()
    return str(ts or "")


class _Http:
    kind = "generic"
    label = "generic"

    def __init__(
        self, cfg: LogBackendConfig, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        if not cfg.url:
            raise LogSearchError(f"{cfg.name}: url is required")
        self.cfg = cfg
        self.name = cfg.name
        self.base = cfg.url.rstrip("/")
        self._transport = transport

    def headers(self) -> dict[str, str]:
        if self.cfg.username:
            raw = f"{self.cfg.username}:{self.cfg.password}".encode()
            return {"Authorization": "Basic " + base64.b64encode(raw).decode()}
        return {}

    async def call(
        self, method: str, url: str, *, ok: tuple[int, ...] = (200,), **kw: Any
    ) -> httpx.Response:
        full = url if url.startswith("http") else f"{self.base}{url}"
        headers = {**self.headers(), **kw.pop("headers", {})}
        async with httpx.AsyncClient(
            timeout=self.cfg.timeout_seconds, verify=self.cfg.verify_tls, transport=self._transport
        ) as c:
            r = await c.request(method, full, headers=headers, **kw)
        if r.status_code not in ok and not 200 <= r.status_code < 300:
            raise LogSearchError(
                f"{self.name}: {method} {url.split('?')[0]} -> HTTP {r.status_code}: {r.text[:200]}"
            )
        return r

    async def health(self) -> dict[str, Any]:
        try:
            await self.search("*", minutes=5, limit=1)
        except Exception as exc:  # noqa: BLE001 — health reports, never raises
            return {
                "name": self.name,
                "type": self.kind,
                "status": "down",
                "detail": str(exc)[:200],
            }
        return {"name": self.name, "type": self.kind, "status": "ok", "detail": self.base}

    async def search(self, query: str, *, minutes: int = 60, limit: int = 100) -> list[LogHit]:
        raise NotImplementedError

    async def send(self, event: dict[str, Any]) -> None:
        raise NotImplementedError


class Elasticsearch(_Http):
    """Elasticsearch 8.x: POST /<index>/_search (query_string + @timestamp range),
    POST /<export_index>/_doc. Auth: API key (Authorization: ApiKey <key>) or basic."""

    kind = "elasticsearch"
    label = "Elasticsearch"

    def headers(self) -> dict[str, str]:
        if self.cfg.token:
            return {"Authorization": f"ApiKey {self.cfg.token}"}
        return super().headers()

    async def search(self, query: str, *, minutes: int = 60, limit: int = 100) -> list[LogHit]:
        ts = self.cfg.options.get("timestamp_field", "@timestamp")
        body = {
            "size": min(limit, MAX_LIMIT),
            "sort": [{ts: {"order": "desc"}}],
            "query": {
                "bool": {
                    "must": [{"query_string": {"query": query or "*"}}],
                    "filter": [{"range": {ts: {"gte": f"now-{int(minutes)}m"}}}],
                }
            },
        }
        r = await self.call("POST", f"/{self.cfg.index or '*'}/_search", json=body)
        out = []
        msg_f, host_f = (
            self.cfg.options.get("message_field", "message"),
            self.cfg.options.get("host_field", "host.name"),
        )
        for h in r.json().get("hits", {}).get("hits", []):
            src = h.get("_source", {})
            host: Any = src
            for part in host_f.split("."):
                host = host.get(part, "") if isinstance(host, dict) else ""
            out.append(
                LogHit(
                    timestamp=_iso(src.get(ts)),
                    message=str(src.get(msg_f, "")),
                    host=str(host or src.get("host", "")),
                    fields={k: v for k, v in src.items() if k not in (msg_f,)},
                )
            )
        return out

    async def send(self, event: dict[str, Any]) -> None:
        doc = {
            "@timestamp": datetime.now(UTC).isoformat(),
            "message": event.get("message") or event.get("event", ""),
            "vireonet": event,
        }
        await self.call("POST", f"/{self.cfg.export_index}/_doc", json=doc, ok=(200, 201))


class OpenSearch(Elasticsearch):
    """OpenSearch 2.x: the same _search / _doc API as Elasticsearch; basic auth
    (or a bearer token via options.bearer=true)."""

    kind = "opensearch"
    label = "OpenSearch"

    def headers(self) -> dict[str, str]:
        if self.cfg.token:
            return {"Authorization": f"Bearer {self.cfg.token}"}
        return _Http.headers(self)


class Splunk(_Http):
    """Splunk Enterprise / Cloud.
    Search: POST /services/search/jobs/export (output_mode=json; one JSON object
    per line), auth Authorization: Bearer <token> (or basic).
    Send: HTTP Event Collector POST <send_url or url:8088>/services/collector/event,
    Authorization: Splunk <HEC token>."""

    kind = "splunk"
    label = "Splunk"

    def headers(self) -> dict[str, str]:
        if self.cfg.token:
            return {"Authorization": f"Bearer {self.cfg.token}"}
        return super().headers()

    async def search(self, query: str, *, minutes: int = 60, limit: int = 100) -> list[LogHit]:
        q = query.strip() or "*"
        if not q.startswith(("search ", "|")):
            idx = f"index={self.cfg.index} " if self.cfg.index else ""
            q = f"search {idx}{q}"
        q = f"{q} | head {min(limit, MAX_LIMIT)}"
        r = await self.call(
            "POST",
            "/services/search/jobs/export",
            data={
                "search": q,
                "output_mode": "json",
                "earliest_time": f"-{int(minutes)}m",
                "latest_time": "now",
            },
        )
        out = []
        for line in r.text.splitlines():
            try:
                row = json.loads(line).get("result")
            except ValueError:
                continue
            if row:
                out.append(
                    LogHit(
                        timestamp=str(row.get("_time", "")),
                        message=str(row.get("_raw", "")),
                        host=str(row.get("host", "")),
                        fields={k: v for k, v in row.items() if not k.startswith("_")},
                    )
                )
        return out

    async def send(self, event: dict[str, Any]) -> None:
        if not self.cfg.send_token:
            raise LogSearchError(f"{self.name}: send_token (HEC token) is required to send events")
        url = (self.cfg.send_url or self.base).rstrip("/") + "/services/collector/event"
        body = {
            "time": time.time(),
            "sourcetype": "vireonet:event",
            "source": "vireonet",
            "event": event,
            **({"index": self.cfg.export_index} if self.cfg.options.get("hec_index") else {}),
        }
        await self.call(
            "POST", url, json=body, headers={"Authorization": f"Splunk {self.cfg.send_token}"}
        )


class Loki(_Http):
    """Grafana Loki: GET /loki/api/v1/query_range (LogQL), POST /loki/api/v1/push.
    Auth: basic or bearer; multi-tenant: X-Scope-OrgID. `index` is the base
    stream selector (default {job=~".+"}); a query that starts with "{" is used as is,
    other text becomes a line filter |= "text"."""

    kind = "loki"
    label = "Grafana Loki"

    def headers(self) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self.cfg.token}"} if self.cfg.token else super().headers()
        if self.cfg.tenant:
            h["X-Scope-OrgID"] = self.cfg.tenant
        return h

    async def search(self, query: str, *, minutes: int = 60, limit: int = 100) -> list[LogHit]:
        q = query.strip()
        if not q.startswith("{"):
            sel = self.cfg.index or '{job=~".+"}'
            q = sel if q in ("", "*") else f"{sel} |= {json.dumps(q)}"
        end = time.time_ns()
        r = await self.call(
            "GET",
            "/loki/api/v1/query_range",
            params={
                "query": q,
                "start": end - int(minutes) * 60 * 10**9,
                "end": end,
                "limit": min(limit, MAX_LIMIT),
                "direction": "backward",
            },
        )
        out = []
        for stream in r.json().get("data", {}).get("result", []):
            labels = stream.get("stream", {})
            for ts, line in stream.get("values", []):
                out.append(
                    LogHit(
                        timestamp=_iso(int(ts)),
                        message=str(line),
                        host=str(labels.get("host") or labels.get("hostname", "")),
                        fields=dict(labels),
                    )
                )
        out.sort(key=lambda h: h.timestamp, reverse=True)
        return out[:limit]

    async def send(self, event: dict[str, Any]) -> None:
        labels = {
            "job": "vireonet",
            "source": str(event.get("source", "vireonet")),
            "event": str(event.get("event", "")),
        }
        body = {
            "streams": [
                {
                    "stream": labels,
                    "values": [[str(time.time_ns()), json.dumps(event, default=str)]],
                }
            ]
        }
        await self.call("POST", "/loki/api/v1/push", json=body, ok=(204,))


class Graylog(_Http):
    """Graylog 5/6: POST /api/search/messages {query, timerange, size} (schema +
    datarows). Auth: access token as basic user with password "token" (or basic).
    Requires header X-Requested-By. Send: a GELF HTTP input (send_url, e.g.
    http://graylog:12201/gelf)."""

    kind = "graylog"
    label = "Graylog"

    def headers(self) -> dict[str, str]:
        if self.cfg.token:
            raw = f"{self.cfg.token}:token".encode()
            return {
                "Authorization": "Basic " + base64.b64encode(raw).decode(),
                "X-Requested-By": "vireonet",
            }
        return {**super().headers(), "X-Requested-By": "vireonet"}

    async def search(self, query: str, *, minutes: int = 60, limit: int = 100) -> list[LogHit]:
        body = {
            "query": query or "*",
            "timerange": {"type": "relative", "range": int(minutes) * 60},
            "size": min(limit, MAX_LIMIT),
            "fields": ["timestamp", "source", "message"],
        }
        r = await self.call(
            "POST", "/api/search/messages", json=body, headers={"Accept": "application/json"}
        )
        data = r.json()
        cols = [c.get("field") for c in data.get("schema", [])]
        out = []
        for row in data.get("datarows", []):
            rec = dict(zip(cols, row, strict=False))
            out.append(
                LogHit(
                    timestamp=_iso(rec.get("timestamp")),
                    message=str(rec.get("message", "")),
                    host=str(rec.get("source", "")),
                    fields=rec,
                )
            )
        return out

    async def send(self, event: dict[str, Any]) -> None:
        if not self.cfg.send_url:
            raise LogSearchError(
                f"{self.name}: send_url (a GELF HTTP input) is required to send events"
            )
        gelf = {
            "version": "1.1",
            "host": "vireonet",
            "short_message": str(event.get("message") or event.get("event", "vireonet event")),
            "timestamp": time.time(),
            "_vireonet": json.dumps(event, default=str),
        }
        await self.call("POST", self.cfg.send_url, json=gelf, ok=(202,))


class FakeLogs:
    """Fake mode: the demo network's syslog (the same lines Log analytics uses)."""

    kind = "fake"
    label = "Demo syslog"

    def __init__(self) -> None:
        self.name = "fake"
        self.sent: list[dict[str, Any]] = []

    def _lines(self) -> list[LogHit]:
        from vireonet.logs.fake_syslog import fake_show_logging
        from vireonet.topology.demo import DEMO_NODES, demo_interfaces

        now = datetime.now(UTC)
        out = []
        for n in DEMO_NODES:
            text = fake_show_logging(n["name"], n["platform"], demo_interfaces(n["name"]), now=now)
            for i, line in enumerate(reversed(text.splitlines())):
                if line.strip():
                    out.append(
                        LogHit(
                            timestamp=(now - timedelta(seconds=30 * i)).isoformat(),
                            message=line,
                            host=n["name"],
                            fields={"platform": n["platform"]},
                        )
                    )
        return out

    async def health(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": "fake",
            "status": "ok",
            "detail": "demo syslog of the fake network",
        }

    async def search(self, query: str, *, minutes: int = 60, limit: int = 100) -> list[LogHit]:
        q = (query or "*").strip().lower()
        hits = [
            h
            for h in self._lines()
            if q in ("", "*") or all(t in f"{h.host} {h.message}".lower() for t in q.split())
        ]
        return hits[: min(limit, MAX_LIMIT)]

    async def send(self, event: dict[str, Any]) -> None:
        self.sent.append(event)


LOG_TYPES: dict[str, type[_Http]] = {
    "elasticsearch": Elasticsearch,
    "opensearch": OpenSearch,
    "splunk": Splunk,
    "loki": Loki,
    "graylog": Graylog,
}


def build_log_backends(settings: Any, *, transport: Any = None) -> dict[str, Any]:
    raw = getattr(settings, "log_backends", "") or "[]"
    items = json.loads(raw) if isinstance(raw, str) else list(raw)
    out: dict[str, Any] = {}
    for item in items:
        cfg = LogBackendConfig(
            **{k: v for k, v in item.items() if k in LogBackendConfig.__dataclass_fields__}
        )
        if cfg.type not in LOG_TYPES:
            raise LogSearchError(
                f"log backend {cfg.name!r}: unknown type {cfg.type!r} (one of: {', '.join(LOG_TYPES)})"
            )
        for secret in ("password", "token", "send_token"):
            env = item.get(f"{secret}_env")
            if env and not getattr(cfg, secret):
                setattr(cfg, secret, os.environ.get(env, ""))
        out[cfg.name] = LOG_TYPES[cfg.type](cfg, transport=transport)
    if getattr(settings, "adapter_mode", "fake") == "fake":
        out.setdefault("fake", FakeLogs())
    return out


async def export_event(
    backends: dict[str, Any], names: list[str], event: dict[str, Any]
) -> list[str]:
    """Send one VireoNet event to the export backends; returns errors (never raises)."""
    errors = []
    for n in names:
        b = backends.get(n)
        if b is None:
            errors.append(f"{n}: not configured")
            continue
        try:
            await b.send(event)
        except Exception as exc:  # noqa: BLE001 — exporting must never break a change
            errors.append(f"{n}: {exc}")
    return errors
