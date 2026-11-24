"""Pre/Post checks: profiles, tests on parsed (TextFSM) data, intelligent diff.

A *profile* (saved template) lists commands and tests::

    name: vlan-and-software
    commands:
      - id: vlans
        command: {default: "show vlan brief", junos: "show vlans"}
        key: [vlan_id]                  # identifies a row across snapshots
        ignore_fields: []               # never compared (counters, uptime, …)
        tolerances: {}                  # numeric fields: {field: {pct: 5} | {abs: 10}}
        raw_ignore: ['uptime is .*']    # regexes removed before the raw diff
    tests:
      - {name: vlan-1-present, command: vlans, type: row_exists, where: {vlan_id: "1"}}
      - {name: all-active, command: vlans, type: all_rows, expect: {status: {op: equals, value: active}}}
      - {name: none-removed, command: vlans, type: no_rows_removed, phase: compare}

**Tests run only on TextFSM-parsed rows** (no screen-scraping); the **diff**
compares both the parsed rows (by key, field by field, with ignored fields and
numeric tolerances) and the raw text (unified diff after removing volatile
lines). Pure functions: the service captures snapshots, this module judges them.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

TEST_TYPES = {
    "row_exists": "at least one row matches `where`",
    "no_rows": "no row matches `where` (e.g. no interface is down)",
    "all_rows": "every row (matching `where`) satisfies `expect`",
    "count": "number of rows (matching `where`) compared with `value` using `op`",
    "field_range": "a numeric field is between `min` and `max` on every matching row",
    "no_rows_removed": "compare: no keyed row of pre is missing in post",
    "no_rows_added": "compare: post has no keyed row that pre did not have",
    "unchanged": "compare: listed `fields` keep their value on every keyed row",
    "delta": "compare: sum or count of a field changes by at most `max_pct` % / `max_abs`",
}
OPS = {
    "equals",
    "not_equals",
    "contains",
    "not_contains",
    "matches",
    "in",
    "not_in",
    "gt",
    "gte",
    "lt",
    "lte",
    "exists",
    "empty",
}
COMPARE_TESTS = {"no_rows_removed", "no_rows_added", "unchanged", "delta"}


class ProfileError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


# --- validation ------------------------------------------------------------------------


def validate_profile(p: Any) -> list[str]:
    errs: list[str] = []
    if not isinstance(p, dict):
        return ["the profile must be an object"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}", str(p.get("name", ""))):
        errs.append("name: letters, digits, space, _ . - (max 80)")
    cmds = p.get("commands") or []
    if not isinstance(cmds, list) or not cmds:
        errs.append("commands: at least one command")
        cmds = []
    ids = set()
    for i, c in enumerate(cmds):
        where = f"commands[{i}]"
        if not isinstance(c, dict):
            errs.append(f"{where}: must be an object")
            continue
        cid = str(c.get("id", ""))
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", cid):
            errs.append(f"{where}: id must be lowercase letters, digits, _")
        if cid in ids:
            errs.append(f"{where}: duplicate id {cid!r}")
        ids.add(cid)
        cmd = c.get("command")
        cmd_map = cmd if isinstance(cmd, dict) else {"default": cmd}
        if not cmd_map or not all(isinstance(v, str) and v.strip() for v in cmd_map.values()):
            errs.append(f"{where}: command must be text or {{platform: text}}")
        for v in cmd_map.values():
            if isinstance(v, str) and not v.strip().lower().startswith(
                ("show ", "display ", "get ")
            ):
                errs.append(f"{where}: {v!r} is not a read-only command (show / display / get)")
        for rx in c.get("raw_ignore") or []:
            try:
                re.compile(rx)
            except re.error as exc:
                errs.append(f"{where}: bad raw_ignore regex {rx!r}: {exc}")
        for f, t in (c.get("tolerances") or {}).items():
            if not isinstance(t, dict) or not ({"pct", "abs"} & set(t)):
                errs.append(f"{where}: tolerance for {f!r} needs pct or abs")
    for i, t in enumerate(p.get("tests") or []):
        where = f"tests[{i}]"
        if not isinstance(t, dict):
            errs.append(f"{where}: must be an object")
            continue
        if t.get("type") not in TEST_TYPES:
            errs.append(f"{where}: type must be one of {', '.join(TEST_TYPES)}")
        if t.get("command") not in ids:
            errs.append(f"{where}: command must be one of the command ids {sorted(ids)}")
        if not t.get("name"):
            errs.append(f"{where}: name is required")
        if t.get("type") in ("unchanged",) and not t.get("fields"):
            errs.append(f"{where}: unchanged needs fields")
        for spec in (t.get("expect") or {}).values():
            if isinstance(spec, dict) and spec.get("op") not in OPS:
                errs.append(f"{where}: expect op must be one of {sorted(OPS)}")
    return errs


def ensure_profile(p: Any) -> dict[str, Any]:
    errs = validate_profile(p)
    if errs:
        raise ProfileError(errs)
    return dict(p)


def command_for(c: dict[str, Any], platform: str) -> str:
    cmd = c.get("command")
    if isinstance(cmd, str):
        return cmd
    if not isinstance(cmd, dict):
        return ""
    return str(cmd.get(platform) or cmd.get(platform.split("-")[0]) or cmd.get("default") or "")


# --- matching ------------------------------------------------------------------------------


def _num(v: Any) -> float | None:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _match(value: Any, spec: Any) -> bool:
    if not isinstance(spec, dict):
        spec = {"op": "equals", "value": spec}
    op, want = spec.get("op", "equals"), spec.get("value")
    sv = (
        ""
        if value is None
        else (" ".join(map(str, value)) if isinstance(value, list) else str(value))
    )
    if op == "exists":
        return value not in (None, "", [])
    if op == "empty":
        return value in (None, "", [])
    if op in ("gt", "gte", "lt", "lte"):
        a, b = _num(value), _num(want)
        if a is None or b is None:
            return False
        return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]
    w = str(want)
    if op == "equals":
        return sv.lower() == w.lower()
    if op == "not_equals":
        return sv.lower() != w.lower()
    if op == "contains":
        return w.lower() in sv.lower()
    if op == "not_contains":
        return w.lower() not in sv.lower()
    if op == "matches":
        return re.search(w, sv) is not None
    if op in ("in", "not_in"):
        opts = [str(x).lower() for x in (want if isinstance(want, list) else str(want).split(","))]
        return bool((sv.lower() in [o.strip() for o in opts]) == (op == "in"))
    return False


def _where(rows: list[dict[str, Any]], where: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not where:
        return rows
    return [r for r in rows if all(_match(r.get(k), v) for k, v in where.items())]


def _rowkey(row: dict[str, Any], key: list[str]) -> str:
    return "|".join(str(row.get(k, "")) for k in key) if key else ""


# --- tests ----------------------------------------------------------------------------------


def _result(test: dict[str, Any], device: str, ok: bool, message: str) -> dict[str, Any]:
    return {
        "name": test["name"],
        "type": test["type"],
        "command": test["command"],
        "device": device,
        "ok": ok,
        "message": message,
    }


def run_single(
    test: dict[str, Any], device: str, rows: list[dict[str, Any]] | None, parse_error: str = ""
) -> dict[str, Any]:
    if rows is None:
        return _result(
            test,
            device,
            False,
            f"no parsed data ({parse_error or 'TextFSM could not parse the output'}): tests need TextFSM rows",
        )
    sel = _where(rows, test.get("where"))
    t = test["type"]
    if t == "row_exists":
        return _result(test, device, bool(sel), f"{len(sel)} matching row(s)")
    if t == "no_rows":
        return _result(
            test, device, not sel, "none" if not sel else f"{len(sel)} row(s) match: {sel[:3]}"
        )
    if t == "all_rows":
        bad = [
            r
            for r in sel
            if not all(_match(r.get(f), spec) for f, spec in (test.get("expect") or {}).items())
        ]
        return _result(
            test,
            device,
            bool(sel) and not bad,
            f"{len(sel) - len(bad)}/{len(sel)} rows as expected"
            + (f"; first bad: {bad[0]}" if bad else ""),
        )
    if t == "count":
        ok = _match(len(sel), {"op": test.get("op", "gte"), "value": test.get("value", 1)})
        return _result(
            test,
            device,
            ok,
            f"{len(sel)} row(s), expected {test.get('op', 'gte')} {test.get('value', 1)}",
        )
    if t == "field_range":
        f, lo, hi = str(test.get("field", "")), _num(test.get("min")), _num(test.get("max"))
        vals = [(_num(r.get(f)), r) for r in sel]
        bad = [
            r
            for v, r in vals
            if v is None or (lo is not None and v < lo) or (hi is not None and v > hi)
        ]
        return _result(
            test,
            device,
            bool(vals) and not bad,
            f"{len(vals) - len(bad)}/{len(vals)} {f} values in [{lo}, {hi}]",
        )
    return _result(test, device, False, f"{t} is a compare test (needs pre and post)")


def run_compare(
    test: dict[str, Any],
    device: str,
    pre: list[dict[str, Any]] | None,
    post: list[dict[str, Any]] | None,
    key: list[str],
) -> dict[str, Any]:
    if pre is None or post is None:
        return _result(
            test,
            device,
            False,
            "pre or post has no parsed data: compare tests need TextFSM rows in both",
        )
    a = {_rowkey(r, key): r for r in _where(pre, test.get("where"))}
    b = {_rowkey(r, key): r for r in _where(post, test.get("where"))}
    t = test["type"]
    if t == "no_rows_removed":
        gone = sorted(set(a) - set(b))
        return _result(
            test, device, not gone, "none removed" if not gone else f"removed: {gone[:10]}"
        )
    if t == "no_rows_added":
        new = sorted(set(b) - set(a))
        return _result(test, device, not new, "none added" if not new else f"added: {new[:10]}")
    if t == "unchanged":
        changed = [
            f"{k}.{f}: {a[k].get(f)!r} → {b[k].get(f)!r}"
            for k in sorted(set(a) & set(b))
            for f in test.get("fields", [])
            if str(a[k].get(f)) != str(b[k].get(f))
        ]
        return _result(
            test, device, not changed, "unchanged" if not changed else "; ".join(changed[:5])
        )
    if t == "delta":
        f = test.get("field")
        if test.get("aggregate", "count") == "sum" and f:
            va = sum(_num(r.get(f)) or 0 for r in a.values())
            vb = sum(_num(r.get(f)) or 0 for r in b.values())
        else:
            va, vb = float(len(a)), float(len(b))
        diff = vb - va
        pct = abs(diff) / va * 100 if va else (0.0 if diff == 0 else 100.0)
        ok = (test.get("max_pct") is None or pct <= float(test["max_pct"])) and (
            test.get("max_abs") is None or abs(diff) <= float(test["max_abs"])
        )
        return _result(test, device, ok, f"{va:g} → {vb:g} ({diff:+g}, {pct:.1f} %)")
    return run_single(test, device, post)


def evaluate(
    profile: dict[str, Any], snapshot: dict[str, Any], pre: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Run the profile's tests. Single tests run on `snapshot`; compare tests
    need `pre` (snapshot is then the post)."""
    cmds = {c["id"]: c for c in profile["commands"]}
    out = []
    for device, per in sorted(snapshot.get("devices", {}).items()):
        for test in profile.get("tests") or []:
            phase = test.get("phase") or ("compare" if test["type"] in COMPARE_TESTS else "both")
            got = per.get(test["command"], {})
            if phase == "compare":
                if pre is None:
                    continue
                before = (pre.get("devices", {}).get(device) or {}).get(test["command"], {})
                out.append(
                    run_compare(
                        test,
                        device,
                        before.get("rows"),
                        got.get("rows"),
                        list(cmds[test["command"]].get("key") or []),
                    )
                )
            elif phase in ("both", snapshot.get("label")):
                out.append(run_single(test, device, got.get("rows"), got.get("error", "")))
    return out


