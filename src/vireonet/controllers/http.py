"""Shared HTTP plumbing for controller adapters."""

from __future__ import annotations

from typing import Any

import httpx

from vireonet.controllers.base import ControllerConfig, ControllerError

MAX_BODY_TEXT = 300


class HttpController:
    """Base: one httpx client per call (controllers are called rarely and a
    stale connection to an appliance is a common failure), login on demand,
    one re-login on 401."""

    vendor = "generic"
    platforms: tuple[str, ...] = ()
    native_upgrade = False
    default_url = ""

    def __init__(
        self, cfg: ControllerConfig, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.cfg = cfg
        self.name = cfg.name
        self.base = (cfg.url or self.default_url).rstrip("/")
        if not self.base:
            raise ControllerError(f"{cfg.name}: controller URL is required")
        self._transport = transport
        self._auth: dict[str, str] = {}

    def opt(self, key: str, default: Any = "") -> Any:
        return self.cfg.options.get(key, default)

    async def login(self, client: httpx.AsyncClient) -> dict[str, str]:
        """Headers that authenticate later requests."""
        return {}

    async def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        json: Any = None,
        data: Any = None,
        headers: dict[str, str] | None = None,
        expect_json: bool = True,
    ) -> Any:
        url = path if path.startswith("http") else f"{self.base}{path}"
        async with httpx.AsyncClient(
            timeout=self.cfg.timeout_seconds, verify=self.cfg.verify_tls, transport=self._transport
        ) as client:
            for attempt in (1, 2):
                if not self._auth:
                    self._auth = await self.login(client)
                resp = await client.request(
                    method,
                    url,
                    params=query,
                    json=json,
                    data=data,
                    headers={**self._auth, **(headers or {})},
                )
                if resp.status_code == 401 and attempt == 1:
                    self._auth = {}
                    continue
                break
        if resp.status_code >= 400:
            raise ControllerError(
                f"{self.name}: {method} {path} -> HTTP {resp.status_code}: {resp.text[:MAX_BODY_TEXT]}"
            )
        if not expect_json:
            return resp.text
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError as exc:
            raise ControllerError(f"{self.name}: {method} {path} did not return JSON") from exc

    async def api(
        self, method: str, path: str, *, query: dict[str, Any] | None = None, body: Any = None
    ) -> Any:
        if not path.startswith("/"):
            raise ControllerError("the API path must start with /")
        return await self.request(method.upper(), path, query=query, json=body)

    async def health(self) -> dict[str, Any]:
        try:
            await self.list_devices()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — health reports, never raises
            return {
                "name": self.name,
                "vendor": self.vendor,
                "status": "down",
                "detail": str(exc)[:200],
            }
        return {"name": self.name, "vendor": self.vendor, "status": "ok", "detail": self.base}
