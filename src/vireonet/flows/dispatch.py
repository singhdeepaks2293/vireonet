"""Start flow runs: manually, or when an event matches enabled flows' triggers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from vireonet.flows.store import FlowStore
from vireonet.flows.triggers import event_trigger_type, match
from vireonet.structlog_config import get_logger

log = get_logger(__name__)

# (run_id) -> None: starts the Temporal FlowWorkflow for a queued run.
Starter = Callable[[str], Awaitable[Any]]


class FlowDispatcher:
    def __init__(self, store: FlowStore, starter: Starter | None) -> None:
        self.store = store
        self._start = starter

    async def _launch(self, run: dict[str, Any]) -> dict[str, Any]:
        if self._start is None:
            await self.store.finish(
                run["id"],
                {
                    "status": "failed",
                    "error": "flows need the Temporal worker (VIREONET_TEMPORAL_MODE=real)",
                },
            )
            run["status"] = "failed"
            return run
        await self._start(run["id"])
        return run

    async def run_manual(
        self, flow_id: str, *, inputs: dict[str, Any], user: str, version: int | None = None
    ) -> dict[str, Any]:
        flow = await self.store.get(flow_id, version)
        run = await self.store.create_run(
            flow,
            trigger_type="manual",
            event={"source": "manual", "inputs": inputs, "user": user},
            inputs=inputs,
            started_by=user,
        )
        assert run is not None
        return await self._launch(run)

    async def run_webhook(self, flow_id: str, body: Any, headers: dict[str, str]) -> dict[str, Any]:
        """The flow's own webhook: always this flow, its published version."""
        flow = await self.store.get(flow_id)
        if flow["published_version"] is None or not flow["enabled"]:
            return {"status": "ignored", "reason": "the flow is not published and enabled"}
        flow = await self.store.get(flow_id, flow["published_version"])
        event = {"source": "webhook", "body": body, "headers": headers}
        trig = flow["trigger"] if flow["trigger"].get("type") == "webhook" else {"type": "webhook"}
        m = match(trig, event)
        if not m.matched:
            return {"status": "ignored", "reason": m.reason}
        run = await self.store.create_run(
            flow,
            trigger_type="webhook",
            event={**event, "match": m.captures},
            dedup_key=m.dedup_key,
            started_by="webhook",
        )
        if run is None:
            return {"status": "duplicate", "dedup_key": m.dedup_key}
        return {"status": "started", "run": await self._launch(run)}

    async def dispatch(
        self, event: dict[str, Any], *, started_by: str = ""
    ) -> list[dict[str, Any]]:
        """Every enabled, published flow whose trigger matches this event.
        Returns one entry per flow considered (started / duplicate / not matched)."""
        ttype = event_trigger_type(event)
        out: list[dict[str, Any]] = []
        for flow in await self.store.triggered_flows({ttype}):
            m = match(flow["trigger"], event)
            if not m.matched:
                out.append({"flow": flow["name"], "status": "not_matched", "reason": m.reason})
                continue
            run = await self.store.create_run(
                flow,
                trigger_type=ttype,
                event={**event, "match": m.captures},
                dedup_key=m.dedup_key,
                started_by=started_by or ttype,
            )
            if run is None:
                out.append({"flow": flow["name"], "status": "duplicate", "dedup_key": m.dedup_key})
                continue
            try:
                run = await self._launch(run)
            except Exception as exc:  # one broken flow must not stop the others
                log.warning("flow start failed", flow=flow["name"], error=str(exc))
                await self.store.finish(
                    run["id"], {"status": "failed", "error": f"could not start: {exc}"}
                )
                out.append({"flow": flow["name"], "status": "failed", "error": str(exc)})
                continue
            out.append(
                {
                    "flow": flow["name"],
                    "status": "started",
                    "run_id": run["id"],
                    "captures": m.captures,
                }
            )
        if out:
            log.info(
                "flow trigger dispatch",
                source=ttype,
                results=[(r["flow"], r["status"]) for r in out],
            )
        return out


def safe_dispatch(
    dispatcher: FlowDispatcher | None,
    *,
    log_backends: dict[str, Any] | None = None,
    export_to: list[str] | None = None,
) -> Callable[[dict[str, Any]], Awaitable[list[dict[str, Any]]]]:
    """An emitter for VireoNet's own events that never raises: starts matching
    flows and (when configured) exports the event to log platforms."""

    async def emit(event: dict[str, Any]) -> list[dict[str, Any]]:
        if log_backends and export_to:
            from vireonet.logsearch import export_event

            for err in await export_event(log_backends, list(export_to), event):
                log.warning("event export failed", event_name=event.get("event"), error=err)
        if dispatcher is None:
            return []
        try:
            return await dispatcher.dispatch(event, started_by="vireonet")
        except Exception as exc:
            log.warning("flow event dispatch failed", event_name=event.get("event"), error=str(exc))
            return []

    return emit
