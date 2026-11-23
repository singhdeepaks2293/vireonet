from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeVar

import httpx
import structlog

from vireonet.adapters.errors import (
    AdapterError,
    AdapterRateLimited,
    AdapterTimeout,
    AdapterUnavailable,
)
from vireonet.structlog_config import redact

T = TypeVar("T")

RETRYABLE = (AdapterTimeout, AdapterUnavailable, AdapterRateLimited)


@dataclass
class AdapterConfig:
    name: str
    base_url: str | None = None
    timeout_seconds: float = 10.0
    max_retries: int = 3
    retry_base_seconds: float = 0.5
    failure_threshold: int = 5
    reset_timeout_seconds: float = 30.0
    rate_limit_per_second: float = 10.0
    enabled: bool = True


@dataclass
class AdapterHealth:
    name: str
    status: str  # ok | degraded | down
    latency_ms: float | None = None
    last_success: str | None = None
    capabilities: list[str] = field(default_factory=list)
    detail: str = ""


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Simple async circuit breaker. Open after N consecutive failures."""

    def __init__(self, failure_threshold: int = 5, reset_timeout_seconds: float = 30.0) -> None:
        self.failure_threshold = failure_threshold
        self.reset_timeout_seconds = reset_timeout_seconds
        self._failures = 0
        self._opened_at: float | None = None
        self._lock = asyncio.Lock()

    async def before_call(self, adapter: str) -> None:
        async with self._lock:
            if self._opened_at is None:
                return
            if time.monotonic() - self._opened_at >= self.reset_timeout_seconds:
                self._opened_at = None
                self._failures = 0
                return
            raise AdapterUnavailable(adapter, "circuit_breaker", "circuit open")

    async def on_success(self) -> None:
        async with self._lock:
            self._failures = 0
            self._opened_at = None

    async def on_failure(self) -> None:
        async with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._opened_at = time.monotonic()


class RateLimiter:
    """Token bucket. `acquire` waits instead of failing, so callers stay simple."""

    def __init__(self, rate_per_second: float, capacity: int = 10) -> None:
        self.rate = rate_per_second
        self.capacity = capacity
        self._tokens = float(capacity)
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
            self._updated = now
            if self._tokens >= 1:
                self._tokens -= 1
                return
            wait = (1 - self._tokens) / self.rate
        await asyncio.sleep(wait)
        async with self._lock:
            self._tokens = max(0.0, self._tokens - 1)


def create_http_client(
    config: AdapterConfig, *, correlation_id: str | None = None, token: str | None = None
) -> httpx.AsyncClient:
    """One factory for all adapters: timeout + correlation header, no secrets logged."""
    headers: dict[str, str] = {"Accept": "application/json"}
    if correlation_id:
        headers["X-Correlation-ID"] = correlation_id
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.AsyncClient(
        base_url=config.base_url or "",
        timeout=httpx.Timeout(config.timeout_seconds, connect=5.0),
        headers=headers,
        # Adapters talk to internal systems; never pick up ambient proxy env
        # vars. (httpx parses NO_PROXY entries as URL patterns, and entries
        # like [::1] crash client construction with InvalidURL.)
        trust_env=False,
    )


class BaseAdapter:
    """Shared behaviour for Real adapters. Fakes subclass this for health/caps only."""

    def __init__(self, config: AdapterConfig, *, correlation_id: str | None = None) -> None:
        self.config = config
        self.correlation_id = correlation_id
        self.log = structlog.get_logger(config.name)
        self.breaker = CircuitBreaker(config.failure_threshold, config.reset_timeout_seconds)
        self.limiter = RateLimiter(config.rate_limit_per_second)

    @property
    def name(self) -> str:
        return self.config.name

    def capabilities(self) -> list[str]:
        return []

    async def health_check(self) -> AdapterHealth:
        return AdapterHealth(name=self.name, status="ok", capabilities=self.capabilities())

    def _log_call(self, operation: str, **fields: Any) -> None:
        # Log keys / counts only. Payloads are redacted and truncated by callers.
        self.log.info("adapter_call", operation=operation, **redact(fields))  # type: ignore[arg-type]

    async def execute_with_retry(
        self,
        operation: str,
        func: Callable[[], Awaitable[T]],
        *,
        idempotent: bool,
    ) -> T:
        """Retry only safe/idempotent calls, only on retryable errors, with jitter.

        Non-idempotent calls (POST that creates) are never retried here — the
        caller must make them idempotent first (idempotency key, M2).
        """
        last: AdapterError | None = None
        attempts = self.config.max_retries + 1 if idempotent else 1
        for attempt in range(attempts):
            await self.breaker.before_call(self.name)
            await self.limiter.acquire()
            try:
                result = await func()
            except RETRYABLE as exc:
                last = exc
                await self.breaker.on_failure()
                if attempt == attempts - 1:
                    raise
                delay = self.config.retry_base_seconds * (2**attempt)
                await asyncio.sleep(delay + random.uniform(0, delay * 0.5))
                continue
            except AdapterError:
                await self.breaker.on_failure()
                raise
            await self.breaker.on_success()
            return result
        assert last is not None
        raise last

    async def paginate(
        self,
        fetch_page: Callable[[int], Awaitable[tuple[list[T], bool]]],
    ) -> list[T]:
        """fetch_page(page) -> (items, has_more). Collects all pages."""
        out: list[T] = []
        page = 1
        while True:
            items, has_more = await fetch_page(page)
            out.extend(items)
            if not has_more:
                return out
            page += 1
