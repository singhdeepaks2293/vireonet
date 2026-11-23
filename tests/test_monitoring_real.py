from __future__ import annotations

import respx
from httpx import Response

from vireonet.adapters.base import AdapterConfig
from vireonet.adapters.monitoring import MonitoringAdapter


def _adapter(backend: str = "alertmanager") -> MonitoringAdapter:
    return MonitoringAdapter(AdapterConfig(name="monitoring", base_url="https://am.example", max_retries=0), backend=backend, base_url="https://am.example")


async def test_monitoring_disabled_is_noop() -> None:
    a = MonitoringAdapter(AdapterConfig(name="monitoring"), backend="none")
    assert await a.silence("leaf-01", 60) == "no-op-monitoring-disabled"
    assert await a.enable_monitoring("leaf-01") is True


@respx.mock
async def test_alertmanager_silence_created() -> None:
    route = respx.post("https://am.example/api/v2/silences").mock(return_value=Response(200, json={"silenceID": "abc-123"}))
    assert await _adapter().silence("leaf-01", 60) == "abc-123"
    assert route.called
    body = route.calls[0].request.content.decode()
    assert "leaf-01" in body


@respx.mock
async def test_alertmanager_unsilence_deletes() -> None:
    route = respx.delete("https://am.example/api/v2/silence/abc-123").mock(return_value=Response(200, json={}))
    await _adapter().unsilence("abc-123")
    assert route.called
