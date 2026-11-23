from __future__ import annotations

from typing import Any


class NetBoxJinjaExport:
    """NetBox Jinja lives in NetBox only. This exports a read-only copy for audit.
    It is NEVER a render source. The tool's Jinja is in netops/templates.
    If the two disagree, the tool's template wins for rendering, and the
    disagreement is reported — NetBox is not silently overwritten."""
    def __init__(self, netbox: Any, git_factory: Any) -> None:
        self._netbox = netbox
        self._factory = git_factory

    async def export_device_template(self, hostname: str, *, ticket_number: str) -> str:
        device = await self._netbox.get_device(hostname)
        # NetBox Jinja is stored as a config context / custom field on the device.
        # The exact field is NetBox-version dependent and is isolated here.
        jinja = str(getattr(device, "config_context", {}).get("jinja_template", "") or "")
        if not jinja:
            return ""
        git = self._factory("netops/netbox-export")
        branch = f"export/{ticket_number}"
        await git.create_branch(branch, "main")
        await git.commit_file(branch, f"{hostname}.jinja", jinja, f"{ticket_number}: NetBox Jinja export for {hostname} (read-only)")
        mr = await git.open_mr(source_branch=branch, target_branch="main", title=f"{ticket_number}: NetBox Jinja export for {hostname}")
        return str(mr.url)
