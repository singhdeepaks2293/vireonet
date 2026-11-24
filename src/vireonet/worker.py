from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

from temporalio.client import Client
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

from vireonet.adapters.protocols import (
    AwxProtocol,
    BatfishProtocol,
    DhcpProtocol,
    NetBoxProtocol,
    NotificationProtocol,
    ServiceNowProtocol,
)
from vireonet.adapters.registry import build_adapters
from vireonet.checks.templates import CheckTemplates
from vireonet.config import get_settings
from vireonet.config_store.device_repos import DeviceRepoManager
from vireonet.config_store.layout import git_layout_from_settings
from vireonet.config_store.store import ConfigStore
from vireonet.controllers import build_controllers
from vireonet.db import create_engine, create_session_factory
from vireonet.drift.activities import DriftActivities, all_drift_activities
from vireonet.drift.schedules import build_drift_schedules, create_temporal_schedule
from vireonet.drift.workflows import DriftDeviceWorkflow, DriftScanWorkflow
from vireonet.excel_push.service import ExcelPushService
from vireonet.execution.awx_engine import AwxEngine
from vireonet.execution.engine import EngineRegistry, ExecutionEngine
from vireonet.execution.fake_engine import FakeExecutionEngine
from vireonet.execution.fake_network import FakeNetwork
from vireonet.execution.mapping import ExecutionMapping
from vireonet.flows.activities import FlowActivities, all_flow_activities
from vireonet.flows.dispatch import FlowDispatcher, safe_dispatch
from vireonet.flows.store import FlowStore
from vireonet.flows.workflow import FlowScheduleWorkflow, FlowWorkflow
from vireonet.itsm.factory import build_itsm
from vireonet.policy import PolicyEngine
from vireonet.reconciliation.drift import DriftStore
from vireonet.reconciliation.service import ReconciliationService
from vireonet.rendering import Renderer
from vireonet.rendering.templates import RenderTemplates
from vireonet.structlog_config import configure_logging, get_logger
from vireonet.temporal.activities import ChangeActivities, all_activities
from vireonet.temporal.workflow import ChangeWorkflow
from vireonet.temporal.ztp_activities import ZtpActivities, all_ztp_activities
from vireonet.temporal.ztp_workflow import ZtpWorkflow
from vireonet.topology.demo import fake_device_repos, seed_demo_netbox
from vireonet.ztp.service import ZtpService

WORKFLOWS = [
    ChangeWorkflow,
    ZtpWorkflow,
    DriftScanWorkflow,
    DriftDeviceWorkflow,
    FlowWorkflow,
    FlowScheduleWorkflow,
]


def workflow_runner() -> SandboxedWorkflowRunner:
    """Temporal's sandbox re-imports workflow modules for determinism checks.
    SQLAlchemy model classes (reached through the activity modules) cannot be
    re-executed in it with SQLAlchemy 2.1+ (TypeError on __annotations__), so
    the worker would not start. Pass vireonet and sqlalchemy straight through:
    workflow code only orchestrates activities; all I/O happens in activities,
    outside the sandbox."""
    return SandboxedWorkflowRunner(
        restrictions=SandboxRestrictions.default.with_passthrough_modules(
            "vireonet", "sqlalchemy", "pydantic", "jinja2", "yaml", "numpy", "sklearn"
        )
    )


