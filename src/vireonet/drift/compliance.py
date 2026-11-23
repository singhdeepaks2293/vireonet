"""Compliance rules for drift detection (D2).

Rules are dicts {name, must_contain / must_not_contain / vlans_match},
whose values may contain Jinja ``{{ ... }}`` placeholders rendered with
the device's merged config context + custom fields + tags fetched from
NetBox. Two hard rules:

- Missing reference data is NEVER a pass: the rule evaluates to a
  finding with status "cannot_evaluate".
- NetBox Jinja templates are NEVER a render source. Only the rule's own
  Jinja placeholders are rendered, with the device context as data.
  A ``jinja_template`` stored on a NetBox device is data about the
  device, not a template to execute.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import jinja2
from jinja2 import StrictUndefined

from vireonet.diff import normalize_config

_VLAN_RE = re.compile(r"^vlan (\d+)\s*$", re.MULTILINE)
_ACCESS_VLAN_RE = re.compile(r"switchport access vlan (\d+)")


def load_rules(path: str | Path | None) -> list[dict[str, Any]]:
    """Load compliance rules from a YAML file.

    Accepts a file path (a top-level list, or a mapping with a "rules"
    key). None / missing file -> no rules. A git-provider object is not
    accepted here: rule loading from a repo is a D5 job; callers pass a
    checked-out path.
    """
    if path is None:
        return []
    file = Path(path)
    if not file.exists():
        return []
    import yaml

    raw: Any = yaml.safe_load(file.read_text(encoding="utf-8")) or []
    if isinstance(raw, dict):
        raw = raw.get("rules", [])
    if not isinstance(raw, list):
        raise ValueError(f"compliance rules {path} must be a list of rule dicts")
    return [dict(r) for r in raw if isinstance(r, dict)]


def _render(value: str, context: dict[str, Any]) -> str:
    env = jinja2.Environment(undefined=StrictUndefined)
    return str(env.from_string(value).render(context))


def _vlans_in_running(running: str) -> set[int]:
    vlans = {int(m.group(1)) for m in _VLAN_RE.finditer(running)}
    vlans |= {int(m.group(1)) for m in _ACCESS_VLAN_RE.finditer(running)}
    return vlans


async def _device_context(netbox: Any, device: str) -> dict[str, Any] | None:
    """Merged NetBox context for a device, or None when unavailable.

    Uses getattr for get_config_context / get_device_custom_fields —
    neither is in the NetBoxProtocol, so an adapter without them yields
    None (cannot_evaluate) instead of crashing. The device record's own
    fields (site, platform, tags) are included when get_device works.
    """
    context: dict[str, Any] = {}
    get_context = getattr(netbox, "get_config_context", None)
    if callable(get_context):
        try:
            value = await get_context(device)
            if isinstance(value, dict):
                context.update(value)
        except Exception:
            return None
    get_custom = getattr(netbox, "get_device_custom_fields", None)
    if callable(get_custom):
        try:
            value = await get_custom(device)
            if isinstance(value, dict):
                context.update(value)
        except Exception:
            return None
    if get_context is None and get_custom is None:
        # No context source at all: the adapter cannot supply reference
        # data, so nothing depending on context can be evaluated.
        return None
    get_device = getattr(netbox, "get_device", None)
    if callable(get_device):
        try:
            record = await get_device(device)
            context.setdefault("site", getattr(record, "site", ""))
            context.setdefault("platform", getattr(record, "platform", ""))
            context.setdefault("device", getattr(record, "name", device))
        except Exception:
            pass
    context.setdefault("device", device)
    return context


async def evaluate_compliance(
    running_config: str,
    rules: list[dict[str, Any]],
    netbox: Any,
    device: str,
) -> list[dict[str, Any]]:
    """Evaluate rules against a running config.

    Returns one dict per rule: {name, kind: "compliance", status,
    detail}. status is "detected" for a violation, "passed" when the
    rule holds, and "cannot_evaluate" when reference data is missing —
    never a pass on missing data.
    """
    running = normalize_config(running_config)
    results: list[dict[str, Any]] = []
    context: dict[str, Any] | None = None
    context_loaded = False
    vlans: set[int] | None = None

    async def _context() -> dict[str, Any] | None:
        nonlocal context, context_loaded
        if not context_loaded:
            context = await _device_context(netbox, device)
            context_loaded = True
        return context

    for rule in rules:
        name = str(rule.get("name", "unnamed"))
        needs_context = any(
            isinstance(rule.get(key), str) and "{{" in str(rule[key])
            for key in ("must_contain", "must_not_contain")
        )
        if "vlans_match" in rule:
            vlans_expected = rule.get("vlans_match")
            if vlans_expected is None:
                results.append(
                    {
                        "name": name,
                        "kind": "compliance",
                        "status": "cannot_evaluate",
                        "detail": {"reason": "vlans_match rule has no expected value"},
                    }
                )
                continue
            list_vlans = getattr(netbox, "list_vlans", None)
            if not callable(list_vlans):
                results.append(
                    {
                        "name": name,
                        "kind": "compliance",
                        "status": "cannot_evaluate",
                        "detail": {"reason": "netbox adapter has no list_vlans"},
                    }
                )
                continue
            try:
                nb_vlans = await list_vlans()
                expected = {int(v.vlan_id) for v in nb_vlans}
            except Exception as exc:  # noqa: BLE001 — missing data, not a pass
                results.append(
                    {
                        "name": name,
                        "kind": "compliance",
                        "status": "cannot_evaluate",
                        "detail": {"reason": f"netbox list_vlans failed: {exc}"},
                    }
                )
                continue
            if vlans is None:
                vlans = _vlans_in_running(running)
            if vlans == expected:
                results.append(
                    {"name": name, "kind": "compliance", "status": "passed", "detail": {}}
                )
            else:
                results.append(
                    {
                        "name": name,
                        "kind": "compliance",
                        "status": "detected",
                        "detail": {
                            "running_vlans": sorted(vlans),
                            "netbox_vlans": sorted(expected),
                        },
                    }
                )
            continue
        if needs_context:
            ctx = await _context()
            if ctx is None:
                results.append(
                    {
                        "name": name,
                        "kind": "compliance",
                        "status": "cannot_evaluate",
                        "detail": {"reason": "netbox config context unavailable"},
                    }
                )
                continue
        else:
            ctx = {}
        try:
            if "must_contain" in rule:
                wanted = _render(str(rule["must_contain"]), ctx or {})
                if wanted in running:
                    results.append(
                        {"name": name, "kind": "compliance", "status": "passed", "detail": {}}
                    )
                else:
                    results.append(
                        {
                            "name": name,
                            "kind": "compliance",
                            "status": "detected",
                            "detail": {"missing": wanted},
                        }
                    )
            elif "must_not_contain" in rule:
                banned = _render(str(rule["must_not_contain"]), ctx or {})
                if banned in running:
                    results.append(
                        {
                            "name": name,
                            "kind": "compliance",
                            "status": "detected",
                            "detail": {"present": banned},
                        }
                    )
                else:
                    results.append(
                        {"name": name, "kind": "compliance", "status": "passed", "detail": {}}
                    )
            else:
                results.append(
                    {
                        "name": name,
                        "kind": "compliance",
                        "status": "cannot_evaluate",
                        "detail": {
                            "reason": "rule has no must_contain/must_not_contain/vlans_match"
                        },
                    }
                )
        except jinja2.UndefinedError as exc:
            results.append(
                {
                    "name": name,
                    "kind": "compliance",
                    "status": "cannot_evaluate",
                    "detail": {"reason": f"jinja render failed (missing context): {exc}"},
                }
            )
    return results
