from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from vireonet.adapters.base import AdapterConfig, AdapterHealth, BaseAdapter, create_http_client
from vireonet.adapters.errors import (
    AdapterAuthError,
    AdapterNotFound,
    AdapterTimeout,
    AdapterUnavailable,
    AdapterValidationError,
)


class MonitoringAdapter(BaseAdapter):
    """Monitoring, behind a flag. Backend: none | alertmanager | librenms.

    Alertmanager: silences via /api/v2/silences. LibreNMS: device enable/disable
    via /api/v0/devices/:hostname — UNCERTAIN, the exact field varies by version.
    With backend=none every method is a no-op returning a sentinel, so the
    workflow does not branch on the backend.
    """

    def __init__(
        self,
        config: AdapterConfig,
        *,
        backend: str = "none",
        base_url: str = "",
        token: str = "",
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(config, correlation_id=correlation_id)
        self.backend = backend
        self.base_url = base_url
        self.token = token

    def capabilities(self) -> list[str]:
        return ["silence", "enable", "disable"] if self.backend != "none" else []

    async def health_check(self) -> AdapterHealth:
        if self.backend == "none":
            return AdapterHealth(
                name=self.name,
                status="ok",
                capabilities=[],
                detail="monitoring disabled (backend=none)",
            )
        try:
            await self._request(
                "GET",
                "/api/v2/status" if self.backend == "alertmanager" else "/api/v0/system",
                idempotent=True,
            )
            return AdapterHealth(name=self.name, status="ok", capabilities=self.capabilities())
        except Exception as exc:  # noqa: BLE001
            return AdapterHealth(
                name=self.name, status="down", detail=str(exc), capabilities=self.capabilities()
            )

    async def _request(
        self, method: str, path: str, *, json_body: dict[str, Any] | None = None, idempotent: bool
    ) -> Any:
        async def do() -> Any:
            headers: dict[str, str] = {}
            if self.token:
                headers["X-Auth-Token"] = self.token
            if self.correlation_id:
                headers["X-Correlation-ID"] = self.correlation_id
            async with create_http_client(
                self.config, correlation_id=self.correlation_id
            ) as client:
                try:
                    resp = await client.request(method, path, json=json_body, headers=headers)
                except httpx.TimeoutException as exc:
                    raise AdapterTimeout(self.name, method, str(exc)) from exc
                except httpx.HTTPError as exc:
                    raise AdapterUnavailable(self.name, method, str(exc)) from exc
                if resp.status_code == 401:
                    raise AdapterAuthError(self.name, method, "monitoring auth failed")
                if resp.status_code == 404:
                    raise AdapterNotFound(self.name, method, path)
                if resp.status_code >= 400:
                    raise AdapterUnavailable(
                        self.name, method, f"status {resp.status_code}: {resp.text[:500]}"
                    )
                return resp.json() if resp.content else {}

        return await self.execute_with_retry(method, do, idempotent=idempotent)

    async def silence(self, device: str, duration_minutes: int) -> str:
        if self.backend == "none":
            return "no-op-monitoring-disabled"
        if self.backend == "alertmanager":
            now = datetime.now(UTC)
            body = {
                "matchers": [{"name": "instance", "value": device, "isRegex": False}],
                "startsAt": now.isoformat(),
                "endsAt": (now + timedelta(minutes=duration_minutes)).isoformat(),
                "createdBy": "vireonet",
                "comment": f"Change window for {device}",
            }
            data = await self._request("POST", "/api/v2/silences", json_body=body, idempotent=False)
            return str(data.get("silenceID", data.get("id", "")))
        if self.backend == "librenms":
            # LibreNMS maintenance / alert suppression is version-specific.
            # This uses the documented device-update endpoint; verify the field
            # name on your LibreNMS version before relying on it.
            await self._request(
                "PATCH", f"/api/v0/devices/{device}", json_body={"disabled": 1}, idempotent=True
            )
            return f"librenms-disabled-{device}"
        raise AdapterValidationError(self.name, "silence", f"unknown backend {self.backend}")

    async def unsilence(self, silence_id: str) -> None:
        if self.backend == "none" or silence_id in {"", "no-op-monitoring-disabled"}:
            return
        if self.backend == "alertmanager":
            await self._request("DELETE", f"/api/v2/silence/{silence_id}", idempotent=True)

    async def enable_monitoring(self, device: str) -> bool:
        if self.backend == "none":
            return True
        if self.backend == "librenms":
            await self._request(
                "PATCH", f"/api/v0/devices/{device}", json_body={"disabled": 0}, idempotent=True
            )
            return True
        # Alertmanager: monitoring is enabled by Prometheus discovery; there is
        # nothing to enable per device. Unsilencing (in close/rollback) is the action.
        return True

    async def disable_monitoring(self, device: str) -> bool:
        if self.backend == "none":
            return True
        if self.backend == "librenms":
            await self._request(
                "PATCH", f"/api/v0/devices/{device}", json_body={"disabled": 1}, idempotent=True
            )
            return True
        return True
