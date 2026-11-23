from __future__ import annotations

from vireonet.ztp.templates import (
    encode_pnp_option43_hex,
    render_bootstrap,
    render_poap_script,
)


def test_ios_template_has_hostname_and_tftp() -> None:
    out = render_bootstrap(
        "ios", {"hostname": "leaf-01", "tftp_server": "10.0.0.5", "config_file": "leaf-01.cfg"}
    )
    assert "hostname leaf-01" in out and "tftp://10.0.0.5/leaf-01.cfg" in out


def test_iosxe_template_has_pnp_server_and_option43() -> None:
    out = render_bootstrap(
        "ios-xe",
        {
            "hostname": "leaf-01",
            "pnp_server_url": "https://pnp.example.com",
            "config_file": "leaf-01.cfg",
        },
    )
    assert "https://pnp.example.com" in out and "5A1N;" in out


def test_pnp_option43_hex_starts_with_5a() -> None:
    assert encode_pnp_option43_hex("https://pnp.example.com").startswith("5a")


def test_nxos_template_has_poap() -> None:
    out = render_bootstrap(
        "nxos", {"hostname": "leaf-01", "http_base_url": "http://10.0.0.5/ztp", "serial": "FOC1"}
    )
    assert "feature poap" in out


def test_poap_script_downloads_by_serial() -> None:
    out = render_poap_script({"serial": "FOC1", "http_base_url": "http://10.0.0.5/ztp"})
    assert "FOC1.cfg" in out and "urllib.request" in out


def test_unknown_platform_rejected() -> None:
    import pytest

    with pytest.raises(ValueError):
        render_bootstrap("junos", {"hostname": "x"})
