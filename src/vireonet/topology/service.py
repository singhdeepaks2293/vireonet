from __future__ import annotations

from typing import Any

from vireonet.rendering.platforms import normalise_platform
from vireonet.telemetry.base import InterfaceRate, TelemetryBackend
from vireonet.topology.demo import DEMO_LINKS, DEMO_NODES


async def build_topology(
    *,
    netbox: Any,
    telemetry: TelemetryBackend | None,
    site: str | None = None,
    demo: bool = False,
) -> dict[str, Any]:
    """Nodes, links and per-link traffic.

    Nodes and links come from NetBox (devices and cables), or the demo
    topology in fake mode. Traffic comes from the telemetry backend: for a
    link A:ifA <-> B:ifB, "a_to_b_bps" is A's out rate on ifA (or B's in
    rate on ifB when A has no data). Links without telemetry have no traffic.
    """
    errors: list[str] = []
    if demo:
        nodes = [dict(n) for n in DEMO_NODES if not site or n["site"] == site]
        cables = [
            {"a_device": a, "a_interface": ai, "b_device": b, "b_interface": bi}
            for a, ai, b, bi in DEMO_LINKS
        ]
    else:
        devices = await netbox.list_devices(site)
        nodes = [
            {
                "name": d.name,
                "platform": normalise_platform(d.platform),
                "role": "",
                "site": d.site,
                "status": d.status,
            }
            for d in devices
        ]
        list_cables = getattr(netbox, "list_cables", None)
        cables = await list_cables(site) if callable(list_cables) else []
    names = {n["name"] for n in nodes}
    cables = [c for c in cables if c["a_device"] in names and c["b_device"] in names]

    rates: dict[tuple[str, str], InterfaceRate] = {}
    if telemetry is not None and nodes:
        try:
            for r in await telemetry.interface_rates(sorted(names)):
                rates[(r.device, r.interface)] = r
        except Exception as exc:  # noqa: BLE001 — topology still renders without traffic
            errors.append(f"telemetry ({telemetry.name}): {exc}")

    links = []
    for n, c in enumerate(cables):
        a = rates.get((c["a_device"], c["a_interface"]))
        b = rates.get((c["b_device"], c["b_interface"]))
        a_to_b = a.out_bps if a else (b.in_bps if b else None)
        b_to_a = a.in_bps if a else (b.out_bps if b else None)
        speed = (a.speed_bps if a else None) or (b.speed_bps if b else None)
        util = (
            max(a_to_b or 0.0, b_to_a or 0.0) / speed
            if speed and (a_to_b is not None or b_to_a is not None)
            else None
        )
        links.append(
            {
                "id": n,
                "source": c["a_device"],
                "target": c["b_device"],
                "source_interface": c["a_interface"],
                "target_interface": c["b_interface"],
                "a_to_b_bps": a_to_b,
                "b_to_a_bps": b_to_a,
                "speed_bps": speed,
                "utilization": util,
            }
        )
    return {
        "site": site,
        "nodes": [{"id": n["name"], **n} for n in nodes],
        "links": links,
        "telemetry": telemetry.name if telemetry is not None else None,
        "errors": errors,
    }
