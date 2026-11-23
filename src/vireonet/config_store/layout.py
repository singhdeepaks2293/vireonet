from __future__ import annotations

import re

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
