"""D2: detection engine — each kind in isolation, plus severity/compliance/Batfish."""

from __future__ import annotations

from typing import Any

import pytest

from vireonet.adapters.protocols import BatfishResult
from vireonet.diff import compute_diff, config_hash
from vireonet.drift import detect as detect_mod
from vireonet.drift.compliance import evaluate_compliance
from vireonet.drift.detect import (
    DetectionEngine,
    DriftFinding,
    compare_state,
    dedup_key,
    enrich_with_batfish,
)
from vireonet.drift.normalise import load_ignore_rules, normalize_for_drift
from vireonet.drift.sections import (
    CiscoconfparseParser,
    SimpleLineParser,
    classify_section_diff,
    managed_sections,
)
from vireonet.drift.severity import severity_for

BASELINE = "hostname leaf-01\ninterface GigabitEthernet0/1\n description uplink\n no shutdown\n"
RUNNING_CHANGED = (
    "hostname leaf-01\ninterface GigabitEthernet0/1\n description CHANGED\n no shutdown\n"
)


def _engine(**kwargs: Any) -> DetectionEngine:
    return DetectionEngine(**kwargs)


async def test_unmanaged_only() -> None:
    findings = await _engine().scan_device(
        device="leaf-01",
        running_config=RUNNING_CHANGED,
        baseline_config=BASELINE,
        baseline_hash=config_hash(BASELINE),
    )
    assert [f.kind for f in findings] == ["unmanaged"]
    assert findings[0].severity == "high"  # interfaces change, leaf-01 not protected
    assert "CHANGED" in findings[0].diff
    assert findings[0].status == "detected"


