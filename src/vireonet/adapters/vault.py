from __future__ import annotations

import time
from typing import Any

import hvac
import hvac.exceptions

from vireonet.adapters.base import AdapterConfig, AdapterHealth, BaseAdapter
from vireonet.adapters.errors import AdapterAuthError, AdapterNotFound, AdapterUnavailable


class VaultAdapter(BaseAdapter):
    """Real Vault adapter via hvac. KV v2 reads, AppRole auth, token renewal.

    Secrets are never cached and never logged. Only the Vault token and its
    expiry are held, and the token is renewed (not the secret) when close to expiry.
    """

    def __init__(
        self,
        config: AdapterConfig,
        *,
        addr: str = "",
        role_id: str = "",
        secret_id: str = "",
        kv_mount: str = "secret",
        bootstrap_policy: str = "bootstrap",
        bootstrap_token_role: str = "",
        bootstrap_no_parent: bool = False,
        client: Any | None = None,
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(config, correlation_id=correlation_id)
        self.addr = addr
        self.role_id = role_id
        self.secret_id = secret_id
        self.kv_mount = kv_mount
        self.bootstrap_policy = bootstrap_policy
        self.bootstrap_token_role = bootstrap_token_role
        self.bootstrap_no_parent = bootstrap_no_parent
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
            return AdapterHealth(
                name=self.name,
                status="ok" if ok else "down",
                capabilities=self.capabilities(),
                detail="" if ok else "vault sealed or not initialised",
            )
        except Exception as exc:  # noqa: BLE001 — health must not raise
            return AdapterHealth(
                name=self.name, status="down", detail=str(exc), capabilities=self.capabilities()
            )

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
        except hvac.exceptions.InvalidPath as exc:
            # KV v2 returns 404 (InvalidPath) for a missing or deleted secret.
            raise AdapterNotFound(self.name, "read_secret", path) from exc
        except hvac.exceptions.Forbidden as exc:
            raise AdapterAuthError(self.name, "read_secret", str(exc)) from exc
        except Exception as exc:
            raise AdapterUnavailable(self.name, "read_secret", str(exc)) from exc
        if not resp or not resp.get("data"):
            raise AdapterNotFound(self.name, "read_secret", path)
        data = resp["data"].get("data", {})
        return {str(k): str(v) for k, v in data.items()}

    async def renew_token(self) -> None:
        client = await self._ensure_auth()
        try:
            resp = client.auth.token.renew_self()
        except Exception as exc:
            raise AdapterAuthError(self.name, "renew_token", str(exc)) from exc
        # The new TTL is auth.lease_duration (seconds); Vault may cap it at
        # the token's max TTL, so never assume a fixed value.
        auth = resp.get("auth", {}) if isinstance(resp, dict) else {}
        lease = auth.get("lease_duration")
        if lease is not None:
            self._token_expires_at = time.time() + float(lease)

    async def issue_bootstrap_token(self, device: str) -> str:
        """Short-lived Vault token for a ZTP device (15m, bootstrap policy).

        The token itself is the credential; VireoNet returns it once and does
        not store it. The device presents it to the callback endpoint (M6).

        By default the token is a child of the AppRole token, so it is revoked
        when that token expires/is revoked (and its TTL is capped by it). For
        an independent (orphan) token either set ``bootstrap_token_role`` to a
        token role (``auth/token/create/<role>``, the role must be ``orphan``
        and allow the bootstrap policy), or set ``bootstrap_no_parent`` — the
        latter needs sudo on ``auth/token/create``, so it is off by default.
        https://developer.hashicorp.com/vault/api-docs/auth/token#create-token
        """
        client = await self._ensure_auth()
        kwargs: dict[str, Any] = {
            "ttl": "15m",
            "policies": [self.bootstrap_policy],
            "meta": {"device": device},
        }
        if self.bootstrap_token_role:
            kwargs["role_name"] = self.bootstrap_token_role
        elif self.bootstrap_no_parent:
            kwargs["no_parent"] = True
        try:
            resp = client.auth.token.create(**kwargs)
        except Exception as exc:
            raise AdapterUnavailable(self.name, "issue_bootstrap_token", str(exc)) from exc
        token = resp.get("auth", {}).get("client_token", "") if isinstance(resp, dict) else ""
        if not token:
            raise AdapterUnavailable(self.name, "issue_bootstrap_token", "Vault returned no token")
        return str(token)
