"""SDLC — Requirements R3: policy + validation (M2).

Protected devices/VLANs, change window, emergency bypass (window only),
max devices, uplink deletes. Validation against Fake NetBox (no network).
"""

from __future__ import annotations

from datetime import UTC, datetime

from vireonet.adapters.fakes import FakeNetBoxAdapter
from vireonet.intent import parse_intent
from vireonet.policy import PolicyEngine
from vireonet.validation import validate_intent

BASE = {
    "devices": ["leaf-01"],
    "site": "dc1",
    "ticket_number": "RITM0001234",
    "requested_by": "alice",
}
# 2026-11-23 is a Monday; 02:00 UTC is inside the default Mon-Fri 01:00-05:00 window.
IN_WINDOW = datetime(2026, 11, 23, 2, 0, tzinfo=UTC)
SATURDAY = datetime(2026, 11, 28, 2, 0, tzinfo=UTC)  # Saturday
OUTSIDE_HOURS = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)


def _policy() -> PolicyEngine:
    return PolicyEngine.from_yaml("policies/default.yaml")


def _intent(**overrides):
    data = dict(BASE)
    data.update(overrides)
    return parse_intent(data)


def test_default_policy_loads_from_yaml() -> None:
    policy = _policy()
    assert policy.max_devices_per_change == 10
    assert policy.protected_devices == {"core-01", "core-02", "fw-01"}
    assert 1 in policy.protected_vlans and 1002 in policy.protected_vlans
    assert policy.change_window_start == "01:00"
    assert policy.change_window_end == "05:00"


def test_in_window_allowed() -> None:
    result = _policy().evaluate(
        _intent(change_type="vlan_add", vlan_id=200, vlan_name="test"), now=IN_WINDOW
    )
    assert result.allowed
    assert result.denials == []


def test_saturday_denied() -> None:
    result = _policy().evaluate(
        _intent(change_type="vlan_add", vlan_id=200, vlan_name="test"), now=SATURDAY
    )
    assert not result.allowed
    assert any("change window" in d for d in result.denials)


def test_outside_hours_denied() -> None:
    result = _policy().evaluate(
        _intent(change_type="vlan_add", vlan_id=200, vlan_name="test"), now=OUTSIDE_HOURS
    )
    assert not result.allowed


def test_emergency_bypasses_window_only() -> None:
    intent = _intent(change_type="vlan_add", vlan_id=200, vlan_name="test")
    result = _policy().evaluate(intent, now=SATURDAY, emergency=True)
    assert result.allowed
    # Emergency does NOT bypass protected devices.
    protected = _intent(change_type="vlan_add", vlan_id=200, vlan_name="test", devices=["core-01"])
    result = _policy().evaluate(protected, now=SATURDAY, emergency=True)
    assert not result.allowed
    assert any("protected device" in d for d in result.denials)


def test_protected_device_denied() -> None:
    intent = _intent(
        change_type="vlan_add", vlan_id=200, vlan_name="test", devices=["core-01", "leaf-01"]
    )
    result = _policy().evaluate(intent, now=IN_WINDOW)
    assert not result.allowed
    assert any("core-01" in d for d in result.denials)


def test_protected_vlan_remove_denied() -> None:
    intent = _intent(change_type="vlan_remove", vlan_id=1)
    result = _policy().evaluate(intent, now=IN_WINDOW)
    assert not result.allowed
    assert any("VLAN 1 is protected" in d for d in result.denials)


def test_max_devices_denied() -> None:
    devices = [f"leaf-{i:02d}" for i in range(11)]
    intent = _intent(change_type="vlan_add", vlan_id=200, vlan_name="test", devices=devices)
    result = _policy().evaluate(intent, now=IN_WINDOW)
    assert not result.allowed
    assert any("blast radius" in d for d in result.denials)


def test_disabling_uplink_denied() -> None:
    intent = _intent(change_type="interface_set", interface="GigabitEthernet0/0", enabled=False)
    result = _policy().evaluate(intent, now=IN_WINDOW)
    assert not result.allowed
    assert any("uplink" in d for d in result.denials)


def test_enabling_uplink_allowed() -> None:
    intent = _intent(change_type="interface_set", interface="GigabitEthernet0/0", enabled=True)
    assert _policy().evaluate(intent, now=IN_WINDOW).allowed


async def test_validation_passes_for_active_device_in_window() -> None:
    intent = _intent(change_type="vlan_add", vlan_id=200, vlan_name="test")
    result = await validate_intent(intent, FakeNetBoxAdapter(), _policy(), now=IN_WINDOW)
    assert result.ok
    assert result.errors == []
    assert result.evidence["devices_checked"] == ["leaf-01"]


async def test_validation_fails_for_staged_device() -> None:
    intent = _intent(change_type="vlan_add", vlan_id=200, vlan_name="test", devices=["leaf-02"])
    result = await validate_intent(intent, FakeNetBoxAdapter(), _policy(), now=IN_WINDOW)
    assert not result.ok
    assert any("leaf-02 is staged" in e for e in result.errors)


async def test_validation_fails_for_unknown_device() -> None:
    intent = _intent(change_type="vlan_add", vlan_id=200, vlan_name="test", devices=["ghost-01"])
    result = await validate_intent(intent, FakeNetBoxAdapter(), _policy(), now=IN_WINDOW)
    assert not result.ok
    assert any("ghost-01" in e for e in result.errors)


async def test_validation_fails_when_vlan_name_conflicts() -> None:
    intent = _intent(change_type="vlan_add", vlan_id=100, vlan_name="different")
    result = await validate_intent(intent, FakeNetBoxAdapter(), _policy(), now=IN_WINDOW)
    assert not result.ok
    assert any("already exists as 'web'" in e for e in result.errors)


async def test_validation_fails_for_missing_vlan_remove() -> None:
    intent = _intent(change_type="vlan_remove", vlan_id=999)
    result = await validate_intent(intent, FakeNetBoxAdapter(), _policy(), now=IN_WINDOW)
    assert not result.ok
    assert any("does not exist" in e for e in result.errors)


async def test_validation_reports_policy_denials_in_evidence() -> None:
    intent = _intent(change_type="vlan_add", vlan_id=200, vlan_name="test")
    result = await validate_intent(intent, FakeNetBoxAdapter(), _policy(), now=SATURDAY)
    assert not result.ok
    assert result.evidence["policy_denials"]
