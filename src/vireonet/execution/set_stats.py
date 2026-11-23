"""Parsing the VIREONET-RESULT block a python runner emits (E2).

The runner prints, on stdout::

    VIREONET-RESULT-BEGIN
    { ... RunnerResult JSON ... }
    VIREONET-RESULT-END

AWX may also expose the same payload via ``set_stats`` (the run_python
playbook calls set_stats with ``vireonet_result``). How AWX surfaces
set_stats values through its API is UNCERTAIN — the engine tries the job
events first and falls back to the stdout markers.
"""

from __future__ import annotations

import json
import re
from typing import Any

_BEGIN = "VIREONET-RESULT-BEGIN"
_END = "VIREONET-RESULT-END"
_BLOCK_RE = re.compile(r"VIREONET-RESULT-BEGIN\s*(.*?)\s*VIREONET-RESULT-END", re.DOTALL)


def parse_result_from_stdout(stdout: str) -> dict[str, Any] | None:
    """RunnerResult dict from the stdout markers, or None if absent/bad JSON."""
    match = _BLOCK_RE.search(stdout)
    if match is None:
        return None
    try:
        parsed = json.loads(match.group(1))
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def parse_result_from_artifacts(artifacts: dict[str, Any]) -> dict[str, Any] | None:
    """RunnerResult dict from a set_stats-style artifact mapping.

    Accepts {"vireonet_result": {...}} (already a dict) or
    {"vireonet_result": "<json string>"}. Anything else is None.
    """
    raw = artifacts.get("vireonet_result")
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None
