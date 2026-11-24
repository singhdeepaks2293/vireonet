"""The flow interpreter: variables, control flow, retries, run-after, errors.

Side effects (API calls, scripts, devices, ITSM, waiting, people) go through
a Runtime. The Temporal FlowWorkflow is one runtime (durable activities,
timers and signals); tests use an in-memory one. Everything else here is
deterministic, so it replays safely inside a Temporal workflow.
"""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from vireonet.flows.catalog import CONTROLLER_WAIT_STEPS, STEPS
from vireonet.flows.expr import (
    ExprError,
    base_context,
    check_condition,
    mask_secrets,
    render,
)

MAX_EXECUTED_STEPS = 5000
MAX_LOG_VALUE = 4000

# Steps the interpreter runs itself (no activity).
LOCAL_STEPS = {
    "set_variable",
    "append_to_list",
    "log",
    "stop",
    "fail",
    "break",
    "continue",
}
CONTROL_STEPS = {"if", "switch", "for_each", "repeat", "while", "try", "parallel"}
RUNTIME_STEPS = {"wait", "approval", "config_push", "run_flow"}


class StepFailed(Exception):
    def __init__(self, message: str, step_id: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.step_id = step_id


class FlowCancelled(Exception):
    pass


class _Break(Exception):
    pass


class _Continue(Exception):
    pass


class _Stop(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class Runtime(Protocol):
    async def action(
        self, step_type: str, params: dict[str, Any], *, step_id: str, timeout: int
    ) -> Any: ...
    async def sleep(self, seconds: float) -> None: ...
    async def approval(self, step_id: str, params: dict[str, Any]) -> dict[str, Any]: ...
    async def config_push(self, step_id: str, params: dict[str, Any]) -> dict[str, Any]: ...
    async def run_flow(self, step_id: str, params: dict[str, Any]) -> dict[str, Any]: ...
    async def record(self, entry: dict[str, Any]) -> None: ...
    def now_iso(self) -> str: ...
    def cancelled(self) -> bool: ...


def _short(value: Any) -> Any:
    """Keep run-log entries small: large outputs are summarised."""
    try:
        text = json.dumps(value, default=str)
    except (TypeError, ValueError):
        text = str(value)
    if len(text) <= MAX_LOG_VALUE:
        return value
    return {"truncated": True, "preview": text[:MAX_LOG_VALUE], "size": len(text)}


def _params(step: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    spec = STEPS[step["type"]]
    out: dict[str, Any] = {}
    for f in spec["fields"]:
        name = f["name"]
        if name not in step and "default" in f:
            val: Any = f["default"]
        elif name in step:
            val = step[name]
        else:
            continue
        if f["kind"] in ("condition", "code"):
            out[name] = val
        else:
            out[name] = render(val, ctx)
    return out


class Interpreter:
    def __init__(self, runtime: Runtime) -> None:
        self.rt = runtime
        self.executed = 0

    async def run(
        self,
        definition: dict[str, Any],
        *,
        trigger: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        run: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        inputs = dict(inputs or {})
        for spec in definition.get("inputs") or []:
            name = spec.get("name")
            if name not in inputs and "default" in spec:
                inputs[name] = spec["default"]
            if spec.get("required") and inputs.get(name) in (None, ""):
                return {
                    "status": "failed",
                    "error": f"input {name!r} is required",
                    "vars": {},
                    "outputs": {},
                }
        ctx = base_context(
            trigger=trigger or {},
            inputs=inputs,
            vars=copy.deepcopy(definition.get("variables") or {}),
            steps={},
            run=run or {},
            error=None,
            match=(trigger or {}).get("match", {}),
        )
        status, error, message = "succeeded", "", ""
        try:
            await self.steps(definition.get("steps") or [], ctx, "steps")
        except _Stop as stop:
            message = stop.message
        except StepFailed as exc:
            status, error = "failed", exc.message
        except FlowCancelled:
            status, error = "cancelled", "cancelled by a user"
        except (_Break, _Continue):
            status, error = "failed", "Exit loop / Next iteration used outside a loop"
        outputs: dict[str, Any] = {}
        if status == "succeeded":
            try:
                outputs = render(definition.get("outputs") or {}, ctx)
            except ExprError as exc:
                status, error = "failed", f"outputs: {exc}"
        return {
            "status": status,
            "error": error,
            "message": message,
            "vars": mask_secrets(ctx["vars"]),
            "outputs": mask_secrets(outputs),
        }

    # --- step lists --------------------------------------------------------------------------

    async def steps(self, steps: list[dict[str, Any]], ctx: dict[str, Any], path: str) -> None:
        last = "succeeded"
        for i, step in enumerate(steps):
            last = await self.step(step, ctx, f"{path}[{i}]", last)

    async def step(
        self, step: dict[str, Any], ctx: dict[str, Any], path: str, previous: str
    ) -> str:
        if self.rt.cancelled():
            raise FlowCancelled()
        sid, stype = str(step.get("id")), str(step.get("type"))
        name = step.get("name") or STEPS.get(stype, {}).get("label", stype)
        base = {"id": sid, "type": stype, "name": name, "path": path}
        if ctx.get("_iteration") is not None:
            base["iteration"] = ctx["_iteration"]
        if step.get("disabled"):
            await self.rt.record({**base, "status": "skipped", "reason": "disabled"})
            return previous
        run_after = step.get("run_after", "success")
        if (run_after == "success" and previous == "failed") or (
            run_after == "failure" and previous != "failed"
        ):
            await self.rt.record({**base, "status": "skipped", "reason": f"run after: {run_after}"})
            return "skipped"
        if step.get("run_if"):
            try:
                ok, cap = check_condition(step["run_if"], ctx, ctx=ctx)
            except ExprError as exc:
                raise StepFailed(f"{sid}: run_if: {exc}", sid) from exc
            if not ok:
                await self.rt.record(
                    {**base, "status": "skipped", "reason": "run_if did not match"}
                )
                return "skipped"
            ctx["match"] = cap or ctx.get("match", {})
        self.executed += 1
        if self.executed > MAX_EXECUTED_STEPS:
            raise StepFailed(
                f"more than {MAX_EXECUTED_STEPS} steps executed: stopping (loop?)", sid
            )

        attempts = max(0, int(step.get("retry_count") or 0)) + 1
        delay = float(step.get("retry_delay_seconds") or 10)
        backoff = float(step.get("retry_backoff") or 2)
        started = self.rt.now_iso()
        for attempt in range(1, attempts + 1):
            try:
                output = await self._execute(step, ctx, path)
            except (_Break, _Continue, _Stop, FlowCancelled):
                await self.rt.record(
                    {
                        **base,
                        "status": "succeeded",
                        "started_at": started,
                        "finished_at": self.rt.now_iso(),
                    }
                )
                raise
            except StepFailed as exc:
                failure = exc
            except ExprError as exc:
                failure = StepFailed(f"{sid}: {exc}", sid)
            else:
                if output is not None or stype not in CONTROL_STEPS:
                    ctx["steps"][sid] = output
                if (
                    step.get("save_as")
                    and stype in STEPS
                    and any(f["name"] == "save_as" for f in STEPS[stype]["fields"])
                ):
                    ctx["vars"][str(render(step["save_as"], ctx))] = output
                await self.rt.record(
                    {
                        **base,
                        "status": "succeeded",
                        "attempt": attempt,
                        "started_at": started,
                        "finished_at": self.rt.now_iso(),
                        "output": _short(mask_secrets(output)),
                    }
                )
                return "succeeded"
            if attempt < attempts:
                await self.rt.record(
                    {**base, "status": "retrying", "attempt": attempt, "error": failure.message}
                )
                await self.rt.sleep(delay * (backoff ** (attempt - 1)))
        error = {"message": failure.message, "step": failure.step_id or sid}
        await self.rt.record(
            {
                **base,
                "status": "failed",
                "attempt": attempts,
                "started_at": started,
                "finished_at": self.rt.now_iso(),
                "error": failure.message,
            }
        )
        ctx["steps"][sid] = {"error": failure.message}
        if step.get("continue_on_error"):
            ctx["vars"]["error"] = error
            ctx["error"] = error
            return "failed"
        raise failure

    # --- one step -----------------------------------------------------------------------------

    async def _execute(self, step: dict[str, Any], ctx: dict[str, Any], path: str) -> Any:
        stype, sid = step["type"], step["id"]
        if stype in CONTROL_STEPS:
            return await getattr(self, f"_c_{stype}")(step, ctx, path)
        params = _params(step, ctx)
        if stype == "set_variable":
            ctx["vars"][str(params["name"])] = params.get("value")
            return params.get("value")
        if stype == "append_to_list":
            lst = ctx["vars"].setdefault(str(params["name"]), [])
            if not isinstance(lst, list):
                raise StepFailed(f"{sid}: vars.{params['name']} is not a list", sid)
            lst.append(params.get("value"))
            return lst
        if stype == "log":
            return {
                "message": mask_secrets(str(params.get("message", ""))),
                "level": params.get("level", "info"),
            }
        if stype == "stop":
            raise _Stop(str(params.get("message") or "stopped"))
        if stype == "fail":
            raise StepFailed(str(params.get("message") or "failed"), sid)
        if stype == "break":
            raise _Break()
        if stype == "continue":
            raise _Continue()
        if stype == "wait":
            seconds = float(params.get("seconds") or 0)
            await self.rt.sleep(max(0.0, seconds))
            return {"waited_seconds": seconds}
        if stype == "approval":
            await self.rt.record(
                {
                    "id": sid,
                    "type": stype,
                    "name": step.get("name") or "Ask for approval",
                    "path": path,
                    "status": "waiting",
                    "params": mask_secrets(params),
                }
            )
            decision = await self.rt.approval(sid, params)
            if not decision.get("approved"):
                raise StepFailed(
                    f"{sid}: not approved by {decision.get('by') or 'timeout'}"
                    + (f": {decision['comment']}" if decision.get("comment") else ""),
                    sid,
                )
            return decision
        if stype == "config_push":
            return await self.rt.config_push(sid, params)
        if stype == "run_flow":
            return await self.rt.run_flow(sid, params)
        timeout = int(step.get("timeout_seconds") or 0)
        try:
            out = await self.rt.action(stype, params, step_id=sid, timeout=timeout)
            if stype in CONTROLLER_WAIT_STEPS and params.get("wait", True):
                out = await self._wait_for_job(sid, params, out)
            return out
        except StepFailed:
            raise
        except Exception as exc:  # activity errors arrive wrapped; keep the message
            raise StepFailed(f"{sid}: {_root_message(exc)}", sid) from exc

    async def _wait_for_job(
        self, sid: str, params: dict[str, Any], out: dict[str, Any]
    ) -> dict[str, Any]:
        """Poll a controller job with durable timers until it finishes."""
        job = dict(out.get("job") or {})
        poll = float(out.get("poll_seconds") or 15)
        limit = float(params.get("timeout_minutes") or 90) * 60
        waited = 0.0
        while not job.get("done"):
            if waited >= limit:
                raise StepFailed(
                    f"{sid}: controller job {job.get('id')} not finished after {limit / 60:g} minutes",
                    sid,
                )
            await self.rt.sleep(poll)
            waited += poll
            job = await self.rt.action(
                "controller_job",
                {
                    "controller": out.get("controller"),
                    "job": job.get("id"),
                    "device": out.get("device"),
                    "version": out.get("version"),
                },
                step_id=sid,
                timeout=120,
            )
        if job.get("status") != "succeeded":
            raise StepFailed(
                f"{sid}: controller job {job.get('id')} failed: {job.get('message') or job.get('progress')}",
                sid,
            )
        return {**out, "job": job, "waited_seconds": waited}

    # --- control flow ---------------------------------------------------------------------------

    async def _c_if(self, step: dict[str, Any], ctx: dict[str, Any], path: str) -> Any:
        for i, branch in enumerate(step.get("branches") or []):
            ok, cap = check_condition(branch.get("condition"), ctx, ctx=ctx)
            if ok:
                if cap:
                    ctx["match"] = cap
                await self.steps(branch.get("steps") or [], ctx, f"{path}.branches[{i}]")
                return {"branch": i, "matched": True}
        await self.steps(step.get("else") or [], ctx, f"{path}.else")
        return {"branch": "else", "matched": False}

    async def _c_switch(self, step: dict[str, Any], ctx: dict[str, Any], path: str) -> Any:
        value = render(step.get("value"), ctx)
        for i, case in enumerate(step.get("cases") or []):
            options = render(case.get("value"), ctx)
            options = options if isinstance(options, list) else [options]
            if any(str(o).casefold() == str(value).casefold() for o in options):
                await self.steps(case.get("steps") or [], ctx, f"{path}.cases[{i}]")
                return {"value": value, "case": i}
        await self.steps(step.get("default") or [], ctx, f"{path}.default")
        return {"value": value, "case": "default"}

    async def _loop_body(
        self, step: dict[str, Any], ctx: dict[str, Any], path: str, label: Any
    ) -> bool:
        """One iteration; False when the loop must end (Exit loop)."""
        ctx["_iteration"] = label
        try:
            await self.steps(step.get("steps") or [], ctx, f"{path}.steps")
        except _Continue:
            pass
        except _Break:
            return False
        finally:
            ctx["_iteration"] = None
        return True

    async def _c_for_each(self, step: dict[str, Any], ctx: dict[str, Any], path: str) -> Any:
        items = render(step.get("items"), ctx)
        if isinstance(items, dict):
            items = [{"key": k, "value": v} for k, v in items.items()]
        if isinstance(items, str):
            items = [ln.strip() for ln in items.replace(",", "\n").splitlines() if ln.strip()]
        if not isinstance(items, list):
            raise StepFailed(
                f"{step['id']}: Repeat with each needs a list, got {type(items).__name__}",
                step["id"],
            )
        limit = int(step.get("max_iterations") or 100)
        if len(items) > limit:
            raise StepFailed(
                f"{step['id']}: {len(items)} items is more than the maximum {limit}", step["id"]
            )
        var = str(step.get("as") or "item")
        concurrency = max(1, int(step.get("concurrency") or 1))
        if concurrency == 1:
            done = 0
            for i, item in enumerate(items):
                ctx["vars"][var], ctx["vars"][f"{var}_index"] = item, i
                done += 1
                if not await self._loop_body(step, ctx, path, i):
                    break
            return {"count": done}
        # Concurrent: each item gets its own copy of the variables (NSP/Mistral
        # with-items semantics); results come back as a list.
        results: list[Any] = [None] * len(items)
        for start in range(0, len(items), concurrency):
            batch = list(enumerate(items))[start : start + concurrency]

            async def one(i: int, item: Any) -> None:
                local = {
                    **ctx,
                    "vars": {**copy.deepcopy(ctx["vars"]), var: item, f"{var}_index": i},
                    "steps": dict(ctx["steps"]),
                }
                await self._loop_body(step, local, path, i)
                results[i] = local["vars"]

            await asyncio.gather(*(one(i, it) for i, it in batch))
        return {"count": len(items), "results": results}

    async def _c_repeat(self, step: dict[str, Any], ctx: dict[str, Any], path: str) -> Any:
        times = int(render(step.get("times"), ctx) or 0)
        if not 0 <= times <= 1000:
            raise StepFailed(f"{step['id']}: times must be 0–1000", step["id"])
        var = str(step.get("as") or "attempt")
        done = 0
        for n in range(1, times + 1):
            ctx["vars"][var] = n
            done = n
            if not await self._loop_body(step, ctx, path, n):
                break
        return {"count": done}

    async def _c_while(self, step: dict[str, Any], ctx: dict[str, Any], path: str) -> Any:
        limit = int(step.get("max_iterations") or 20)
        delay = float(step.get("delay_seconds") or 0)
        n = 0
        while True:
            ok, _cap = check_condition(step.get("condition"), ctx, ctx=ctx)
            if not ok:
                return {"iterations": n, "hit_limit": False}
            if n >= limit:
                await self.rt.record(
                    {
                        "id": step["id"],
                        "type": "while",
                        "path": path,
                        "status": "warning",
                        "message": f"stopped after max_iterations={limit}",
                    }
                )
                return {"iterations": n, "hit_limit": True}
            n += 1
            if not await self._loop_body(step, ctx, path, n):
                return {"iterations": n, "hit_limit": False}
            if delay:
                await self.rt.sleep(delay)

    async def _c_try(self, step: dict[str, Any], ctx: dict[str, Any], path: str) -> Any:
        caught: dict[str, Any] | None = None
        try:
            await self.steps(step.get("steps") or [], ctx, f"{path}.steps")
        except StepFailed as exc:
            caught = {"message": exc.message, "step": exc.step_id}
            ctx["vars"]["error"] = caught
            ctx["error"] = caught
            await self.steps(step.get("catch") or [], ctx, f"{path}.catch")
        finally:
            await self.steps(step.get("finally") or [], ctx, f"{path}.finally")
        return {"error": caught}

    async def _c_parallel(self, step: dict[str, Any], ctx: dict[str, Any], path: str) -> Any:
        branches = step.get("branches") or []
        fail_fast = bool(step.get("fail_fast", True))

        async def branch(i: int, b: dict[str, Any]) -> Any:
            try:
                await self.steps(b.get("steps") or [], ctx, f"{path}.branches[{i}]")
                return None
            except StepFailed as exc:
                if fail_fast:
                    raise
                return exc.message

        errors = await asyncio.gather(*(branch(i, b) for i, b in enumerate(branches)))
        failed = [e for e in errors if e]
        if failed:
            raise StepFailed(f"{step['id']}: {len(failed)} branch(es) failed: {failed}", step["id"])
        return {"branches": len(branches)}


def _root_message(exc: BaseException) -> str:
    """The innermost message (Temporal wraps activity errors in layers)."""
    seen = exc
    while seen.__cause__ is not None:
        seen = seen.__cause__
    msg = str(getattr(seen, "message", "") or seen)
    return msg or type(seen).__name__


class MemoryRuntime:
    """Runs a flow in-process: for tests and the 'test run' preview."""

    def __init__(
        self,
        actions: Callable[[str, dict[str, Any]], Awaitable[Any]] | None = None,
        *,
        approvals: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._actions = actions
        self.log: list[dict[str, Any]] = []
        self.slept: list[float] = []
        self.approvals = approvals or {}
        self.pushes: list[dict[str, Any]] = []
        self._clock = 0

    async def action(
        self, step_type: str, params: dict[str, Any], *, step_id: str, timeout: int
    ) -> Any:
        if self._actions is None:
            out: dict[str, Any] = {
                "simulated": True,
                "type": step_type,
                "params": mask_secrets(params),
            }
            if step_type in CONTROLLER_WAIT_STEPS or step_type == "controller_job":
                out["job"] = {"id": "simulated", "status": "succeeded", "done": True}
            return out
        return await self._actions(step_type, params)

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    async def approval(self, step_id: str, params: dict[str, Any]) -> dict[str, Any]:
        return self.approvals.get(
            step_id, {"approved": params.get("on_timeout") == "approve", "by": "timeout"}
        )

    async def config_push(self, step_id: str, params: dict[str, Any]) -> dict[str, Any]:
        self.pushes.append(params)
        return {
            "change_id": "simulated",
            "ticket_number": params.get("ticket_number"),
            "result": "closed",
        }

    async def run_flow(self, step_id: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"status": "succeeded", "flow": params.get("flow"), "vars": {}, "outputs": {}}

    async def record(self, entry: dict[str, Any]) -> None:
        self.log.append(entry)

    def now_iso(self) -> str:
        self._clock += 1
        return f"t+{self._clock}"

    def cancelled(self) -> bool:
        return False
