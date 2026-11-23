from __future__ import annotations

from vireonet.adapters.awx import AwxAdapter
from vireonet.adapters.base import AdapterConfig, BaseAdapter
from vireonet.adapters.batfish import BatfishAdapter
from vireonet.adapters.dhcp import DhcpAdapter
from vireonet.adapters.errors import AdapterUnavailable
from vireonet.adapters.eveng import EveNgAdapter
from vireonet.adapters.fakes import ALL_FAKE_ADAPTERS
from vireonet.adapters.gitlab import GitLabAdapter
from vireonet.adapters.monitoring import MonitoringAdapter
from vireonet.adapters.netbox import NetBoxAdapter
from vireonet.adapters.notification import NotificationAdapter
from vireonet.adapters.servicenow import ServiceNowAdapter
from vireonet.adapters.vault import VaultAdapter
from vireonet.config import Settings


def build_adapters(settings: Settings) -> dict[str, BaseAdapter]:
    if settings.adapter_mode == "fake":
        return {name: cls() for name, cls in ALL_FAKE_ADAPTERS.items()}
    if settings.adapter_mode == "real":

        def cfg(name: str, url: str | None) -> AdapterConfig:
            return AdapterConfig(
                name=name,
                base_url=url or None,
                timeout_seconds=settings.adapter_timeout_seconds,
                max_retries=settings.adapter_max_retries,
                failure_threshold=settings.adapter_failure_threshold,
                reset_timeout_seconds=settings.adapter_reset_timeout_seconds,
            )

        out: dict[str, BaseAdapter] = {name: cls() for name, cls in ALL_FAKE_ADAPTERS.items()}
        out["servicenow"] = ServiceNowAdapter(
            cfg("servicenow", settings.servicenow_base_url),
            auth_mode=settings.servicenow_auth_mode,
            client_id=settings.servicenow_client_id,
            client_secret=settings.servicenow_client_secret,
            username=settings.servicenow_username,
            password=settings.servicenow_password,
        )
        out["netbox"] = NetBoxAdapter(
            cfg("netbox", settings.netbox_base_url), token=settings.netbox_token
        )
        out["awx"] = AwxAdapter(cfg("awx", settings.awx_base_url), token=settings.awx_token)
        out["vault"] = VaultAdapter(
            cfg("vault", settings.vault_addr),
            addr=settings.vault_addr,
            role_id=settings.vault_role_id,
            secret_id=settings.vault_secret_id,
            kv_mount=settings.vault_kv_mount,
            bootstrap_policy=settings.vault_bootstrap_policy,
        )
        out["gitlab"] = GitLabAdapter(
            cfg("gitlab", settings.gitlab_base_url),
            token=settings.gitlab_token,
            project_id=settings.gitlab_project_id,
        )
        out["batfish"] = BatfishAdapter(
            cfg("batfish", f"http://{settings.batfish_host}:{settings.batfish_port}"),
            host=settings.batfish_host,
            port=settings.batfish_port,
            snapshot_dir=settings.batfish_snapshot_dir,
        )
        out["eveng"] = EveNgAdapter(
            cfg("eveng", settings.eveng_base_url),
            username=settings.eveng_username,
            password=settings.eveng_password,
        )
        out["dhcp"] = DhcpAdapter(
            cfg("dhcp", None),
            netbox=out["netbox"],  # type: ignore[arg-type]
            gitlab=out["gitlab"],
            awx=out["awx"],
            server=settings.dhcp_server,
            tftp_server=settings.dhcp_tftp_server,
            http_base_url=settings.dhcp_http_base_url,
            pnp_server_url=settings.dhcp_pnp_server_url,
            config_path=settings.dhcp_config_path,
        )
        out["notification"] = NotificationAdapter(
            cfg("notification", None),
            smtp_host=settings.smtp_host,
            smtp_port=settings.smtp_port,
            smtp_username=settings.smtp_username,
            smtp_password=settings.smtp_password,
            email_from=settings.notification_email_from,
            email_to=settings.notification_email_to,
            teams_webhook_url=settings.teams_webhook_url,
            slack_webhook_url=settings.slack_webhook_url,
        )
        out["monitoring"] = MonitoringAdapter(
            cfg("monitoring", settings.monitoring_base_url),
            backend=settings.monitoring_backend,
            base_url=settings.monitoring_base_url,
            token=settings.monitoring_token,
        )
        return out
    raise AdapterUnavailable(
        "registry", "build_adapters", f"unknown adapter mode {settings.adapter_mode}"
    )
