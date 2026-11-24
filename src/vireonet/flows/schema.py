"""Flow definition validation and normalisation.

A definition::

    {
      "inputs":    [{"name": "device", "label": "Device", "type": "device", "required": true}],
      "variables": {"attempts": 0},
      "steps":     [ {"id": "s1", "type": "log", "message": "hi"}, ... ],
      "outputs":   {"summary": "{{ vars.result }}"}
    }

Container steps hold nested step lists: ``if`` has ``branches`` [{condition,
steps}] and ``else``; ``switch`` has ``cases`` [{value, steps}] and
``default``; ``parallel`` has ``branches`` [{name, steps}]; loops and ``try``
use the keys listed in the catalog.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from vireonet.flows.catalog import INPUT_TYPES, STEP_SETTINGS, STEPS, TRIGGERS, container_keys
from vireonet.flows.expr import check_template, condition_errors

MAX_STEPS = 500
MAX_DEPTH = 12
MAX_LOOP = 1000
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_SETTING_NAMES = {f["name"] for f in STEP_SETTINGS}


class FlowValidationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def child_lists(step: dict[str, Any]) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    """(label, list) for every nested step list of a step."""
    t = step.get("type")
    if t == "if":
        for i, b in enumerate(step.get("branches") or []):
            yield f"branches[{i}]", b.setdefault("steps", [])
    if t == "switch":
        for i, c in enumerate(step.get("cases") or []):
            yield f"cases[{i}]", c.setdefault("steps", [])
    if t == "parallel":
        for i, b in enumerate(step.get("branches") or []):
            yield f"branches[{i}]", b.setdefault("steps", [])
    for key in container_keys(str(t)):
        yield key, step.setdefault(key, [])


def walk(steps: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for s in steps:
        yield s
        for _label, kids in child_lists(s):
            yield from walk(kids)


def uses_scripts(definition: dict[str, Any]) -> bool:
    return any(
        STEPS.get(str(s.get("type")), {}).get("admin_only")
        for s in walk(definition.get("steps", []))
    )


def normalise(definition: dict[str, Any]) -> dict[str, Any]:
    """Fill in missing step ids (s1, s2, …) and empty containers."""
    d: dict[str, Any] = {
        "inputs": list(definition.get("inputs") or []),
        "variables": dict(definition.get("variables") or {}),
        "steps": list(definition.get("steps") or []),
        "outputs": dict(definition.get("outputs") or {}),
    }
    steps: list[dict[str, Any]] = d["steps"]
    taken = {str(s.get("id")) for s in walk(steps) if s.get("id")}
    n = 0
    for s in walk(steps):
        if not s.get("id"):
            n += 1
            while f"s{n}" in taken:
                n += 1
            s["id"] = f"s{n}"
            taken.add(s["id"])
    return d


def _templates(value: Any, where: str) -> list[str]:
    if isinstance(value, str):
        err = check_template(value)
        return [f"{where}: {err}"] if err else []
    if isinstance(value, dict):
        return [e for k, v in value.items() for e in _templates(v, f"{where}.{k}")]
    if isinstance(value, list):
        return [e for i, v in enumerate(value) for e in _templates(v, f"{where}[{i}]")]
    return []


def validate_trigger(trigger: dict[str, Any] | None) -> list[str]:
    trig = trigger or {"type": "manual"}
    errs: list[str] = []
    ttype = trig.get("type", "manual")
    if ttype not in TRIGGERS:
        return [f"trigger: unknown type {ttype!r} (one of: {', '.join(TRIGGERS)})"]
    errs += condition_errors(trig.get("conditions"), "trigger.conditions")
    if ttype == "schedule":
        cron, every = trig.get("cron"), trig.get("every_minutes")
        if not cron and not every:
            errs.append("trigger: a schedule needs cron or every_minutes")
        if cron:
            try:
                from croniter import croniter

                if not croniter.is_valid(str(cron)):
                    errs.append(f"trigger: bad cron {cron!r}")
            except ImportError:  # pragma: no cover
                if len(str(cron).split()) != 5:
                    errs.append(f"trigger: cron needs 5 fields, got {cron!r}")
        if every is not None and (not isinstance(every, int) or every < 1):
            errs.append("trigger: every_minutes must be a whole number ≥ 1")
    if ttype == "kafka" and not trig.get("topic"):
        errs.append("trigger: a Kafka trigger needs a topic")
    if trig.get("dedup_key"):
        errs += _templates(trig["dedup_key"], "trigger.dedup_key")
    return errs


def validate_definition(definition: dict[str, Any], *, allow_scripts: bool = True) -> list[str]:
    errs: list[str] = []
    if not isinstance(definition, dict):
        return ["definition must be an object"]
    for i, inp in enumerate(definition.get("inputs") or []):
        if not isinstance(inp, dict) or not _ID.match(str(inp.get("name", ""))):
            errs.append(f"inputs[{i}]: name must be letters, digits and _ (starting with a letter)")
        elif inp.get("type", "text") not in INPUT_TYPES:
            errs.append(f"inputs[{i}]: type must be one of {INPUT_TYPES}")
    for name in definition.get("variables") or {}:
        if not _ID.match(str(name)):
            errs.append(f"variables: bad name {name!r}")
    errs += _templates(definition.get("outputs") or {}, "outputs")
    seen: set[str] = set()
    count = 0

    def check(steps: Any, where: str, depth: int) -> None:
        nonlocal count
        if not isinstance(steps, list):
            errs.append(f"{where}: must be a list of steps")
            return
        if depth > MAX_DEPTH:
            errs.append(f"{where}: nested more than {MAX_DEPTH} levels")
            return
        for i, step in enumerate(steps):
            count += 1
            here = f"{where}[{i}]"
            if not isinstance(step, dict):
                errs.append(f"{here}: must be an object")
                continue
            sid, stype = str(step.get("id", "")), str(step.get("type", ""))
            here = f"{here} ({sid or '?'}:{stype or '?'})"
            if not _ID.match(sid):
                errs.append(f"{here}: id must be letters, digits and _ (starting with a letter)")
            elif sid in seen:
                errs.append(f"{here}: duplicate step id {sid!r}")
            seen.add(sid)
            spec = STEPS.get(stype)
            if spec is None:
                errs.append(f"{here}: unknown step type {stype!r}")
                continue
            if spec.get("admin_only") and not allow_scripts:
                errs.append(f"{here}: {spec['label']} steps can only be saved by an admin")
            names = {f["name"] for f in spec["fields"]}
            for f in spec["fields"]:
                val = step.get(f["name"], f.get("default"))
                if f.get("required") and val in (None, "", [], {}):
                    errs.append(f"{here}: {f['label']} is required")
                if f["kind"] == "condition":
                    errs.extend(condition_errors(val, f"{here}.{f['name']}"))
                elif (
                    f["kind"] == "number"
                    and val not in (None, "")
                    and not isinstance(val, (int, float))
                ):
                    if not (isinstance(val, str) and "{{" in val):
                        errs.append(f"{here}: {f['label']} must be a number")
                elif f["kind"] != "code":
                    errs.extend(_templates(val, f"{here}.{f['name']}"))
            errs.extend(condition_errors(step.get("run_if"), f"{here}.run_if"))
            if step.get("run_after", "success") not in ("success", "failure", "always"):
                errs.append(f"{here}: run_after must be success, failure or always")
            for key in ("max_iterations", "times"):
                v = step.get(key)
                if isinstance(v, int) and not 0 < v <= MAX_LOOP:
                    errs.append(f"{here}: {key} must be between 1 and {MAX_LOOP}")
            if stype == "if":
                branches = step.get("branches") or []
                if not branches:
                    errs.append(f"{here}: an If needs at least one condition branch")
                for j, b in enumerate(branches):
                    errs.extend(
                        condition_errors(b.get("condition"), f"{here}.branches[{j}].condition")
                    )
                    if not b.get("condition"):
                        errs.append(f"{here}.branches[{j}]: condition is required")
            if stype == "switch" and not step.get("cases"):
                errs.append(f"{here}: a Switch needs at least one case")
            if stype == "parallel" and len(step.get("branches") or []) < 2:
                errs.append(f"{here}: Run in parallel needs at least two branches")
            known = (
                names
                | _SETTING_NAMES
                | {"id", "type", "name", "branches", "cases"}
                | set(container_keys(stype))
            )
            for extra in sorted(set(step) - known):
                errs.append(f"{here}: unknown field {extra!r}")
            for label, kids in child_lists(step):
                check(kids, f"{here}.{label}", depth + 1)

    check(definition.get("steps", []), "steps", 0)
    if count > MAX_STEPS:
        errs.append(f"a flow can have at most {MAX_STEPS} steps (has {count})")
    return errs


def ensure_valid(
    definition: dict[str, Any], trigger: dict[str, Any] | None, *, allow_scripts: bool
) -> dict[str, Any]:
    d = normalise(definition)
    errors = validate_trigger(trigger) + validate_definition(d, allow_scripts=allow_scripts)
    if errors:
        raise FlowValidationError(errors)
    return d
