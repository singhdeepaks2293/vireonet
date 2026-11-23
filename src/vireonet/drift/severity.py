"""Drift severity policy (D2).

The policy file (policies/drift.yaml) names the protected devices, the
critical sections, and the high-severity section; the code implements
exactly that policy and nothing more:

- critical: unmanaged change + a changed line in a critical section
  (acl / routing / aaa) + device in protected_devices.
- high: unmanaged change + a changed line in the interfaces section.
- low: the change touches only unmanaged sections.
- medium: everything else (the default).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from vireonet.diff import compute_diff
from vireonet.drift.sections import MANAGED_SECTIONS_DEFAULT, sections_in_diff

DEFAULT_POLICY_PATH = "policies/drift.yaml"

DEFAULT_PROTECTED_DEVICES: list[str] = ["core-01", "core-02", "fw-01"]
DEFAULT_CRITICAL_SECTIONS: list[str] = ["acl", "routing", "aaa"]
DEFAULT_HIGH_SECTIONS: list[str] = ["interfaces"]


def load_severity_policy(path: str | Path = DEFAULT_POLICY_PATH) -> dict[str, Any]:
    """Load the severity policy YAML. Missing file -> built-in defaults."""
    file = Path(path)
    if not file.exists():
        return {
            "protected_devices": list(DEFAULT_PROTECTED_DEVICES),
            "critical_sections": list(DEFAULT_CRITICAL_SECTIONS),
            "high_sections": list(DEFAULT_HIGH_SECTIONS),
            "managed_sections": sorted(MANAGED_SECTIONS_DEFAULT),
            "default_severity": "medium",
        }
    raw: Any = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"severity policy {path} must be a mapping")
    return {
        "protected_devices": list(raw.get("protected_devices", DEFAULT_PROTECTED_DEVICES)),
        "critical_sections": list(raw.get("critical_sections", DEFAULT_CRITICAL_SECTIONS)),
        "high_sections": list(raw.get("high_sections", DEFAULT_HIGH_SECTIONS)),
        "managed_sections": list(raw.get("managed_sections", sorted(MANAGED_SECTIONS_DEFAULT))),
        "default_severity": str(raw.get("default_severity", "medium")),
    }


def _changed_sections(diff: dict[str, Any] | str) -> set[str]:
    if isinstance(diff, dict):
        rows = diff.get("lines")
        if isinstance(rows, list):
            return sections_in_diff([dict(r) if isinstance(r, dict) else {} for r in rows])
        unified = diff.get("unified")
        if isinstance(unified, list):
            return _sections_from_unified([str(line) for line in unified])
        return set()
    return _sections_from_unified(diff.split("\n"))


def _sections_from_unified(lines: list[str]) -> set[str]:
    from vireonet.drift.sections import section_of_line

    sections: set[str] = set()
    current = "other"
    for line in lines:
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith(" "):
            if line[1:] and not line[1:].startswith((" ", "\t")):
                current = section_of_line(line[1:])
            continue
        if line.startswith(("+", "-")):
            body = line[1:]
            if body and not body.startswith((" ", "\t")):
                current = section_of_line(body)
            sections.add(current)
    return sections


def severity_for(
    diff: dict[str, Any] | str,
    device: str,
    protected_devices: list[str] | None = None,
    *,
    policy: dict[str, Any] | None = None,
) -> str:
    """Severity for an unmanaged diff, per policies/drift.yaml."""
    loaded = policy if policy is not None else load_severity_policy()
    protected = (
        set(protected_devices)
        if protected_devices is not None
        else set(str(d) for d in loaded.get("protected_devices", DEFAULT_PROTECTED_DEVICES))
    )
    critical_sections = set(
        str(s) for s in loaded.get("critical_sections", DEFAULT_CRITICAL_SECTIONS)
    )
    high_sections = set(str(s) for s in loaded.get("high_sections", DEFAULT_HIGH_SECTIONS))
    managed = set(str(s) for s in loaded.get("managed_sections", sorted(MANAGED_SECTIONS_DEFAULT)))
    sections = _changed_sections(diff)
    if not sections:
        return "low"
    if not sections & managed:
        return "low"
    if sections & critical_sections and device in protected:
        return "critical"
    if sections & high_sections:
        return "high"
    return str(loaded.get("default_severity", "medium"))


def severity_for_configs(
    baseline: str, running: str, device: str, protected_devices: list[str] | None = None
) -> str:
    """Convenience: severity for the diff between two config texts."""
    return severity_for(compute_diff(device, baseline, running), device, protected_devices)
