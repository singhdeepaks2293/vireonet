from __future__ import annotations

from vireonet.adapters.fakes import FakeBatfishAdapter
from vireonet.batfish_gate import run_batfish_gate
from vireonet.intent import parse_intent

BASE = {"devices": ["leaf-01"], "site": "dc1", "ticket_number": "RITM1", "requested_by": "alice"}


async def test_gate_passes_clean_candidate() -> None:
    intent = parse_intent({**BASE, "change_type": "vlan_add", "vlan_id": 200, "vlan_name": "new"})
    report = await run_batfish_gate(
        intent, {"leaf-01.cfg": "hostname leaf-01\n"}, FakeBatfishAdapter()
    )
    assert report["passed"] is True
    assert report["files"] == ["leaf-01.cfg"]


async def test_gate_fails_on_undefined() -> None:
    intent = parse_intent({**BASE, "change_type": "vlan_add", "vlan_id": 200, "vlan_name": "new"})
    # Fake Batfish fails when a file contains UNDEFINED.
    report = await run_batfish_gate(intent, {"leaf-01.cfg": "UNDEFINED"}, FakeBatfishAdapter())
    # Candidate = current + intended lines, so UNDEFINED is in the candidate.
    assert report["passed"] is False
