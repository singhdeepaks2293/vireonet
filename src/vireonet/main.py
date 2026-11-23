from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vireonet.adapters.base import AdapterHealth
from vireonet.adapters.registry import build_adapters
from vireonet.api.adapters import router as adapters_router
from vireonet.api.auth import router as auth_router
from vireonet.api.changes import router as changes_router
from vireonet.api.health import router as health_router
from vireonet.api.iam import router as iam_router
from vireonet.api.locks import router as locks_router
from vireonet.api.config_repos import router as config_repos_router
from vireonet.api.setup_gitea import router as setup_gitea_router
from vireonet.api.webhooks import router as webhooks_router
from vireonet.api.ztp import router as ztp_router
from vireonet.config import Settings, get_settings
from vireonet.db import create_engine, create_session_factory
from vireonet.config_store.device_repos import DeviceRepoManager
from vireonet.iam.service import UserService
from vireonet.setup.gitea_setup import GiteaSetupService
from vireonet.observability import metrics_bytes, set_ticket
from vireonet.policy import PolicyEngine
from vireonet.safety import DeviceLockService
from vireonet.services.changes import ChangeService
from vireonet.structlog_config import configure_logging, get_logger
from vireonet.temporal.client import FakeTemporalClient, RealTemporalClient
from vireonet.ztp.service import ZtpService


def create_app(
    settings: Settings | None = None, *,
    adapters: dict | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    policy: PolicyEngine | None = None,
    temporal_client: object | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    if settings.jwt_secret == "dev-only-change-me":
        get_logger(__name__).warning("jwt_secret is the dev default; JWTs are signed with the dev secret")
    adapters = adapters or build_adapters(settings)
    if session_factory is None:
        session_factory = create_session_factory(create_engine(settings.database_url))
    if policy is None:
        policy = PolicyEngine.from_yaml(settings.policy_file) if Path(settings.policy_file).exists() else PolicyEngine.default()
    if temporal_client is None:
        temporal_client = FakeTemporalClient() if settings.adapter_mode == "fake" else RealTemporalClient(settings)
    app = FastAPI(title="VireoNet", version="0.1.0")
    app.state.settings = settings
    app.state.adapters = adapters
    app.state.temporal_client = temporal_client
    app.state.change_service = ChangeService(session_factory, servicenow=adapters["servicenow"], netbox=adapters["netbox"], policy=policy)  # type: ignore[arg-type]
    app.state.ztp_service = ZtpService(session_factory, netbox=adapters["netbox"], token_ttl_seconds=settings.ztp_token_ttl_seconds)  # type: ignore[arg-type]
    app.state.lock_service = DeviceLockService(session_factory)
    app.state.user_service = UserService(session_factory)
    app.state.gitea_setup_service = GiteaSetupService()
    # Device repos are per-device Git repos (M12). The factory binds a
    # GitProvider to one repo at a time; the token comes from settings here
    # (Vault wiring is a follow-up — see config_repos router note).
    _git_settings = settings
    app.state.device_repo_manager = DeviceRepoManager(
        git_factory=lambda repo: __import__("vireonet.adapters.git.registry", fromlist=["get_git_provider"]).get_git_provider(
            provider=_git_settings.git_provider, base_url=_git_settings.git_base_url,
            token=_git_settings.gitlab_token or "", repo=repo,
        )
    )

    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        # Ticket is the correlation ID. Prefer the X-Correlation-ID header
        # (set by ServiceNow/GitLab webhooks); otherwise use a request id.
        # Never log the Authorization header or the request body.
        set_ticket(request.headers.get("X-Correlation-ID", "") or str(uuid.uuid4()))
        return await call_next(request)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics() -> Response:
        body, content_type = metrics_bytes()
        return Response(content=body, media_type=content_type)

    @app.get("/adapters/health")
    async def adapters_health() -> dict[str, list[AdapterHealth]]:
        return {"adapters": [await a.health_check() for a in adapters.values()]}  # type: ignore[union-attr]

    app.include_router(changes_router)
    app.include_router(webhooks_router)
    app.include_router(ztp_router)
    app.include_router(locks_router)
    app.include_router(health_router)
    app.include_router(adapters_router)
    app.include_router(auth_router)
    app.include_router(iam_router)
    app.include_router(setup_gitea_router)
    app.include_router(config_repos_router)
    return app


app = create_app()
