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
async def list_adapter_settings(request: Request, _role: str = Depends(require_role("viewer"))) -> list[AdapterSettingResponse]:
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
        out.append(AdapterSettingResponse(
            name=name, enabled=True, mode=mode, endpoint=_redact_endpoint(base_url),
            timeout_seconds=float(timeout) if timeout is not None else None,
            retry_max_attempts=int(retry_max) if retry_max is not None else None,
            rate_limit_per_second=float(rate_limit) if rate_limit is not None else None,
            capabilities=capabilities,
        ))
    return sorted(out, key=lambda a: a.name)
