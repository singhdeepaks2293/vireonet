from __future__ import annotations

from vireonet.adapters.protocols import BatfishProtocol
from vireonet.intent import ChangeIntent
from vireonet.render import render_candidate_configs


def _normalise_filenames(configs: dict[str, str]) -> dict[str, str]:
    """Current configs may be keyed by device name or by filename; the gate
    and the adapter both work in '<device>.cfg' filenames."""
    return {
        name if name.endswith(".cfg") else f"{name}.cfg": content
        for name, content in configs.items()
    }


async def run_batfish_gate(
    intent: ChangeIntent,
    current_configs: dict[str, str],
    batfish: BatfishProtocol,
    *,
    blocking: bool = False,
) -> dict[str, object]:
    """Structured Batfish gate. Returns the BatfishResult as a dict for CI / Temporal.

    Pass requires: no undefined references, no duplicate IPs, no VLAN/interface
    inconsistency, no reachability regression, no BGP session regression.
    The adapter decides the per-question details; the gate decides pass/fail.

    `blocking` decides whether a failure blocks the change: `blocks` is True
    only when the gate failed AND blocking is set. A non-blocking failure is
    recorded as a warning (see `warning`) and does not block CI.
    """
    current = _normalise_filenames(current_configs)
    candidate = render_candidate_configs(intent, current)
    analyse_differential = getattr(batfish, "analyse_differential", None)
    if analyse_differential is not None:
        result = await analyse_differential(current, candidate)
    else:  # minimal BatfishProtocol implementations without differential support
        result = await batfish.analyse(candidate)
    blocks = (not result.passed) and blocking
    warning: str | None = None
    if not result.passed and not blocking:
        warning = "Batfish found failures but the gate is non-blocking; recorded as warnings"
    return {
        "passed": result.passed,
        "blocking": blocking,
        "blocks": blocks,
        "failures": result.failures,
        "evidence": result.evidence,
        "differential": result.differential,
        "files": sorted(candidate),
        "warning": warning,
    }
