from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vireonet.adapters.base import AdapterHealth, BaseAdapter
from vireonet.adapters.registry import build_adapters
from vireonet.api.adapter_config import router as adapter_config_router
from vireonet.api.adapters import router as adapters_router
from vireonet.api.auth import router as auth_router
from vireonet.api.changes import router as changes_router
from vireonet.api.checks import router as checks_router
from vireonet.api.config_repos import router as config_repos_router
from vireonet.api.controllers import router as controllers_router
from vireonet.api.drift import router as drift_router
from vireonet.api.excel_push import router as excel_push_router
from vireonet.api.flows import router as flows_router
from vireonet.api.gitea_view import router as gitea_view_router
from vireonet.api.health import router as health_router
from vireonet.api.help import router as help_router
from vireonet.api.iam import router as iam_router
from vireonet.api.locks import router as locks_router
from vireonet.api.logs import router as logs_router
from vireonet.api.ml import router as ml_router
from vireonet.api.prepost import router as prepost_router
from vireonet.api.scripts import router as scripts_router
from vireonet.api.security import require_role
from vireonet.api.setup_gitea import router as setup_gitea_router
from vireonet.api.textfsm import router as textfsm_router
from vireonet.api.topology import router as topology_router
from vireonet.api.webhooks import router as webhooks_router
from vireonet.api.ztp import router as ztp_router
from vireonet.checks.templates import CheckTemplates, TemplateValidationError
from vireonet.config import (
    DEFAULT_JWT_SECRET,
    Settings,
    get_settings,
    validate_drift_settings,
    validate_settings,
)
from vireonet.config_store.device_repos import DeviceRepoManager
from vireonet.config_store.layout import git_layout_from_settings
from vireonet.config_store.store import ConfigStore
from vireonet.db import create_engine, create_session_factory
from vireonet.drift.events import DebouncedScanner
from vireonet.excel_push.service import ExcelPushService
from vireonet.execution.awx_engine import AwxEngine
from vireonet.execution.engine import EngineRegistry, ExecutionEngine
from vireonet.execution.fake_engine import FakeExecutionEngine
from vireonet.execution.mapping import ExecutionMapping
from vireonet.iam.service import UserService
from vireonet.itsm.factory import build_itsm
from vireonet.observability import metrics_bytes, set_ticket
from vireonet.policy import PolicyEngine
from vireonet.reconciliation.drift import DriftStore
from vireonet.rendering import Renderer
from vireonet.rendering.templates import RenderTemplateError, RenderTemplates
from vireonet.safety import DeviceLockService
from vireonet.scripts.manifest import ScriptManifest
from vireonet.services.changes import ChangeService
from vireonet.setup.gitea_setup import GiteaSetupService
from vireonet.structlog_config import configure_logging, get_logger
from vireonet.telemetry.factory import build_telemetry
from vireonet.telemetry.fake import FakeTelemetry
from vireonet.temporal.client import FakeTemporalClient, RealTemporalClient
from vireonet.topology.demo import demo_interfaces, fake_device_repos, seed_demo_netbox
from vireonet.ztp.service import ZtpService


