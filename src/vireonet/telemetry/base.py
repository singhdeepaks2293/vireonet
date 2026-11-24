from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol

import httpx


class TelemetryError(RuntimeError):
    """The monitoring backend failed or returned something unusable."""


@dataclass
class InterfaceRate:
    device: str
    interface: str
    in_bps: float
    out_bps: float
    speed_bps: float | None = None
    in_errors_ps: float | None = None
    out_errors_ps: float | None = None
    oper_status: str | None = None
    timestamp: str | None = None
    source: str = ""

    @property
    def in_util(self) -> float | None:
        return self.in_bps / self.speed_bps if self.speed_bps else None

    @property
    def out_util(self) -> float | None:
        return self.out_bps / self.speed_bps if self.speed_bps else None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["in_util"] = self.in_util
        d["out_util"] = self.out_util
        return d


@dataclass
class MetricPoint:
    timestamp: str
    in_bps: float
    out_bps: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TelemetryBackend(Protocol):
    name: str

    async def health(self) -> dict[str, Any]: ...

    async def interface_rates(self, devices: list[str]) -> list[InterfaceRate]: ...

    async def interface_history(
        self, device: str, interface: str, minutes: int = 60, step_seconds: int = 300
    ) -> list[MetricPoint]: ...


class HttpBackend:
    """Shared HTTP plumbing: base URL, timeout, TLS verification, errors."""

    name = "http"

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        verify_tls: bool = True,
        headers: dict[str, str] | None = None,
        auth: tuple[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url:
            raise TelemetryError(f"{self.name}: base URL is required")
        self.base_url = base_url.rstrip("/")
        self._kw: dict[str, Any] = {
            "timeout": timeout,
            "verify": verify_tls,
            "headers": headers or {},
        }
        if auth:
            self._kw["auth"] = auth
        if transport is not None:
            self._kw["transport"] = transport

    async def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            async with httpx.AsyncClient(base_url=self.base_url, **self._kw) as client:
                resp = await client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise TelemetryError(f"{self.name}: {exc.__class__.__name__}: {exc}") from exc
        if resp.status_code in (401, 403):
            raise TelemetryError(f"{self.name}: authentication failed ({resp.status_code})")
        if resp.status_code >= 400:
            raise TelemetryError(f"{self.name}: HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError as exc:
            raise TelemetryError(f"{self.name}: response is not JSON") from exc
