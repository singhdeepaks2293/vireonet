"""F3: Batfish gate blocking semantics (non-blocking failure warns, blocking blocks)."""

from __future__ import annotations

from typing import Any, cast

from vireonet.adapters.base import AdapterHealth
from vireonet.adapters.fakes import FakeBatfishAdapter
from vireonet.adapters.protocols import BatfishResult
from vireonet.batfish_gate import run_batfish_gate
from vireonet.intent import parse_intent

BASE = {"devices": ["leaf-01"], "site": "dc1", "ticket_number": "RITM1", "requested_by": "alice"}


def _diff(report: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], report["differential"])


def _intent() -> Any:
    return parse_intent({**BASE, "change_type": "vlan_add", "vlan_id": 200, "vlan_name": "new"})


async def test_non_blocking_failure_warns_but_does_not_block() -> None:
    # Fake Batfish fails when a file contains UNDEFINED; candidate = current + lines.
    report = await run_batfish_gate(
        _intent(), {"leaf-01.cfg": "UNDEFINED"}, FakeBatfishAdapter(), blocking=False
    )
    assert report["passed"] is False
    assert report["blocking"] is False
    assert report["blocks"] is False
    assert (
        report["warning"]
        == "Batfish found failures but the gate is non-blocking; recorded as warnings"
    )
    assert _diff(report)["differential"] is True


async def test_blocking_failure_blocks() -> None:
    report = await run_batfish_gate(
        _intent(), {"leaf-01.cfg": "UNDEFINED"}, FakeBatfishAdapter(), blocking=True
    )
    assert report["passed"] is False
    assert report["blocking"] is True
    assert report["blocks"] is True
    assert report["warning"] is None


async def test_passing_gate_never_blocks() -> None:
    report = await run_batfish_gate(
        _intent(), {"leaf-01.cfg": "hostname leaf-01\n"}, FakeBatfishAdapter(), blocking=True
    )
    assert report["passed"] is True
    assert report["blocks"] is False
    assert report["warning"] is None


async def test_gate_normalises_device_keyed_current_configs() -> None:
    report = await run_batfish_gate(
        _intent(), {"leaf-01": "hostname leaf-01\n"}, FakeBatfishAdapter()
    )
    assert report["passed"] is True
    assert report["files"] == ["leaf-01.cfg"]
    # Candidate adds the rendered VLAN lines, so the file changed vs current.
    assert _diff(report)["changed_files"] == ["leaf-01.cfg"]


class _AnalyseOnlyBatfish:
    """Minimal BatfishProtocol-shaped stub with no analyse_differential:
    the gate must fall back to analyse() and report an empty differential."""

    def capabilities(self) -> list[str]:
        return ["reachability"]

    async def health_check(self) -> AdapterHealth:
        return AdapterHealth(name="batfish", status="ok", capabilities=self.capabilities())

    async def analyse(self, files: dict[str, str]) -> BatfishResult:
        return BatfishResult(passed=False, failures=["boom"], evidence={"files": len(files)})


async def test_gate_falls_back_when_adapter_has_no_differential() -> None:
    report = await run_batfish_gate(
        _intent(), {"leaf-01.cfg": "hostname leaf-01\n"}, _AnalyseOnlyBatfish(), blocking=True
    )  # type: ignore[arg-type]
    assert report["passed"] is False
    assert report["blocks"] is True
    assert report["differential"] == {}
