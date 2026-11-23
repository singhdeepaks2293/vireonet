"""Custom-script runner (E3).

A script runs only through the execution engine (job_type="python") — the
runner itself has no Git or NetBox access, by construction: it can only
ask the engine to run a job. Manifest checks happen before any AWX call,
the script's existence is verified in Git, declared changes are validated
against the manifest, and an apply must hash-identically to its approved
dry-run.
"""

from __future__ import annotations

from typing import Any

from vireonet.execution.engine import ExecutionEngine
from vireonet.execution.types import DeviceRef, StepName, StepRequest, StepResult
from vireonet.scripts.contract import dry_run_hash, validate_declared_changes
from vireonet.scripts.manifest import ScriptEntry, ScriptManifest


class ScriptRunner:
    def __init__(
        self,
        engine: ExecutionEngine,
        git: Any,
        manifest: ScriptManifest,
    ) -> None:
        self._engine = engine
        self._git = git
        self._manifest = manifest

    async def run(
        self,
        name: str,
        sha: str,
        step: StepName,
        ticket: str,
        device: DeviceRef,
        change_type: str,
        rendered_config: str,
        dry_run: bool,
        approved_hash: str | None = None,
    ) -> StepResult:
        # 1. Manifest gate — before any AWX call.
        entry: ScriptEntry = self._manifest.check(name, sha, change_type, device.platform)
        # 2. The script must exist in Git at the manifest ref.
        get_file = getattr(self._git, "get_file", None)
        if get_file is None:
            raise ValueError("git provider has no get_file; cannot verify script exists")
        try:
            await get_file(f"scripts/{name}.py", entry.ref)
        except Exception as exc:
            raise ValueError(f"script {name} not found in git at {entry.ref}") from exc
        # 3. Run through the engine as a python job.
        req = StepRequest(
            ticket_number=ticket,
            device=device,
            step=step,
            change_type=change_type,
            job_type="python",
            rendered_config=rendered_config,
            dry_run=dry_run,
            extra_vars={"script_name": name, "script_sha": sha},
            correlation_id=ticket,
        )
        method = getattr(self._engine, step)
        result: StepResult = await method(req)
        if not result.ok:
            return result
        # 4. Declared changes must be complete and in scope.
        violations = validate_declared_changes(result, entry)
        if violations:
            raise ValueError(f"script {name} declared-change violations: {violations}")
        # 5. An apply must match the approved dry-run exactly.
        if step == "apply" and approved_hash is not None and dry_run_hash(result) != approved_hash:
            raise ValueError("apply output differs from the approved dry-run")
        return result
