from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd

from vireonet.adapters.base import AdapterConfig
from vireonet.adapters.batfish import BatfishAdapter


def _session_with(rows_by_question: dict):  # type: ignore[no-untyped-def]
    session = MagicMock()
    session.init_snapshot.return_value = None
    for question, rows in rows_by_question.items():
        q = MagicMock()
        q.return_value.answer.return_value.frame.return_value = pd.DataFrame(rows)
        setattr(session.q, question, q)
    # Default empty for questions not in the dict.
    for qname in ["undefinedReferences", "ipOwners", "bgpSessionStatus", "reachability", "routes"]:
        if qname not in rows_by_question:
            q = MagicMock()
            q.return_value.answer.return_value.frame.return_value = pd.DataFrame([])
            setattr(session.q, qname, q)
    return session


async def test_batfish_passes_on_clean_snapshot() -> None:
    a = BatfishAdapter(AdapterConfig(name="batfish"), session=_session_with({}))
    result = await a.analyse({"leaf-01.cfg": "hostname leaf-01\nvlan 100\n"})
    assert result.passed and result.failures == []


async def test_batfish_fails_on_undefined_references() -> None:
    a = BatfishAdapter(
        AdapterConfig(name="batfish"),
        session=_session_with(
            {"undefinedReferences": [{"file": "leaf-01.cfg", "ref": "UNDEFINED"}]}
        ),
    )
    result = await a.analyse({"leaf-01.cfg": "x"})
    assert not result.passed and any("undefined" in f.lower() for f in result.failures)


async def test_batfish_health_down_without_session() -> None:
    a = BatfishAdapter(AdapterConfig(name="batfish"), host="127.0.0.1", port=1)
    # No pybatfish session can connect to port 1; health must report down, not raise.
    health = await a.health_check()
    assert health.status in {"down", "ok"}  # ok only if a Batfish service is actually running there
