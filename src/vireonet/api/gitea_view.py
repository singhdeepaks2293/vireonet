"""Gitea inside the orchestrator: is Gitea installed, and where to show it.

The Gitea tab appears in the UI only when Gitea answers. For the embedded
view to load, Gitea must allow framing by the VireoNet origin: the simplest
way is to serve it under the same host (reverse proxy /gitea/ → Gitea, with
Gitea's ROOT_URL set to that path) and set VIREONET_GITEA_EMBED_URL=/gitea/.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request

from vireonet.api.security import CurrentUser, require_role

router = APIRouter(prefix="/gitea", tags=["gitea"])


def _ui_url(settings: Any) -> str:
    explicit = str(getattr(settings, "gitea_ui_url", "") or "")
    if explicit:
        return explicit.rstrip("/")
    if str(getattr(settings, "git_provider", "")).lower() == "gitea":
        base = str(getattr(settings, "git_base_url", "") or "").rstrip("/")
        return base.removesuffix("/api/v1")
    return ""


@router.get("/info")
async def info(
    request: Request, _u: CurrentUser = Depends(require_role("viewer"))
) -> dict[str, Any]:
    settings = request.app.state.settings
    ui = _ui_url(settings)
    out: dict[str, Any] = {
        "configured": bool(ui),
        "installed": False,
        "version": "",
        "ui_url": ui,
        "embed_url": str(getattr(settings, "gitea_embed_url", "") or ui),
        "repos": [],
        "detail": "",
    }
    if not ui:
        out["detail"] = (
            "Gitea is not configured (VIREONET_GITEA_UI_URL, or VIREONET_GIT_PROVIDER=gitea with VIREONET_GIT_BASE_URL)"
        )
        return out
    api = str(getattr(settings, "gitea_api_url", "") or f"{ui}/api/v1").rstrip("/")
    transport = getattr(request.app.state, "gitea_transport", None)
    try:
        async with httpx.AsyncClient(timeout=5.0, transport=transport) as client:
            v = await client.get(f"{api}/version")
            if v.status_code == 200:
                out["installed"], out["version"] = True, str(v.json().get("version", ""))
                r = await client.get(f"{api}/repos/search", params={"limit": 50})
                if r.status_code == 200:
                    out["repos"] = [
                        {
                            "name": x.get("full_name"),
                            "url": x.get("html_url"),
                            "updated": x.get("updated_at"),
                        }
                        for x in (r.json().get("data") or [])
                    ]
            else:
                out["detail"] = f"Gitea answered HTTP {v.status_code}"
    except httpx.HTTPError as exc:
        out["detail"] = f"Gitea is not reachable: {exc}"
    return out
