from __future__ import annotations

from collections.abc import Callable

from vireonet.adapters.awx import AwxAdapter
from vireonet.adapters.base import AdapterConfig, BaseAdapter
from vireonet.adapters.batfish import BatfishAdapter
from vireonet.adapters.containerlab import ContainerlabAdapter
from vireonet.adapters.dhcp import DhcpAdapter
from vireonet.adapters.errors import AdapterUnavailable
from vireonet.adapters.eveng import EveNgAdapter
from vireonet.adapters.fakes import ALL_FAKE_ADAPTERS
from vireonet.adapters.gitlab import GitLabAdapter
from vireonet.adapters.lab import LabAdapter
from vireonet.adapters.monitoring import MonitoringAdapter
from vireonet.adapters.netbox import NetBoxAdapter
from vireonet.adapters.notification import NotificationAdapter
from vireonet.adapters.servicenow import ServiceNowAdapter
from vireonet.adapters.vault import VaultAdapter
from vireonet.config import Settings

LAB_BACKENDS = ("eveng", "containerlab", "both")


def _eveng_adapter(settings: Settings, cfg: AdapterConfig) -> EveNgAdapter:
    return EveNgAdapter(
        cfg,
        username=settings.eveng_username,
        password=settings.eveng_password,
    )


def _containerlab_adapter(settings: Settings, cfg: AdapterConfig) -> ContainerlabAdapter:
    if not settings.containerlab_host or not settings.containerlab_user:
        raise AdapterUnavailable(
            "registry",
            "build_lab_adapters",
            "containerlab backend needs VIREONET_CONTAINERLAB_HOST and VIREONET_CONTAINERLAB_USER",
        )
    return ContainerlabAdapter(
        cfg,
        host=settings.containerlab_host,
        user=settings.containerlab_user,
        key_path=settings.containerlab_key_path or None,
        password=settings.containerlab_password or None,
    )


def build_lab_adapters(
    settings: Settings, cfg: Callable[[str, str | None], AdapterConfig]
) -> dict[str, BaseAdapter]:
    """Lab backends selected by VIREONET_LAB_BACKEND (E4).

    ``eveng`` -> ``eveng``; ``containerlab`` -> ``containerlab``; ``both``
    -> ``lab:eveng`` + ``lab:containerlab`` (distinct names so both can
    coexist in one adapter map). An unknown value raises at startup; a
    containerlab backend without host+user refuses to build.
    """
    backend = settings.lab_backend
    if backend not in LAB_BACKENDS:
        raise AdapterUnavailable(
            "registry",
            "build_lab_adapters",
            f"unknown lab backend {backend!r} (want eveng|containerlab|both)",
        )
    out: dict[str, BaseAdapter] = {}
    if backend in {"eveng", "both"}:
        key = "lab:eveng" if backend == "both" else "eveng"
        out[key] = _eveng_adapter(settings, cfg("eveng", settings.eveng_base_url))
    if backend in {"containerlab", "both"}:
        key = "lab:containerlab" if backend == "both" else "containerlab"
        out[key] = _containerlab_adapter(settings, cfg("containerlab", None))
    return out


def lab_adapter_for(settings: Settings, backend: str) -> LabAdapter:
    """The one LabAdapter named by ``backend`` (eveng|containerlab).

    Used by scripts/lab_smoke.py so the smoke run builds exactly the
    backend it was asked for, with the same validation as the registry.
    """
    if backend == "eveng":
        return _eveng_adapter(
            settings, AdapterConfig(name="eveng", base_url=settings.eveng_base_url)
        )
    if backend == "containerlab":
        return _containerlab_adapter(settings, AdapterConfig(name="containerlab"))
    raise AdapterUnavailable(
        "registry", "lab_adapter_for", f"unknown lab backend {backend!r} (want eveng|containerlab)"
    )


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
        # Lab backends (E4): replace the fake lab adapters with the real
        # ones selected by VIREONET_LAB_BACKEND. build_lab_adapters raises
        # on an unknown backend or missing containerlab host/user.
        out.pop("eveng", None)
        out.pop("containerlab", None)
        out.update(build_lab_adapters(settings, cfg))
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
