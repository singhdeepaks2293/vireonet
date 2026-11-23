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
from vireonet.db import create_engine, create_session_factory
from vireonet.policy import PolicyEngine
from vireonet.structlog_config import configure_logging
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
    )
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[ChangeWorkflow, ZtpWorkflow],
        activities=all_activities(change_activities) + all_ztp_activities(ztp_activities),
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
