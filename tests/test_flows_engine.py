"""Workflow builder core: expressions, conditions, validation, interpreter."""

from __future__ import annotations

from typing import Any

import pytest

from vireonet.flows.catalog import STEPS, TRIGGERS, catalog
from vireonet.flows.engine import Interpreter, MemoryRuntime
from vireonet.flows.expr import (
    ExprError,
    base_context,
    check_condition,
    condition_errors,
    mask_secrets,
    render,
    resolve_secrets,
)
from vireonet.flows.schema import (
    FlowValidationError,
    ensure_valid,
    normalise,
    uses_scripts,
    validate_definition,
    validate_trigger,
)

TICKET = {
    "ticket": {
        "key": "CHG0030005",
        "summary": "OS UPGRADE leaf-01 to 17.9.4",
        "status": "Implement",
        "fields": {"category": "Network", "priority": "2", "devices": ["leaf-01", "leaf-03"]},
    },
    "previous": {"ticket": {"status": "Scheduled"}},
}


# --- templates ---------------------------------------------------------------------------


def test_render_keeps_types_for_single_expressions() -> None:
    ctx = base_context(vars={"n": 3, "devs": ["a", "b"]}, trigger=TICKET)
    assert render("{{ vars.devs }}", ctx) == ["a", "b"]
    assert render("{{ vars.n + 1 }}", ctx) == 4
    assert render("n={{ vars.n }}", ctx) == "n=3"
    assert render({"k": ["{{ trigger.ticket.key }}"]}, ctx) == {"k": ["CHG0030005"]}
    assert render({"{{ vars.devs[0] }}": "cfg {{ vars.n }}"}, ctx) == {"a": "cfg 3"}  # keys too
    assert render("{{ trigger.ticket.summary | regex_findall('\\d+\\.\\d+\\.\\d+') }}", ctx) == [
        "17.9.4"
    ]
    with pytest.raises(ExprError, match="no attribute 'missing'"):
        render("{{ vars.missing }}", ctx)


def test_sandbox_blocks_python_internals() -> None:
    with pytest.raises(ExprError):
        render("{{ ''.__class__.__mro__[1].__subclasses__() }}", base_context())


def test_secrets_never_render_in_clear() -> None:
    out = render("Bearer {{ secrets.NETBOX_TOKEN }}", base_context())
    assert "NETBOX_TOKEN" in out and "abc" not in out
    assert mask_secrets(out) == "Bearer <secret NETBOX_TOKEN>"
    assert resolve_secrets({"h": out}, lambda n: "abc") == {"h": "Bearer abc"}


# --- conditions ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cond", "expected"),
    [
        ({"field": "ticket.summary", "op": "contains", "value": "os upgrade"}, True),
        (
            {
                "field": "ticket.summary",
                "op": "contains",
                "value": "os upgrade",
                "case_sensitive": True,
            },
            False,
        ),
        ({"field": "ticket.summary", "op": "starts_with", "value": "OS"}, True),
        ({"field": "ticket.summary", "op": "ends_with", "value": "17.9.4"}, True),
        ({"field": "ticket.summary", "op": "wildcard", "value": "os upgrade *"}, True),
        ({"field": "ticket.summary", "op": "contains_any", "value": ["reboot", "upgrade"]}, True),
        ({"field": "ticket.summary", "op": "contains_all", "value": "upgrade, leaf"}, True),
        ({"field": "ticket.summary", "op": "not_contains", "value": "rollback"}, True),
        ({"field": "ticket.fields.priority", "op": "in", "value": ["1", "2"]}, True),
        ({"field": "ticket.fields.priority", "op": "lte", "value": 2}, True),
        ({"field": "ticket.fields.priority", "op": "gt", "value": 2}, False),
        ({"field": "ticket.fields.devices", "op": "contains", "value": "leaf-03"}, True),
        ({"field": "ticket.fields.devices", "op": "length_gt", "value": 1}, True),
        ({"field": "ticket.fields.owner", "op": "missing"}, True),
        ({"field": "ticket.fields.owner", "op": "not_equals", "value": "x"}, True),
        ({"field": "ticket.status", "op": "changed", "from": "Scheduled", "to": "Implement"}, True),
        ({"field": "ticket.status", "op": "changed", "to": "Closed"}, False),
        ({"field": "*", "op": "contains", "value": "17.9.4"}, True),
        ({"expr": "trigger.ticket.fields.category | lower == 'network'"}, True),
        ({"none": [{"field": "ticket.summary", "op": "contains", "value": "TEST"}]}, True),
        ({"not": {"field": "ticket.status", "op": "equals", "value": "implement"}}, False),
        (
            {
                "any": [
                    {"field": "ticket.key", "op": "equals", "value": "X"},
                    {"field": "ticket.key", "op": "starts_with", "value": "CHG"},
                ]
            },
            True,
        ),
    ],
)
def test_condition_operators(cond: dict[str, Any], expected: bool) -> None:
    ok, _ = check_condition(cond, TICKET, ctx=base_context(trigger=TICKET, **TICKET))
    assert ok is expected