# --- diff -------------------------------------------------------------------------------------


def _within(f: str, a: Any, b: Any, tolerances: dict[str, Any]) -> bool:
    if str(a) == str(b):
        return True
    tol = tolerances.get(f)
    na, nb = _num(a), _num(b)
    if tol is None or na is None or nb is None:
        return False
    if "abs" in tol and abs(nb - na) <= float(tol["abs"]):
        return True
    return "pct" in tol and (abs(nb - na) / abs(na) * 100 if na else 100.0) <= float(tol["pct"])


def parsed_diff(
    cmd: dict[str, Any], pre: list[dict[str, Any]] | None, post: list[dict[str, Any]] | None
) -> dict[str, Any]:
    if pre is None or post is None:
        return {"available": False}
    key = list(cmd.get("key") or [])
    ignore = set(cmd.get("ignore_fields") or [])
    tol = dict(cmd.get("tolerances") or {})
    if not key:  # no key: rows compared as a set
        sa = {repr(sorted((k, v) for k, v in r.items() if k not in ignore)) for r in pre}
        sb = {repr(sorted((k, v) for k, v in r.items() if k not in ignore)) for r in post}
        return {
            "available": True,
            "key": [],
            "added": len(sb - sa),
            "removed": len(sa - sb),
            "changed": [],
            "rows_added": [],
            "rows_removed": [],
        }
    a = {_rowkey(r, key): r for r in pre}
    b = {_rowkey(r, key): r for r in post}
    changed = []
    for k in sorted(set(a) & set(b)):
        fields = {
            f: {"pre": a[k].get(f), "post": b[k].get(f)}
            for f in sorted(set(a[k]) | set(b[k]))
            if f not in ignore and not _within(f, a[k].get(f), b[k].get(f), tol)
        }
        if fields:
            changed.append({"key": k, "fields": fields})
    return {
        "available": True,
        "key": key,
        "ignored": sorted(ignore),
        "tolerances": tol,
        "rows_added": [b[k] for k in sorted(set(b) - set(a))],
        "rows_removed": [a[k] for k in sorted(set(a) - set(b))],
        "added": len(set(b) - set(a)),
        "removed": len(set(a) - set(b)),
        "changed": changed,
    }


