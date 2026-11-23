from __future__ import annotations

from vireonet.adapters.dhcp import DhcpAdapter
from vireonet.adapters.fakes import FakeDhcpAdapter
from vireonet.adapters.registry import build_adapters
from vireonet.config import Settings


def test_fake_mode_dhcp_is_fake() -> None:
    adapters = build_adapters(Settings(adapter_mode="fake"))
    assert isinstance(adapters["dhcp"], FakeDhcpAdapter)


def test_real_mode_dhcp_is_real_kea() -> None:
    adapters = build_adapters(
        Settings(adapter_mode="real", dhcp_server="kea", dhcp_tftp_server="10.0.0.9")
    )
    dhcp = adapters["dhcp"]
    assert isinstance(dhcp, DhcpAdapter)
    assert dhcp.server == "kea"
    assert dhcp.tftp_server == "10.0.0.9"
    assert dhcp._netbox is not None
    assert dhcp._awx is not None
    assert dhcp._gitlab is not None
    assert "remove_reservation" in dhcp.capabilities()
