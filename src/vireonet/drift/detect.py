"""Drift detection engine (D2).

Four independent kinds, each evaluated in isolation so a test can
exercise one without the others:

1. unmanaged — running config hash vs baseline hash; equal means no
   finding and no diff is computed at all.
2. intent — rendered intent vs running config.
3. state — NetBox state vs collected facts, compared by key.
4. compliance — rules from compliance.py (only when rules are wired in).

Dedup is the caller's job (D3 / the scan-storage activity): dedup_key
is a pure function of (device, kind, running hash) so the store can
recognise a repeat finding without re-running detection.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from vireonet.diff import compute_diff, config_hash, normalize_config
from vireonet.drift.compliance import evaluate_compliance
from vireonet.drift.sections import MANAGED_SECTIONS_DEFAULT
from vireonet.drift.severity import severity_for
from vireonet.structlog_config import get_logger

log = get_logger(__name__)

DriftKind = Literal["unmanaged", "intent", "state", "compliance"]


class DriftFinding(BaseModel):
    kind: DriftKind
    severity: str
    diff: str
    section: str
    detail: dict[str, Any] = {}
    status: str = "detected"


def dedup_key(finding: DriftFinding, device: str) -> str:
    """Stable identity for a finding: device|kind|running_hash.

    Two scans of the same running config produce the same key, so the
    store can update last_seen instead of creating a duplicate record.
    A different running hash is a different finding.
    """
    running_hash = str(finding.detail.get("running_hash", ""))
    return f"{device}|{finding.kind}|{running_hash}"


def compare_state(
    netbox_state: dict[str, Any] | None, collected_facts: dict[str, Any] | None
) -> list[DriftFinding]:
    """Compare NetBox state vs collected facts, by key.

    A key missing on either side is a finding, not a crash; a value
    mismatch is a finding. Dict values are compared per sub-key, list
    values as sets, everything else by equality.
    """
    findings: list[DriftFinding] = []
    nb = netbox_state or {}
    facts = collected_facts or {}
    for key in sorted(set(nb) | set(facts)):
        section = _section_for_state_key(key)
        if key not in nb:
            findings.append(
                DriftFinding(
                    kind="state",
                    severity="medium",
                    diff="",
                    section=section,
                    detail={"key": key, "netbox": None, "collected": facts[key]},
                )
            )
            continue
        if key not in facts:
            findings.append(
                DriftFinding(
                    kind="state",
                    severity="medium",
                    diff="",
                    section=section,
                    detail={"key": key, "netbox": nb[key], "collected": None},
                )
            )
            continue
        nb_value, fact_value = nb[key], facts[key]
        if isinstance(nb_value, dict) and isinstance(fact_value, dict):
            for sub in sorted(set(nb_value) | set(fact_value)):
                if nb_value.get(sub) != fact_value.get(sub):
                    findings.append(
                        DriftFinding(
                            kind="state",
                            severity="medium",
                            diff="",
                            section=section,
                            detail={
                                "key": f"{key}.{sub}",
                                "netbox": nb_value.get(sub),
                                "collected": fact_value.get(sub),
                            },
                        )
                    )
        elif isinstance(nb_value, list) and isinstance(fact_value, list):
            if set(map(str, nb_value)) != set(map(str, fact_value)):
                findings.append(
                    DriftFinding(
                        kind="state",
                        severity="medium",
                        diff="",
                        section=section,
                        detail={"key": key, "netbox": nb_value, "collected": fact_value},
                    )
                )
        elif nb_value != fact_value:
            findings.append(
                DriftFinding(
                    kind="state",
                    severity="medium",
                    diff="",
                    section=section,
                    detail={"key": key, "netbox": nb_value, "collected": fact_value},
                )
            )
    return findings


def _section_for_state_key(key: str) -> str:
    lowered = key.lower()
    if "interface" in lowered:
        return "interfaces"
    if "vlan" in lowered:
        return "interfaces"
    if "neighbor" in lowered or "neighbour" in lowered or "route" in lowered:
        return "routing"
    if "acl" in lowered:
        return "acl"
    return "other"


class DetectionEngine:
    """Detect drift for one device from one collected running config."""

    def __init__(
        self,
        *,
        protected_devices: list[str] | None = None,
        managed_sections: set[str] | None = None,
        compliance_rules: list[dict[str, Any]] | None = None,
        netbox: Any = None,
        batfish: Any = None,
    ) -> None:
        self._protected = protected_devices
        self._managed = set(managed_sections) if managed_sections else set(MANAGED_SECTIONS_DEFAULT)
        self._rules = compliance_rules
        self._netbox = netbox
        self._batfish = batfish

    async def scan_device(
        self,
        *,
        device: str,
        running_config: str,
        baseline_config: str | None = None,
        baseline_hash: str | None = None,
        rendered_intent: str | None = None,
        netbox_state: dict[str, Any] | None = None,
        collected_facts: dict[str, Any] | None = None,
    ) -> list[DriftFinding]:
        findings: list[DriftFinding] = []
        running_normalized = normalize_config(running_config)
        running_hash = config_hash(running_normalized)

        # 1. Unmanaged: hash first. Equal -> no finding, no diff computed.
        expected_hash = baseline_hash
        if expected_hash is None and baseline_config is not None:
            expected_hash = config_hash(baseline_config)
        if expected_hash is not None and running_hash != expected_hash:
            diff_text = ""
            diff_dict: dict[str, Any] = {}
            if baseline_config is not None:
                diff_dict = compute_diff(device, baseline_config, running_config)
                diff_text = "\n".join(str(line) for line in diff_dict["unified"])
            severity = severity_for(diff_dict if diff_dict else diff_text, device, self._protected)
            findings.append(
                DriftFinding(
                    kind="unmanaged",
                    severity=severity,
                    diff=diff_text,
                    section="other",
                    detail={
                        "running_hash": running_hash,
                        "baseline_hash": expected_hash,
                    },
                )
            )

        # 2. Intent: rendered intent vs running config.
        if rendered_intent is not None:
            intent_normalized = normalize_config(rendered_intent)
            if intent_normalized != running_normalized:
                diff_dict = compute_diff(device, rendered_intent, running_config)
                diff_text = "\n".join(str(line) for line in diff_dict["unified"])
                findings.append(
                    DriftFinding(
                        kind="intent",
                        severity=severity_for(diff_dict, device, self._protected),
                        diff=diff_text,
                        section="other",
                        detail={"running_hash": running_hash},
                    )
                )

        # 3. State: NetBox vs collected facts.
        for finding in compare_state(netbox_state, collected_facts):
            finding.detail["running_hash"] = running_hash
            findings.append(finding)

        # 4. Compliance, only when rules are wired in.
        if self._rules:
            for result in await evaluate_compliance(
                running_config, self._rules, self._netbox, device
            ):
                status = str(result["status"])
                if status == "passed":
                    continue
                findings.append(
                    DriftFinding(
                        kind="compliance",
                        severity="medium",
                        diff="",
                        section="other",
                        detail={
                            "running_hash": running_hash,
                            "rule": str(result["name"]),
                            "compliance_detail": result["detail"],
                        },
                        status=status,
                    )
                )
        return findings


async def enrich_with_batfish(
    finding: DriftFinding,
    batfish: Any,
    baseline_snapshot: dict[str, str],
    candidate_snapshot: dict[str, str],
) -> DriftFinding:
    """Attach Batfish reachability impact to a finding's detail.

    Calls the Batfish adapter's differential analysis the way the E3/F3
    gate does (analyse_differential, falling back to analyse). On ANY
    exception the finding is returned unchanged and the failure is
    logged: detection must not break because Batfish is down.
    """
    try:
        analyse_differential = getattr(batfish, "analyse_differential", None)
        if callable(analyse_differential):
            result = await analyse_differential(baseline_snapshot, candidate_snapshot)
        else:
            result = await batfish.analyse(candidate_snapshot)
        detail = dict(finding.detail)
        detail["reachability_impact"] = {
            "passed": bool(result.passed),
            "failures": list(result.failures),
            "evidence": dict(result.evidence),
            "differential": dict(result.differential),
        }
        return finding.model_copy(update={"detail": detail})
    except Exception as exc:  # noqa: BLE001 — Batfish down must not break detection
        log.warning("batfish enrichment failed", error=str(exc))
        return finding