def raw_diff(cmd: dict[str, Any], pre: str | None, post: str | None) -> dict[str, Any]:
    if pre is None or post is None:
        return {"available": False}
    pats = [re.compile(rx) for rx in cmd.get("raw_ignore") or []]

    def clean(text: str) -> list[str]:
        out = []
        for line in text.splitlines():
            for p in pats:
                line = p.sub("<ignored>", line)
            out.append(line.rstrip())
        return out

    lines = list(difflib.unified_diff(clean(pre), clean(post), "pre", "post", lineterm="", n=1))
    return {"available": True, "changed": bool(lines), "diff": "\n".join(lines[:400])}


def compare(profile: dict[str, Any], pre: dict[str, Any], post: dict[str, Any]) -> dict[str, Any]:
    cmds = {c["id"]: c for c in profile["commands"]}
    devices = sorted(set(pre.get("devices", {})) | set(post.get("devices", {})))
    diffs = []
    for d in devices:
        for cid, c in cmds.items():
            a = (pre.get("devices", {}).get(d) or {}).get(cid, {})
            b = (post.get("devices", {}).get(d) or {}).get(cid, {})
            pd = parsed_diff(c, a.get("rows"), b.get("rows"))
            rd = raw_diff(c, a.get("raw"), b.get("raw"))
            diffs.append(
                {
                    "device": d,
                    "command": cid,
                    "parsed": pd,
                    "raw": rd,
                    "changed": bool(
                        pd.get("added")
                        or pd.get("removed")
                        or pd.get("changed")
                        or (not pd.get("available") and rd.get("changed"))
                    ),
                }
            )
    results = evaluate(profile, post, pre)
    return {
        "ok": all(r["ok"] for r in results),
        "tests": results,
        "diffs": diffs,
        "summary": {
            "tests": len(results),
            "failed": sum(not r["ok"] for r in results),
            "changed": sum(d["changed"] for d in diffs),
        },
    }
