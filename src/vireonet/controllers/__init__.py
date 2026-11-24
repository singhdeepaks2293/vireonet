"""Vendor controllers: see controllers.base."""

from __future__ import annotations

import json
from typing import Any

from vireonet.controllers.base import Controller, ControllerConfig, ControllerError


def build_controllers(
    settings: Any,
    *,
    network: Any = None,
    inventory: list[dict[str, Any]] | None = None,
    transport: Any = None,
    sessions: Any = None,
) -> dict[str, Controller]:
    """Controllers from VIREONET_CONTROLLERS (JSON list). In fake mode, one
    fake controller per vendor is added (named after the vendor type) unless
    a controller with that name is configured."""
    from vireonet.controllers.vendors import VENDORS

    out: dict[str, Controller] = {}
    raw = getattr(settings, "controllers", "") or "[]"
    items = json.loads(raw) if isinstance(raw, str) else list(raw)
    for item in items:
        cfg = ControllerConfig(
            **{k: v for k, v in item.items() if k in ControllerConfig.__dataclass_fields__}
        )
        if cfg.type not in VENDORS:
            raise ControllerError(
                f"controller {cfg.name!r}: unknown type {cfg.type!r} (one of: {', '.join(VENDORS)})"
            )
        for secret in ("password", "token"):
            env = item.get(f"{secret}_env")
            if env and not getattr(cfg, secret):
                import os

                setattr(cfg, secret, os.environ.get(env, ""))
        out[cfg.name] = VENDORS[cfg.type](cfg, transport=transport)  # type: ignore[assignment]
    if getattr(settings, "adapter_mode", "fake") == "fake":
        from vireonet.controllers.fake import FAKE_PLATFORMS, FakeController
        from vireonet.execution.fake_network import FakeNetwork
        from vireonet.topology.demo import DEMO_NODES

        net = network or FakeNetwork()
        for kind in FAKE_PLATFORMS:
            out.setdefault(
                kind,
                FakeController(
                    kind,
                    net,
                    inventory if inventory is not None else list(DEMO_NODES),
                    sessions=sessions,
                ),
            )
    return out


__all__ = ["Controller", "ControllerConfig", "ControllerError", "build_controllers"]
