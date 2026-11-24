"""Built-in renderers: Cisco IOS / IOS-XE, NX-OS, Arista EOS, Juniper Junos.

Each returns the config lines for one device. Cisco/Arista use hierarchical
CLI (child lines indented one space); Junos uses set/delete lines.
"""

from __future__ import annotations

import hashlib
import ipaddress
from typing import Any

from vireonet.rendering.base import UnsupportedChange


def _ios_addr(value: str) -> str:
    """ACL address for IOS: any | host A | NET WILDCARD."""
    v = value.strip()
    if v.lower() == "any" or v.lower().startswith("host "):
        return v
    net = ipaddress.ip_network(v, strict=False)
    if net.prefixlen == net.max_prefixlen:
        return f"host {net.network_address}"
    return f"{net.network_address} {net.hostmask}"


def _cidr_addr(value: str) -> str:
    """ACL address for NX-OS / EOS: any | host A | A/len."""
    v = value.strip()
    if v.lower() == "any" or v.lower().startswith("host "):
        return v
    net = ipaddress.ip_network(v, strict=False)
    return str(net)


def _port(i: Any) -> str:
    return f" eq {i.destination_port}" if getattr(i, "destination_port", None) else ""


# --- Cisco IOS / IOS-XE ----------------------------------------------------


def render_ios(i: Any, device: str) -> list[str]:
    ct = i.change_type
    if ct == "vlan_add":
        lines = [f"vlan {i.vlan_id}", f" name {i.vlan_name}"]
        for iface in i.interfaces:
            lines += [
                f"interface {iface}",
                " switchport mode access",
                f" switchport access vlan {i.vlan_id}",
            ]
        return lines
    if ct == "vlan_remove":
        return [f"no vlan {i.vlan_id}"]
    if ct == "acl_entry_add":
        return [
            f"ip access-list extended {i.acl_name}",
            f" {i.action} {i.protocol} {_ios_addr(i.source)} {_ios_addr(i.destination)}{_port(i)}",
        ]
    if ct == "acl_entry_remove":
        return [f"ip access-list extended {i.acl_name}", f" no {i.sequence}"]
    if ct == "static_route_add":
        vrf = f"vrf {i.vrf} " if i.vrf != "default" else ""
        ad = f" {i.admin_distance}" if i.admin_distance != 1 else ""
        return [f"ip route {vrf}{i.prefix.network_address} {i.prefix.netmask} {i.next_hop}{ad}"]
    if ct == "static_route_remove":
        vrf = f"vrf {i.vrf} " if i.vrf != "default" else ""
        nh = f" {i.next_hop}" if getattr(i, "next_hop", None) else ""
        return [f"no ip route {vrf}{i.prefix.network_address} {i.prefix.netmask}{nh}"]
    if ct == "interface_set":
        return _cisco_interface(i, indent=" ")
    if ct == "ztp_device":
        return [f"hostname {device}"]
    raise UnsupportedChange(f"ios: no renderer for {ct}")


def _cisco_interface(i: Any, indent: str) -> list[str]:
    lines = [f"interface {i.interface}"]
    if i.description is not None:
        lines.append(f"{indent}description {i.description}")
    if i.enabled is False:
        lines.append(f"{indent}shutdown")
    elif i.enabled is True:
        lines.append(f"{indent}no shutdown")
    if i.mtu:
        lines.append(f"{indent}mtu {i.mtu}")
    return lines


# --- Cisco NX-OS -----------------------------------------------------------


def render_nxos(i: Any, device: str) -> list[str]:
    ct = i.change_type
    if ct == "vlan_add":
        lines = [f"vlan {i.vlan_id}", f"  name {i.vlan_name}"]
        for iface in i.interfaces:
            lines += [
                f"interface {iface}",
                "  switchport",
                "  switchport mode access",
                f"  switchport access vlan {i.vlan_id}",
            ]
        return lines
    if ct == "vlan_remove":
        return [f"no vlan {i.vlan_id}"]
    if ct == "acl_entry_add":
        return [
            f"ip access-list {i.acl_name}",
            f"  {i.action} {i.protocol} {_cidr_addr(i.source)} {_cidr_addr(i.destination)}{_port(i)}",
        ]
    if ct == "acl_entry_remove":
        return [f"ip access-list {i.acl_name}", f"  no {i.sequence}"]
    if ct == "static_route_add":
        ad = f" {i.admin_distance}" if i.admin_distance != 1 else ""
        route = f"ip route {i.prefix} {i.next_hop}{ad}"
        return [f"vrf context {i.vrf}", f"  {route}"] if i.vrf != "default" else [route]
    if ct == "static_route_remove":
        if not getattr(i, "next_hop", None):
            raise UnsupportedChange("nxos: removing a static route needs next_hop in the intent")
        route = f"no ip route {i.prefix} {i.next_hop}"
        return [f"vrf context {i.vrf}", f"  {route}"] if i.vrf != "default" else [route]
    if ct == "interface_set":
        return _cisco_interface(i, indent="  ")
    if ct == "ztp_device":
        return [f"hostname {device}"]
    raise UnsupportedChange(f"nxos: no renderer for {ct}")


