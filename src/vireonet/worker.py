from __future__ import annotations

import asyncio
from typing import cast

from temporalio.client import Client
from temporalio.worker import Worker

from vireonet.adapters.protocols import (
    AwxProtocol,
    BatfishProtocol,
    DhcpProtocol,
    NetBoxProtocol,
    NotificationProtocol,
    ServiceNowProtocol,
)
from vireonet.adapters.registry import build_adapters
from vireonet.config import get_settings
from vireonet.config_store.device_repos import DeviceRepoManager
from vireonet.config_store.store import ConfigStore
from vireonet.db import create_engine, create_session_factory
from vireonet.drift.activities import DriftActivities, all_drift_activities
from vireonet.drift.schedules import build_drift_schedules, create_temporal_schedule
from vireonet.drift.workflows import DriftDeviceWorkflow, DriftScanWorkflow
from vireonet.execution.awx_engine import AwxEngine
from vireonet.execution.engine import EngineRegistry, ExecutionEngine
from vireonet.execution.fake_engine import FakeExecutionEngine
from vireonet.execution.mapping import ExecutionMapping
from vireonet.policy import PolicyEngine
from vireonet.reconciliation.drift import DriftStore
from vireonet.reconciliation.service import ReconciliationService
from vireonet.structlog_config import configure_logging, get_logger
from vireonet.temporal.activities import ChangeActivities, all_activities
from vireonet.temporal.workflow import ChangeWorkflow
from vireonet.temporal.ztp_activities import ZtpActivities, all_ztp_activities
from vireonet.temporal.ztp_workflow import ZtpWorkflow
from vireonet.ztp.service import ZtpService


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    adapters = build_adapters(settings)
    engine = create_engine(settings.database_url)
    sessions = create_session_factory(engine)
    policy = PolicyEngine.from_yaml(settings.policy_file)
    try:
        mapping = ExecutionMapping.from_yaml(settings.execution_file)
    except (OSError, ValueError):
        mapping = ExecutionMapping.default()
    registry = EngineRegistry()
    if settings.adapter_mode == "fake":
        execution_engine: ExecutionEngine = FakeExecutionEngine()
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
    )
    reconciliation = ReconciliationService(
        config_store,
        device_repos,
        netbox=cast(NetBoxProtocol, adapters["netbox"]),
        drift_store=DriftStore(sessions),
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
        dhcp_server=settings.dhcp_server,
        ip_pool=settings.ztp_ip_pool,
        management_interface=settings.ztp_management_interface,
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
        workflows=[ChangeWorkflow, ZtpWorkflow, DriftScanWorkflow, DriftDeviceWorkflow],
        activities=all_activities(change_activities)
        + all_ztp_activities(ztp_activities)
        + all_drift_activities(drift_activities),
    )
    get_logger(__name__).info(
        "worker started", engine="awx", templates=sorted(mapping.all_templates())
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
