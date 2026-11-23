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
    """Mimics a pybatfish question object.

    Primary path: answer(snapshot="candidate", reference_snapshot="current").
    With differential_only=True, answer() raises TypeError when snapshot kwargs
    are passed, forcing the adapter's q(differential=True) fallback.
    """

    def __init__(self, rows: list[dict[str, Any]], *, differential_only: bool = False) -> None:
        self._rows = rows
        self._differential_only = differential_only
        self._differential = False

    def __call__(self, **kwargs: Any) -> _Question:
        self._differential = bool(kwargs.get("differential", False))
        return self

    def answer(self, **kwargs: Any) -> _Answer:
        if kwargs:
            assert kwargs == {"snapshot": "candidate", "reference_snapshot": "current"}, kwargs
            if self._differential_only:
                raise TypeError("answer() got unexpected keyword arguments 'snapshot'")
        elif self._differential_only and not self._differential:
            raise TypeError("differential questions need q(differential=True)")
        return _Answer(self._rows)


class _StubSession:
    """Mimics a pybatfish Session: .q questions and init_snapshot recording names."""

    def __init__(
        self, rows_by_question: dict[str, list[dict[str, Any]]], *, differential_only: bool = False
    ) -> None:
        self.q = SimpleNamespace()
        self.snapshots: list[str] = []
        for name, rows in rows_by_question.items():
            setattr(self.q, name, _Question(rows, differential_only=differential_only))
        for name in [
            "reachability",
            "routes",
            "bgpSessionStatus",
            "undefinedReferences",
            "ipOwners",
        ]:
            if not hasattr(self.q, name):
                setattr(self.q, name, _Question([], differential_only=differential_only))

    def init_snapshot(self, path: str, name: str, overwrite: bool = False) -> None:
        self.snapshots.append(name)


def _adapter(
    rows_by_question: dict[str, list[dict[str, Any]]], **kw: Any
) -> tuple[BatfishAdapter, _StubSession]:
    session = _StubSession(rows_by_question, **kw)
    return BatfishAdapter(AdapterConfig(name="batfish"), session=session), session


async def test_differential_reachability_counts_lost_and_gained() -> None:
    adapter, session = _adapter(
        {
            "reachability": [
                {"Differential": "decreased", "Flow": "10.0.0.1 -> 10.0.0.2"},
                {"Differential": "increased", "Flow": "10.0.0.1 -> 10.0.0.3"},
                {"Differential": "increased", "Flow": "10.0.0.1 -> 10.0.0.4"},
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


async def test_differential_acl_impact_lists_changed_acl_lines() -> None:
    adapter, _ = _adapter({"reachability": [{"Differential": "decreased"}]})
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
            "reachability": [{"Differential": "unknown"}, {"Status": "not_applicable"}],
        }
    )
    current = {"leaf-01.cfg": "hostname leaf-01\n"}
    candidate = {"leaf-01.cfg": "hostname leaf-01\nvlan 200\n"}
    result = await adapter.analyse_differential(current, candidate)
    reach = result.differential["reachability"]
    assert isinstance(reach, dict)
    assert reach["lost"] == 0 and reach["gained"] == 0 and reach["changed"] == 2
    assert result.passed and result.failures == []


async def test_differential_falls_back_to_differential_flag() -> None:
    adapter, _ = _adapter(
        {"reachability": [{"Differential": "decreased"}]},
        differential_only=True,
    )
    current = {"leaf-01.cfg": "hostname leaf-01\n"}
    candidate = {"leaf-01.cfg": "hostname leaf-01\nvlan 200\n"}
    result = await adapter.analyse_differential(current, candidate)
    reach = result.differential["reachability"]
    assert isinstance(reach, dict)
    assert reach["lost"] == 1
    assert any("reachability lost: 1 flow(s)" in f for f in result.failures)
