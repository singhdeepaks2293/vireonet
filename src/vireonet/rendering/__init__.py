"""Vendor-neutral config rendering: intent + device platform -> config lines.

Built-in renderers cover Cisco IOS / IOS-XE, NX-OS, Arista EOS and Juniper
Junos (set format). Operators can override any (change type, platform)
pair, or add new platforms, in policies/render_templates.yaml.
"""

from vireonet.rendering.base import RenderedConfig, UnsupportedChange
from vireonet.rendering.platforms import KNOWN_PLATFORMS, normalise_platform
from vireonet.rendering.registry import Renderer

__all__ = [
    "KNOWN_PLATFORMS",
    "RenderedConfig",
    "Renderer",
    "UnsupportedChange",
    "normalise_platform",
]