async def main() -> None:
    settings = get_settings()
    # Adapter settings saved from the UI override the environment.
    try:
        from vireonet.adapter_config import apply_overrides, load_overrides

        _eng = create_engine(settings.database_url)
        settings = apply_overrides(
            settings, await load_overrides(create_session_factory(_eng), settings.jwt_secret)
        )
        await _eng.dispose()
    except Exception as exc:  # noqa: BLE001 — fresh DB without the table: env only
        get_logger(__name__).info("no UI adapter settings applied", reason=str(exc)[:200])
    configure_logging(settings.log_level)
    adapters = build_adapters(settings)
    if settings.adapter_mode == "fake":
        seed_demo_netbox(adapters.get("netbox"))
    engine = create_engine(settings.database_url)
    sessions = create_session_factory(engine)
    # Same rule as the API (main.create_app): the policy file when it exists,
    # else the built-in default policy.
    policy = (
        PolicyEngine.from_yaml(settings.policy_file)
        if Path(settings.policy_file).exists()
        else PolicyEngine.default()
    )
    try:
        mapping = ExecutionMapping.from_yaml(settings.execution_file)
    except (OSError, ValueError):
        mapping = ExecutionMapping.default()
    registry = EngineRegistry()
    fake_network: FakeNetwork | None = None
    if settings.adapter_mode == "fake":
        # Fake mode simulates devices so template checks see realistic output.
        fake_network = FakeNetwork()
        from vireonet.execution.fake_network import NetworkStateStore

        execution_engine: ExecutionEngine = FakeExecutionEngine(
            network=fake_network,
            state_store=NetworkStateStore(sessions) if settings.temporal_mode == "real" else None,
        )
    else:
        awx_engine = AwxEngine(
            cast(AwxProtocol, adapters["awx"]),
            mapping,
            base_url=settings.awx_base_url,
        )
        # Real mode refuses to start with a mapping that names templates
        # AWX does not have.
        missing = await awx_engine.validate_templates()
        if missing:
            raise RuntimeError("execution templates missing in AWX: " + ", ".join(missing))
        execution_engine = awx_engine
    registry.register("awx", execution_engine)
    config_store = ConfigStore(sessions)
    _git_settings = settings
    if settings.adapter_mode == "fake":
        # Fake mode: an in-memory Git seeded with the demo running configs.
        device_repos = fake_device_repos(config_store, git_layout_from_settings(settings))
    else:
        device_repos = DeviceRepoManager(
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
    reconciliation = ReconciliationService(
        config_store,
        device_repos,
        netbox=cast(NetBoxProtocol, adapters["netbox"]),
        drift_store=DriftStore(sessions),
    )
    # Flows: VireoNet's own events (change finished, drift found) can trigger
    # flows. The emitter starts FlowWorkflows through the Temporal client.
    from vireonet.temporal.client import RealTemporalClient

    flow_starter = RealTemporalClient(settings)
    flow_dispatcher = FlowDispatcher(FlowStore(sessions), flow_starter.start_flow)
    from vireonet.logsearch import build_log_backends

    log_backends = build_log_backends(settings)
    emit = safe_dispatch(
        flow_dispatcher, log_backends=log_backends, export_to=list(settings.log_export_to)
    )
    # One ITSM provider for notes, closure and Excel attachments.
    itsm = build_itsm(
        settings, adapters, sessions=sessions if settings.temporal_mode == "real" else None
    )
    change_activities = ChangeActivities(
        sessions,
        servicenow=cast(ServiceNowProtocol, adapters["servicenow"]),
        netbox=cast(NetBoxProtocol, adapters["netbox"]),
        awx=cast(AwxProtocol, adapters["awx"]),
        policy=policy,
        lock_ttl_seconds=settings.lock_ttl_seconds,
        wave_size=settings.wave_size,
        abort_threshold=settings.abort_threshold,
        notification=cast(NotificationProtocol | None, adapters.get("notification")),
        monitoring=adapters.get("monitoring"),  # type: ignore[arg-type]
        monitoring_silence_minutes=settings.monitoring_silence_minutes,
        engine=execution_engine,
        reconciliation=reconciliation,
        # An invalid template file stops the worker in every mode: silently
        # falling back to defaults would drop the operator's own checks.
        check_templates=CheckTemplates.load(settings.check_templates_file),
        renderer=Renderer(RenderTemplates.load(settings.render_templates_file)),
        device_repos=device_repos,
        rollback_source=settings.rollback_source,
        wave_health_checks=settings.wave_health_checks,
        emit=emit,
        itsm=itsm,
        excel_push=ExcelPushService(
            sessions,
            itsm=itsm,
            max_bytes=settings.excel_max_bytes,
            max_device_bytes=settings.excel_max_device_config_bytes,
        ),
    )
    ztp_service = ZtpService(
        sessions,
        netbox=cast(NetBoxProtocol, adapters["netbox"]),
        token_ttl_seconds=settings.ztp_token_ttl_seconds,
    )
    ztp_activities = ZtpActivities(
        sessions,
        netbox=cast(NetBoxProtocol, adapters["netbox"]),
        dhcp=cast(DhcpProtocol, adapters["dhcp"]),
        awx=adapters["awx"],  # type: ignore[arg-type]
        batfish=cast(BatfishProtocol, adapters["batfish"]),
        servicenow=cast(ServiceNowProtocol, adapters["servicenow"]),
        ztp_service=ztp_service,
        tftp_server=settings.dhcp_tftp_server,
        http_base_url=settings.dhcp_http_base_url,
        pnp_server_url=settings.dhcp_pnp_server_url,
        policy=policy,
        engine=execution_engine,
        config_store=config_store,
        device_repos=device_repos,
    )
    drift_activities = DriftActivities(
        sessions,
        netbox=cast(NetBoxProtocol, adapters["netbox"]),
        engine=execution_engine,
        config_store=config_store,
        drift_store=DriftStore(sessions),
        lock_ttl_seconds=300,
        emit=emit,
    )
    flow_activities = FlowActivities(
        sessions,
        settings=settings,
        engine=execution_engine,
        netbox=adapters["netbox"],
        itsm=itsm,
        notifier=adapters.get("notification"),
        policy=policy,
        servicenow=adapters["servicenow"],
        check_templates=change_activities._check_templates,
        log_backends=log_backends,
        controllers=build_controllers(
            settings,
            network=fake_network if settings.adapter_mode == "fake" else None,
            sessions=sessions if settings.temporal_mode == "real" else None,
        ),
    )
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    # Drift schedules are created by the worker at startup (D3). A
    # failure here must not stop the worker: schedules already existing
    # or a Temporal without the schedule API are logged, not fatal.
    for spec in build_drift_schedules(settings):
        try:
            schedule_id = await create_temporal_schedule(client, spec)
            get_logger(__name__).info("drift schedule created", schedule=schedule_id)
        except Exception as exc:  # noqa: BLE001 — schedules are best-effort at startup
            get_logger(__name__).warning(
                "drift schedule not created", schedule=spec.name, error=str(exc)
            )
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=WORKFLOWS,
        workflow_runner=workflow_runner(),
        activities=all_activities(change_activities)
        + all_ztp_activities(ztp_activities)
        + all_drift_activities(drift_activities)
        + all_flow_activities(flow_activities),
    )
    kafka_task = None
    if settings.kafka_bootstrap:
        from vireonet.flows.kafka import KafkaTriggerConsumer, aiokafka_factory

        kafka = KafkaTriggerConsumer(flow_dispatcher, aiokafka_factory(settings))

        async def _kafka() -> None:
            # Restart the consumer after errors (broker down, rebalance) instead of dying.
            while True:
                try:
                    await kafka.run()
                    return
                except Exception as exc:  # noqa: BLE001
                    get_logger(__name__).warning("kafka trigger consumer error", error=str(exc))
                    await asyncio.sleep(10)

        kafka_task = asyncio.create_task(_kafka())
        get_logger(__name__).info("kafka trigger source on", bootstrap=settings.kafka_bootstrap)
    get_logger(__name__).info(
        "worker started", engine="awx", templates=sorted(mapping.all_templates())
    )
    try:
        await worker.run()
    finally:
        if kafka_task is not None:
            kafka_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
