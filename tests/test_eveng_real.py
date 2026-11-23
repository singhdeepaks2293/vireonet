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


@respx.mock
async def test_eveng_start_get_stop_wipe() -> None:
    respx.post(f"{BASE}/api/auth/login").mock(
        return_value=Response(
            200, json={"status": "success"}, headers={"Set-Cookie": "session=abc; Path=/"}
        )
    )
    respx.post(f"{BASE}/api/labs").mock(
        return_value=Response(201, json={"data": {"path": "/ci-1"}})
    )
    respx.get(url__regex=r".*/api/labs/.*").mock(
        return_value=Response(
            200, json={"data": {"name": "ci-1", "status": "running", "nodes": {"leaf-01": {}}}}
        )
    )
    respx.post(url__regex=r".*/nodes/stop.*").mock(return_value=Response(200, json={}))
    respx.delete(url__regex=r".*/api/labs/.*").mock(return_value=Response(200, json={}))
    a = _adapter()
    lab = await a.start_lab("ci-1", "two-leaf.unl")
    assert lab.id == "/ci-1"
    got = await a.get_lab(lab.id)
    assert got.status == "running" and "leaf-01" in got.nodes
    await a.stop_lab(lab.id)
    await a.wipe_lab(lab.id)  # must not raise — cleanup is part of the contract


@respx.mock
async def test_eveng_wait_for_nodes() -> None:
    respx.post(f"{BASE}/api/auth/login").mock(return_value=Response(200, json={}))
    respx.get(url__regex=r".*/api/labs/.*").mock(
        return_value=Response(
            200, json={"data": {"name": "ci-1", "status": "running", "nodes": {"leaf-01": {}}}}
        )
    )
    lab = await _adapter().wait_for_nodes("/ci-1", timeout_seconds=5)
    assert lab.nodes == ["leaf-01"]
