from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from vireonet.api.security import require_role

router = APIRouter()


class AdapterSettingResponse(BaseModel):
    name: str
    enabled: bool
    mode: str  # real, fake, recorded
    endpoint: str  # scheme + host + port only; no path, query or credentials
    timeout_seconds: float | None = None
    retry_max_attempts: int | None = None
    rate_limit_per_second: float | None = None
    capabilities: list[str]
    # core | itsm | telemetry | controller | logs | git | inbound
    category: str = "core"
    note: str = ""


def _redact_endpoint(raw: str) -> str:
    """Return scheme + host + port only. Strips path, query, fragment and userinfo."""
    if not raw:
        return ""
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.hostname:
        return "(not a URL)"
    host = parsed.hostname
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}"


@router.get("/adapters/settings", response_model=list[AdapterSettingResponse])
async def list_adapter_settings(
    request: Request, _role: str = Depends(require_role("viewer"))
) -> list[AdapterSettingResponse]:
    registry = getattr(request.app.state, "adapters", {}) or {}
    settings = getattr(request.app.state, "settings", None)
    mode = getattr(settings, "adapter_mode", "fake") if settings is not None else "fake"
    out: list[AdapterSettingResponse] = []
    for name, adapter in registry.items():
        # Per-adapter config lives on the adapter's own settings object when it
        # has one; otherwise fall back to the global settings. No secrets are
        # read here — base_url is the only URL field, and it is redacted above.
        cfg = getattr(adapter, "config", None) or settings
        base_url = ""
        if cfg is not None:
            base_url = str(getattr(cfg, "base_url", "") or getattr(cfg, "url", "") or "")
        # Some adapters expose their URL under a named attribute instead.
        if not base_url:
            base_url = str(getattr(adapter, "base_url", "") or getattr(adapter, "url", "") or "")
        timeout = getattr(cfg, "timeout_seconds", None) if cfg is not None else None
        retry_max = getattr(cfg, "retry_max_attempts", None) if cfg is not None else None
        rate_limit = getattr(cfg, "rate_limit_per_second", None) if cfg is not None else None
        try:
            caps = adapter.capabilities() if hasattr(adapter, "capabilities") else []
            if hasattr(caps, "__await__"):
                caps = await caps
            capabilities = list(caps)
        except Exception:
            capabilities = []
        out.append(
            AdapterSettingResponse(
                name=name,
                enabled=True,
                mode=mode,
                endpoint=_redact_endpoint(base_url),
                timeout_seconds=float(timeout) if timeout is not None else None,
                retry_max_attempts=int(retry_max) if retry_max is not None else None,
                rate_limit_per_second=float(rate_limit) if rate_limit is not None else None,
                capabilities=capabilities,
            )
        )
    out = sorted(out, key=lambda a: a.name)
    out.extend(_itsm_rows(request, mode))
    out.extend(_telemetry_rows(request, mode))
    out.extend(_controller_rows(request, mode))
    out.extend(_log_rows(request, mode))
    out.extend(_git_rows(request, mode))
    out.extend(_inbound_rows(request))
    return out


def _git_rows(request: Request, mode: str) -> list[AdapterSettingResponse]:
    from vireonet.adapters.git.registry import DEFAULT_BASE_URLS

    settings = request.app.state.settings
    active = str(getattr(settings, "git_provider", "")).lower()
    url = str(getattr(settings, "git_base_url", "") or "")
    return [
        AdapterSettingResponse(
            name=f"git: {p}",
            enabled=p == active,
            mode=(mode if p == active else "available"),
            endpoint=_redact_endpoint(url or DEFAULT_BASE_URLS[p]) if p == active else "",
            capabilities=[
                "files",
                "branches",
                "commits",
                "merge_requests",
                "pipelines",
                "comments",
                "webhooks",
            ],
            category="git",
            note="active (VIREONET_GIT_PROVIDER)"
            if p == active
            else f"set VIREONET_GIT_PROVIDER={p}",
        )
        for p in DEFAULT_BASE_URLS
    ]