def test_regex_captures_named_groups() -> None:
    cond = {
        "all": [
            {
                "field": "ticket.summary",
                "op": "matches",
                "value": r"^OS UPGRADE (?P<device>\S+) to (?P<version>[\d.]+)$",
            },
        ]
    }
    ok, cap = check_condition(cond, TICKET)
    assert ok and cap["device"] == "leaf-01" and cap["version"] == "17.9.4" and cap["match"]


def test_condition_validation_messages() -> None:
    assert condition_errors({"field": "x", "op": "nope", "value": 1})[0].startswith(
        "condition: unknown operator"
    )
    assert "bad regex" in condition_errors({"field": "x", "op": "matches", "value": "(unclosed"})[0]
    assert condition_errors({"field": "x", "op": "contains"}) == [
        "condition: operator 'contains' needs a value"
    ]
    assert condition_errors({"all": [{"field": "x", "op": "exists"}]}) == []
    with pytest.raises(ExprError, match="longer"):
        check_condition({"field": "a", "op": "matches", "value": "a" * 600}, {"a": "a"})


# --- validation ----------------------------------------------------------------------------


def test_catalog_shape() -> None:
    c = catalog()
    assert {s["type"] for s in c["steps"]} == set(STEPS)
    assert {"itsm", "netbox", "zabbix", "prometheus", "git", "kafka", "schedule", "webhook"} <= set(
        TRIGGERS
    )
    assert all(t.get("event") is not None for t in c["triggers"])
    assert {"run_if", "retry_count", "continue_on_error", "run_after"} <= {
        f["name"] for f in c["step_settings"]
    }


def test_normalise_assigns_ids_and_validation_finds_problems() -> None:
    d = normalise(
        {
            "steps": [
                {"type": "log", "message": "a"},
                {
                    "type": "if",
                    "branches": [
                        {"condition": {"expr": "true"}, "steps": [{"type": "log", "message": "b"}]}
                    ],
                },
            ]
        }
    )
    ids = [d["steps"][0]["id"], d["steps"][1]["id"], d["steps"][1]["branches"][0]["steps"][0]["id"]]
    assert len(set(ids)) == 3
    errs = validate_definition(
        {
            "steps": [
                {"id": "a", "type": "log"},
                {"id": "a", "type": "unicorn"},
                {
                    "id": "c",
                    "type": "while",
                    "condition": {"field": "x", "op": "exists"},
                    "max_iterations": 5000,
                    "steps": [],
                },
                {"id": "d", "type": "if", "branches": []},
                {"id": "e", "type": "log", "message": "{{ broken", "bogus": 1},
                {"id": "f", "type": "script", "code": "result = 1"},
            ]
        },
        allow_scripts=False,
    )
    text = "\n".join(errs)
    for expected in [
        "Message is required",
        "duplicate step id",
        "unknown step type",
        "max_iterations must be between",
        "at least one condition branch",
        "unknown field 'bogus'",
        "only be saved by an admin",
    ]:
        assert expected in text, expected
    assert any(
        "e:log" in e
        and "unexpected" in e.lower()
        or "e:log" in e
        and "end of template" in e.lower()
        for e in errs
    )


def test_trigger_validation() -> None:
    assert validate_trigger({"type": "schedule"}) == [
        "trigger: a schedule needs cron or every_minutes"
    ]
    assert "bad cron" in validate_trigger({"type": "schedule", "cron": "61 * * * *"})[0]
    assert validate_trigger({"type": "schedule", "cron": "0 2 * * 1-5"}) == []
    assert validate_trigger({"type": "kafka"}) == ["trigger: a Kafka trigger needs a topic"]
    assert "unknown type" in validate_trigger({"type": "carrier-pigeon"})[0]
    with pytest.raises(FlowValidationError):
        ensure_valid({"steps": [{"type": "fail"}]}, {"type": "manual"}, allow_scripts=True)
    assert uses_scripts({"steps": [{"type": "try", "steps": [{"type": "script", "code": "x"}]}]})


# --- interpreter ------------------------------------------------------------------------------


async def run(
    steps: list[dict[str, Any]], *, actions: Any = None, trigger: Any = None, **kw: Any
) -> tuple[dict[str, Any], MemoryRuntime]:
    rt = MemoryRuntime(actions, approvals=kw.pop("approvals", None))
    d = ensure_valid({"steps": steps, **kw}, {"type": "manual"}, allow_scripts=True)
    return await Interpreter(rt).run(d, trigger=trigger or {}, inputs=kw.get("inputs_given")), rt


