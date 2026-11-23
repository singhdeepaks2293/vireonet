"""SDLC — Requirements R2: intent parsing (M1).

All 8 discriminated intents parse; invalid intents are rejected.
No network, no DB.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from vireonet.intent import parse_intent

BASE = {
    "devices": ["leaf-01"],
    "site": "dc1",
    "ticket_number": "RITM0001234",
    "requested_by": "alice",
}


def _intent(**overrides):
    data = dict(BASE)
    data.update(overrides)
    return data


@pytest.mark.parametrize(
    "data",
    [
        _intent(
            change_type="vlan_add", vlan_id=100, vlan_name="web", interfaces=["GigabitEthernet0/1"]
        ),
        _intent(change_type="vlan_remove", vlan_id=100),
        _intent(
            change_type="acl_entry_add",
            acl_name="WEB",
            action="permit",
            protocol="tcp",
            source="10.0.0.0/24",
            destination="10.0.1.0/24",
            destination_port=443,
        ),
        _intent(change_type="acl_entry_remove", acl_name="WEB", sequence=10),
        _intent(change_type="static_route_add", prefix="10.1.0.0/24", next_hop="10.0.0.1"),
        _intent(change_type="static_route_remove", prefix="10.1.0.0/24"),
        _intent(
            change_type="interface_set",
            interface="GigabitEthernet0/1",
            description="uplink",
            enabled=True,
            mtu=9000,
        ),
        _intent(
            change_type="ztp_device",
            serial="FOC1234A",
            model="C9300-48P",
            platform="ios-xe",
            mac_address="00:11:22:33:44:55",
        ),
    ],
)
def test_all_eight_intents_parse(data: dict) -> None:
    intent = parse_intent(data)
    assert intent.change_type == data["change_type"]
    assert intent.ticket_number == "RITM0001234"
    assert intent.devices == ["leaf-01"]


def test_discriminator_selects_the_right_model() -> None:
    intent = parse_intent(_intent(change_type="vlan_add", vlan_id=100, vlan_name="web"))
    assert intent.vlan_id == 100
    assert intent.vlan_name == "web"
    route = parse_intent(
        _intent(change_type="static_route_add", prefix="10.1.0.0/24", next_hop="10.0.0.1")
    )
    assert str(route.prefix) == "10.1.0.0/24"
    assert str(route.next_hop) == "10.0.0.1"
    assert route.admin_distance == 1  # default


def test_unknown_change_type_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_intent(_intent(change_type="reboot_device"))


def test_missing_discriminator_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_intent(dict(BASE))


@pytest.mark.parametrize("bad", [{"vlan_id": 0}, {"vlan_id": 4095}, {"vlan_id": "not-a-number"}])
def test_vlan_id_range_enforced(bad: dict) -> None:
    with pytest.raises(ValidationError):
        parse_intent(_intent(change_type="vlan_add", vlan_name="web", **bad))


def test_empty_devices_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_intent(_intent(change_type="vlan_remove", vlan_id=100, devices=[]))


def test_blank_device_name_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_intent(_intent(change_type="vlan_remove", vlan_id=100, devices=[" "]))


def test_bad_mac_rejected_for_ztp() -> None:
    with pytest.raises(ValidationError):
        parse_intent(
            _intent(change_type="ztp_device", serial="FOC1", model="C9300", mac_address="not-a-mac")
        )


def test_bad_interface_pattern_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_intent(_intent(change_type="interface_set", interface="not an interface"))


def test_bad_prefix_and_next_hop_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_intent(
            _intent(change_type="static_route_add", prefix="10.1.0.0/33", next_hop="10.0.0.1")
        )
    with pytest.raises(ValidationError):
        parse_intent(
            _intent(change_type="static_route_add", prefix="10.1.0.0/24", next_hop="999.0.0.1")
        )


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        parse_intent(_intent(change_type="vlan_remove", vlan_id=100, surprise_field="x"))


def test_mtu_range_enforced() -> None:
    with pytest.raises(ValidationError):
        parse_intent(_intent(change_type="interface_set", interface="GigabitEthernet0/1", mtu=100))
    ok = parse_intent(
        _intent(change_type="interface_set", interface="GigabitEthernet0/1", mtu=9216)
    )
    assert ok.mtu == 9216
