from __future__ import annotations

from vireonet.intent import ChangeIntent


def render_intent(intent: ChangeIntent) -> dict[str, list[str]]:
    """Intent -> per-device config lines. One renderer for CI and the workflow.

    Add = merge lines. Delete = explicit `no` lines (see ansible/playbooks/apply.yml).
    """
    per_device: dict[str, list[str]] = {}
    for device in intent.devices:
        lines: list[str] = []
        if intent.change_type == "vlan_add":
            lines += [f"vlan {intent.vlan_id}", f" name {intent.vlan_name}"]  # type: ignore[attr-defined]
            for iface in getattr(intent, "interfaces", []):
                lines += [f"interface {iface}", " switchport mode access", f" switchport access vlan {intent.vlan_id}"]  # type: ignore[attr-defined]
        elif intent.change_type == "vlan_remove":
            lines += [f"no vlan {intent.vlan_id}"]  # type: ignore[attr-defined]
        elif intent.change_type == "acl_entry_add":
            lines += [f"ip access-list extended {intent.acl_name}", f" {intent.action} {intent.protocol} {intent.source} {intent.destination}" + (f" eq {intent.destination_port}" if getattr(intent, "destination_port", None) else "")]  # type: ignore[attr-defined]
        elif intent.change_type == "acl_entry_remove":
            lines += [f"ip access-list extended {intent.acl_name}", f" no {intent.sequence}"]  # type: ignore[attr-defined]
        elif intent.change_type == "static_route_add":
            lines += [f"ip route {intent.prefix} {intent.next_hop} {getattr(intent, 'admin_distance', 1)}"]  # type: ignore[attr-defined]
        elif intent.change_type == "static_route_remove":
            lines += [f"no ip route {intent.prefix} {intent.next_hop}"]  # type: ignore[attr-defined]
        elif intent.change_type == "interface_set":
            lines += [f"interface {intent.interface}"]  # type: ignore[attr-defined]
            if getattr(intent, "description", None):
                lines.append(f" description {intent.description}")  # type: ignore[attr-defined]
            if getattr(intent, "enabled", None) is False:
                lines.append(" shutdown")
            elif getattr(intent, "enabled", None) is True:
                lines.append(" no shutdown")
            if getattr(intent, "mtu", None):
                lines.append(f" mtu {intent.mtu}")  # type: ignore[attr-defined]
        elif intent.change_type == "ztp_device":
            lines += [f"hostname {device}", f"! ztp serial {intent.serial} model {intent.model}"]  # type: ignore[attr-defined]
        per_device[device] = lines
    return per_device


def render_candidate_configs(intent: ChangeIntent, current_configs: dict[str, str]) -> dict[str, str]:
    """Candidate snapshot for Batfish: current config + intended lines per device.

    File per device: `<device>.cfg`. Batfish needs full configs, not diffs.
    """
    rendered = render_intent(intent)
    out: dict[str, str] = {}
    for device, lines in rendered.items():
        current = current_configs.get(device, current_configs.get(f"{device}.cfg", ""))
        body = (current.rstrip() + "\n" if current else "") + "\n".join(lines) + "\n"
        out[f"{device}.cfg"] = body
    return out
