from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator


class IntentBase(BaseModel):
    model_config = {"extra": "forbid"}

    devices: list[str] = Field(min_length=1, description="Device hostnames from NetBox")
    site: str = Field(min_length=1)
    ticket_number: str = Field(min_length=1, description="ServiceNow ticket, e.g. RITM0001234")
    requested_by: str = Field(min_length=1)

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


class VlanRemoveIntent(IntentBase):
    change_type: Literal["vlan_remove"] = "vlan_remove"
    vlan_id: int = Field(ge=1, le=4094)


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


class InterfaceSetIntent(IntentBase):
    change_type: Literal["interface_set"] = "interface_set"
    interface: str = Field(pattern=r"^[A-Za-z]+[0-9/]+$")
    description: str | None = None
    enabled: bool | None = None
    mtu: int | None = Field(default=None, ge=576, le=9216)


class ZtpDeviceIntent(IntentBase):
    change_type: Literal["ztp_device"] = "ztp_device"
    serial: str = Field(min_length=1)
    model: str = Field(min_length=1)
    platform: Literal["ios", "ios-xe", "nxos"] = "ios-xe"
    mac_address: str = Field(pattern=r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


ChangeIntent = Annotated[
    VlanAddIntent | VlanRemoveIntent | AclEntryAddIntent | AclEntryRemoveIntent | StaticRouteAddIntent | StaticRouteRemoveIntent | InterfaceSetIntent | ZtpDeviceIntent,
    Field(discriminator="change_type"),
]


def parse_intent(data: dict) -> ChangeIntent:
    """Parse raw webhook/MR payload into a validated intent. Raises ValidationError."""
    # Pydantic discriminated union: adapter validates the discriminator field.
    from pydantic import TypeAdapter

    return TypeAdapter(ChangeIntent).validate_python(data)
