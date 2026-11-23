from __future__ import annotations

import re

from vireonet.adapters.git.base import GitProvider
from vireonet.config_store.layout import FORBIDDEN_IN_TEMPLATES


class TemplateStore:
    """Jinja templates only, from netops/templates. Render source for VireoNet.
    NetBox Jinja is NOT read from here — see netbox_export.py."""
    def __init__(self, git: GitProvider):
        self._git = git

    async def get_template(self, name: str, ref: str = "main") -> str:
        # name is e.g. "ios-xe/vlan.j2" — never a device hostname.
        if ".." in name or name.startswith("/"):
            raise ValueError("invalid template name")
        content = (await self._git.get_file(f"templates/{name}", ref)).content
        self._validate(content)
        return content

    def _validate(self, content: str) -> None:
        for pattern in FORBIDDEN_IN_TEMPLATES:
            if re.search(pattern, content, re.IGNORECASE):
                raise ValueError(f"template violates separation rule: matches {pattern!r} (no IPs/secrets/device values in templates)")

    async def list_templates(self, ref: str = "main") -> list[str]:
        # The GitProvider protocol has no list API; templates are listed from a
        # manifest file in the repo root, not by directory walk.
        manifest = (await self._git.get_file("manifest.yaml", ref)).content
        return [line.strip("- ").strip() for line in manifest.splitlines() if line.strip().startswith("- ")]
