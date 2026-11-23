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


def test_vlan_add_parses() -> None:
    intent = parse_intent({**BASE, "change_type": "vlan_add", "vlan_id": 100, "vlan_name": "web"})
    assert intent.change_type == "vlan_add"
    assert intent.vlan_id == 100  # type: ignore[attr-defined]


def test_vlan_id_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_intent({**BASE, "change_type": "vlan_add", "vlan_id": 5000, "vlan_name": "web"})


def test_unknown_change_type_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_intent({**BASE, "change_type": "reboot_everything"})


def test_static_route_validates_ip() -> None:
    with pytest.raises(ValidationError):
        parse_intent(
            {
                **BASE,
                "change_type": "static_route_add",
                "prefix": "not-a-prefix",
                "next_hop": "10.0.0.1",
            }
        )
    ok = parse_intent(
        {**BASE, "change_type": "static_route_add", "prefix": "10.1.0.0/16", "next_hop": "10.0.0.1"}
    )
    assert str(ok.prefix) == "10.1.0.0/16"  # type: ignore[attr-defined]


def test_ztp_mac_validated() -> None:
    with pytest.raises(ValidationError):
        parse_intent(
            {
                **BASE,
                "change_type": "ztp_device",
                "serial": "FOC1",
                "model": "C9300",
                "mac_address": "bad",
            }
        )
    ok = parse_intent(
        {
            **BASE,
            "change_type": "ztp_device",
            "serial": "FOC1",
            "model": "C9300",
            "mac_address": "aa:bb:cc:dd:ee:ff",
        }
    )
    assert ok.serial == "FOC1"  # type: ignore[attr-defined]


def test_empty_devices_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_intent({**BASE, "devices": [], "change_type": "vlan_remove", "vlan_id": 100})
