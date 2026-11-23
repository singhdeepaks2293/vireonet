from __future__ import annotations

from dataclasses import dataclass, field

from vireonet.intent import ChangeIntent


@dataclass
class Plan:
    devices: list[str] = field(default_factory=list)
    waves: list[list[str]] = field(default_factory=list)
    blast_radius: int = 0
    diff_summary: str = ""
    steps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "devices": self.devices,
            "waves": self.waves,
            "blast_radius": self.blast_radius,
            "diff_summary": self.diff_summary,
            "steps": self.steps,
        }


def plan(intent: ChangeIntent, *, wave_size: int = 2) -> Plan:
    """Minimal planner for M3. Full diff rendering / ordering is M4.

    Waves are sequential; devices inside a wave run in parallel. Wave size
    2 keeps blast radius small by default.
    """
    waves = [
        list(intent.devices[i : i + wave_size]) for i in range(0, len(intent.devices), wave_size)
    ]
    summaries = {
        "vlan_add": f"add VLAN {getattr(intent, 'vlan_id', '?')} ({getattr(intent, 'vlan_name', '')})",
        "vlan_remove": f"remove VLAN {getattr(intent, 'vlan_id', '?')}",
        "acl_entry_add": f"add ACL entry to {getattr(intent, 'acl_name', '')}",
        "acl_entry_remove": f"remove ACL entry {getattr(intent, 'sequence', '')} from {getattr(intent, 'acl_name', '')}",
        "static_route_add": f"add route {getattr(intent, 'prefix', '')} via {getattr(intent, 'next_hop', '')}",
        "static_route_remove": f"remove route {getattr(intent, 'prefix', '')}",
        "interface_set": f"set {getattr(intent, 'interface', '')}",
        "ztp_device": f"provision {intent.devices[0]} ({getattr(intent, 'serial', '')})",
    }
    return Plan(
        devices=list(intent.devices),
        waves=waves,
        blast_radius=len(intent.devices),
        diff_summary=summaries.get(intent.change_type, intent.change_type),
        steps=["backup", "pre-check", "dry-run", "apply", "post-check"],
    )
