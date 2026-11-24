"""Platform names. NetBox platform slugs vary by site; normalise them once."""

from __future__ import annotations

# Built-in (Python) renderers.
KNOWN_PLATFORMS = ("ios", "ios-xe", "nxos", "eos", "junos")
# Shipped as render templates in policies/render_templates.yaml.
TEMPLATE_PLATFORMS = ("fortios", "panos", "sros", "srlinux", "exos")

_ALIASES = {
    "ios": "ios",
    "cisco-ios": "ios",
    "cisco_ios": "ios",
    "ios-xe": "ios-xe",
    "iosxe": "ios-xe",
    "cisco-ios-xe": "ios-xe",
    "cisco_iosxe": "ios-xe",
    "cisco-iosxe": "ios-xe",
    "nxos": "nxos",
    "nx-os": "nxos",
    "cisco-nxos": "nxos",
    "cisco_nxos": "nxos",
    "eos": "eos",
    "arista-eos": "eos",
    "arista_eos": "eos",
    "junos": "junos",
    "juniper-junos": "junos",
    "juniper_junos": "junos",
    # Cisco ISR: ISR 1000/4000 run IOS-XE; the older ISR G2 (1900/2900/3900) run IOS.
    "isr": "ios-xe",
    "cisco-isr": "ios-xe",
    "isr-g2": "ios",
    "arista": "eos",
    # Fortinet FortiGate (FortiOS)
    "fortinet": "fortios",
    "fortios": "fortios",
    "fortigate": "fortios",
    "fortinet-fortios": "fortios",
    # Palo Alto Networks (PAN-OS)
    "paloalto": "panos",
    "palo-alto": "panos",
    "panos": "panos",
    "pan-os": "panos",
    "paloalto-panos": "panos",
    # Nokia: "nokia" means SR OS routers (7750/7250); SR Linux is separate.
    "nokia": "sros",
    "nokia-sros": "sros",
    "sros": "sros",
    "timos": "sros",
    "nokia-srlinux": "srlinux",
    "srlinux": "srlinux",
    "srl": "srlinux",
    # Extreme Networks: "extreme" means ExtremeXOS (switch engine).
    "extreme": "exos",
    "exos": "exos",
    "extreme-exos": "exos",
}


def normalise_platform(slug: str | None) -> str:
    """Canonical platform name. Unknown slugs are returned lower-cased, so a
    user render template can still target them (e.g. "huawei-vrp")."""
    key = (slug or "").strip().lower()
    return _ALIASES.get(key, key)
