"""Log platforms: list and search (read-only; sending happens from flows / event export)."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from vireonet.api.security import CurrentUser, require_role
from vireonet.logsearch import LogSearchError

router = APIRouter(prefix="/logs", tags=["logs"])


class SearchBody(BaseModel):
    backend: str
    query: str = "*"
    minutes: int = Field(default=60, ge=1, le=60 * 24 * 31)
    limit: int = Field(default=100, ge=1, le=1000)


@router.get("/backends")
async def backends(
    request: Request, _u: CurrentUser = Depends(require_role("viewer"))
) -> dict[str, Any]:
    bs = getattr(request.app.state, "log_backends", {}) or {}
    health = await asyncio.gather(*(b.health() for b in bs.values()), return_exceptions=True)
    return {
        "items": [
            {
                "name": n,
                "type": b.kind,
                "label": b.label,
                **(h if isinstance(h, dict) else {"status": "down", "detail": str(h)}),
            }
            for (n, b), h in zip(bs.items(), health, strict=True)
        ]
    }


@router.post("/search")
async def search(
    body: SearchBody, request: Request, _u: CurrentUser = Depends(require_role("viewer"))
) -> dict[str, Any]:
    b = (getattr(request.app.state, "log_backends", {}) or {}).get(body.backend)
    if b is None:
        raise HTTPException(status_code=404, detail=f"no log platform named {body.backend!r}")
    try:
        hits = await b.search(body.query, minutes=body.minutes, limit=body.limit)
    except LogSearchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"backend": body.backend, "count": len(hits), "hits": [h.to_dict() for h in hits]}
