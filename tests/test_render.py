from __future__ import annotations

from vireonet.intent import parse_intent
from vireonet.render import render_candidate_configs, render_intent

BASE = {"devices": ["leaf-01"], "site": "dc1", "ticket_number": "RITM1", "requested_by": "alice"}


def test_render_vlan_add() -> None:
    intent = parse_intent(
        {
            **BASE,
            "change_type": "vlan_add",
            "vlan_id": 100,
            "vlan_name": "web",
            "interfaces": ["GigabitEthernet0/1"],
        }
    )
    lines = render_intent(intent)["leaf-01"]
    assert "vlan 100" in lines and " name web" in lines
    assert " switchport access vlan 100" in lines


def test_render_vlan_remove_uses_no() -> None:
    intent = parse_intent({**BASE, "change_type": "vlan_remove", "vlan_id": 100})
    assert render_intent(intent)["leaf-01"] == ["no vlan 100"]


def test_candidate_is_current_plus_intended() -> None:
    intent = parse_intent({**BASE, "change_type": "vlan_add", "vlan_id": 100, "vlan_name": "web"})
    out = render_candidate_configs(intent, {"leaf-01.cfg": "hostname leaf-01\n"})
    assert out["leaf-01.cfg"].startswith("hostname leaf-01\n")
    assert "vlan 100" in out["leaf-01.cfg"]
