from __future__ import annotations

from typing import Any

import yaml

from vireonet.adapters.git.base import GitProvider
from vireonet.config_store.layout import FORBIDDEN_IN_VARS


class VarsStore:
    """YAML vars only, from netops/vars. No Jinja logic in this repo."""

    def __init__(self, git: GitProvider):
        self._git = git

    async def get_vars(
        self, *, site: str, role: str, device_type: str, ref: str = "main"
    ) -> dict[str, Any]:
        # Precedence, lowest to highest: site -> role -> device_type. Later keys win.
        merged: dict[str, Any] = {}
        for path in [f"site/{site}.yaml", f"role/{role}.yaml", f"device-type/{device_type}.yaml"]:
            try:
                raw = (await self._git.get_file(path, ref)).content
            except Exception:
                continue
            for pattern in FORBIDDEN_IN_VARS:
                if pattern in raw:
                    raise ValueError(
                        f"vars file {path} violates separation rule: Jinja delimiter {pattern!r} in YAML repo"
                    )
            merged.update(yaml.safe_load(raw) or {})
        return merged
