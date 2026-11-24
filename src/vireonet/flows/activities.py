"""Temporal activities for flows: load/record/finish runs and every action
that touches the outside world (API, script, devices, ITSM, NetBox, …)."""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import ipaddress
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import urlsplit

from temporalio import activity
from temporalio.exceptions import ApplicationError

from vireonet.flows.expr import mask_secrets, resolve_secrets
from vireonet.flows.store import FlowStore

MAX_TEXT = 100_000


@dataclass
class FlowLoadInput:
    run_id: str


@dataclass
class FlowRecordInput:
    run_id: str
    entries: list[dict[str, Any]] = field(default_factory=list)
    status: str = ""
    waiting: dict[str, Any] | None = None


@dataclass
class FlowFinishInput:
    run_id: str
    result: dict[str, Any] = field(default_factory=dict)


@dataclass
class FlowActionInput:
    run_id: str
    step_id: str
    step_type: str
    params: dict[str, Any] = field(default_factory=dict)
    timeout: int = 0


@dataclass
class FlowChangeInput:
    run_id: str
    flow_name: str
    step_id: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class FlowScheduledRunInput:
    flow_id: str
    scheduled_at: str = ""


@dataclass
class FlowSubrunInput:
    parent_run_id: str
    flow: str
    inputs: dict[str, Any] = field(default_factory=dict)


def _fail(message: str) -> ApplicationError:
    # Step retries are the flow's own (retry_count); never retry in Temporal.
    return ApplicationError(message, non_retryable=True)


def _host_allowed(url: str, allowed: list[str], denied: list[str]) -> str | None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return f"only http and https URLs are allowed, not {parts.scheme or 'none'!r}"
    host = (parts.hostname or "").lower()
    if not host:
        return "the URL has no host"
    for pattern in denied:
        if "/" in pattern:
            try:
                if ipaddress.ip_address(host) in ipaddress.ip_network(pattern, strict=False):
                    return f"{host} is in a blocked range ({pattern})"
            except ValueError:
                continue
        elif fnmatch.fnmatch(host, pattern.lower()):
            return f"{host} is blocked ({pattern})"
    if not any(fnmatch.fnmatch(host, p.lower()) for p in allowed):
        return f"{host} is not in VIREONET_FLOW_HTTP_ALLOWED_HOSTS"
    return None


def _ip_problem(ip: str, denied: list[str], block_private: bool) -> str | None:
    addr = ipaddress.ip_address(ip.split("%", 1)[0])
    for pattern in denied:
        if "/" in pattern:
            with contextlib.suppress(ValueError):
                if addr in ipaddress.ip_network(pattern, strict=False):
                    return f"resolves to {addr}, which is in a blocked range ({pattern})"
    if block_private and (
        addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved
    ):
        return f"resolves to private address {addr} (VIREONET_FLOW_HTTP_BLOCK_PRIVATE)"
    return None