async def test_equal_hash_no_drift_and_no_diff_computed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _spy(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append("compute_diff")
        return compute_diff(*args, **kwargs)

    monkeypatch.setattr(detect_mod, "compute_diff", _spy)
    findings = await _engine().scan_device(
        device="leaf-01",
        running_config=BASELINE,
        baseline_config=BASELINE,
        baseline_hash=config_hash(BASELINE),
    )
    assert findings == []
    assert calls == []


async def test_intent_only() -> None:
    rendered = "hostname leaf-01\ninterface GigabitEthernet0/1\n description intended\n"
    findings = await _engine().scan_device(
        device="leaf-01",
        running_config=BASELINE,
        baseline_config=BASELINE,
        baseline_hash=config_hash(BASELINE),
        rendered_intent=rendered,
    )
    assert [f.kind for f in findings] == ["intent"]
    assert "intended" in findings[0].diff


async def test_state_only_missing_and_mismatch() -> None:
    findings = compare_state(
        {"interfaces": {"Gi0/1": "up", "Gi0/2": "up"}, "vlans": [100]},
        {"interfaces": {"Gi0/1": "down"}, "vlans": [100, 200]},
    )
    assert findings
    assert all(f.kind == "state" for f in findings)
    keys = {str(f.detail.get("key")) for f in findings}
    assert "interfaces.Gi0/1" in keys  # mismatch
    assert "interfaces.Gi0/2" in keys  # missing on the collected side
    assert "vlans" in keys  # set differs


async def test_state_only_via_scan_device() -> None:
    findings = await _engine().scan_device(
        device="leaf-01",
        running_config=BASELINE,
        baseline_config=BASELINE,
        baseline_hash=config_hash(BASELINE),
        netbox_state={"interfaces": {"Gi0/1": "up"}},
        collected_facts={"interfaces": {"Gi0/1": "down"}},
    )
    assert [f.kind for f in findings] == ["state"]


async def test_ignore_rules_suppress_timestamp_only_change() -> None:
    rules = load_ignore_rules("policies/drift_ignore.yaml")
    baseline = "hostname leaf-01\nLast configuration change at 10:00:00 UTC Mon Nov 23 2026\n"
    running = "hostname leaf-01\nLast configuration change at 12:00:00 UTC Mon Nov 23 2026\n"
    norm_base = normalize_for_drift(baseline, "ios", rules)
    norm_run = normalize_for_drift(running, "ios", rules)
    assert norm_base == norm_run
    findings = await _engine().scan_device(
        device="leaf-01",
        running_config=norm_run,
        baseline_config=norm_base,
        baseline_hash=config_hash(norm_base),
    )
    assert findings == []


async def test_unmanaged_block_low_severity() -> None:
    baseline = "hostname leaf-01\nntp server 10.0.0.1\n"
    running = "hostname leaf-01\nntp server 10.0.0.2\n"
    findings = await _engine().scan_device(
        device="leaf-01",
        running_config=running,
        baseline_config=baseline,
        baseline_hash=config_hash(baseline),
    )
    assert [f.kind for f in findings] == ["unmanaged"]
    assert findings[0].severity == "low"


def test_severity_policy() -> None:
    acl_base = "hostname core-01\nip access-list extended WEB\n permit tcp any any eq 80\n"
    acl_run = "hostname core-01\nip access-list extended WEB\n permit tcp any any eq 443\n"
    assert severity_for(compute_diff("core-01", acl_base, acl_run), "core-01") == "critical"
    assert severity_for(compute_diff("leaf-01", acl_base, acl_run), "leaf-01") == "medium"
    assert severity_for(compute_diff("leaf-01", BASELINE, RUNNING_CHANGED), "leaf-01") == "high"


def test_sections_parsers_agree_and_classify() -> None:
    config = (
        "interface GigabitEthernet0/1\n description uplink\n"
        "ip access-list extended WEB\n permit tcp any any eq 80\n"
        "ntp server 10.0.0.1\n"
    )
    simple = SimpleLineParser().split(config)
    assert set(simple) == {"interfaces", "acl", "ntp"}
    parsed = CiscoconfparseParser().split(config)
    assert set(parsed) == {"interfaces", "acl", "ntp"}
    assert parsed["interfaces"] == simple["interfaces"]
    assert classify_section_diff("interfaces") == "full"
    assert classify_section_diff("ntp") == "low"
    assert classify_section_diff("snmp", managed_sections("leaf-01", {"snmp"})) == "full"


class _NoContextNetBox:
    """NetBox stub with neither get_config_context nor get_device_custom_fields."""

    async def list_vlans(self) -> list[Any]:
        return []


async def test_compliance_missing_netbox_data_is_cannot_evaluate_not_pass() -> None:
    rules = [{"name": "needs-context", "must_contain": "ntp server {{ ntp_server }}"}]
    results = await evaluate_compliance("hostname leaf-01\n", rules, _NoContextNetBox(), "leaf-01")
    assert len(results) == 1
    assert results[0]["status"] == "cannot_evaluate"


async def test_compliance_vlans_match_without_list_vlans_is_cannot_evaluate() -> None:
    class _Bare:
        pass

    results = await evaluate_compliance(
        "hostname leaf-01\nvlan 100\n", [{"name": "vlans", "vlans_match": True}], _Bare(), "leaf-01"
    )
    assert results[0]["status"] == "cannot_evaluate"


async def test_compliance_via_engine_reports_cannot_evaluate_finding() -> None:
    engine = _engine(
        compliance_rules=[{"name": "needs-context", "must_contain": "ntp server {{ ntp_server }}"}],
        netbox=_NoContextNetBox(),
    )
    findings = await engine.scan_device(
        device="leaf-01",
        running_config=BASELINE,
        baseline_config=BASELINE,
        baseline_hash=config_hash(BASELINE),
    )
    assert [f.kind for f in findings] == ["compliance"]
    assert findings[0].status == "cannot_evaluate"


class _FailingBatfish:
    async def analyse_differential(self, current: dict[str, str], candidate: dict[str, str]) -> Any:
        raise RuntimeError("batfish is down")

    async def analyse(self, files: dict[str, str]) -> BatfishResult:
        raise RuntimeError("batfish is down")


async def test_batfish_down_detection_still_completes() -> None:
    finding = DriftFinding(kind="unmanaged", severity="high", diff="x", section="interfaces")
    enriched = await enrich_with_batfish(
        finding, _FailingBatfish(), {"leaf-01.cfg": BASELINE}, {"leaf-01.cfg": RUNNING_CHANGED}
    )
    assert enriched is finding
    assert "reachability_impact" not in enriched.detail


class _WorkingBatfish:
    async def analyse_differential(
        self, current: dict[str, str], candidate: dict[str, str]
    ) -> BatfishResult:
        return BatfishResult(passed=False, failures=["unreachable"], evidence={"q": 1})

    async def analyse(self, files: dict[str, str]) -> BatfishResult:
        return BatfishResult(passed=True)


async def test_batfish_enrichment_attaches_impact() -> None:
    finding = DriftFinding(kind="unmanaged", severity="high", diff="x", section="interfaces")
    enriched = await enrich_with_batfish(
        finding, _WorkingBatfish(), {"leaf-01.cfg": BASELINE}, {"leaf-01.cfg": RUNNING_CHANGED}
    )
    impact = enriched.detail["reachability_impact"]
    assert impact["passed"] is False
    assert impact["failures"] == ["unreachable"]


def test_dedup_key_stable_and_hash_sensitive() -> None:
    f1 = DriftFinding(
        kind="unmanaged",
        severity="high",
        diff="x",
        section="interfaces",
        detail={"running_hash": "abc"},
    )
    f2 = DriftFinding(
        kind="unmanaged",
        severity="high",
        diff="x",
        section="interfaces",
        detail={"running_hash": "abc"},
    )
    f3 = DriftFinding(
        kind="unmanaged",
        severity="high",
        diff="x",
        section="interfaces",
        detail={"running_hash": "def"},
    )
    assert dedup_key(f1, "leaf-01") == dedup_key(f2, "leaf-01") == "leaf-01|unmanaged|abc"
    assert dedup_key(f1, "leaf-01") != dedup_key(f3, "leaf-01")
    assert dedup_key(f1, "leaf-01") != dedup_key(f1, "leaf-02")
