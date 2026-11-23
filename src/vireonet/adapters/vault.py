from __future__ import annotations

import time
from typing import Any

import hvac

from vireonet.adapters.base import AdapterConfig, AdapterHealth, BaseAdapter
from vireonet.adapters.errors import AdapterAuthError, AdapterNotFound, AdapterUnavailable


class VaultAdapter(BaseAdapter):
    """Real Vault adapter via hvac. KV v2 reads, AppRole auth, token renewal.

    Secrets are never cached and never logged. Only the Vault token and its
    expiry are held, and the token is renewed (not the secret) when close to expiry.
    """

    def __init__(
        self, config: AdapterConfig, *, addr: str = "", role_id: str = "", secret_id: str = "",
        kv_mount: str = "secret", bootstrap_policy: str = "bootstrap", client: Any | None = None,
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(config, correlation_id=correlation_id)
        self.addr = addr
        self.role_id = role_id
        self.secret_id = secret_id
        self.kv_mount = kv_mount
        self.bootstrap_policy = bootstrap_policy
        self._client = client  # injected in tests; otherwise built lazily
        self._token_expires_at: float = 0.0

    def capabilities(self) -> list[str]:
        return ["kv_read", "approle", "token_renew", "bootstrap_token"]

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = hvac.Client(url=self.addr)
        return self._client

    async def health_check(self) -> AdapterHealth:
        try:
            client = self._get_client()
            health = client.sys.read_health_status(method="GET")
            ok = bool(health.get("initialized") and not health.get("sealed"))
            return AdapterHealth(name=self.name, status="ok" if ok else "down", capabilities=self.capabilities(), detail="" if ok else "vault sealed or not initialised")
        except Exception as exc:  # noqa: BLE001 — health must not raise
            return AdapterHealth(name=self.name, status="down", detail=str(exc), capabilities=self.capabilities())

    async def _ensure_auth(self) -> Any:
        client = self._get_client()
        if client.is_authenticated() and time.time() < self._token_expires_at - 60:
            return client
        if not self.role_id or not self.secret_id:
            raise AdapterAuthError(self.name, "approle", "Vault role_id/secret_id not configured")
        try:
            resp = client.auth.approle.login(role_id=self.role_id, secret_id=self.secret_id)
        except Exception as exc:
            raise AdapterAuthError(self.name, "approle", str(exc)) from exc
        auth = resp.get("auth", {}) if isinstance(resp, dict) else {}
        # hvac returns the token in auth.client_token; lease_duration is its TTL.
        if auth.get("client_token"):
            client.token = auth["client_token"]
        self._token_expires_at = time.time() + float(auth.get("lease_duration", 3600))
        return client

    async def read_secret(self, path: str) -> dict[str, str]:
        client = await self._ensure_auth()
        try:
            resp = client.secrets.kv.v2.read_secret_version(path=path, mount_point=self.kv_mount)
        except Exception as exc:
            raise AdapterUnavailable(self.name, "read_secret", str(exc)) from exc
        if not resp or not resp.get("data"):
            raise AdapterNotFound(self.name, "read_secret", path)
        data = resp["data"].get("data", {})
        return {str(k): str(v) for k, v in data.items()}

    async def renew_token(self) -> None:
        client = await self._ensure_auth()
        try:
            client.auth.token.renew_self()
            self._token_expires_at = time.time() + 3600
        except Exception as exc:
            raise AdapterAuthError(self.name, "renew_token", str(exc)) from exc

    async def issue_bootstrap_token(self, device: str) -> str:
        """Short-lived Vault token for a ZTP device (15m, bootstrap policy).

        The token itself is the credential; VireoNet returns it once and does
        not store it. The device presents it to the callback endpoint (M6).
        """
        client = await self._ensure_auth()
        try:
            resp = client.auth.token.create(ttl="15m", policies=[self.bootstrap_policy], meta={"device": device})
        except Exception as exc:
            raise AdapterUnavailable(self.name, "issue_bootstrap_token", str(exc)) from exc
        token = resp.get("auth", {}).get("client_token", "") if isinstance(resp, dict) else ""
        if not token:
            raise AdapterUnavailable(self.name, "issue_bootstrap_token", "Vault returned no token")
        return str(token)
