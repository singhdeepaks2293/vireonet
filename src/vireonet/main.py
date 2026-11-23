from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
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
from vireonet.api.drift import router as drift_router
from vireonet.api.health import router as health_router
from vireonet.api.iam import router as iam_router
from vireonet.api.locks import router as locks_router
from vireonet.api.scripts import router as scripts_router
from vireonet.api.setup_gitea import router as setup_gitea_router
from vireonet.api.webhooks import router as webhooks_router
from vireonet.api.ztp import router as ztp_router
from vireonet.config import (
    DEFAULT_JWT_SECRET,
    Settings,
    get_settings,
    validate_drift_settings,
    validate_settings,
)
from vireonet.config_store.device_repos import DeviceRepoManager
from vireonet.config_store.store import ConfigStore
from vireonet.db import create_engine, create_session_factory
from vireonet.drift.events import DebouncedScanner
from vireonet.execution.awx_engine import AwxEngine
from vireonet.execution.engine import EngineRegistry, ExecutionEngine
from vireonet.execution.fake_engine import FakeExecutionEngine
from vireonet.execution.mapping import ExecutionMapping
from vireonet.iam.service import UserService
from vireonet.observability import metrics_bytes, set_ticket
from vireonet.policy import PolicyEngine
from vireonet.reconciliation.drift import DriftStore
from vireonet.safety import DeviceLockService
from vireonet.scripts.manifest import ScriptManifest
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
    # D6: drift settings. Real mode refuses unsafe drift settings;
    # fake/recorded mode only warns (validate_settings already returned
    # [] there, so this is the only place the drift checks surface).
    drift_problems = validate_drift_settings(settings)
    if drift_problems:
        if settings.adapter_mode == "real":
            raise RuntimeError(
                "refusing to start in real mode (drift settings): " + "; ".join(drift_problems)
            )
        get_logger(__name__).warning(
            "drift settings need attention (fake mode: warning only)",
            problems="; ".join(drift_problems),
        )
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
    # Execution engine (E1): the mapping + engine the worker and the
    # ChangeService share. Fake mode uses the fake engine and skips
    # template validation; real mode validates templates at startup
    # (in the lifespan below) and refuses to start without them.
    try:
        mapping = ExecutionMapping.from_yaml(settings.execution_file)
    except (OSError, ValueError):
        mapping = ExecutionMapping.default()
    registry = EngineRegistry()
    awx_engine: AwxEngine | None = None
    if settings.adapter_mode == "fake":
        execution_engine: ExecutionEngine = FakeExecutionEngine()
    else:
        awx_engine = AwxEngine(
            adapters["awx"],  # type: ignore[arg-type]
            mapping,
            base_url=settings.awx_base_url,
        )
        execution_engine = awx_engine
    registry.register("awx", execution_engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        if settings.adapter_mode == "real":
            # E5 real-mode startup validation: the script manifest must
            # parse, and the selected lab backend's settings must be
            # present. Fake mode never fails startup for missing real-mode
            # settings. (Unknown lab backend / missing containerlab host
            # already raise in build_adapters above; this re-checks here so
            # the failure is reported even if adapters were injected.)
            # Local checks run before the AWX template check so a bad
            # manifest is reported instead of a failed AWX call.
            try:
                ScriptManifest.from_yaml(settings.script_manifest)
            except (OSError, ValueError) as exc:
                raise RuntimeError(
                    f"script manifest {settings.script_manifest} does not load: {exc}"
                ) from exc
            if settings.lab_backend not in {"eveng", "containerlab", "both"}:
                raise RuntimeError(
                    f"unknown lab backend {settings.lab_backend!r} (want eveng|containerlab|both)"
                )
            if settings.lab_backend in {"containerlab", "both"} and (
                not settings.containerlab_host or not settings.containerlab_user
            ):
                raise RuntimeError(
                    "containerlab backend needs VIREONET_CONTAINERLAB_HOST and "
                    "VIREONET_CONTAINERLAB_USER"
                )
        if awx_engine is not None:
            missing = await awx_engine.validate_templates()
            if missing:
                raise RuntimeError("execution templates missing in AWX: " + ", ".join(missing))
        yield

    app = FastAPI(title="VireoNet", version="0.1.0", lifespan=lifespan)
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
    app.state.session_factory = session_factory
    app.state.temporal_client = temporal_client
    app.state.engine_registry = registry
    app.state.execution_engine = execution_engine
    config_store = ConfigStore(session_factory)
    app.state.config_store = config_store
    app.state.change_service = ChangeService(
        session_factory,
        servicenow=adapters["servicenow"],  # type: ignore[arg-type]
        netbox=adapters["netbox"],  # type: ignore[arg-type]
        policy=policy,
        config_store=config_store,
        awx=adapters["awx"],  # type: ignore[arg-type]
        engine=execution_engine,
    )
    app.state.ztp_service = ZtpService(
        session_factory,
        netbox=adapters["netbox"],  # type: ignore[arg-type]
        token_ttl_seconds=settings.ztp_token_ttl_seconds,
        dhcp=adapters["dhcp"],  # type: ignore[arg-type]
    )
    app.state.lock_service = DeviceLockService(session_factory)
    app.state.user_service = UserService(session_factory)
    app.state.drift_debouncer = DebouncedScanner()
    app.state.drift_store = DriftStore(session_factory)
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
    app.include_router(drift_router)
    app.include_router(webhooks_router)
    app.include_router(ztp_router)
    app.include_router(locks_router)
    app.include_router(health_router)
    app.include_router(adapters_router)
    app.include_router(auth_router)
    app.include_router(iam_router)
    app.include_router(scripts_router)
    app.include_router(setup_gitea_router)
    app.include_router(config_repos_router)
    return app


app = create_app()