def _inbound_rows(request: Request) -> list[AdapterSettingResponse]:
    """Systems that call VireoNet: flow trigger sources (webhook endpoints)."""
    from vireonet.flows.catalog import TRIGGERS
    from vireonet.flows.sources import NORMALISERS, TRIGGER_FOR

    settings = request.app.state.settings
    token = bool(
        getattr(settings, "flow_event_token", "") or getattr(settings, "webhook_secret", "")
    )
    rows = []
    for src in sorted(NORMALISERS):
        if src in TRIGGER_FOR:  # aliases (alertmanager, github, gitlab, gitea) share a trigger
            continue
        rows.append(
            AdapterSettingResponse(
                name=f"inbound: {src}",
                enabled=True,
                mode="webhook",
                endpoint="",
                capabilities=["flow_trigger"],
                category="inbound",
                note=f"{TRIGGERS.get(src, {}).get('label', src)} → POST /webhooks/flows/source/{src}"
                + ("" if token else " (set VIREONET_FLOW_EVENT_TOKEN)"),
            )
        )
    rows.append(
        AdapterSettingResponse(
            name="inbound: kafka",
            enabled=bool(getattr(settings, "kafka_bootstrap", "")),
            mode="consumer" if getattr(settings, "kafka_bootstrap", "") else "available",
            endpoint="",
            capabilities=["flow_trigger"],
            category="inbound",
            note="Kafka trigger source (VIREONET_KAFKA_BOOTSTRAP)",
        )
    )
    return rows


LOG_CAPS = ["search", "send_events"]


def _log_rows(request: Request, mode: str) -> list[AdapterSettingResponse]:
    from vireonet.logsearch import LOG_TYPES

    configured = getattr(request.app.state, "log_backends", {}) or {}
    export = set(getattr(request.app.state.settings, "log_export_to", []) or [])
    rows = [
        AdapterSettingResponse(
            name=f"logs: {name}",
            enabled=True,
            mode="fake" if b.kind == "fake" else "real",
            endpoint=_redact_endpoint(str(getattr(b, "base", "") or "fake://demo-syslog")),
            capabilities=LOG_CAPS,
            category="logs",
            note=f"{b.label}{' · receives VireoNet events' if name in export else ''}",
        )
        for name, b in sorted(configured.items())
    ]
    used = {b.kind for b in configured.values()}
    for kind, cls in LOG_TYPES.items():
        if kind not in used:
            rows.append(
                AdapterSettingResponse(
                    name=f"logs: {kind}",
                    enabled=False,
                    mode="available",
                    endpoint="",
                    capabilities=LOG_CAPS,
                    category="logs",
                    note=f'{cls.label}: add {{"type": "{kind}", …}} to VIREONET_LOG_BACKENDS',
                )
            )
    return rows


CONTROLLER_CAPS = ["devices", "images", "distribute", "activate", "upgrade", "jobs", "api"]


def _controller_rows(request: Request, mode: str) -> list[AdapterSettingResponse]:
    from vireonet.controllers.vendors import VENDORS

    configured = getattr(request.app.state, "controllers", {}) or {}
    rows = []
    for name, c in sorted(configured.items()):
        rows.append(
            AdapterSettingResponse(
                name=f"controller: {name}",
                enabled=True,
                mode="fake" if type(c).__name__ == "FakeController" else "real",
                endpoint=_redact_endpoint(str(getattr(c, "base", "") or f"fake://{name}")),
                capabilities=CONTROLLER_CAPS,
                category="controller",
                note=f"{getattr(c, 'vendor', '')}: {', '.join(getattr(c, 'platforms', ()))}",
            )
        )
    used = {getattr(getattr(c, "cfg", None), "type", n) for n, c in configured.items()}
    for kind, cls in VENDORS.items():
        if kind not in used:
            rows.append(
                AdapterSettingResponse(
                    name=f"controller: {kind}",
                    enabled=False,
                    mode="available",
                    endpoint="",
                    capabilities=CONTROLLER_CAPS,
                    category="controller",
                    note=f'{cls.vendor}: add {{"type": "{kind}", …}} to VIREONET_CONTROLLERS',
                )
            )
    return rows


