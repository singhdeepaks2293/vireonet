"""Compatibility wrapper over vireonet.rendering (vendor-neutral renderers).

New code should use vireonet.rendering.Renderer with each device's platform.
These helpers keep the old single-platform API for CI scripts and Batfish;
``platforms`` maps device -> platform and defaults every device to IOS.
"""

from __future__ import annotations

from vireonet.intent import ChangeIntent
from vireonet.rendering import Renderer


def render_intent(
    intent: ChangeIntent,
    platforms: dict[str, str] | None = None,
    renderer: Renderer | None = None,
) -> dict[str, list[str]]:
    """Intent -> per-device config lines. Adds merge; deletes are explicit
    `no` (Cisco/Arista) or `delete` (Junos) lines."""
    r = renderer or Renderer()
    plats = platforms or {}
    return {d: r.render(intent, d, plats.get(d, "ios")).lines for d in intent.devices}


def render_candidate_configs(
    intent: ChangeIntent,
    current_configs: dict[str, str],
    platforms: dict[str, str] | None = None,
) -> dict[str, str]:
    """Candidate snapshot for Batfish: current config + intended lines per device.

    File per device: `<device>.cfg`. Batfish needs full configs, not diffs.
    Appending works for line-oriented CLI configs (Cisco, Arista). Junos and
    set-format platforms need a vendor-aware merge before Batfish; not yet done.
    """
    rendered = render_intent(intent, platforms)
    out: dict[str, str] = {}
    for device, lines in rendered.items():
        current = current_configs.get(device, current_configs.get(f"{device}.cfg", ""))
        body = (current.rstrip() + "\n" if current else "") + "\n".join(lines) + "\n"
        out[f"{device}.cfg"] = body
    return out
