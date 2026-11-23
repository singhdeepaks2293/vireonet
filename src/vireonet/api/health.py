from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()


class ComponentHealth(BaseModel):
    name: str
    status: str  # ok, down, not_configured, unknown
    detail: str


class SystemHealthResponse(BaseModel):
    status: str  # ok, degraded, down
    components: list[ComponentHealth]


@router.get("/system/health", response_model=SystemHealthResponse)
async def system_health(request: Request) -> SystemHealthResponse:
    components: list[ComponentHealth] = []
    # API itself: if this handler runs, the API is up.
    components.append(ComponentHealth(name="api", status="ok", detail="FastAPI responding"))
    # Database: real SELECT 1 through the session factory.
    db_status, db_detail = "unknown", "session factory not configured"
    factory = getattr(request.app.state, "session_factory", None)
    if factory is not None:
        try:
            async with factory() as session:
                await session.execute(__import__("sqlalchemy").text("SELECT 1"))
            db_status, db_detail = "ok", "SELECT 1 succeeded"
        except Exception as exc:
            db_status, db_detail = "down", f"SELECT 1 failed: {exc}"
    components.append(ComponentHealth(name="database", status=db_status, detail=db_detail))
    # Temporal: namespace describe if a client is configured.
    temporal = getattr(request.app.state, "temporal_client", None)
    if temporal is None:
        components.append(ComponentHealth(name="temporal", status="not_configured", detail="temporal client not configured"))
    else:
        try:
            # RealTemporalClient exposes the underlying client; FakeTemporalClient
            # reports ok without a network call. Both have a health_check() in M3.
            if hasattr(temporal, "health_check"):
                ok = await temporal.health_check()
                components.append(ComponentHealth(name="temporal", status="ok" if ok else "down", detail="namespace reachable" if ok else "namespace describe failed"))
            else:
                components.append(ComponentHealth(name="temporal", status="ok", detail="client configured"))
        except Exception as exc:
            components.append(ComponentHealth(name="temporal", status="down", detail=f"health check failed: {exc}"))
    overall = "ok"
    if any(c.status == "down" for c in components):
        overall = "down" if any(c.name in {"api", "database"} for c in components if c.status == "down") else "degraded"
    return SystemHealthResponse(status=overall, components=components)
