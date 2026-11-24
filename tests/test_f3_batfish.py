"""F3: Batfish differential reachability + ACL impact (real adapter, stubbed session)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from vireonet.adapters.base import AdapterConfig
from vireonet.adapters.batfish import BatfishAdapter


class _Frame:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def to_dict(self, orient: str = "records") -> list[dict[str, Any]]:
        assert orient == "records"
        return list(self._rows)


class _Answer:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def frame(self) -> _Frame:
        return _Frame(self._rows)


class _Question:
    """Mimics a pybatfish question object: q(**params).answer(snapshot=..., reference_snapshot=...)."""

    def __init__(self, name: str, rows: list[dict[str, Any]], calls: list[Any]) -> None:
        self._name = name
        self._rows = rows
        self._calls = calls
        self._params: dict[str, Any] = {}

    def __call__(self, **kwargs: Any) -> _Question:
        self._params = kwargs
        return self

    def answer(self, **kwargs: Any) -> _Answer:
        assert "snapshot" in kwargs, kwargs
        self._calls.append((self._name, dict(self._params), dict(kwargs)))
        return _Answer(self._rows)


class _StubSession:
    """Mimics a pybatfish Session: .q questions and init_snapshot recording names."""

    def __init__(self, rows_by_question: dict[str, list[dict[str, Any]]]) -> None:
        self.q = SimpleNamespace()
        self.snapshots: list[str] = []
        self.paths: list[str] = []
        self.calls: list[Any] = []
        for name in [
            "differentialReachability",
            "reachability",
            "routes",
            "bgpSessionStatus",
            "undefinedReferences",
            "ipOwners",
            *rows_by_question,
        ]:
            setattr(self.q, name, _Question(name, rows_by_question.get(name, []), self.calls))

    def init_snapshot(self, path: str, name: str, overwrite: bool = False) -> None:
        self.snapshots.append(name)
        self.paths.append(path)


def _trace(disposition: str) -> SimpleNamespace:
    return SimpleNamespace(disposition=disposition)


def _flow(snapshot: str, reference: str, flow: str = "10.0.0.1 -> 10.0.0.2") -> dict[str, Any]:
    """One differentialReachability row (Snapshot_* = candidate, Reference_* = current)."""
    return {
        "Flow": flow,
        "Snapshot_Traces": [_trace(snapshot)],
        "Snapshot_TraceCount": 1,
        "Reference_Traces": [_trace(reference)],
        "Reference_TraceCount": 1,
    }


LOST = _flow("DENIED_IN", "ACCEPTED")
GAINED = _flow("ACCEPTED", "NO_ROUTE")


def _adapter(
    rows_by_question: dict[str, list[dict[str, Any]]],
) -> tuple[BatfishAdapter, _StubSession]:
    session = _StubSession(rows_by_question)
    return BatfishAdapter(AdapterConfig(name="batfish"), session=session), session


async def test_differential_reachability_counts_lost_and_gained() -> None:
    adapter, session = _adapter(
        {
            "differentialReachability": [
                LOST,
                _flow("ACCEPTED", "DENIED_OUT", "10.0.0.1 -> 10.0.0.3"),
                _flow("DELIVERED_TO_SUBNET", "NO_ROUTE", "10.0.0.1 -> 10.0.0.4"),
            ],
        }
    )
    current = {"leaf-01.cfg": "hostname leaf-01\naccess-list 100 permit ip any any\n"}
    candidate = {"leaf-01.cfg": "hostname leaf-01\naccess-list 100 permit ip any any\nvlan 200\n"}
    result = await adapter.analyse_differential(current, candidate)
    assert session.snapshots == ["current", "candidate"]
    assert result.differential["differential"] is True
    reach = result.differential["reachability"]
    assert isinstance(reach, dict)
    assert (
        reach["lost"] == 1
        and reach["gained"] == 2
        and reach["changed"] == 0
        and reach["total"] == 3
    )
    assert len(reach["lost_sample"]) == 1 and len(reach["gained_sample"]) == 2
    assert not result.passed
    assert any("reachability lost: 1 flow(s)" in f for f in result.failures)
    assert result.differential["changed_files"] == ["leaf-01.cfg"]
    assert (
        "differentialReachability",
        {},
        {"snapshot": "candidate", "reference_snapshot": "current"},
    ) in session.calls


async def test_differential_acl_impact_lists_changed_acl_lines() -> None:
    adapter, _ = _adapter({"differentialReachability": [LOST]})
    current = {
        "leaf-01.cfg": "hostname leaf-01\naccess-list 100 permit ip any any\naccess-list 100 deny ip any any\n"
    }
    candidate = {"leaf-01.cfg": "hostname leaf-01\naccess-list 100 deny ip any any\n"}
    result = await adapter.analyse_differential(current, candidate)
    impact = result.differential["acl_impact"]
    assert isinstance(impact, list) and len(impact) == 1
    entry = impact[0]
    assert entry["file"] == "leaf-01.cfg"
    assert "access-list 100 permit ip any any" in str(entry["line"])
    assert str(entry["line"]).startswith("-")
    assert entry["flows_lost"] == 1 and entry["flows_gained"] == 0
    assert (
        entry["attribution"] == "co-occurrence with reachability differential, not a Batfish proof"
    )


async def test_no_current_snapshot_falls_back_with_differential_false() -> None:
    adapter, session = _adapter({})
    result = await adapter.analyse_differential({}, {"leaf-01.cfg": "hostname leaf-01\n"})
    assert result.differential == {
        "differential": False,
        "note": "no current snapshot; differential skipped",
    }
    assert result.passed
    # Fallback is a single-snapshot analyse of the candidate only.
    assert session.snapshots == ["candidate"]


async def test_unknown_status_counts_as_changed_not_lost() -> None:
    adapter, _ = _adapter(
        {
            "differentialReachability": [
                _flow("ACCEPTED", "ACCEPTED"),  # path changed, outcome same
                {"Flow": "x", "Snapshot_Traces": [], "Reference_Traces": []},
            ],
        }
    )
    current = {"leaf-01.cfg": "hostname leaf-01\n"}
    candidate = {"leaf-01.cfg": "hostname leaf-01\nvlan 200\n"}
    result = await adapter.analyse_differential(current, candidate)
    reach = result.differential["reachability"]
    assert isinstance(reach, dict)
    assert reach["lost"] == 0 and reach["gained"] == 0 and reach["changed"] == 2
    assert result.passed and result.failures == []


async def test_gated_checks_run_on_candidate_and_duplicates_only() -> None:
    adapter, session = _adapter(
        {
            "ipOwners": [{"IP": "10.0.0.1", "Node": "leaf-01"}],
            "bgpSessionStatus": [{"Node": "leaf-01", "Established_Status": "NOT_ESTABLISHED"}],
        }
    )
    current = {"leaf-01.cfg": "hostname leaf-01\n"}
    candidate = {"leaf-01.cfg": "hostname leaf-01\nvlan 200\n"}
    result = await adapter.analyse_differential(current, candidate)
    assert ("ipOwners", {"duplicatesOnly": True}, {"snapshot": "candidate"}) in session.calls
    assert any("duplicate IPs: 1" in f for f in result.failures)
    assert any("BGP sessions down: 1" in f for f in result.failures)
