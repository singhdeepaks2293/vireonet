"""In-app help: serves docs/USER_MANUAL.md to the UI."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from vireonet.api.security import require_role

router = APIRouter(prefix="/help", tags=["help"])

_CANDIDATES = (
    Path(__file__).resolve().parents[3] / "docs" / "USER_MANUAL.md",  # source checkout
    Path.cwd() / "docs" / "USER_MANUAL.md",  # container WORKDIR
)


@router.get("/manual")
async def manual(request: Request, _role: str = Depends(require_role("viewer"))) -> dict[str, str]:
    configured = getattr(request.app.state.settings, "user_manual_file", "")
    for path in ([Path(configured)] if configured else []) + list(_CANDIDATES):
        if path.is_file():
            return {"source": path.name, "markdown": path.read_text(encoding="utf-8")}
    raise HTTPException(status_code=404, detail="docs/USER_MANUAL.md is not installed")


_IMAGE = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}\.png$")


@router.get("/images/{name}")
async def image(name: str) -> FileResponse:
    """Screenshots used by the manual. Public on purpose (an <img> cannot send
    a login token) and limited to docs/images/<name>.png."""
    if not _IMAGE.match(name):
        raise HTTPException(status_code=404, detail="not found")
    for base in _CANDIDATES:
        path = base.parent / "images" / name
        if path.is_file():
            return FileResponse(
                path, media_type="image/png", headers={"Cache-Control": "max-age=3600"}
            )
    raise HTTPException(status_code=404, detail="not found")


_DOC_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TITLES = {
    "USER_MANUAL": "User manual",
    "ADAPTERS": "Adapter integration guide",
    "SDLC": "SDLC",
    "DRIFT": "Drift internals",
    "SCRIPTS": "Custom scripts",
    "LAB": "Labs (EVE-NG / containerlab)",
    "runbook": "Runbook",
}


def _docs_dir() -> Path | None:
    for base in _CANDIDATES:
        if base.parent.is_dir():
            return base.parent
    return None


@router.get("/docs")
async def docs(_role: str = Depends(require_role("viewer"))) -> dict[str, list[dict[str, str]]]:
    d = _docs_dir()
    items = []
    for f in sorted(d.glob("*.md")) if d else []:
        items.append(
            {"name": f.stem, "title": _TITLES.get(f.stem, f.stem.replace("_", " ").title())}
        )
    items.sort(key=lambda x: (x["name"] != "USER_MANUAL", x["title"]))
    return {"items": items}


@router.get("/docs/{name}")
async def doc(name: str, _role: str = Depends(require_role("viewer"))) -> dict[str, str]:
    d = _docs_dir()
    if not _DOC_NAME.match(name) or d is None or not (d / f"{name}.md").is_file():
        raise HTTPException(status_code=404, detail="no such document")
    return {
        "name": name,
        "title": _TITLES.get(name, name),
        "markdown": (d / f"{name}.md").read_text(encoding="utf-8"),
    }
