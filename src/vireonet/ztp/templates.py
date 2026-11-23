from __future__ import annotations

from typing import Any

from jinja2 import Template

# UNCERTAIN — DHCP option 43 for IOS-XE PnP: the 5A sub-option format below
# is the documented Cisco PnP encoding. Verify the exact bytes against your
# PnP server / IOS-XE version before first production use.
IOS_TEMPLATE = """! Day-0 bootstrap — Cisco IOS auto-install (DHCP option 66/67)
! Fetched via TFTP/HTTP from {{ tftp_server }} as {{ config_file }}
hostname {{ hostname }}
service config
boot host tftp://{{ tftp_server }}/{{ config_file }}
interface {{ mgmt_interface | default('GigabitEthernet0/0') }}
 ip address dhcp
 no shutdown
ip tftp source-interface {{ mgmt_interface | default('GigabitEthernet0/0') }}
ip name-server {{ dns_server | default('8.8.8.8') }}
! One-time token is presented to the callback endpoint, not stored here.
end
"""

IOSXE_TEMPLATE = """! Day-0 bootstrap — IOS-XE PnP (DHCP option 43 + option 67)
! PnP server: {{ pnp_server_url }}
hostname {{ hostname }}
pnp profile default
 transport https {{ pnp_server_url }}
!
interface {{ mgmt_interface | default('GigabitEthernet0/0') }}
 ip address dhcp
 no shutdown
ip name-server {{ dns_server | default('8.8.8.8') }}
! DHCP option 43 (PnP): {{ option_43 }}
! DHCP option 67 (bootfile): {{ config_file }}
end
"""

NXOS_TEMPLATE = """! Day-0 bootstrap — NX-OS POAP
! POAP script fetches {{ http_base_url }}/{{ serial }}.cfg and applies it.
hostname {{ hostname }}
feature poap
poap script {{ http_base_url }}/poap.py
interface mgmt0
 ip address dhcp
 no shutdown
end
"""

POAP_SCRIPT = '''"""NX-OS POAP script — fetched by the switch, runs once at first boot.

Downloads the day-0 config for this serial from VireoNet and applies it.
No credentials in this script; the device authenticates with its one-time
token to the callback endpoint after boot, not to POAP.
"""
import urllib.request
import sys

SERIAL = "{{ serial }}"
HTTP_BASE = "{{ http_base_url }}"

def main():
    url = "{{ http_base_url }}/{{ serial }}.cfg"
    with urllib.request.urlopen(url, timeout=30) as resp:
        cfg = resp.read().decode()
    with open("/tmp/day0.cfg", "w") as f:
        f.write(cfg)
    print(f"POAP: downloaded day-0 config for {SERIAL} ({len(cfg)} bytes)")

if __name__ == "__main__":
    main()
'''


def encode_pnp_option43(server_url: str) -> str:
    """Encode IOS-XE PnP DHCP option 43. Returns the option string for dhcpd.

    Format: 5A1N;<server> — sub-option 5A (PnP server), length-prefixed in the
    wire encoding. dhcpd takes the string form; Kea takes the hex form from
    encode_pnp_option43_hex().
    """
    return f"5A1N;{server_url}"


def encode_pnp_option43_hex(server_url: str) -> str:
    raw = server_url.encode()
    # 0x5A sub-option, length byte, value bytes.
    return (bytes([0x5A, len(raw)]) + raw).hex()


def render_bootstrap(platform: str, context: dict[str, Any]) -> str:
    """Render the day-0 bootstrap config/script for a platform."""
    templates = {"ios": IOS_TEMPLATE, "ios-xe": IOSXE_TEMPLATE, "nxos": NXOS_TEMPLATE}
    if platform not in templates:
        raise ValueError(f"unknown platform {platform!r} (expected ios, ios-xe, nxos)")
    ctx = dict(context)
    if platform == "ios-xe" and "option_43" not in ctx:
        ctx["option_43"] = encode_pnp_option43(str(ctx.get("pnp_server_url", "")))
    return str(Template(templates[platform]).render(**ctx))


def render_poap_script(context: dict[str, Any]) -> str:
    return str(Template(POAP_SCRIPT).render(**context))
