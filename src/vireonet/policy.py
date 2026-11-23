from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from vireonet.intent import ChangeIntent


@dataclass
class PolicyResult:
    allowed: bool
    denials: list[str] = field(default_factory=list)


@dataclass
class PolicyEngine:
    max_devices_per_change: int = 10
    protected_devices: set[str] = field(default_factory=set)
    protected_vlans: set[int] = field(default_factory=set)
    uplink_interfaces: set[str] = field(default_factory=set)
    change_window_days: set[str] = field(default_factory=set)
    change_window_start: str = "01:00"
    change_window_end: str = "05:00"
    change_window_tz: str = "UTC"
    deny_delete_on_uplink: bool = True
    deny_vlan_remove_if_in_use: bool = True

    @classmethod
    def from_yaml(cls, path: str | Path) -> PolicyEngine:
        data = yaml.safe_load(Path(path).read_text()) or {}
        cw = data.get("change_window") or {}
        return cls(
            max_devices_per_change=int(data.get("max_devices_per_change", 10)),
            protected_devices=set(data.get("protected_devices") or []),
            protected_vlans={int(v) for v in (data.get("protected_vlans") or [])},
            uplink_interfaces=set(data.get("uplink_interfaces") or []),
            change_window_days=set(cw.get("days") or []),
            change_window_start=str(cw.get("start", "01:00")),
            change_window_end=str(cw.get("end", "05:00")),
            change_window_tz=str(cw.get("timezone", "UTC")),
            deny_delete_on_uplink=bool(data.get("deny_delete_on_uplink", True)),
            deny_vlan_remove_if_in_use=bool(data.get("deny_vlan_remove_if_in_use", True)),
        )

    @classmethod
    def default(cls) -> PolicyEngine:
        return cls()

    def evaluate(
        self, intent: ChangeIntent, *, now: datetime | None = None, emergency: bool = False
    ) -> PolicyResult:
        denials: list[str] = []
        if len(intent.devices) > self.max_devices_per_change:
            denials.append(
                f"blast radius: {len(intent.devices)} devices exceeds max {self.max_devices_per_change}"
            )
        protected = sorted(set(intent.devices) & self.protected_devices)
        if protected:
            denials.append(f"protected device(s): {', '.join(protected)}")
        # Deletes on uplinks, and protected VLANs, are denied regardless of window.
        if intent.change_type in {"vlan_remove", "acl_entry_remove", "static_route_remove"}:
            if self.deny_delete_on_uplink and hasattr(intent, "interfaces"):
                uplinks = sorted(set(getattr(intent, "interfaces", [])) & self.uplink_interfaces)
                if uplinks:
                    denials.append(f"delete on uplink interface(s): {', '.join(uplinks)}")
            if intent.change_type == "vlan_remove" and getattr(intent, "vlan_id", None) in self.protected_vlans:
                denials.append(f"VLAN {intent.vlan_id} is protected")  # type: ignore[attr-defined]
        if intent.change_type == "interface_set":
            if getattr(intent, "interface", None) in self.uplink_interfaces and getattr(intent, "enabled", None) is False:
                denials.append(f"disabling uplink interface {intent.interface} is denied")  # type: ignore[attr-defined]
        if not emergency and self.change_window_days:
            when = now or datetime.now(ZoneInfo(self.change_window_tz))
            if when.astimezone(ZoneInfo(self.change_window_tz)).strftime("%a") not in self.change_window_days:
                denials.append("outside change window: weekday not allowed")
            else:
                hhmm = when.astimezone(ZoneInfo(self.change_window_tz)).strftime("%H:%M")
                if not (self.change_window_start <= hhmm < self.change_window_end):
                    denials.append(
                        f"outside change window: {hhmm} not in {self.change_window_start}-{self.change_window_end} {self.change_window_tz}"
                    )
        return PolicyResult(allowed=not denials, denials=denials)