# --- Arista EOS ------------------------------------------------------------


def render_eos(i: Any, device: str) -> list[str]:
    ct = i.change_type
    if ct == "vlan_add":
        lines = [f"vlan {i.vlan_id}", f"   name {i.vlan_name}"]
        for iface in i.interfaces:
            lines += [
                f"interface {iface}",
                "   switchport mode access",
                f"   switchport access vlan {i.vlan_id}",
            ]
        return lines
    if ct == "vlan_remove":
        return [f"no vlan {i.vlan_id}"]
    if ct == "acl_entry_add":
        return [
            f"ip access-list {i.acl_name}",
            f"   {i.action} {i.protocol} {_cidr_addr(i.source)} {_cidr_addr(i.destination)}{_port(i)}",
        ]
    if ct == "acl_entry_remove":
        return [f"ip access-list {i.acl_name}", f"   no {i.sequence}"]
    if ct == "static_route_add":
        vrf = f"vrf {i.vrf} " if i.vrf != "default" else ""
        ad = f" {i.admin_distance}" if i.admin_distance != 1 else ""
        return [f"ip route {vrf}{i.prefix} {i.next_hop}{ad}"]
    if ct == "static_route_remove":
        vrf = f"vrf {i.vrf} " if i.vrf != "default" else ""
        nh = f" {i.next_hop}" if getattr(i, "next_hop", None) else ""
        return [f"no ip route {vrf}{i.prefix}{nh}"]
    if ct == "interface_set":
        return _cisco_interface(i, indent="   ")
    if ct == "ztp_device":
        return [f"hostname {device}"]
    raise UnsupportedChange(f"eos: no renderer for {ct}")


# --- Juniper Junos (set format) -----------------------------------------------


def _junos_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _junos_term(i: Any) -> str:
    key = f"{i.action}|{i.protocol}|{i.source}|{i.destination}|{getattr(i, 'destination_port', '')}"
    return "vn-" + hashlib.sha256(key.encode()).hexdigest()[:8]


def render_junos(i: Any, device: str) -> list[str]:
    ct = i.change_type
    if ct == "vlan_add":
        lines = [f"set vlans {i.vlan_name} vlan-id {i.vlan_id}"]
        for iface in i.interfaces:
            base = f"set interfaces {iface} unit 0 family ethernet-switching"
            lines += [f"{base} interface-mode access", f"{base} vlan members {i.vlan_name}"]
        return lines
    if ct == "vlan_remove":
        if not getattr(i, "vlan_name", None):
            raise UnsupportedChange("junos: removing a VLAN needs vlan_name in the intent")
        return [f"delete vlans {i.vlan_name}"]
    if ct == "acl_entry_add":
        base = f"set firewall family inet filter {i.acl_name} term {_junos_term(i)}"
        lines = []
        for addr, kind in ((i.source, "source-address"), (i.destination, "destination-address")):
            a = addr.strip()
            if a.lower() != "any":
                a = a.removeprefix("host ").strip()
                lines.append(f"{base} from {kind} {ipaddress.ip_network(a, strict=False)}")
        if i.protocol != "ip":
            lines.append(f"{base} from protocol {i.protocol}")
        if getattr(i, "destination_port", None):
            lines.append(f"{base} from destination-port {i.destination_port}")
        lines.append(f"{base} then {'accept' if i.action == 'permit' else 'discard'}")
        return lines
    if ct == "acl_entry_remove":
        raise UnsupportedChange(
            "junos: filter terms are named, not numbered; add a render template for "
            "acl_entry_remove/junos (policies/render_templates.yaml)"
        )
    if ct == "static_route_add":
        scope = (
            f"routing-instances {i.vrf} routing-options"
            if i.vrf != "default"
            else "routing-options"
        )
        lines = [f"set {scope} static route {i.prefix} next-hop {i.next_hop}"]
        if i.admin_distance != 1:
            lines.append(f"set {scope} static route {i.prefix} preference {i.admin_distance}")
        return lines
    if ct == "static_route_remove":
        scope = (
            f"routing-instances {i.vrf} routing-options"
            if i.vrf != "default"
            else "routing-options"
        )
        return [f"delete {scope} static route {i.prefix}"]
    if ct == "interface_set":
        lines = []
        if i.description is not None:
            lines.append(f"set interfaces {i.interface} description {_junos_quote(i.description)}")
        if i.enabled is False:
            lines.append(f"set interfaces {i.interface} disable")
        elif i.enabled is True:
            lines.append(f"delete interfaces {i.interface} disable")
        if i.mtu:
            lines.append(f"set interfaces {i.interface} mtu {i.mtu}")
        return lines
    if ct == "ztp_device":
        return [f"set system host-name {device}"]
    raise UnsupportedChange(f"junos: no renderer for {ct}")


BUILTIN = {
    "ios": (render_ios, "cli"),
    "ios-xe": (render_ios, "cli"),
    "nxos": (render_nxos, "cli"),
    "eos": (render_eos, "cli"),
    "junos": (render_junos, "set"),
}
