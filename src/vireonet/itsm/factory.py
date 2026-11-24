from __future__ import annotations

from typing import Any

from vireonet.itsm.base import ItsmError, ItsmProvider


def build_itsm(
    settings: Any, adapters: dict[str, Any] | None = None, *, sessions: Any = None
) -> ItsmProvider | None:
    """ITSM provider from settings. "auto": fake in fake mode, ServiceNow in
    real mode when it is configured, else none."""
    kind = str(getattr(settings, "itsm_provider", "auto"))
    mode = str(getattr(settings, "adapter_mode", "fake"))
    if kind == "auto":
        kind = (
            "fake"
            if mode == "fake"
            else ("servicenow" if getattr(settings, "servicenow_base_url", "") else "none")
        )
    kw: dict[str, Any] = {
        "timeout": float(getattr(settings, "adapter_timeout_seconds", 10.0)) + 5,
        "verify_tls": bool(getattr(settings, "itsm_verify_tls", True)),
    }
    if kind == "none":
        return None
    if kind == "fake":
        from vireonet.itsm.fake import FakeItsm

        return FakeItsm(sessions=sessions)
    if kind == "servicenow":
        from vireonet.itsm.servicenow import ServiceNowItsm

        adapter = (adapters or {}).get("servicenow")
        if adapter is None or not hasattr(adapter, "list_attachments"):
            raise ItsmError("servicenow ITSM needs the ServiceNow adapter (VIREONET_SERVICENOW_*)")
        return ServiceNowItsm(adapter)
    if kind == "jira":
        from vireonet.itsm.jira import JiraItsm

        return JiraItsm(
            str(getattr(settings, "jira_base_url", "")),
            email=str(getattr(settings, "jira_email", "")),
            api_token=str(getattr(settings, "jira_api_token", "")),
            pat=str(getattr(settings, "jira_pat", "")),
            close_transition=str(getattr(settings, "jira_close_transition", "Done")),
            planned_start_field=str(getattr(settings, "jira_planned_start_field", "")),
            planned_end_field=str(getattr(settings, "jira_planned_end_field", "")),
            **kw,
        )
    if kind == "freshservice":
        from vireonet.itsm.freshservice import FreshserviceItsm

        return FreshserviceItsm(
            str(getattr(settings, "freshservice_base_url", "")),
            api_key=str(getattr(settings, "freshservice_api_key", "")),
            **kw,
        )
    if kind == "bmc_helix":
        from vireonet.itsm.bmc_helix import BmcHelixItsm

        return BmcHelixItsm(
            str(getattr(settings, "bmc_base_url", "")),
            username=str(getattr(settings, "bmc_username", "")),
            password=str(getattr(settings, "bmc_password", "")),
            **kw,
        )
    raise ItsmError(f"unknown ITSM provider {kind!r}")
