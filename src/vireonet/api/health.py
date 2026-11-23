from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from vireonet.adapters.base import AdapterHealth

router = APIRouter()


class ComponentHealth(BaseModel):
    name: str
    status: str  # ok, down, not_configured, unknown
    detail: str


class ExecutionEnvironmentHealth(BaseModel):
    """The AWX Execution Environment job templates run in.

    ``status`` is ``unknown`` unless AWX can confirm the registration —
    the AWX API exposes no call that proves a named Execution Environment
    is registered on the job templates, so VireoNet never claims it is
    registered when it cannot verify that. ``image`` is None: no separate
    image is configured; ``name`` (settings.awx_execution_environment)
    is the EE name.
    """

    name: str
    image: str | None = None
    status: str  # registered, unknown
    detail: str


class LabBackendHealth(BaseModel):
    name: str
    status: str  # ok, down, not_configured, unknown
    capabilities: list[str] = []
    detail: str = ""


class SystemHealthResponse(BaseModel):
    status: str  # ok, degraded, down
    components: list[ComponentHealth]
    awx: ComponentHealth | None = None
    execution_environment: ExecutionEnvironmentHealth | None = None
    lab_backends: list[LabBackendHealth] = []


def _lab_backend_names(lab_backend: str) -> list[str]:
    if lab_backend == "eveng":
        return ["eveng"]
    if lab_backend == "containerlab":
        return ["containerlab"]
    if lab_backend == "both":
        return ["eveng", "containerlab"]
    return []


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
        components.append(
            ComponentHealth(
                name="temporal", status="not_configured", detail="temporal client not configured"
            )
        )
    else:
        try:
            # RealTemporalClient exposes the underlying client; FakeTemporalClient
            # reports ok without a network call. Both have a health_check() in M3.
            if hasattr(temporal, "health_check"):
                ok = await temporal.health_check()
                components.append(
                    ComponentHealth(
                        name="temporal",
                        status="ok" if ok else "down",
                        detail="namespace reachable" if ok else "namespace describe failed",
                    )
                )
            else:
                components.append(
                    ComponentHealth(name="temporal", status="ok", detail="client configured")
                )
        except Exception as exc:
            components.append(
                ComponentHealth(
                    name="temporal", status="down", detail=f"health check failed: {exc}"
                )
            )
    # AWX (E5): the engine's health_check; "unknown" when no AWX adapter
    # is wired at all.
    settings = getattr(request.app.state, "settings", None)
    adapters = getattr(request.app.state, "adapters", {}) or {}
    awx_health: ComponentHealth | None = None
    awx_status = "unknown"
    awx_adapter = adapters.get("awx")
    if awx_adapter is None:
        awx_health = ComponentHealth(
            name="awx", status="not_configured", detail="awx adapter not configured"
        )
    else:
        try:
            health: AdapterHealth = await awx_adapter.health_check()
            awx_status = health.status
            awx_health = ComponentHealth(
                name="awx", status=health.status, detail=health.detail or "AWX reachable"
            )
        except Exception as exc:
            awx_status = "down"
            awx_health = ComponentHealth(
                name="awx", status="down", detail=f"health check failed: {exc}"
            )
    # Execution Environment (E5): named in settings; registration is
    # unverifiable via the AWX API, so status is honestly "unknown".
    ee_name = (
        getattr(settings, "awx_execution_environment", "VireoNet EE") if settings else "VireoNet EE"
    )
    execution_environment = ExecutionEnvironmentHealth(
        name=ee_name,
        image=None,
        status="unknown",
        detail=(
            f"AWX is {awx_status}; the AWX API exposes no call that confirms "
            f"the '{ee_name}' Execution Environment is registered on the job "
            "templates — verify in AWX (Job Templates -> Execution Environment)"
        ),
    )
    # Lab backends (E5): each backend enabled by VIREONET_LAB_BACKEND,
    # with its health_check status and capabilities.
    lab_backends: list[LabBackendHealth] = []
    lab_backend = getattr(settings, "lab_backend", "eveng") if settings else "eveng"
    names = _lab_backend_names(str(lab_backend))
    if not names:
        lab_backends.append(
            LabBackendHealth(
                name="lab",
                status="unknown",
                detail=f"unknown lab backend {lab_backend!r} (want eveng|containerlab|both)",
            )
        )
    for backend_name in names:
        display = "EVE-NG" if backend_name == "eveng" else "Containerlab"
        adapter = adapters.get(backend_name) or adapters.get(f"lab:{backend_name}")
        if adapter is None:
            lab_backends.append(
                LabBackendHealth(
                    name=display,
                    status="not_configured",
                    detail=f"{backend_name} lab adapter not configured",
                )
            )
            continue
        try:
            health = await adapter.health_check()
            lab_backends.append(
                LabBackendHealth(
                    name=display,
                    status=health.status,
                    capabilities=list(health.capabilities),
                    detail=health.detail,
                )
            )
        except Exception as exc:
            lab_backends.append(
                LabBackendHealth(
                    name=display,
                    status="down",
                    capabilities=list(adapter.capabilities()),
                    detail=f"health check failed: {exc}",
                )
            )
    overall = "ok"
    if any(c.status == "down" for c in components):
        overall = (
            "down"
            if any(c.name in {"api", "database"} for c in components if c.status == "down")
            else "degraded"
        )
    if overall == "ok" and (
        (awx_health is not None and awx_health.status == "down")
        or any(b.status == "down" for b in lab_backends)
    ):
        overall = "degraded"
    return SystemHealthResponse(
        status=overall,
        components=components,
        awx=awx_health,
        execution_environment=execution_environment,
        lab_backends=lab_backends,
    )
