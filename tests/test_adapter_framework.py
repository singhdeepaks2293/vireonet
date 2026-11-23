from __future__ import annotations

import httpx
import pytest
import respx

from vireonet.adapters.base import (
    AdapterConfig,
    BaseAdapter,
    CircuitBreaker,
    RateLimiter,
    create_http_client,
)
from vireonet.adapters.errors import AdapterTimeout, AdapterUnavailable


class _Probe(BaseAdapter):
    def __init__(self) -> None:
        super().__init__(AdapterConfig(name="probe", max_retries=2, retry_base_seconds=0.01))


async def test_retry_only_on_retryable_then_succeeds() -> None:
    probe = _Probe()
    calls = {"n": 0}

    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise AdapterTimeout("probe", "op", "timeout")
        return "ok"

    assert await probe.execute_with_retry("op", flaky, idempotent=True) == "ok"
    assert calls["n"] == 3


async def test_non_idempotent_call_never_retried() -> None:
    probe = _Probe()
    calls = {"n": 0}

    async def always_fail() -> str:
        calls["n"] += 1
        raise AdapterTimeout("probe", "op", "timeout")

    with pytest.raises(AdapterTimeout):
        await probe.execute_with_retry("op", always_fail, idempotent=False)
    assert calls["n"] == 1


async def test_circuit_breaker_opens_after_threshold() -> None:
    breaker = CircuitBreaker(failure_threshold=2, reset_timeout_seconds=60)
    await breaker.on_failure()
    await breaker.on_failure()
    with pytest.raises(AdapterUnavailable):
        await breaker.before_call("probe")
    await breaker.on_success()
    await breaker.before_call("probe")  # closed again


async def test_rate_limiter_acquire_does_not_raise() -> None:
    limiter = RateLimiter(rate_per_second=100)
    await limiter.acquire()
    await limiter.acquire()


async def test_http_client_sets_correlation_header() -> None:
    cfg = AdapterConfig(name="probe", base_url="https://example.invalid")
    async with create_http_client(cfg, correlation_id="RITM0001234") as client:
        assert client.headers["X-Correlation-ID"] == "RITM0001234"


@respx.mock
async def test_http_client_timeout_is_set() -> None:
    route = respx.get("https://example.invalid/devices").mock(return_value=httpx.Response(200, json=[]))
    cfg = AdapterConfig(name="probe", base_url="https://example.invalid", timeout_seconds=5)
    async with create_http_client(cfg, correlation_id="RITM1") as client:
        resp = await client.get("/devices")
        assert resp.status_code == 200
        assert route.called