async def test_variables_if_elif_else_and_outputs() -> None:
    steps = [
        {"type": "set_variable", "name": "version", "value": "{{ trigger.match.version }}"},
        {
            "type": "if",
            "branches": [
                {
                    "condition": {"field": "vars.version", "op": "starts_with", "value": "16."},
                    "steps": [{"type": "set_variable", "name": "path", "value": "old"}],
                },
                {
                    "condition": {
                        "field": "vars.version",
                        "op": "matches",
                        "value": r"^17\.(?P<minor>\d+)",
                    },
                    "steps": [
                        {"type": "set_variable", "name": "path", "value": "17-{{ match.minor }}"}
                    ],
                },
            ],
            "else": [{"type": "set_variable", "name": "path", "value": "unknown"}],
        },
    ]
    result, _ = await run(
        steps, trigger={"match": {"version": "17.9.4"}}, outputs={"path": "{{ vars.path }}"}
    )
    assert result["status"] == "succeeded" and result["outputs"] == {"path": "17-9"}


async def test_loops_switch_break_continue() -> None:
    steps = [
        {"type": "set_variable", "name": "seen", "value": "{{ [] }}"},
        {
            "type": "for_each",
            "items": ["leaf-01", "skip-me", "leaf-03", "stop", "never"],
            "as": "dev",
            "steps": [
                {
                    "type": "if",
                    "branches": [
                        {
                            "condition": {"field": "vars.dev", "op": "equals", "value": "skip-me"},
                            "steps": [{"type": "continue"}],
                        }
                    ],
                },
                {
                    "type": "if",
                    "branches": [
                        {
                            "condition": {"field": "vars.dev", "op": "equals", "value": "stop"},
                            "steps": [{"type": "break"}],
                        }
                    ],
                },
                {"type": "append_to_list", "name": "seen", "value": "{{ vars.dev }}"},
            ],
        },
        {"type": "set_variable", "name": "n", "value": 0},
        {
            "type": "while",
            "condition": {"field": "vars.n", "op": "lt", "value": 3},
            "max_iterations": 10,
            "steps": [{"type": "set_variable", "name": "n", "value": "{{ vars.n + 1 }}"}],
        },
        {
            "type": "repeat",
            "times": 2,
            "as": "k",
            "steps": [{"type": "append_to_list", "name": "seen", "value": "r{{ vars.k }}"}],
        },
        {
            "type": "switch",
            "value": "{{ vars.n }}",
            "cases": [
                {"value": [1, 2], "steps": []},
                {"value": 3, "steps": [{"type": "set_variable", "name": "case", "value": "three"}]},
            ],
            "default": [],
        },
    ]
    result, _ = await run(steps)
    assert result["vars"]["seen"] == ["leaf-01", "leaf-03", "r1", "r2"]
    assert result["vars"]["n"] == 3 and result["vars"]["case"] == "three"


async def test_while_stops_at_max_iterations_and_loop_limits() -> None:
    result, rt = await run(
        [
            {
                "type": "while",
                "condition": {"expr": "true"},
                "max_iterations": 4,
                "delay_seconds": 5,
                "steps": [{"type": "log", "message": "x"}],
            }
        ]
    )
    assert result["status"] == "succeeded" and rt.slept == [5, 5, 5, 5]
    assert any(e.get("status") == "warning" for e in rt.log)
    too_many, _ = await run(
        [{"type": "for_each", "items": list(range(5)), "max_iterations": 3, "steps": []}]
    )
    assert too_many["status"] == "failed" and "more than the maximum 3" in too_many["error"]


async def test_try_catch_finally_and_continue_on_error_and_run_after() -> None:
    calls: list[str] = []

    async def actions(t: str, p: dict[str, Any]) -> Any:
        calls.append(t)
        if t == "http_request":
            raise RuntimeError("HTTP 503 from api.example")
        return {"ok": True}

    steps = [
        {
            "type": "try",
            "steps": [
                {"type": "http_request", "url": "https://api.example/x"},
                {"type": "log", "message": "not reached"},
            ],
            "catch": [
                {"type": "set_variable", "name": "caught", "value": "{{ vars.error.message }}"}
            ],
            "finally": [{"type": "set_variable", "name": "cleanup", "value": True}],
        },
        {
            "id": "risky",
            "type": "http_request",
            "url": "https://api.example/y",
            "continue_on_error": True,
        },
        {"type": "set_variable", "name": "after_success_only", "value": 1},
        {
            "type": "set_variable",
            "name": "on_failure",
            "value": "{{ vars.error.step }}",
            "run_after": "failure",
        },
        {"type": "notify", "message": "always", "run_after": "always"},
    ]
    result, rt = await run(steps, actions=actions)
    v = result["vars"]
    assert result["status"] == "succeeded"
    assert "HTTP 503" in v["caught"] and v["cleanup"] is True
    assert "after_success_only" not in v  # previous step failed (continue_on_error)
    assert calls == ["http_request", "http_request", "notify"]
    statuses = [e["status"] for e in rt.log if e.get("type") == "set_variable"]
    assert "skipped" in statuses