def create_app(
    settings: Settings | None = None,
    *,
    adapters: dict[str, BaseAdapter] | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    policy: PolicyEngine | None = None,
    temporal_client: object | None = None,
) -> FastAPI:
    if settings is None:
        # Production start: environment first, then values saved from the UI
        # (Adapters → Configure). Explicit settings (tests) are used as given.
        from vireonet.adapter_config import load_and_apply_sync

        settings = load_and_apply_sync(get_settings())
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
    from vireonet.config import flow_security_warnings

    for warning in flow_security_warnings(settings) if settings.adapter_mode == "real" else []:
        get_logger(__name__).warning("flow security", detail=warning)
    adapters = adapters or build_adapters(settings)
    if settings.adapter_mode == "fake":
        seed_demo_netbox(adapters.get("netbox"))
    if session_factory is None:
        session_factory = create_session_factory(create_engine(settings.database_url))
    if policy is None:
        policy = (
            PolicyEngine.from_yaml(settings.policy_file)
            if Path(settings.policy_file).exists()
            else PolicyEngine.default()
        )
    if temporal_client is None:
        use_fake = settings.temporal_mode == "fake" or (
            settings.temporal_mode == "auto" and settings.adapter_mode == "fake"
        )
        temporal_client = FakeTemporalClient() if use_fake else RealTemporalClient(settings)
    # Execution engine (E1): the mapping + engine the worker and the
    # ChangeService share. Fake mode uses the fake engine and skips
    # template validation; real mode validates templates at startup
    # (in the lifespan below) and refuses to start without them.
    try:
        mapping = ExecutionMapping.from_yaml(settings.execution_file)
    except (OSError, ValueError):
        mapping = ExecutionMapping.default()
    # Check templates (policies/checks.yaml). Real mode refuses to start on an
    # invalid file; fake mode keeps the error so the UI can show it.
    check_templates: CheckTemplates | None
    check_templates_error: list[str] = []
    try:
        check_templates = CheckTemplates.load(settings.check_templates_file)
    except TemplateValidationError as exc:
        if settings.adapter_mode == "real":
            raise RuntimeError(
                f"check templates {settings.check_templates_file} are invalid: {exc}"
            ) from exc
        get_logger(__name__).warning("check templates invalid", errors=exc.errors)
        check_templates, check_templates_error = None, exc.errors
    # Render templates (policies/render_templates.yaml), same rules as above.
    render_templates_error: list[str] = []
    try:
        renderer: Renderer | None = Renderer(RenderTemplates.load(settings.render_templates_file))
    except RenderTemplateError as exc:
        if settings.adapter_mode == "real":
            raise RuntimeError(
                f"render templates {settings.render_templates_file} are invalid: {exc}"
            ) from exc
        get_logger(__name__).warning("render templates invalid", errors=exc.errors)
        renderer, render_templates_error = None, exc.errors
    registry = EngineRegistry()
    awx_engine: AwxEngine | None = None
    if settings.adapter_mode == "fake":
        from vireonet.execution.fake_network import FakeNetwork, NetworkStateStore

        api_network = FakeNetwork()
        execution_engine: ExecutionEngine = FakeExecutionEngine(
            network=api_network,
            state_store=NetworkStateStore(session_factory)
            if settings.temporal_mode == "real"
            else None,
        )
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
    app.state.check_templates = check_templates
    app.state.check_templates_error = check_templates_error
    app.state.renderer = renderer
    app.state.policy = policy
    # ITSM provider (tickets, attachments) and the Excel config push.
    # Fake ITSM with a separate worker process: share ticket state through the DB.
    app.state.itsm = build_itsm(
        settings,
        adapters,
        sessions=session_factory if settings.temporal_mode == "real" else None,
    )
    # Workflow builder: stored flows, runs and the trigger dispatcher.
    from vireonet.flows.dispatch import FlowDispatcher
    from vireonet.flows.store import FlowStore

    app.state.flow_store = FlowStore(session_factory)
    from vireonet.controllers import build_controllers
    from vireonet.logsearch import build_log_backends

    app.state.log_backends = build_log_backends(settings)
    app.state.controllers = build_controllers(
        settings,
        network=getattr(execution_engine, "network", None),
        sessions=session_factory if settings.temporal_mode == "real" else None,
    )
    app.state.flow_dispatcher = FlowDispatcher(
        app.state.flow_store,
        temporal_client.start_flow if hasattr(temporal_client, "start_flow") else None,
    )
    app.state.excel_push = ExcelPushService(
        session_factory,
        itsm=app.state.itsm,
        max_bytes=settings.excel_max_bytes,
        max_device_bytes=settings.excel_max_device_config_bytes,
    )
    # Interface telemetry (traffic) for topology and ML. Fake mode uses the
    # demo topology's interfaces so the synthetic traffic lines up with links.
    telemetry = build_telemetry(settings)
    if isinstance(telemetry, FakeTelemetry):
        telemetry = FakeTelemetry(interfaces_for=demo_interfaces)
    app.state.telemetry = telemetry
    app.state.render_templates_error = render_templates_error
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
    if settings.adapter_mode == "fake":
        # Fake mode: an in-memory Git seeded with the demo running configs.
        app.state.device_repo_manager = fake_device_repos(
            config_store, git_layout_from_settings(settings)
        )
    else:
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
            layout=git_layout_from_settings(settings),
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
    async def metrics(request: Request) -> Response:
        import hmac as _hmac

        if settings.metrics_token:
            supplied = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            if not _hmac.compare_digest(supplied.encode(), settings.metrics_token.encode()):
                return Response(status_code=401, content="metrics token required")
        body, content_type = metrics_bytes()
        return Response(content=body, media_type=content_type)

    @app.get("/adapters/health")
    async def adapters_health(
        _role: str = Depends(require_role("viewer")),
    ) -> dict[str, list[AdapterHealth]]:
        return {"adapters": [await a.health_check() for a in adapters.values()]}

    app.include_router(changes_router)
    app.include_router(topology_router)
    app.include_router(ml_router)
    app.include_router(excel_push_router)
    app.include_router(textfsm_router)
    app.include_router(checks_router)
    app.include_router(drift_router)
    app.include_router(webhooks_router)
    app.include_router(ztp_router)
    app.include_router(locks_router)
    app.include_router(health_router)
    app.include_router(adapters_router)
    # An adapter saying "not found" (unknown ticket, device, …) is a 404, and any
    # other adapter failure a 502, never an unexplained 500.
    from fastapi.responses import JSONResponse

    from vireonet.adapters.errors import AdapterError, AdapterNotFound

    @app.exception_handler(AdapterNotFound)
    async def _adapter_not_found(_request: Request, exc: AdapterNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": f"not found: {exc}"})

    @app.exception_handler(AdapterError)
    async def _adapter_error(_request: Request, exc: AdapterError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": f"adapter error: {exc}"})

    app.include_router(auth_router)
    app.include_router(help_router)
    app.include_router(flows_router)
    app.include_router(controllers_router)
    app.include_router(logs_router)
    app.include_router(gitea_view_router)
    app.include_router(adapter_config_router)
    app.include_router(prepost_router)
    app.include_router(iam_router)
    app.include_router(scripts_router)
    app.include_router(setup_gitea_router)
    app.include_router(config_repos_router)
    return app


app = create_app()
