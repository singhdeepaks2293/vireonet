from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from vireonet.intent import parse_intent
from vireonet.policy import PolicyEngine

BASE = {"devices": ["leaf-01"], "site": "dc1", "ticket_number": "RITM1", "requested_by": "alice"}
# Tuesday 02:00 UTC — inside the default window.
INSIDE = datetime(2026, 11, 24, 2, 0, tzinfo=ZoneInfo("UTC"))  # a Tuesday
OUTSIDE = datetime(2026, 11, 24, 12, 0, tzinfo=ZoneInfo("UTC"))


def test_default_policy_from_yaml() -> None:
    p = PolicyEngine.from_yaml("policies/default.yaml")
    assert p.max_devices_per_change == 10
    assert "core-01" in p.protected_devices
    assert 1 in p.protected_vlans


def test_max_blast_radius_denied() -> None:
    p = PolicyEngine.from_yaml("policies/default.yaml")
    intent = parse_intent(
        {
            **BASE,
            "devices": [f"leaf-{i:02d}" for i in range(11)],
            "change_type": "vlan_add",
            "vlan_id": 100,
            "vlan_name": "web",
        }
    )
    assert "blast radius" in p.evaluate(intent, now=INSIDE).denials[0]


def test_protected_device_denied() -> None:
    p = PolicyEngine.from_yaml("policies/default.yaml")
    intent = parse_intent(
        {
            **BASE,
            "devices": ["core-01"],
            "change_type": "vlan_add",
            "vlan_id": 100,
            "vlan_name": "web",
        }
    )
    r = p.evaluate(intent, now=INSIDE)
    assert not r.allowed and any("core-01" in d for d in r.denials)


def test_change_window_denied_and_emergency_bypasses_only_window() -> None:
    p = PolicyEngine.from_yaml("policies/default.yaml")
    intent = parse_intent({**BASE, "change_type": "vlan_add", "vlan_id": 100, "vlan_name": "web"})
    assert not p.evaluate(intent, now=OUTSIDE).allowed
    assert p.evaluate(intent, now=OUTSIDE, emergency=True).allowed
    # Emergency does NOT bypass protected-device denial.
    bad = parse_intent(
        {
            **BASE,
            "devices": ["core-01"],
            "change_type": "vlan_add",
            "vlan_id": 100,
            "vlan_name": "web",
        }
    )
    assert not p.evaluate(bad, now=OUTSIDE, emergency=True).allowed


def test_protected_vlan_remove_denied() -> None:
    p = PolicyEngine.from_yaml("policies/default.yaml")
    intent = parse_intent({**BASE, "change_type": "vlan_remove", "vlan_id": 1})
    assert not p.evaluate(intent, now=INSIDE).allowed