async def test_retries_with_backoff_then_success() -> None:
    attempts = {"n": 0}

    async def flaky(t: str, p: dict[str, Any]) -> Any:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("timeout")
        return {"ok": True}

    result, rt = await run(
        [
            {
                "type": "http_request",
                "url": "https://x",
                "retry_count": 3,
                "retry_delay_seconds": 2,
                "retry_backoff": 3,
            }
        ],
        actions=flaky,
    )
    assert result["status"] == "succeeded" and rt.slept == [2, 6]
    assert [e["status"] for e in rt.log] == ["retrying", "retrying", "succeeded"]


async def test_run_if_disabled_save_as_and_magic_step_outputs() -> None:
    async def actions(t: str, p: dict[str, Any]) -> Any:
        return {"platform": "ios-xe", "name": p["device"]}

    steps = [
        {"id": "dev", "type": "get_device", "device": "leaf-01", "save_as": "device"},
        {"type": "set_variable", "name": "p", "value": "{{ steps.dev.platform }}"},
        {
            "type": "set_variable",
            "name": "skipped",
            "value": 1,
            "run_if": {"field": "vars.device.platform", "op": "equals", "value": "nxos"},
        },
        {"type": "set_variable", "name": "off", "value": 1, "disabled": True},
    ]
    result, _ = await run(steps, actions=actions)
    assert result["vars"]["p"] == "ios-xe" and result["vars"]["device"]["name"] == "leaf-01"
    assert "skipped" not in result["vars"] and "off" not in result["vars"]


async def test_parallel_and_concurrent_for_each() -> None:
    seen: list[str] = []

    async def actions(t: str, p: dict[str, Any]) -> Any:
        seen.append(p["message"])
        return {}

    steps = [
        {
            "type": "parallel",
            "branches": [
                {"name": "a", "steps": [{"type": "notify", "message": "A"}]},
                {"name": "b", "steps": [{"type": "notify", "message": "B"}]},
            ],
        },
        {
            "id": "fe",
            "type": "for_each",
            "items": ["x", "y", "z"],
            "concurrency": 2,
            "steps": [{"type": "set_variable", "name": "mine", "value": "{{ vars.item }}!"}],
        },
    ]
    result, _ = await run(steps, actions=actions)
    assert sorted(seen) == ["A", "B"]
    assert result["status"] == "succeeded"


async def test_approval_fail_stop_and_inputs() -> None:
    rejected, _ = await run(
        [{"id": "ok", "type": "approval", "message": "Upgrade?"}],
        approvals={"ok": {"approved": False, "by": "bob", "comment": "not today"}},
    )
    assert rejected["status"] == "failed" and "not approved by bob: not today" in rejected["error"]
    approved, _ = await run(
        [{"id": "ok", "type": "approval", "message": "Upgrade?", "save_as": "decision"}],
        approvals={"ok": {"approved": True, "by": "alice"}},
    )
    assert approved["vars"]["decision"]["by"] == "alice"
    stopped, _ = await run(
        [{"type": "stop", "message": "nothing to do"}, {"type": "fail", "message": "never"}]
    )
    assert stopped["status"] == "succeeded" and stopped["message"] == "nothing to do"
    failed, _ = await run([{"type": "fail", "message": "boom {{ 1 + 1 }}"}])
    assert failed == {**failed, "status": "failed", "error": "boom 2"}
    rt = MemoryRuntime()
    d = ensure_valid(
        {"inputs": [{"name": "device", "type": "device", "required": True}], "steps": []},
        None,
        allow_scripts=True,
    )
    assert (await Interpreter(rt).run(d, inputs={}))["error"] == "input 'device' is required"


async def test_runaway_loops_are_stopped() -> None:
    from vireonet.flows import engine

    old = engine.MAX_EXECUTED_STEPS
    engine.MAX_EXECUTED_STEPS = 50
    try:
        result, _ = await run(
            [{"type": "repeat", "times": 1000, "steps": [{"type": "log", "message": "x"}]}]
        )
    finally:
        engine.MAX_EXECUTED_STEPS = old
    assert result["status"] == "failed" and "more than 50 steps" in result["error"]