async def _resolved_problem(url: str, denied: list[str], block_private: bool) -> str | None:
    """DNS-rebinding guard: the *addresses* a name resolves to must be allowed too."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port)
    except OSError as exc:
        return f"cannot resolve {host}: {exc}"
    for info in infos:
        problem = _ip_problem(str(info[4][0]), denied, block_private)
        if problem:
            return f"{host} {problem}"
    return None


def _status_ok(code: int, expected: list[Any]) -> bool:
    for e in expected or ["200-299"]:
        s = str(e).strip()
        if "-" in s:
            lo, hi = s.split("-", 1)
            if lo.isdigit() and hi.isdigit() and int(lo) <= code <= int(hi):
                return True
        elif s.isdigit() and int(s) == code:
            return True
    return False


class FlowActivities:
    def __init__(
        self,
        sessions: Any,
        *,
        settings: Any,
        engine: Any = None,
        netbox: Any = None,
        itsm: Any = None,
        notifier: Any = None,
        policy: Any = None,
        servicenow: Any = None,
        check_templates: Any = None,
        http_transport: Any = None,
        controllers: dict[str, Any] | None = None,
        log_backends: dict[str, Any] | None = None,
    ) -> None:
        self._logs = log_backends or {}
        self._controllers = controllers or {}
        self._sessions = sessions
        self.store = FlowStore(sessions)
        self._settings = settings
        self._engine = engine
        self._netbox = netbox
        self._itsm = itsm
        self._notifier = notifier
        self._policy = policy
        self._snow = servicenow
        self._check_templates = check_templates
        self._http_transport = http_transport

    # --- run bookkeeping -------------------------------------------------------------------

    @activity.defn(name="flow_load")
    async def flow_load(self, inp: FlowLoadInput) -> dict[str, Any]:
        run = await self.store.get_run(inp.run_id)
        flow = await self.store.get(run["flow_id"], run["version"])
        await self.store.append_log(inp.run_id, [], status="running")
        return {
            "flow_id": flow["id"],
            "flow_name": flow["name"],
            "version": flow["version"],
            "definition": flow["definition"],
            "trigger": run["trigger_event"],
            "inputs": run["inputs"],
            "started_by": run["started_by"],
        }

    @activity.defn(name="flow_record")
    async def flow_record(self, inp: FlowRecordInput) -> None:
        await self.store.append_log(
            inp.run_id, inp.entries, status=inp.status or None, waiting=inp.waiting
        )

    @activity.defn(name="flow_finish")
    async def flow_finish(self, inp: FlowFinishInput) -> None:
        await self.store.finish(inp.run_id, inp.result)

    @activity.defn(name="flow_prepare_subrun")
    async def flow_prepare_subrun(self, inp: FlowSubrunInput) -> dict[str, Any]:
        from vireonet.flows.store import FlowNotFound

        try:
            flow = await self.store.get_by_name(inp.flow)
        except FlowNotFound as exc:
            raise _fail(f"no flow named {inp.flow!r}") from exc
        run = await self.store.create_run(
            flow,
            trigger_type="flow",
            event={"source": "flow", "parent_run_id": inp.parent_run_id},
            inputs=inp.inputs,
            started_by=f"flow-run {inp.parent_run_id}",
            parent_run_id=inp.parent_run_id,
        )
        assert run is not None  # no dedup key, never a duplicate
        return {"run_id": run["id"]}

    @activity.defn(name="flow_scheduled_run")
    async def flow_scheduled_run(self, inp: FlowScheduledRunInput) -> dict[str, Any]:
        from vireonet.flows.store import FlowNotFound
        from vireonet.flows.triggers import match

        try:
            flow = await self.store.get(inp.flow_id)
        except FlowNotFound:
            return {"reason": "flow deleted"}
        if not flow["enabled"] or flow["published_version"] is None:
            return {"reason": "flow disabled"}
        flow = await self.store.get(inp.flow_id, flow["published_version"])
        event = {"source": "schedule", "event": "tick", "scheduled_at": inp.scheduled_at}
        m = match(flow["trigger"], event)
        if not m.matched:
            return {"reason": m.reason}
        run = await self.store.create_run(
            flow,
            trigger_type="schedule",
            event={**event, "match": m.captures},
            dedup_key=f"schedule:{inp.scheduled_at}",
            started_by="schedule",
        )
        return {"run_id": run["id"]} if run else {"reason": "already ran for this tick"}

    # --- the actions ------------------------------------------------------------------------------

    @activity.defn(name="flow_action")
    async def flow_action(self, inp: FlowActionInput) -> Any:
        handler = getattr(self, f"_a_{inp.step_type}", None)
        if handler is None:
            raise _fail(f"no action {inp.step_type!r}")
        params = resolve_secrets(inp.params, self._secret)
        try:
            return await handler(params, inp)
        except ApplicationError:
            raise
        except Exception as exc:
            raise _fail(f"{type(exc).__name__}: {mask_secrets(str(exc))}") from exc

    def _textfsm_dir(self) -> str:
        return str(getattr(self._settings, "textfsm_user_dir", "") or "policies/textfsm")

    def _secret(self, name: str) -> str:
        value = os.environ.get(f"VIREONET_FLOW_SECRET_{name.upper()}")
        if value is None:
            raise _fail(f"secret {name!r} is not set (VIREONET_FLOW_SECRET_{name.upper()})")
        return value

    async def _a_http_request(self, p: dict[str, Any], inp: FlowActionInput) -> dict[str, Any]:
        import httpx

        url = str(p.get("url", ""))
        problem = _host_allowed(
            url,
            list(getattr(self._settings, "flow_http_allowed_hosts", ["*"])),
            list(getattr(self._settings, "flow_http_denied_hosts", [])),
        )
        if problem:
            raise _fail(problem)
        if self._http_transport is None:  # real network: check where the name points
            problem = await _resolved_problem(
                url,
                list(getattr(self._settings, "flow_http_denied_hosts", [])),
                bool(getattr(self._settings, "flow_http_block_private", False)),
            )
            if problem:
                raise _fail(problem)
        timeout = float(p.get("timeout_seconds") or inp.timeout or 30)
        body = p.get("body")
        async with httpx.AsyncClient(
            timeout=timeout,
            transport=self._http_transport,
            verify=bool(getattr(self._settings, "flow_http_verify_tls", True)),
            follow_redirects=False,
        ) as client:
            resp = await client.request(
                str(p.get("method") or "GET").upper(),
                url,
                headers={str(k): str(v) for k, v in (p.get("headers") or {}).items()},
                params={str(k): str(v) for k, v in (p.get("query") or {}).items()} or None,
                json=body if body not in (None, "") else None,
            )
        text = resp.text[:MAX_TEXT]
        try:
            data = resp.json()
        except ValueError:
            data = None
        ok = _status_ok(resp.status_code, list(p.get("expect_status") or []))
        out = {
            "status": resp.status_code,
            "ok": ok,
            "headers": {
                k: v
                for k, v in resp.headers.items()
                if k.lower() not in ("set-cookie", "authorization")
            },
            "json": data,
            "text": text if data is None else "",
        }
        if not ok:
            raise _fail(f"HTTP {resp.status_code} from {urlsplit(url).hostname}: {text[:300]}")
        return out

    async def _a_script(self, p: dict[str, Any], inp: FlowActionInput) -> Any:
        from vireonet.flows.sandbox import DEFAULT_ALLOWED_IMPORTS, ScriptError, run_script

        st = self._settings
        if not getattr(st, "flow_scripts_enabled", True):
            raise _fail("custom scripts are disabled (VIREONET_FLOW_SCRIPTS_ENABLED=false)")
        timeout = min(
            int(p.get("timeout_seconds") or inp.timeout or 30),
            int(getattr(st, "flow_script_max_seconds", 300)),
        )
        req: dict[str, Any] = {
            "code": str(p.get("code", "")),
            "inputs": p.get("inputs") or {},
            "timeout": timeout,
            "memory_mb": int(getattr(st, "flow_script_memory_mb", 512)),
            "allowed_imports": list(
                getattr(st, "flow_script_allowed_imports", None) or DEFAULT_ALLOWED_IMPORTS
            ),
            "require_isolation": bool(getattr(st, "flow_script_require_isolation", False)),
        }
        if getattr(st, "flow_script_runner", "local") == "sidecar":
            return await self._script_sidecar(req, timeout)
        try:
            out = await run_script(
                req["code"],
                req["inputs"],
                timeout=timeout,
                memory_mb=req["memory_mb"],
                allowed_imports=req["allowed_imports"],
                require_isolation=req["require_isolation"],
            )
        except ScriptError as exc:
            raise _fail(str(exc)) from exc
        return {**out, "runner": "local"}

    async def _script_sidecar(self, req: dict[str, Any], timeout: int) -> Any:
        import httpx

        from vireonet.flows.script_runner import sign

        st = self._settings
        secret = str(getattr(st, "flow_script_runner_secret", ""))
        if not secret:
            raise _fail("VIREONET_FLOW_SCRIPT_RUNNER_SECRET is not set")
        body = json.dumps(req, default=str).encode()
        async with httpx.AsyncClient(
            timeout=timeout + 15, transport=self._http_transport
        ) as client:
            resp = await client.post(
                str(getattr(st, "flow_script_runner_url", "http://script-runner:8100")).rstrip("/")
                + "/run",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-VireoNet-Signature": sign(secret, body),
                },
            )
        data = (
            resp.json()
            if resp.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        if resp.status_code != 200:
            raise _fail(str(data.get("error") or f"script runner answered HTTP {resp.status_code}"))
        return data

    async def _platform(self, device: str) -> str:
        if self._netbox is None:
            return "ios"
        dev = await self._netbox.get_device(device)
        from vireonet.rendering.platforms import normalise_platform

        return normalise_platform(dev.platform) or dev.platform

    async def _a_run_commands(self, p: dict[str, Any], inp: FlowActionInput) -> dict[str, Any]:
        from vireonet.checks.templates import DEFAULT_ALLOWED_PREFIXES
        from vireonet.execution.types import DeviceRef, StepRequest

        if self._engine is None:
            raise _fail("no execution engine configured")
        devices = _str_list(p.get("devices"))
        commands = _str_list(p.get("commands"))
        prefixes = tuple(
            getattr(getattr(self._check_templates, "data", None), "allowed_command_prefixes", None)
            or DEFAULT_ALLOWED_PREFIXES
        )
        for c in commands:
            if not c.lower().startswith(tuple(x.lower() for x in prefixes)) or any(
                ch in c for ch in ";|`$\n"
            ):
                raise _fail(f"{c!r} is not a read-only command (allowed: {', '.join(prefixes)})")
        out: dict[str, Any] = {}
        for device in devices:
            platform = await self._platform(device)
            req = StepRequest(
                ticket_number=f"FLOW-{inp.run_id[:8]}",
                device=DeviceRef(name=device, platform=platform),
                step="run_commands",
                change_type="flow",
                extra_vars={"commands": json.dumps(commands)},
                dry_run=True,
                correlation_id=inp.run_id,
            )
            result = await self._engine.run_commands(req)
            if not result.ok:
                raise _fail(f"run_commands failed on {device} (job {result.job_id})")
            outputs = [str(o)[:MAX_TEXT] for o in result.output.get("command_outputs") or []]
            per: dict[str, Any] = {}
            for cmd, text in zip(commands, outputs, strict=False):
                if p.get("parse"):
                    from vireonet.textfsm_lib import TextFsmError, parse

                    try:
                        per[cmd] = parse(platform, cmd, text, user_dir=self._textfsm_dir()).rows
                    except TextFsmError as exc:
                        per[cmd] = {"error": str(exc), "text": text}
                else:
                    per[cmd] = text
            out[device] = per
        return out

    async def _a_parse_textfsm(self, p: dict[str, Any], inp: FlowActionInput) -> dict[str, Any]:
        from vireonet.textfsm_lib import TextFsmError, parse

        try:
            r = parse(
                str(p.get("platform", "ios")),
                str(p.get("command", "")),
                str(p.get("text", "")),
                template_text=str(p.get("template") or "") or None,
                user_dir=self._textfsm_dir(),
            )
        except TextFsmError as exc:
            raise _fail(str(exc)) from exc
        return {"rows": r.rows, "template": r.template, "source": r.source}

    async def _a_run_checks(self, p: dict[str, Any], inp: FlowActionInput) -> dict[str, Any]:
        from vireonet.checks.base import CheckContext, CheckEngine
        from vireonet.checks.templates import (
            CheckTemplates,
            TemplateCommandCheck,
            engine_command_runner,
        )
        from vireonet.intent import parse_intent

        devices = _str_list(p.get("devices"))
        data = {
            "change_type": p.get("change_type"),
            "devices": devices,
            "site": (p.get("intent") or {}).get("site", "dc1"),
            "ticket_number": f"FLOW-{inp.run_id[:8]}",
            "requested_by": "flow",
            **(p.get("intent") or {}),
        }
        try:
            intent = parse_intent(data)
        except Exception as exc:
            raise _fail(f"intent for {p.get('change_type')!r} checks is incomplete: {exc}") from exc
        templates = self._check_templates or CheckTemplates.default()
        phase = "pre" if p.get("phase") == "pre" else "post"
        checks: list[Any] = [
            c
            for c in templates.checks_for(intent, cast(Any, phase))
            if isinstance(c, TemplateCommandCheck)
        ]
        ctx = CheckContext(
            intent=intent,
            ticket_number=intent.ticket_number,
            netbox=self._netbox,
            awx=None,  # type: ignore[arg-type]
            run_commands=engine_command_runner(self._engine, intent) if self._engine else None,
        )
        results = await CheckEngine().run(checks, ctx) if checks else []
        ok = all(r.ok for r in results)
        out = {
            "ok": ok,
            "phase": phase,
            "results": [{"name": r.name, "ok": r.ok, "message": r.message} for r in results],
            "note": "built-in checks that need a running change (locks, backups, dry-run) are not run here",
        }
        if not ok and p.get("fail_on_error"):
            raise _fail(
                "checks failed: " + "; ".join(f"{r.name}: {r.message}" for r in results if not r.ok)
            )
        return out

    async def _a_get_device(self, p: dict[str, Any], inp: FlowActionInput) -> dict[str, Any]:
        if self._netbox is None:
            raise _fail("NetBox is not configured")
        d = await self._netbox.get_device(str(p.get("device")))
        return {
            "name": d.name,
            "platform": d.platform,
            "site": d.site,
            "status": d.status,
            "model": d.model,
            "serial": d.serial,
            "primary_ip": d.primary_ip,
        }

    def _need_itsm(self) -> Any:
        if self._itsm is None:
            raise _fail("no ITSM provider configured (VIREONET_ITSM_PROVIDER)")
        return self._itsm

    async def _a_itsm_get_ticket(self, p: dict[str, Any], inp: FlowActionInput) -> dict[str, Any]:
        itsm = self._need_itsm()
        key = str(p.get("ticket"))
        t = await itsm.get_ticket(key)
        atts = await itsm.list_attachments(key)
        return {**t.to_dict(), "attachments": [a.to_dict() for a in atts]}

    async def _a_itsm_comment(self, p: dict[str, Any], inp: FlowActionInput) -> dict[str, Any]:
        await self._need_itsm().add_comment(str(p.get("ticket")), str(p.get("text")))
        return {"ticket": p.get("ticket"), "commented": True}

    async def _a_itsm_close(self, p: dict[str, Any], inp: FlowActionInput) -> dict[str, Any]:
        await self._need_itsm().close(str(p.get("ticket")), str(p.get("note")))
        return {"ticket": p.get("ticket"), "closed": True}

    async def _a_notify(self, p: dict[str, Any], inp: FlowActionInput) -> dict[str, Any]:
        if self._notifier is None:
            return {"sent": False, "reason": "no notification adapter"}
        sent = await self._notifier.send(
            str(
                p.get("channel")
                or getattr(self._settings, "notification_default_channel", "")
                or "slack"
            ),
            "VireoNet flow",
            str(p.get("message")),
            f"FLOW-{inp.run_id[:8]}",
        )
        return {"sent": bool(sent)}

    # --- pre/post checks ---------------------------------------------------------------------

    async def _a_run_check_profile(self, p: dict[str, Any], inp: FlowActionInput) -> dict[str, Any]:
        from vireonet.prepost.engine import ProfileError
        from vireonet.prepost.service import PrePostService

        svc = PrePostService(
            self._sessions,
            engine=self._engine,
            netbox=self._netbox,
            textfsm_dir=self._textfsm_dir(),
        )
        pre_id = str(p.get("compare_with") or "") or None
        try:
            snap = await svc.capture(
                str(p.get("profile")),
                _str_list(p.get("devices")),
                label=str(p.get("label") or "adhoc"),
                ticket=f"FLOW-{inp.run_id[:8]}",
                user="flow",
                pre_id=pre_id,
            )
            cmp = await svc.compare(pre_id, snap["id"]) if pre_id else None
        except (KeyError, ProfileError, RuntimeError) as exc:
            raise _fail(f"check profile: {exc}") from exc
        failed = [r for r in snap["results"] if not r["ok"]]
        out: dict[str, Any] = {
            "snapshot_id": snap["id"],
            "ok": not failed,
            "failed": len(failed),
            "tests": snap["results"],
        }
        if cmp is not None:
            out["diff"] = {
                "changed": [f"{d['device']}/{d['command']}" for d in cmp["diffs"] if d["changed"]],
                "details": cmp["diffs"],
            }
        if failed and p.get("fail_on_test_failure", True):
            raise _fail(
                "check profile tests failed: "
                + "; ".join(f"{r['device']} {r['name']}: {r['message']}" for r in failed[:5])
            )
        return out

    # --- intelligence ----------------------------------------------------------------------------

    async def _a_predict_change_risk(
        self, p: dict[str, Any], inp: FlowActionInput
    ) -> dict[str, Any]:
        import asyncio as _asyncio

        from vireonet.ml.neural import change_risk, default_change_risk_model

        trained = await _asyncio.to_thread(default_change_risk_model)
        feats = {
            k: p[k]
            for k in (
                "change_type",
                "devices",
                "platforms",
                "hour",
                "emergency",
                "prior_failure_rate",
            )
            if k in p and p[k] not in ("", None)
        }
        return change_risk(trained, **feats)

    async def _a_classify_ticket(self, p: dict[str, Any], inp: FlowActionInput) -> dict[str, Any]:
        import asyncio as _asyncio

        from vireonet.ml.neural import ticket_text

        devices: list[str] = []
        if self._netbox is not None:
            try:
                devices = [d.name for d in await self._netbox.list_devices()]
            except Exception:  # noqa: BLE001 — classification works without device names
                devices = []
        return await _asyncio.to_thread(ticket_text, str(p.get("text", "")), devices)

    # --- log platforms ---------------------------------------------------------------------

    def _log_backend(self, name: Any) -> Any:
        key = str(name or "")
        if key not in self._logs:
            raise _fail(
                f"no log platform named {key!r} (configured: {', '.join(sorted(self._logs)) or 'none'})"
            )
        return self._logs[key]

    async def _a_log_search(self, p: dict[str, Any], inp: FlowActionInput) -> dict[str, Any]:
        from vireonet.logsearch import LogSearchError

        b = self._log_backend(p.get("backend"))
        try:
            hits = await b.search(
                str(p.get("query", "*")),
                minutes=int(p.get("minutes") or 60),
                limit=int(p.get("limit") or 100),
            )
        except LogSearchError as exc:
            raise _fail(str(exc)) from exc
        return {"backend": b.name, "count": len(hits), "hits": [h.to_dict() for h in hits]}

    async def _a_log_send(self, p: dict[str, Any], inp: FlowActionInput) -> dict[str, Any]:
        from vireonet.logsearch import LogSearchError

        b = self._log_backend(p.get("backend"))
        event = {
            "source": "vireonet-flow",
            "event": "flow.message",
            "message": str(p.get("message", "")),
            "run_id": inp.run_id,
            **(p.get("fields") or {}),
        }
        try:
            await b.send(event)
        except LogSearchError as exc:
            raise _fail(str(exc)) from exc
        return {"backend": b.name, "sent": True}

    # --- vendor controllers ---------------------------------------------------------------

    async def _controller(self, name: Any, device: Any = None) -> Any:
        """A configured controller by name, or "auto": the one whose platforms
        include the device's (NetBox) platform."""
        key = str(name or "auto")
        if key != "auto":
            if key not in self._controllers:
                raise _fail(
                    f"no controller named {key!r} (configured: {', '.join(sorted(self._controllers)) or 'none'})"
                )
            return self._controllers[key]
        if not device:
            raise _fail("controller auto needs a device")
        platform = await self._platform(str(device))
        for c in self._controllers.values():
            if platform in getattr(c, "platforms", ()):
                return c
        raise _fail(f"no controller manages platform {platform!r} (device {device})")

    async def _ctl_call(self, coro: Any) -> Any:
        from vireonet.controllers.base import ControllerError

        try:
            return await coro
        except ControllerError as exc:
            raise _fail(str(exc)) from exc

    async def _a_controller_get_device(
        self, p: dict[str, Any], inp: FlowActionInput
    ) -> dict[str, Any]:
        c = await self._controller(p.get("controller"), p.get("device"))
        d = await self._ctl_call(c.get_device(str(p.get("device"))))
        return {**d.to_dict(), "controller": c.name}

    async def _a_controller_list_images(
        self, p: dict[str, Any], inp: FlowActionInput
    ) -> list[dict[str, Any]]:
        c = await self._controller(p.get("controller"), p.get("device"))
        platform = await self._platform(str(p["device"])) if p.get("device") else ""
        return [i.to_dict() for i in await self._ctl_call(c.list_images(platform))]

    async def _job_action(self, kind: str, p: dict[str, Any]) -> dict[str, Any]:
        c = await self._controller(p.get("controller"), p.get("device"))
        dev = await self._ctl_call(c.get_device(str(p.get("device"))))
        version = str(p.get("version", "")).strip()
        if not version:
            raise _fail("a version is required")
        if kind == "upgrade" and not getattr(c, "native_upgrade", True):
            kind = "distribute+activate"
        if kind == "distribute+activate":
            # Two jobs: distribute now; the activate starts when distribute finishes.
            job = await self._ctl_call(c.distribute(dev, version))
            job.id = f"{job.id}||then-activate"
        else:
            job = await self._ctl_call(getattr(c, kind)(dev, version))
        return {
            "controller": c.name,
            "vendor": getattr(c, "vendor", ""),
            "device": dev.name,
            "version": version,
            "from_version": dev.version,
            "action": kind,
            "job": job.to_dict(),
            "poll_seconds": int(getattr(self._settings, "controller_poll_seconds", 15)),
        }

    async def _a_controller_distribute(
        self, p: dict[str, Any], inp: FlowActionInput
    ) -> dict[str, Any]:
        return await self._job_action("distribute", p)

    async def _a_controller_activate(
        self, p: dict[str, Any], inp: FlowActionInput
    ) -> dict[str, Any]:
        return await self._job_action("activate", p)

    async def _a_controller_upgrade(
        self, p: dict[str, Any], inp: FlowActionInput
    ) -> dict[str, Any]:
        return await self._job_action("upgrade", p)

    async def _a_controller_job(self, p: dict[str, Any], inp: FlowActionInput) -> dict[str, Any]:
        c = await self._controller(p.get("controller"), p.get("device"))
        jid = str(p.get("job", ""))
        then_activate = jid.endswith("||then-activate")
        base_id = jid.removesuffix("||then-activate")
        job = await self._ctl_call(c.job(base_id))
        if then_activate and job.done and job.status == "succeeded":
            # Distribute finished: start activate (Panorama-style controllers).
            dev = await self._ctl_call(c.get_device(str(p.get("device"))))
            version = str(p.get("version") or "")
            if not version:
                raise _fail("internal: activate needs the version")
            job = await self._ctl_call(c.activate(dev, version))
        elif then_activate and not job.done:
            job.id = jid  # keep the marker until distribute finishes
        out: dict[str, Any] = job.to_dict()
        return out

    async def _a_controller_api(self, p: dict[str, Any], inp: FlowActionInput) -> Any:
        c = await self._controller(p.get("controller"))
        return await self._ctl_call(
            c.api(
                str(p.get("method", "GET")),
                str(p.get("path", "")),
                query=p.get("query") or None,
                body=p.get("body") if p.get("body") not in (None, "") else None,
            )
        )

    # --- changes --------------------------------------------------------------------------------

    @activity.defn(name="flow_create_change")
    async def flow_create_change(self, inp: FlowChangeInput) -> dict[str, Any]:
        """Create (or find) the VireoNet change; the workflow then runs it."""
        from vireonet.services.changes import ChangeService

        p = inp.params
        intent = dict(p.get("intent") or {})
        ticket = str(p.get("ticket_number") or intent.get("ticket_number") or "").strip()
        if not ticket:
            raise _fail("config_push needs a ticket number")
        intent["ticket_number"] = ticket
        intent.setdefault("requested_by", f"flow:{inp.flow_name}")
        full = p.get("automation", "full") == "full"
        now = datetime.now(UTC)
        gates = {
            "skip_ci": full or bool(intent.get("skip_ci", False)),
            "pre_approved": full,
            "approval_note": f"approved by flow {inp.flow_name!r} (run {inp.run_id})"
            if full
            else "",
            "window_start": (now - timedelta(seconds=5)).isoformat() if full else "",
            "window_end": (now + timedelta(minutes=int(p.get("window_minutes") or 120))).isoformat()
            if full
            else "",
        }
        if intent.get("change_type") == "config_push":
            intent.update(gates)
        svc = ChangeService(
            self._sessions, servicenow=self._snow, netbox=self._netbox, policy=self._policy
        )
        try:
            change, created = await svc.create_from_intent(intent, actor=f"flow:{inp.flow_name}")
        except Exception as exc:
            raise _fail(f"the change intent is invalid: {exc}") from exc
        state = change.state.value if hasattr(change.state, "value") else str(change.state)
        return {
            "change_id": str(change.id),
            "ticket_number": change.ticket_number,
            "state": state,
            "created": created,
            "intent": change.intent,
            "requester": change.requester,
            "gates": gates,
            "soak_seconds": int(getattr(self._settings, "post_apply_soak_seconds", 0)),
        }

    @activity.defn(name="flow_change_state")
    async def flow_change_state(self, change_id: str) -> dict[str, Any]:
        from vireonet.models import AuditLog, Change

        async with self._sessions() as s:
            import uuid as _uuid

            from sqlalchemy import select

            change = await s.get(Change, _uuid.UUID(change_id))
            if change is None:
                raise _fail(f"change {change_id} not found")
            last = (
                await s.execute(
                    select(AuditLog.reason)
                    .where(AuditLog.change_id == change.id)
                    .order_by(AuditLog.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            state = change.state.value if hasattr(change.state, "value") else str(change.state)
            return {"state": state, "reason": last or ""}


def _str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [v.strip() for v in value.replace(",", "\n").splitlines() if v.strip()]
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value)] if value else []


def all_flow_activities(a: FlowActivities) -> list[Any]:
    return [
        a.flow_load,
        a.flow_record,
        a.flow_finish,
        a.flow_prepare_subrun,
        a.flow_scheduled_run,
        a.flow_action,
        a.flow_create_change,
        a.flow_change_state,
    ]