ITSM_PROVIDERS = {
    "servicenow": (
        "VIREONET_ITSM_PROVIDER=servicenow (+ VIREONET_SERVICENOW_*)",
        "servicenow_base_url",
    ),
    "jira": ("VIREONET_ITSM_PROVIDER=jira, VIREONET_JIRA_*", "jira_base_url"),
    "freshservice": (
        "VIREONET_ITSM_PROVIDER=freshservice, VIREONET_FRESHSERVICE_*",
        "freshservice_base_url",
    ),
    "bmc_helix": ("VIREONET_ITSM_PROVIDER=bmc_helix, VIREONET_BMC_*", "bmc_base_url"),
}
ITSM_CAPS = ["tickets", "attachments", "work_notes", "close", "state_trigger", "planned_window"]

TELEMETRY_BACKENDS = {
    "zabbix": "VIREONET_TELEMETRY_BACKEND=zabbix",
    "prometheus/snmp_exporter": "VIREONET_TELEMETRY_BACKEND=prometheus, PRESET=snmp_exporter (SNMP)",
    "prometheus/gnmic": "VIREONET_TELEMETRY_BACKEND=prometheus, PRESET=gnmic (gNMI/gRPC)",
    "librenms": "VIREONET_TELEMETRY_BACKEND=librenms",
    "prtg": "VIREONET_TELEMETRY_BACKEND=prtg",
    "solarwinds": "VIREONET_TELEMETRY_BACKEND=solarwinds",
}


def _itsm_rows(request: Request, mode: str) -> list[AdapterSettingResponse]:
    settings = request.app.state.settings
    active = getattr(getattr(request.app.state, "itsm", None), "name", None)
    rows = []
    for name, (how, url_field) in ITSM_PROVIDERS.items():
        on = active == name
        rows.append(
            AdapterSettingResponse(
                name=f"itsm: {name}",
                enabled=on,
                mode=mode if on else "available",
                endpoint=_redact_endpoint(str(getattr(settings, url_field, "") or ""))
                if on
                else "",
                capabilities=ITSM_CAPS,
                category="itsm",
                note="active" if on else f"enable with {how}",
            )
        )
    if active == "fake":
        rows.insert(
            0,
            AdapterSettingResponse(
                name="itsm: fake",
                enabled=True,
                mode="fake",
                endpoint="fake://itsm",
                capabilities=ITSM_CAPS,
                category="itsm",
                note="demo tickets (fake mode)",
            ),
        )
    return rows


def _telemetry_rows(request: Request, mode: str) -> list[AdapterSettingResponse]:
    settings = request.app.state.settings
    active = getattr(getattr(request.app.state, "telemetry", None), "name", None)
    rows = []
    for name, how in TELEMETRY_BACKENDS.items():
        on = active == name or (active == "zabbix" and name == "zabbix")
        rows.append(
            AdapterSettingResponse(
                name=f"telemetry: {name}",
                enabled=on,
                mode=mode if on else "available",
                endpoint=_redact_endpoint(str(getattr(settings, "telemetry_base_url", "") or ""))
                if on
                else "",
                capabilities=["interface_rates", "history"]
                if name != "librenms"
                else ["interface_rates"],
                category="telemetry",
                note="active" if on else f"enable with {how}",
            )
        )
    if active == "fake":
        rows.insert(
            0,
            AdapterSettingResponse(
                name="telemetry: fake",
                enabled=True,
                mode="fake",
                endpoint="fake://telemetry",
                capabilities=["interface_rates", "history"],
                category="telemetry",
                note="synthetic traffic (fake mode)",
            ),
        )
    return rows
