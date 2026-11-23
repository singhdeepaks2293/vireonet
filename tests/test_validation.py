from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from vireonet.adapters.fakes import FakeNetBoxAdapter
from vireonet.intent import parse_intent
from vireonet.policy import PolicyEngine
from vireonet.validation import validate_intent

BASE = {"devices": ["leaf-01"], "site": "dc1", "ticket_number": "RITM1", "requested_by": "alice"}
NOW = datetime(2026, 11, 24, 2, 0, tzinfo=ZoneInfo("UTC"))


async def test_vlan_add_valid() -> None:
    intent = parse_intent({**BASE, "change_type": "vlan_add", "vlan_id": 200, "vlan_name": "new"})
    r = await validate_intent(intent, FakeNetBoxAdapter(), PolicyEngine.from_yaml("policies/default.yaml"), now=NOW)
    assert r.ok, r.errors


async def test_vlan_add_name_conflict() -> None:
    intent = parse_intent({**BASE, "change_type": "vlan_add", "vlan_id": 100, "vlan_name": "other"})
    r = await validate_intent(intent, FakeNetBoxAdapter(), PolicyEngine.default(), now=NOW)
    assert not r.ok and any("already exists" in e for e in r.errors)


async def test_missing_device_rejected() -> None:
    intent = parse_intent({**BASE, "devices": ["ghost-01"], "change_type": "vlan_add", "vlan_id": 200, "vlan_name": "new"})
    r = await validate_intent(intent, FakeNetBoxAdapter(), PolicyEngine.default(), now=NOW)
    assert not r.ok and any("ghost-01" in e for e in r.errors)


async def test_staged_device_rejected_for_change_but_ok_for_ztp() -> None:
    # leaf-02 is staged in the Fake.
    intent = parse_intent({**BASE, "devices": ["leaf-02"], "change_type": "vlan_add", "vlan_id": 200, "vlan_name": "new"})
    r = await validate_intent(intent, FakeNetBoxAdapter(), PolicyEngine.default(), now=NOW)
    assert not r.ok
    ztp = parse_intent({**BASE, "devices": ["leaf-02"], "change_type": "ztp_device", "serial": "NEW123", "model": "C9300", "mac_address": "aa:bb:cc:dd:ee:ff"})
    r2 = await validate_intent(ztp, FakeNetBoxAdapter(), PolicyEngine.default(), now=NOW)
    assert r2.ok, r2.errors
