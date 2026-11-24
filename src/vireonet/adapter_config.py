"""Adapter settings edited in the UI (Adapters → Configure).

Environment variables keep working exactly as before. Values saved in the
UI are stored in the adapter_overrides table and override the environment
at the next start of the API and the worker. Secrets (passwords, tokens,
keys) are encrypted with a key derived from VIREONET_JWT_SECRET and are
never returned by the API: it only says whether a secret is set.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any, get_args, get_origin

from cryptography.fernet import Fernet, InvalidToken

GROUPS: dict[str, dict[str, Any]] = {
    "servicenow": {"label": "ServiceNow", "prefixes": ["servicenow_"]},
    "itsm": {
        "label": "ITSM (Jira, Freshservice, BMC Helix, triggers)",
        "prefixes": ["itsm_", "jira_", "freshservice_", "bmc_"],
    },
    "netbox": {"label": "NetBox", "prefixes": ["netbox_"]},
    "awx": {"label": "AWX / Ansible Automation Platform", "prefixes": ["awx_"]},
    "git": {"label": "Git provider", "prefixes": ["git_"]},
    "gitea": {"label": "Gitea tab", "prefixes": ["gitea_"]},
    "vault": {"label": "Vault", "prefixes": ["vault_"]},
    "batfish": {"label": "Batfish", "prefixes": ["batfish_"]},
    "labs": {"label": "Labs (EVE-NG, containerlab)", "prefixes": ["eveng_", "containerlab_"]},
    "dhcp": {"label": "DHCP / ZTP", "prefixes": ["dhcp_"]},
    "notifications": {"label": "Notifications", "prefixes": ["notification_"]},
    "monitoring": {"label": "Monitoring (alert silencing)", "prefixes": ["monitoring_"]},
    "telemetry": {
        "label": "Telemetry (Zabbix, Prometheus, LibreNMS, PRTG, SolarWinds)",
        "prefixes": ["telemetry_"],
    },
    "controllers": {
        "label": "Vendor controllers",
        "names": ["controllers", "controller_poll_seconds"],
    },
    "logs": {"label": "Log platforms", "names": ["log_backends", "log_export_to"]},
    "kafka": {"label": "Kafka trigger source", "prefixes": ["kafka_"]},
    "flows": {"label": "Flows (API and script security)", "prefixes": ["flow_"]},
}
SECRET_SUFFIXES = ("password", "secret", "token", "api_key", "_pat", "secret_id", "client_secret")
NOT_SECRET = {"git_token_secret", "flow_event_token_header"}  # Vault path names, not secrets
JSON_FIELDS = {"controllers", "log_backends"}


def is_secret(name: str) -> bool:
    return name not in NOT_SECRET and name.endswith(SECRET_SUFFIXES)


def _fernet(jwt_secret: str) -> Fernet:
    key = hashlib.sha256(b"vireonet-adapter-config:" + jwt_secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(value: str, jwt_secret: str) -> str:
    return _fernet(jwt_secret).encrypt(value.encode()).decode()


def decrypt(token: str, jwt_secret: str) -> str | None:
    try:
        return _fernet(jwt_secret).decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None  # the JWT secret changed: the stored secret must be entered again


def field_kind(annotation: Any) -> tuple[str, list[str]]:
    origin = get_origin(annotation)
    if str(origin) == "typing.Literal" or origin is getattr(__import__("typing"), "Literal", None):
        return "select", [str(a) for a in get_args(annotation)]
    args = [a for a in get_args(annotation) if a is not type(None)]
    base = args[0] if origin is not None and args and origin is not list else annotation
    if origin is list:
        return "list", []
    if base is bool:
        return "bool", []
    if base in (int, float):
        return "number", []
    return "text", []


def group_fields(settings_cls: Any) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for g, spec in GROUPS.items():
        names = list(spec.get("names", []))
        for name in settings_cls.model_fields:
            if any(name.startswith(p) for p in spec.get("prefixes", [])) and name not in names:
                names.append(name)
        out[g] = [n for n in names if n in settings_cls.model_fields]
    return out


def editable(settings_cls: Any) -> set[str]:
    return {n for names in group_fields(settings_cls).values() for n in names}


def coerce(settings_cls: Any, name: str, value: Any) -> Any:
    kind, _ = field_kind(settings_cls.model_fields[name].annotation)
    if name in JSON_FIELDS:
        text = value if isinstance(value, str) else json.dumps(value)
        parsed = json.loads(text or "[]")
        if not isinstance(parsed, list):
            raise ValueError(f"{name} must be a JSON list")
        return json.dumps(parsed)
    if kind == "list" and isinstance(value, str):
        return [v.strip() for v in value.replace("\n", ",").split(",") if v.strip()]
    return value


async def load_overrides(sessions: Any, jwt_secret: str) -> dict[str, Any]:
    from sqlalchemy import select

    from vireonet.models import AdapterOverride

    async with sessions() as s:
        rows = (await s.execute(select(AdapterOverride))).scalars().all()
    out: dict[str, Any] = {}
    for r in rows:
        if r.secret:
            plain = decrypt(str(r.value.get("enc", "")), jwt_secret)
            if plain is not None:
                out[r.key] = plain
        else:
            out[r.key] = r.value.get("v")
    return out


def apply_overrides(settings: Any, overrides: dict[str, Any]) -> Any:
    """Settings with the UI overrides on top (validated like env values)."""
    allowed = editable(type(settings))
    clean = {k: v for k, v in overrides.items() if k in allowed}
    if not clean:
        return settings
    return type(settings).model_validate({**settings.model_dump(), **clean})


def load_and_apply_sync(settings: Any) -> Any:
    """For create_app (sync): apply DB overrides when no event loop is running."""
    import asyncio

    try:
        asyncio.get_running_loop()
        return settings  # inside a running loop (tests): leave the settings alone
    except RuntimeError:
        pass
    if not getattr(settings, "adapter_config_from_db", True):
        return settings

    async def run() -> dict[str, Any]:
        from vireonet.db import create_engine, create_session_factory

        engine = create_engine(settings.database_url)
        try:
            return await load_overrides(create_session_factory(engine), settings.jwt_secret)
        finally:
            await engine.dispose()

    try:
        overrides = asyncio.run(run())
    except Exception:  # noqa: BLE001 — no table yet (fresh DB), DB down: start with env only
        return settings
    return apply_overrides(settings, overrides)
