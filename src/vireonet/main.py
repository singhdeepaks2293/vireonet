from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vireonet.adapters.base import AdapterHealth, BaseAdapter
from vireonet.adapters.registry import build_adapters
from vireonet.api.adapters import router as adapters_router
from vireonet.api.auth import router as auth_router
from vireonet.api.changes import router as changes_router
from vireonet.api.config_repos import router as config_repos_router
from vireonet.api.health import router as health_router
from vireonet.api.iam import router as iam_router
from vireonet.api.locks import router as locks_router
from vireonet.api.setup_gitea import router as setup_gitea_router
from vireonet.api.webhooks import router as webhooks_router
from vireonet.api.ztp import router as ztp_router
from vireonet.config import DEFAULT_JWT_SECRET, Settings, get_settings, validate_settings
from vireonet.config_store.device_repos import DeviceRepoManager
from vireonet.config_store.store import ConfigStore
from vireonet.db import create_engine, create_session_factory
from vireonet.iam.service import UserService
from vireonet.observability import metrics_bytes, set_ticket
from vireonet.policy import PolicyEngine
from vireonet.safety import DeviceLockService
from vireonet.services.changes import ChangeService
from vireonet.setup.gitea_setup import GiteaSetupService
from vireonet.structlog_config import configure_logging, get_logger
from vireonet.temporal.client import FakeTemporalClient, RealTemporalClient
from vireonet.ztp.service import ZtpService


def create_app(
    settings: Settings | None = None,
    *,
    adapters: dict[str, BaseAdapter] | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    policy: PolicyEngine | None = None,
    temporal_client: object | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    if settings.jwt_secret == DEFAULT_JWT_SECRET:
        get_logger(__name__).warning(
            "jwt_secret is the dev default; JWTs are signed with the dev secret"
        )
    problems = validate_settings(settings)
    if problems:
        raise RuntimeError("refusing to start in real mode: " + "; ".join(problems))
    adapters = adapters or build_adapters(settings)
    if session_factory is None:
        session_factory = create_session_factory(create_engine(settings.database_url))
    if policy is None:
        policy = (
            PolicyEngine.from_yaml(settings.policy_file)
            if Path(settings.policy_file).exists()
            else PolicyEngine.default()
        )
    if temporal_client is None:
        temporal_client = (
            FakeTemporalClient()
            if settings.adapter_mode == "fake"
            else RealTemporalClient(settings)
        )
    app = FastAPI(title="VireoNet", version="0.1.0")
    # CORS: the browser UI (5173) is a different origin from the API (8000).
    # Without this the browser blocks login and every API call, while curl
    # works fine — exactly the "API login works, UI login fails" symptom.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.settings = settings
    app.state.adapters = adapters
    app.state.temporal_client = temporal_client
    config_store = ConfigStore(session_factory)
    app.state.config_store = config_store
    app.state.change_service = ChangeService(
        session_factory,
        servicenow=adapters["servicenow"],  # type: ignore[arg-type]
        netbox=adapters["netbox"],  # type: ignore[arg-type]
        policy=policy,
        config_store=config_store,
        awx=adapters["awx"],  # type: ignore[arg-type]
    )
    app.state.ztp_service = ZtpService(
        session_factory,
        netbox=adapters["netbox"],  # type: ignore[arg-type]
        token_ttl_seconds=settings.ztp_token_ttl_seconds,
        dhcp=adapters["dhcp"],  # type: ignore[arg-type]
    )
    app.state.lock_service = DeviceLockService(session_factory)
    app.state.user_service = UserService(session_factory)
    app.state.gitea_setup_service = GiteaSetupService()
    # Device repos are per-device Git repos (M12). The factory binds a
    # GitProvider to one repo at a time; the token comes from settings here
    # (Vault wiring is a follow-up — see config_repos router note).
    _git_settings = settings
    app.state.device_repo_manager = DeviceRepoManager(
        git_factory=lambda repo: __import__(
            "vireonet.adapters.git.registry", fromlist=["get_git_provider"]
        ).get_git_provider(
            provider=_git_settings.git_provider,
            base_url=_git_settings.git_base_url,
            token=_git_settings.gitlab_token or "",
            repo=repo,
        ),
        config_store=config_store,
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
        return {"adapters": [await a.health_check() for a in adapters.values()]}

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
