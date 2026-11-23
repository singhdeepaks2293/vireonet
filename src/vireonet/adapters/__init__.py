"""Adapter layer."""

from vireonet.adapters.awx import AwxAdapter
from vireonet.adapters.base import AdapterConfig, AdapterHealth, BaseAdapter
from vireonet.adapters.batfish import BatfishAdapter
from vireonet.adapters.errors import (
    AdapterAuthError,
    AdapterConflict,
    AdapterError,
    AdapterNotFound,
    AdapterRateLimited,
    AdapterTimeout,
    AdapterUnavailable,
    AdapterValidationError,
)
from vireonet.adapters.eveng import EveNgAdapter
from vireonet.adapters.gitlab import GitLabAdapter
from vireonet.adapters.monitoring import MonitoringAdapter
from vireonet.adapters.netbox import NetBoxAdapter
from vireonet.adapters.notification import NotificationAdapter
from vireonet.adapters.registry import build_adapters
from vireonet.adapters.servicenow import ServiceNowAdapter
from vireonet.adapters.vault import VaultAdapter

__all__ = [
    "AdapterAuthError",
    "AdapterConfig",
    "AdapterConflict",
    "AdapterError",
    "AdapterHealth",
    "AdapterNotFound",
    "AdapterRateLimited",
    "AdapterTimeout",
    "AdapterUnavailable",
    "AdapterValidationError",
    "AwxAdapter",
    "BaseAdapter",
    "BatfishAdapter",
    "EveNgAdapter",
    "GitLabAdapter",
    "MonitoringAdapter",
    "NetBoxAdapter",
    "NotificationAdapter",
    "ServiceNowAdapter",
    "VaultAdapter",
    "build_adapters",
]
