from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

TEMPLATES_REPO = "netops/templates"
VARS_REPO = "netops/vars"
NETBOX_EXPORT_REPO = "netops/netbox-export"


def device_repo(hostname: str) -> str:
    """One repo per device. Hostname is normalised; serial is NOT in the name."""
    slug = re.sub(r"[^a-z0-9-]", "-", hostname.lower()).strip("-")
    if not slug:
        raise ValueError("hostname must contain at least one letter or digit")
    return f"netops/config-{slug}"


def device_paths(hostname: str) -> dict[str, str]:
    return {
        "rendered": "rendered/running-config.txt",
        "candidate": "rendered/candidate-config.txt",
        "backup": "backups/running-config.txt",
        "vars_snapshot": "vars.yaml",
        "metadata": "metadata.yaml",
        "netbox_export": f"netbox-export/{hostname}.jinja",
    }


# Separation rules, enforced by TemplateStore / VarsStore / CI (policies/config_store.yaml):
# - templates repo: *.j2 only. No .yaml with device values, no secrets, no hostnames/IPs.
# - vars repo: *.yaml only. No Jinja delimiters {{ }} or {% %}.
# - device repo: rendered config + vars snapshot + metadata only. No Jinja templates.
# - netbox export: read-only copy of NetBox Jinja, never a render source.
FORBIDDEN_IN_TEMPLATES = [r"\d+\.\d+\.\d+\.\d+", r"password", r"secret", r"{{.*hostname.*}}"]
FORBIDDEN_IN_VARS = [r"{{", r"{%"]


@dataclass(frozen=True)
class GitLayout:
    """Where each device's running config lives in Git.

    per_device (default, original layout): repo netops/config-<device>,
        file backups/running-config.txt.
    monorepo: one repo (``repo``) with one file per device, by default
        ``<device>/config``.

    In both, ``branch`` (main or master) holds the approved running config:
    drift is the device differing from it, and rollback restores from it.
    """

    mode: Literal["per_device", "monorepo"] = "per_device"
    repo: str = ""
    path_template: str = "{device}/config"
    branch: str = "main"

    def repo_for(self, hostname: str) -> str:
        if self.mode == "monorepo":
            if not self.repo:
                raise ValueError("git layout monorepo needs a repo (VIREONET_GIT_CONFIG_REPO)")
            return self.repo
        return device_repo(hostname)

    def running_path(self, hostname: str) -> str:
        if self.mode == "monorepo":
            if not hostname or "/" in hostname or ".." in hostname or hostname.startswith("."):
                raise ValueError(f"hostname {hostname!r} cannot be used as a Git path")
            return self.path_template.format(device=hostname)
        return device_paths(hostname)["backup"]


def git_layout_from_settings(settings: object) -> GitLayout:
    """GitLayout from Settings (git_layout, git_config_repo/git_repo, ...)."""
    mode = str(getattr(settings, "git_layout", "per_device"))
    return GitLayout(
        mode="monorepo" if mode == "monorepo" else "per_device",
        repo=str(getattr(settings, "git_config_repo", "") or getattr(settings, "git_repo", "")),
        path_template=str(getattr(settings, "git_config_path", "{device}/config")),
        branch=str(getattr(settings, "git_default_branch", "main") or "main"),
    )
