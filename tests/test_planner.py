from __future__ import annotations

from vireonet.intent import parse_intent
from vireonet.planner import plan

BASE = {"devices": ["leaf-01", "leaf-02", "leaf-03"], "site": "dc1", "ticket_number": "RITM1", "requested_by": "alice"}


def test_plan_waves_and_blast_radius() -> None:
    intent = parse_intent({**BASE, "change_type": "vlan_add", "vlan_id": 100, "vlan_name": "web"})
    p = plan(intent, wave_size=2)
    assert p.waves == [["leaf-01", "leaf-02"], ["leaf-03"]]
    assert p.blast_radius == 3
    assert "100" in p.diff_summary
