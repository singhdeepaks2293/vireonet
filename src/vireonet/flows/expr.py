"""Expressions, templates and conditions for flows.

Templates are sandboxed Jinja: ``"Upgrade {{ trigger.ticket.key }}"``. A
string that is exactly one ``{{ expression }}`` keeps the expression's type
(list, number, dict), like Power Automate's dynamic content.

Conditions are data, so the UI can build them without code::

    {"all": [
        {"field": "ticket.summary", "op": "contains", "value": "OS UPGRADE"},
        {"field": "ticket.summary", "op": "matches", "value": "to (?P<version>\\\\d+\\\\.\\\\d+\\\\.\\\\d+)"},
        {"any": [{"field": "ticket.priority", "op": "in", "value": ["1", "2"]},
                 {"expr": "trigger.ticket.fields.category == 'network'"}]},
    ]}

Everything here is deterministic (no clock, no I/O), so the Temporal flow
workflow can evaluate it during replay.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Iterator
from typing import Any

from jinja2 import StrictUndefined, TemplateSyntaxError, Undefined
from jinja2.sandbox import ImmutableSandboxedEnvironment

SECRET_MARK = "\x00vn-secret:"
_SECRET_RE = re.compile(r"\x00vn-secret:([A-Za-z0-9_]+)\x00")
_WHOLE_EXPR = re.compile(r"^\s*\{\{(.*)\}\}\s*$", re.S)
MAX_REGEX_LEN = 500
MAX_TEXT_FOR_REGEX = 200_000


class ExprError(ValueError):
    """A template or condition could not be evaluated."""


class _Secrets:
    """`{{ secrets.NAME }}` renders a marker that only the activity, at the
    moment of the call, swaps for the real value. Secrets therefore never
    reach Temporal history, run logs or the database."""

    def __getattr__(self, name: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_]+", name):
            raise ExprError(f"bad secret name {name!r}")
        return f"{SECRET_MARK}{name}\x00"

    __getitem__ = __getattr__


def _regex_search(value: Any, pattern: str, ignorecase: bool = False) -> bool:
    return _compile(pattern, ignorecase).search(str(value)[:MAX_TEXT_FOR_REGEX]) is not None


def _regex_findall(value: Any, pattern: str, ignorecase: bool = False) -> list[Any]:
    return _compile(pattern, ignorecase).findall(str(value)[:MAX_TEXT_FOR_REGEX])


def _regex_replace(value: Any, pattern: str, repl: str, ignorecase: bool = False) -> str:
    return _compile(pattern, ignorecase).sub(repl, str(value)[:MAX_TEXT_FOR_REGEX])


def _compile(pattern: str, ignorecase: bool = False) -> re.Pattern[str]:
    if len(pattern) > MAX_REGEX_LEN:
        raise ExprError(f"regex longer than {MAX_REGEX_LEN} characters")
    try:
        return re.compile(pattern, re.I if ignorecase else 0)
    except re.error as exc:
        raise ExprError(f"bad regex {pattern!r}: {exc}") from exc


def _make_env() -> ImmutableSandboxedEnvironment:
    env = ImmutableSandboxedEnvironment(undefined=StrictUndefined, autoescape=False)
    env.filters.update(
        regex_search=_regex_search,
        regex_findall=_regex_findall,
        regex_replace=_regex_replace,
        to_int=lambda v, d=0: _num(v, d, int),
        to_float=lambda v, d=0.0: _num(v, d, float),
        split_lines=lambda v: [ln for ln in str(v).splitlines() if ln.strip()],
    )
    env.tests.update(
        regex=lambda v, p: _regex_search(v, p),
        contains=lambda v, x: x in v,
    )
    return env


def _num(v: Any, default: Any, kind: type) -> Any:
    try:
        return kind(v)
    except (TypeError, ValueError):
        return default


_ENV = _make_env()


def base_context(**parts: Any) -> dict[str, Any]:
    return {"secrets": _Secrets(), **parts}


# --- templates ------------------------------------------------------------------------


def check_template(text: str) -> str | None:
    """Syntax error text, or None when the template compiles."""
    if not isinstance(text, str) or "{{" not in text and "{%" not in text:
        return None
    try:
        m = _WHOLE_EXPR.match(text)
        if m and "{{" not in m.group(1):
            _ENV.compile_expression(m.group(1))
        else:
            _ENV.from_string(text)
    except TemplateSyntaxError as exc:
        return f"{exc.message} in {text[:80]!r}"
    return None


def render(value: Any, ctx: dict[str, Any]) -> Any:
    """Render templates anywhere inside a value (dicts and lists too)."""
    if isinstance(value, str):
        if "{{" not in value and "{%" not in value:
            return value
        try:
            m = _WHOLE_EXPR.match(value)
            if m and "{{" not in m.group(1) and "}}" not in m.group(1):
                out = _ENV.compile_expression(m.group(1), undefined_to_none=False)(**ctx)
                if isinstance(out, Undefined):
                    str(out)  # raises the StrictUndefined error
                return out
            return _ENV.from_string(value).render(**ctx)
        except ExprError:
            raise
        except Exception as exc:  # undefined variable, type error, sandbox refusal
            raise ExprError(f"{value[:120]!r}: {exc}") from exc
    if isinstance(value, dict):
        # Keys can be templates too: {"{{ vars.device }}": "config…"}.
        return {
            (str(render(k, ctx)) if isinstance(k, str) and "{{" in k else k): render(v, ctx)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [render(v, ctx) for v in value]
    return value


def evaluate(expression: str, ctx: dict[str, Any]) -> Any:
    try:
        return _ENV.compile_expression(expression, undefined_to_none=False)(**ctx)
    except Exception as exc:
        raise ExprError(f"{expression[:120]!r}: {exc}") from exc


def resolve_secrets(value: Any, lookup: Any) -> Any:
    """Swap secret markers for real values (activity side only)."""
    if isinstance(value, str) and SECRET_MARK in value:
        return _SECRET_RE.sub(lambda m: str(lookup(m.group(1))), value)
    if isinstance(value, dict):
        return {k: resolve_secrets(v, lookup) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_secrets(v, lookup) for v in value]
    return value


def mask_secrets(value: Any) -> Any:
    if isinstance(value, str) and SECRET_MARK in value:
        return _SECRET_RE.sub(lambda m: f"<secret {m.group(1)}>", value)
    if isinstance(value, dict):
        return {k: mask_secrets(v) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_secrets(v) for v in value]
    return value


# --- conditions ---------------------------------------------------------------------------

OPERATORS: dict[str, str] = {
    "equals": "is equal to",
    "not_equals": "is not equal to",
    "contains": "contains",
    "not_contains": "does not contain",
    "starts_with": "starts with",
    "ends_with": "ends with",
    "matches": "matches regex",
    "not_matches": "does not match regex",
    "wildcard": "matches wildcard (* and ?)",
    "contains_any": "contains any of the words",
    "contains_all": "contains all of the words",
    "in": "is one of",
    "not_in": "is not one of",
    "gt": "is greater than",
    "gte": "is at least",
    "lt": "is less than",
    "lte": "is at most",
    "exists": "exists",
    "missing": "is missing",
    "is_empty": "is empty",
    "not_empty": "is not empty",
    "is_true": "is true",
    "is_false": "is false",
    "changed": "changed (from → to)",
    "length_gt": "has more items than",
}
NO_VALUE_OPS = {"exists", "missing", "is_empty", "not_empty", "is_true", "is_false"}
_MISSING = object()


def get_path(data: Any, path: str) -> Any:
    """`ticket.fields.category`, `items.0.name`, `headers["X-Id"]`-free dotted paths."""
    cur = data
    for part in [p for p in re.split(r"\.|\[(\d+)\]", path) if p]:
        if isinstance(cur, dict):
            if part not in cur:
                return _MISSING
            cur = cur[part]
        elif isinstance(cur, (list, tuple)) and part.lstrip("-").isdigit():
            idx = int(part)
            if not -len(cur) <= idx < len(cur):
                return _MISSING
            cur = cur[idx]
        else:
            return _MISSING
    return cur


def _text_leaves(data: Any, depth: int = 0) -> Iterator[str]:
    if depth > 8:
        return
    if isinstance(data, str):
        yield data
    elif isinstance(data, dict):
        for v in data.values():
            yield from _text_leaves(v, depth + 1)
    elif isinstance(data, list):
        for v in data:
            yield from _text_leaves(v, depth + 1)


def _as_list(v: Any) -> list[Any]:
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        return [p.strip() for p in re.split(r"[,\n]", v) if p.strip()]
    return [v]


def _number(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _compare(op: str, left: Any, right: Any, cs: bool, captures: dict[str, Any]) -> bool:
    def norm(x: Any) -> str:
        s = "" if x is None else str(x)
        return s if cs else s.casefold()

    if op in ("exists", "missing"):
        return (left is not _MISSING) == (op == "exists")
    if left is _MISSING:
        return op in ("not_equals", "not_contains", "not_matches", "not_in", "is_empty")
    if op == "is_empty":
        return left in (None, "", [], {})
    if op == "not_empty":
        return left not in (None, "", [], {})
    if op == "is_true":
        return left is True or norm(left) in ("true", "yes", "1", "on")
    if op == "is_false":
        return left is False or norm(left) in ("false", "no", "0", "off", "")
    if op in ("equals", "not_equals"):
        ln, rn = _number(left), _number(right)
        same = ln == rn if ln is not None and rn is not None else norm(left) == norm(right)
        return same == (op == "equals")
    if op in ("contains", "not_contains"):
        if isinstance(left, list):
            found = any(norm(x) == norm(right) for x in left)
        else:
            found = norm(right) in norm(left)
        return found == (op == "contains")
    if op == "starts_with":
        return norm(left).startswith(norm(right))
    if op == "ends_with":
        return norm(left).endswith(norm(right))
    if op in ("matches", "not_matches"):
        m = _compile(str(right), not cs).search(str(left)[:MAX_TEXT_FOR_REGEX])
        if m and op == "matches":
            captures.update({k: v for k, v in m.groupdict().items() if v is not None})
            captures.setdefault("match", m.group(0))
            for i, g in enumerate(m.groups(), start=1):
                captures.setdefault(f"group{i}", g)
        return (m is not None) == (op == "matches")
    if op == "wildcard":
        return fnmatch.fnmatchcase(norm(left), norm(right))
    if op in ("contains_any", "contains_all"):
        words = [norm(w) for w in _as_list(right)]
        hay = norm(left)
        hits = [w in hay for w in words]
        return any(hits) if op == "contains_any" else bool(words) and all(hits)
    if op in ("in", "not_in"):
        options = [norm(x) for x in _as_list(right)]
        return (norm(left) in options) == (op == "in")
    if op in ("gt", "gte", "lt", "lte", "length_gt"):
        lv = len(left) if op == "length_gt" and hasattr(left, "__len__") else _number(left)
        rv = _number(right)
        if lv is None or rv is None:
            return False
        return {
            "gt": lv > rv,
            "gte": lv >= rv,
            "lt": lv < rv,
            "lte": lv <= rv,
            "length_gt": lv > rv,
        }[op]
    raise ExprError(f"unknown operator {op!r}")


def condition_errors(cond: Any, where: str = "condition") -> list[str]:
    """Structural problems in a condition, for validation before saving."""
    errs: list[str] = []
    if cond in (None, {}):
        return errs
    if not isinstance(cond, dict):
        return [f"{where}: must be an object"]
    for key in ("all", "any", "none"):
        if key in cond:
            if not isinstance(cond[key], list):
                return [f"{where}.{key}: must be a list"]
            for i, c in enumerate(cond[key]):
                errs += condition_errors(c, f"{where}.{key}[{i}]")
            return errs
    if "not" in cond:
        return condition_errors(cond["not"], f"{where}.not")
    if "expr" in cond:
        err = check_template("{{ " + str(cond["expr"]) + " }}")
        return [f"{where}: {err}"] if err else []
    op = cond.get("op")
    if op not in OPERATORS:
        return [f"{where}: unknown operator {op!r} (one of: {', '.join(OPERATORS)})"]
    if not cond.get("field"):
        errs.append(f"{where}: needs a field (a path such as ticket.summary, or * for any text)")
    if op in ("matches", "not_matches"):
        try:
            _compile(str(cond.get("value", "")))
        except ExprError as exc:
            errs.append(f"{where}: {exc}")
    if op not in NO_VALUE_OPS and op != "changed" and "value" not in cond:
        errs.append(f"{where}: operator {op!r} needs a value")
    return errs


def check_condition(
    cond: Any, data: dict[str, Any], *, ctx: dict[str, Any] | None = None
) -> tuple[bool, dict[str, Any]]:
    """(matched, captures). `data` is what field paths are looked up in; `ctx`
    is the template context for values and expressions (defaults to data)."""
    captures: dict[str, Any] = {}
    ok = _check(cond, data, ctx if ctx is not None else data, captures)
    return ok, captures


def _check(cond: Any, data: dict[str, Any], ctx: dict[str, Any], cap: dict[str, Any]) -> bool:
    if cond in (None, {}):
        return True
    if "all" in cond:
        return all(_check(c, data, ctx, cap) for c in cond["all"])
    if "any" in cond:
        return any(_check(c, data, ctx, cap) for c in cond["any"])
    if "none" in cond:
        return not any(_check(c, data, ctx, {}) for c in cond["none"])
    if "not" in cond:
        return not _check(cond["not"], data, ctx, {})
    if "expr" in cond:
        return bool(evaluate(str(cond["expr"]), ctx))
    op = str(cond.get("op"))
    cs = bool(cond.get("case_sensitive", False))
    field = str(cond.get("field", ""))
    right = render(cond.get("value"), ctx)
    if op == "changed":
        prev = get_path(data.get("previous", {}), field)
        cur = get_path(data, field)
        frm, to = cond.get("from"), cond.get("to")
        if cur is _MISSING or prev is _MISSING or str(prev) == str(cur):
            return False
        return (frm in (None, "") or _compare("equals", prev, frm, cs, {})) and (
            to in (None, "") or _compare("equals", cur, to, cs, {})
        )
    if field == "*":  # any text anywhere in the event
        local: dict[str, Any] = {}
        hit = any(_compare(op, leaf, right, cs, local) for leaf in _text_leaves(data))
        if hit:
            cap.update(local)
        return hit
    left = render(field, ctx) if "{{" in field else get_path(data, field)
    local = {}
    hit = _compare(op, left, right, cs, local)
    if hit:
        cap.update(local)
    return hit
