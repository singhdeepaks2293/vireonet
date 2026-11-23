from __future__ import annotations

from vireonet.adapters.protocols import BatfishProtocol
from vireonet.intent import ChangeIntent
from vireonet.render import render_candidate_configs


async def run_batfish_gate(
    intent: ChangeIntent, current_configs: dict[str, str], batfish: BatfishProtocol,
) -> dict:
    """Structured Batfish gate. Returns the BatfishResult as a dict for CI / Temporal.

    Pass requires: no undefined references, no duplicate IPs, no VLAN/interface
    inconsistency, no reachability regression, no BGP session regression.
    The adapter decides the per-question details; the gate decides pass/fail.
    """
    candidate = render_candidate_configs(intent, current_configs)
    result = await batfish.analyse(candidate)
    # Differential evidence: if the adapter also compared current vs candidate,
    # its evidence already contains it. The gate only re-states the verdict.
    return {"passed": result.passed, "failures": result.failures, "evidence": result.evidence, "files": sorted(candidate)}
