from __future__ import annotations

import respx
from httpx import Response

from vireonet.adapters.base import AdapterConfig
from vireonet.adapters.eveng import EveNgAdapter

BASE = "https://eveng.example"


def _adapter() -> EveNgAdapter:
    return EveNgAdapter(
        AdapterConfig(name="eveng", base_url=BASE, max_retries=0), username="admin", password="eve"
    )


NODES = {
    "code": 200,
    "status": "success",
    "data": {"1": {"id": 1, "name": "leaf-01", "status": 2, "url": "telnet://eveng.example:32769"}},
}


def _mock_lab(folder: str = "") -> dict[str, respx.Route]:
    lab = f"{BASE}/api/labs{folder}/ci-1.unl"
    return {
        "login": respx.post(f"{BASE}/api/auth/login").mock(
            return_value=Response(
                200, json={"status": "success"}, headers={"Set-Cookie": "session=abc; Path=/"}
            )
        ),
        "create": respx.post(f"{BASE}/api/labs").mock(
            return_value=Response(200, json={"code": 200, "status": "success", "message": "ok"})
        ),
        "start": respx.get(f"{lab}/nodes/start").mock(return_value=Response(200, json={})),
        "stop": respx.get(f"{lab}/nodes/stop").mock(return_value=Response(200, json={})),
        "nodes": respx.get(f"{lab}/nodes").mock(return_value=Response(200, json=NODES)),
        "lab": respx.get(lab).mock(return_value=Response(200, json={"data": {"name": "ci-1"}})),
        "delete": respx.delete(lab).mock(return_value=Response(200, json={})),
    }


@respx.mock
async def test_eveng_start_get_stop_wipe() -> None:
    routes = _mock_lab()
    a = _adapter()
    lab = await a.start_lab("ci-1", "two-leaf.unl")
    assert lab.id == "/ci-1.unl"
    assert routes["start"].called
    got = await a.get_lab(lab.id)
    assert got.status == "running" and "leaf-01" in got.nodes
    await a.stop_lab(lab.id)
    assert routes["stop"].called
    await a.wipe_lab(lab.id)  # must not raise — cleanup is part of the contract
    assert routes["delete"].called


@respx.mock
async def test_eveng_wait_for_nodes() -> None:
    _mock_lab()
    lab = await _adapter().wait_for_nodes("/ci-1", timeout_seconds=5)
    assert lab.nodes == ["leaf-01"]
