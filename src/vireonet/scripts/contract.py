"""Declared-change contract (E3): a script's declared changes must be
complete and inside the manifest scope, and a dry-run must hash identically
to the apply it approves.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from vireonet.execution.types import DeclaredChange, StepResult
from vireonet.scripts.manifest import ScriptEntry


def validate_declared_changes(result: StepResult, entry: ScriptEntry) -> list[str]:
    """Violations of the declared-change contract; [] means it holds.

    - declared_changes must be non-empty (a script that changes nothing
      declares nothing, and a script that changes something must say so);
    - every change's scope ``object.field`` must be in the manifest
      ``writes`` list, or its object type must be.
    """
    violations: list[str] = []
    if not result.declared_changes:
        violations.append("declared_changes is empty; a script must declare every change")
        return violations
    for change in result.declared_changes:
        scope = f"{change.object_type}.{change.field}"
        if scope not in entry.writes and change.object_type not in entry.writes:
            violations.append(f"declared change {scope} is outside manifest writes {entry.writes}")
    return violations


def dry_run_hash(result: StepResult) -> str:
    """SHA-256 of the result's output + declared changes (sorted keys).

    An apply whose hash differs from the approved dry-run's hash changed
    something the dry-run did not predict, and must be refused.
    """
    payload: dict[str, Any] = {
        "output": result.output,
        "declared_changes": [
            c.model_dump() if isinstance(c, DeclaredChange) else dict(c)
            for c in result.declared_changes
        ],
    }
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
