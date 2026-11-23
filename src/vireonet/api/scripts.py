"""Scripts API (E5): read-only list of the script manifest.

Admin role only. The manifest (policies/scripts.yaml) is edited by merge
request, never through this API — there are no write endpoints here.
``last_run`` comes from the audit log when a run of that script is
recorded there, else None; the endpoint never fails because the audit
query cannot run (e.g. no session factory), it just reports None.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from vireonet.api.security import require_role
from vireonet.models import AuditLog
from vireonet.scripts.manifest import ScriptManifest

router = APIRouter()


class ScriptResponse(BaseModel):
    name: str
    sha: str
    allowed_change_types: list[str]
    allowed_platforms: list[str]
    writes: list[str]
    last_run: str | None = None


def _evidence_names_script(evidence: Any, name: str) -> bool:
    """True when ``script_name == name`` appears anywhere nested in evidence."""
    if isinstance(evidence, dict):
        if evidence.get("script_name") == name:
            return True
        return any(_evidence_names_script(v, name) for v in evidence.values())
    if isinstance(evidence, list):
        return any(_evidence_names_script(v, name) for v in evidence)
    return False


async def _last_runs(factory: Any, names: list[str]) -> dict[str, str]:
    """Most recent audit-log timestamp per script name, or {} on any failure."""
    if factory is None or not names:
        return {}
    try:
        async with factory() as session:
            rows = (
                (
                    await session.execute(
                        select(AuditLog).order_by(AuditLog.created_at.desc()).limit(500)
                    )
                )
                .scalars()
                .all()
            )
    except Exception:  # noqa: BLE001 — last_run is best-effort, never fatal
        return {}
    out: dict[str, str] = {}
    for row in rows:
        if len(out) == len(names):
            break
        for name in names:
            if (
                name not in out
                and row.created_at is not None
                and _evidence_names_script(row.evidence or {}, name)
            ):
                out[name] = row.created_at.isoformat()
    return out


@router.get("/scripts", response_model=list[ScriptResponse])
async def list_scripts(
    request: Request, _role: str = Depends(require_role("admin"))
) -> list[ScriptResponse]:
    settings = request.app.state.settings
    try:
        manifest = ScriptManifest.from_yaml(settings.script_manifest)
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=500, detail=f"script manifest does not load: {exc}"
        ) from exc
    factory = getattr(request.app.state, "session_factory", None)
    last = await _last_runs(factory, [e.name for e in manifest.scripts])
    return [
        ScriptResponse(
            name=e.name,
            sha=e.sha,
            allowed_change_types=list(e.allowed_change_types),
            allowed_platforms=list(e.allowed_platforms),
            writes=list(e.writes),
            last_run=last.get(e.name),
        )
        for e in manifest.scripts
    ]
