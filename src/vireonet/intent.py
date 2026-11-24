from __future__ import annotations

import re
from ipaddress import IPv4Address, IPv4Network
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# Interface names across vendors: GigabitEthernet0/1, Ethernet1/10, et-0/0/1,
# ethernet-1/1 (SR Linux), 1/1/1 (SR OS), 1:1 (EXOS), port1 (FortiOS),
# ethernet1/1 (PAN-OS). No spaces or quotes, so a name cannot inject CLI.
INTERFACE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9/:._-]{0,63}$"


class IntentBase(BaseModel):
    model_config = {"extra": "forbid"}

    devices: list[str] = Field(min_length=1, description="Device hostnames from NetBox")
    site: str = Field(min_length=1)
    ticket_number: str = Field(min_length=1, description="ServiceNow ticket, e.g. RITM0001234")
    requested_by: str = Field(min_length=1)
    # Optional Pre/Post check profile: snapshot before, snapshot after, run its
    # tests and diff; a failed test fails the post-check (and rolls back).
    check_profile: str = Field(default="", max_length=128)

    @field_validator("devices")
    @classmethod
    def _non_empty_devices(cls, v: list[str]) -> list[str]:
        if any(not d.strip() for d in v):
            raise ValueError("device names must be non-empty")
        return v


class VlanAddIntent(IntentBase):
    change_type: Literal["vlan_add"] = "vlan_add"
    vlan_id: int = Field(ge=1, le=4094)
    vlan_name: str = Field(min_length=1, max_length=32)
    interfaces: list[str] = Field(default_factory=list)

    @field_validator("interfaces")
    @classmethod
    def _valid_interfaces(cls, v: list[str]) -> list[str]:
        bad = [i for i in v if not re.fullmatch(INTERFACE_PATTERN, i)]
        if bad:
            raise ValueError(f"invalid interface names: {bad}")
        return v


class VlanRemoveIntent(IntentBase):
    change_type: Literal["vlan_remove"] = "vlan_remove"
    vlan_id: int = Field(ge=1, le=4094)
    # Junos deletes VLANs by name; optional for Cisco/Arista.
    vlan_name: str | None = Field(default=None, min_length=1, max_length=32)


class AclEntryAddIntent(IntentBase):
    change_type: Literal["acl_entry_add"] = "acl_entry_add"
    acl_name: str
    action: Literal["permit", "deny"]
    protocol: Literal["tcp", "udp", "icmp", "ip"] = "tcp"
    source: str
    destination: str
    destination_port: int | None = Field(default=None, ge=1, le=65535)


class AclEntryRemoveIntent(IntentBase):
    change_type: Literal["acl_entry_remove"] = "acl_entry_remove"
    acl_name: str
    sequence: int = Field(ge=1)


class StaticRouteAddIntent(IntentBase):
    change_type: Literal["static_route_add"] = "static_route_add"
    prefix: IPv4Network
    next_hop: IPv4Address
    vrf: str = "default"
    admin_distance: int = Field(default=1, ge=1, le=255)


class StaticRouteRemoveIntent(IntentBase):
    change_type: Literal["static_route_remove"] = "static_route_remove"
    prefix: IPv4Network
    vrf: str = "default"
    # NX-OS needs the next hop to remove a route; optional elsewhere.
    next_hop: IPv4Address | None = None


class InterfaceSetIntent(IntentBase):
    change_type: Literal["interface_set"] = "interface_set"
    interface: str = Field(pattern=INTERFACE_PATTERN)
    description: str | None = None
    enabled: bool | None = None
    mtu: int | None = Field(default=None, ge=576, le=9216)


class ZtpDeviceIntent(IntentBase):
    change_type: Literal["ztp_device"] = "ztp_device"
    serial: str = Field(min_length=1)
    model: str = Field(min_length=1)
    platform: Literal["ios", "ios-xe", "nxos"] = "ios-xe"
    mac_address: str = Field(pattern=r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


class ConfigPushIntent(IntentBase):
    """Raw device config from an Excel workbook (ITSM attachment or upload).

    Replaces only the render step: the change still goes through approval,
    locks, backup, dry-run, waves, post-checks and automatic rollback.
    """

    change_type: Literal["config_push"] = "config_push"
    # device -> config block to push (from the "config" sheet).
    configs: dict[str, str] = Field(min_length=1)
    # device -> config to push to undo (the "backup" sheet's Rollback config).
    rollback_configs: dict[str, str] = Field(default_factory=dict)
    source: Literal["excel"] = "excel"
    source_file: str = ""
    itsm: str = ""
    # ExcelWorkbook row holding the original workbook (kept out of the intent
    # itself: workflow payloads must stay small).
    workbook_id: str = ""
    # Gates (see ChangeWorkflowInput): Excel pushes skip CI (no merge request);
    # pre_approved = end-to-end automation, the ITSM state is the approval.
    skip_ci: bool = True
    pre_approved: bool = False
    approval_note: str = ""
    # Planned window (ISO 8601 UTC). Nothing touches a device before the start;
    # after the end nothing new starts and a partial rollout is rolled back.
    window_start: str = ""
    window_end: str = ""

    @model_validator(mode="after")
    def _devices_match(self) -> ConfigPushIntent:
        if set(self.configs) != set(self.devices):
            raise ValueError("configs must have exactly one entry per device")
        extra = set(self.rollback_configs) - set(self.devices)
        if extra:
            raise ValueError(f"rollback config for devices not in the change: {sorted(extra)}")
        for d, text in {**self.configs, **self.rollback_configs}.items():
            if any(ord(c) < 32 and c not in "\n\t" for c in text):
                raise ValueError(f"{d}: config contains control characters")
        return self


ChangeIntent = Annotated[
    VlanAddIntent
    | VlanRemoveIntent
    | AclEntryAddIntent
    | AclEntryRemoveIntent
    | StaticRouteAddIntent
    | StaticRouteRemoveIntent
    | InterfaceSetIntent
    | ZtpDeviceIntent
    | ConfigPushIntent,
    Field(discriminator="change_type"),
]


def parse_intent(data: dict[str, Any]) -> ChangeIntent:
    """Parse raw webhook/MR payload into a validated intent. Raises ValidationError."""
    # Pydantic discriminated union: adapter validates the discriminator field.
    from pydantic import TypeAdapter

    return TypeAdapter(ChangeIntent).validate_python(data)
