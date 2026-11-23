"""SDLC — Requirements R4: adapter framework + Fakes (M1/M2).

Deterministic Fakes, retry only on idempotent calls, circuit breaker,
correlation header, secrets never logged. No network.
"""

from __future__ import annotations

import pytest

from vireonet.adapters.base import (
    AdapterConfig,
    BaseAdapter,
    CircuitBreaker,
    RateLimiter,
    create_http_client,
)
from vireonet.adapters.errors import (
    AdapterNotFound,
    AdapterTimeout,
    AdapterUnavailable,
    AdapterValidationError,
)
from vireonet.adapters.fakes import FakeNetBoxAdapter, FakeServiceNowAdapter
from vireonet.structlog_config import redact


async def test_fake_netbox_is_deterministic() -> None:
    a, b = FakeNetBoxAdapter(), FakeNetBoxAdapter()
    assert (await a.get_device("leaf-01")).serial == "FOC1234A"
    assert (await b.get_device("leaf-01")).serial == "FOC1234A"
    assert (await a.get_device("leaf-01")).model == (await b.get_device("leaf-01")).model


async def test_fake_netbox_unknown_device_raises_not_found() -> None:
    with pytest.raises(AdapterNotFound):
        await FakeNetBoxAdapter().get_device("ghost-01")


async def test_fake_allocate_ip_increments_deterministically() -> None:
    fake = FakeNetBoxAdapter()
    assert await fake.allocate_ip("10.0.1.0/24", "leaf-01") == "10.0.1.101"
    assert await fake.allocate_ip("10.0.1.0/24", "leaf-02") == "10.0.1.102"


async def test_fake_netbox_new_methods() -> None:
    """SDLC fix: FakeNetBox now implements the full NetBoxProtocol."""
    fake = FakeNetBoxAdapter()
    assert "GigabitEthernet0/0" in await fake.list_interfaces("leaf-01")
    assert [v.vlan_id for v in await fake.list_vlans()] == [100]
    assert await fake.check_ip_free("10.0.9.9") is True
    assert await fake.check_ip_free("10.0.0.11") is False
    dev = await fake.create_device("leaf-03", "FOC1234C", "dc1")
    assert dev.name == "leaf-03"
    inv = await fake.export_inventory_for_awx()
    assert "leaf-01" in inv["all"]["hosts"]  # type: ignore[index]


async def test_fake_servicenow_duplicate_work_note_not_written_twice() -> None:
    fake = FakeServiceNowAdapter()
    assert await fake.add_work_note("RITM0001234", "hello", "key-1") is True
    assert await fake.add_work_note("RITM0001234", "hello", "key-1") is False
    assert await fake.add_work_note("RITM0001234", "hello", "key-2") is True


async def test_fake_servicenow_new_methods() -> None:
    """SDLC fix: FakeServiceNow now implements get_request_by_sys_id etc."""
    fake = FakeServiceNowAdapter()
    assert (await fake.get_request_by_sys_id("sys-1")).ticket == "RITM0001234"
    assert await fake.attach_file("RITM0001234", "plan.txt", b"x") == "attach-RITM0001234-plan.txt"
    assert await fake.get_approval_state("RITM0001234") == "approved"


async def test_fake_health_and_capabilities() -> None:
    for fake in (FakeNetBoxAdapter(), FakeServiceNowAdapter()):
        health = await fake.health_check()
        assert health.status == "ok"
        assert health.capabilities == fake.capabilities()
        assert health.capabilities


def test_http_client_sets_correlation_and_auth_headers() -> None:
    cfg = AdapterConfig(name="t", base_url="https://example.invalid", timeout_seconds=5.0)
    client = create_http_client(cfg, correlation_id="RITM0001234", token="tok-123")
    assert client.headers["X-Correlation-ID"] == "RITM0001234"
    assert client.headers["Authorization"] == "Bearer tok-123"
    assert client.timeout.connect == 5.0


def test_http_client_ignores_proxy_env() -> None:
    # Regression: NO_PROXY=[::1] crashed httpx client construction.
    cfg = AdapterConfig(name="t", base_url="https://example.invalid")
    client = create_http_client(cfg)
    assert client.trust_env is False


async def test_retry_only_idempotent_calls() -> None:
    cfg = AdapterConfig(
        name="t", max_retries=2, retry_base_seconds=0.001, rate_limit_per_second=1000.0
    )
    adapter = BaseAdapter(cfg)
    calls = {"n": 0}

    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise AdapterTimeout("t", "op", "timeout")
        return "ok"

    assert await adapter.execute_with_retry("op", flaky, idempotent=True) == "ok"
    assert calls["n"] == 3

    calls["n"] = 0
    with pytest.raises(AdapterTimeout):
        await adapter.execute_with_retry("op", flaky, idempotent=False)
    assert calls["n"] == 1  # non-idempotent: never retried


async def test_non_retryable_error_never_retried() -> None:
    cfg = AdapterConfig(
        name="t", max_retries=3, retry_base_seconds=0.001, rate_limit_per_second=1000.0
    )
    adapter = BaseAdapter(cfg)
    calls = {"n": 0}

    async def bad() -> str:
        calls["n"] += 1
        raise AdapterValidationError("t", "op", "bad input")

    with pytest.raises(AdapterValidationError):
        await adapter.execute_with_retry("op", bad, idempotent=True)
    assert calls["n"] == 1


async def test_circuit_breaker_opens_after_threshold() -> None:
    breaker = CircuitBreaker(failure_threshold=2, reset_timeout_seconds=60.0)
    await breaker.before_call("t")  # closed: no raise
    await breaker.on_failure()
    await breaker.before_call("t")  # 1 failure: still closed
    await breaker.on_failure()
    with pytest.raises(AdapterUnavailable, match="circuit open"):
        await breaker.before_call("t")
    await breaker.on_success()  # success resets
    await breaker.before_call("t")


async def test_paginate_collects_all_pages() -> None:
    cfg = AdapterConfig(name="t")
    adapter = BaseAdapter(cfg)

    async def fetch(page: int):
        return ([page], page < 3)

    assert await adapter.paginate(fetch) == [1, 2, 3]


def test_redact_masks_secrets_recursively() -> None:
    data = {
        "username": "alice",
        "password": "hunter2",
        "nested": {"token": "abc", "count": 3},
        "items": [{"secret": "s"}],
    }
    out = redact(data)
    assert out["username"] == "alice"
    assert out["password"] == "***"
    assert out["nested"]["token"] == "***"
    assert out["nested"]["count"] == 3
    assert out["items"][0]["secret"] == "***"
    assert "hunter2" not in str(out)
    assert "abc" not in str(out)


async def test_rate_limiter_allows_burst_then_waits() -> None:
    limiter = RateLimiter(rate_per_second=1000.0, capacity=2)
    await limiter.acquire()
    await limiter.acquire()
    # Third acquire waits for a token; with 1000/s it is fast but must not raise.
    await limiter.acquire()
