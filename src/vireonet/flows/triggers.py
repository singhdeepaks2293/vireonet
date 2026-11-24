"""Does an event start a flow? Trigger type, event filter, conditions, dedup."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from vireonet.flows.expr import ExprError, base_context, check_condition, render
from vireonet.flows.sources import TRIGGER_FOR


@dataclass
class TriggerMatch:
    matched: bool
    reason: str
    captures: dict[str, Any] = field(default_factory=dict)
    dedup_key: str = ""


def event_trigger_type(event: dict[str, Any]) -> str:
    src = str(event.get("source", "manual"))
    return TRIGGER_FOR.get(src, src)


def _default_dedup(ttype: str, event: dict[str, Any]) -> str:
    if ttype == "itsm":
        t = event.get("ticket") or {}
        return f"{t.get('key', '')}:{t.get('status', '')}"
    if ttype == "kafka":  # redelivery after a restart must not run twice
        return f"{event.get('topic', '')}:{event.get('partition', '')}:{event.get('offset', '')}"
    if ttype == "zabbix":
        return f"{event.get('event_id', '')}:{event.get('event', '')}"
    if ttype == "prometheus":
        return (
            f"{event.get('fingerprint', '')}:{event.get('event', '')}:{event.get('starts_at', '')}"
        )
    return ""


def _strip_prefix(cond: Any) -> Any:
    if isinstance(cond, list):
        return [_strip_prefix(c) for c in cond]
    if not isinstance(cond, dict):
        return cond
    out = {
        k: _strip_prefix(v) if k in ("all", "any", "none", "not") else v for k, v in cond.items()
    }
    field_path = out.get("field")
    if isinstance(field_path, str) and field_path.startswith("trigger."):
        out["field"] = field_path.removeprefix("trigger.")
    return out


def match(trigger: dict[str, Any] | None, event: dict[str, Any]) -> TriggerMatch:
    trig = trigger or {"type": "manual"}
    ttype = str(trig.get("type", "manual"))
    etype = event_trigger_type(event)
    if ttype != etype:
        return TriggerMatch(False, f"trigger type is {ttype}, event is {etype}")
    wanted = [str(e).lower() for e in trig.get("events") or []]
    if wanted and str(event.get("event", "")).lower() not in wanted:
        return TriggerMatch(False, f"event {event.get('event')!r} not in {wanted}")
    if ttype == "itsm" and trig.get("states"):
        status = str((event.get("ticket") or {}).get("status", "")).casefold()
        if status not in {str(s).casefold() for s in trig["states"]}:
            return TriggerMatch(False, f"ticket state {status!r} not in {trig['states']}")
    if ttype == "kafka" and trig.get("topic") and trig["topic"] != event.get("topic"):
        return TriggerMatch(False, f"topic {event.get('topic')!r} is not {trig['topic']!r}")
    # Field paths are relative to the event; "trigger.ticket.key" (the same
    # path the flow's steps use) also works. An event may have its own field
    # called "trigger" (Zabbix does), so the alias is only a path prefix.
    ctx = base_context(**{k: v for k, v in event.items() if k != "secrets"})
    ctx["trigger"] = event
    try:
        ok, captures = check_condition(_strip_prefix(trig.get("conditions")), event, ctx=ctx)
    except ExprError as exc:
        return TriggerMatch(False, f"condition error: {exc}")
    if not ok:
        return TriggerMatch(False, "conditions did not match")
    key = trig.get("dedup_key")
    if key == "none":
        dedup = ""
    elif key:
        try:
            dedup = str(render(key, {**ctx, "match": captures}))
        except ExprError as exc:
            return TriggerMatch(False, f"dedup_key error: {exc}")
    else:
        dedup = _default_dedup(ttype, event)
    return TriggerMatch(True, "matched", captures, dedup)
