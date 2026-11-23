from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from vireonet.adapters.base import AdapterConfig
from vireonet.adapters.errors import AdapterAuthError, AdapterNotFound
from vireonet.adapters.vault import VaultAdapter


def _fake_hvac() -> MagicMock:
    client = MagicMock()
    client.is_authenticated.return_value = False
    client.auth.approle.login.return_value = {
        "auth": {"client_token": "tok-1", "lease_duration": 3600}
    }
    client.secrets.kv.v2.read_secret_version.return_value = {
        "data": {"data": {"token": "awx-token"}}
    }
    client.auth.token.create.return_value = {"auth": {"client_token": "bootstrap-1"}}
    return client


def _adapter(client: MagicMock) -> VaultAdapter:
    return VaultAdapter(
        AdapterConfig(name="vault"),
        addr="https://vault.example",
        role_id="r",
        secret_id="s",
        client=client,
    )


async def test_vault_read_secret_via_approle() -> None:
    client = _fake_hvac()
    a = _adapter(client)
    assert await a.read_secret("vireonet/awx") == {"token": "awx-token"}
    client.auth.approle.login.assert_called_once()


async def test_vault_read_missing_raises_not_found() -> None:
    client = _fake_hvac()
    client.secrets.kv.v2.read_secret_version.return_value = {"data": None}
    with pytest.raises(AdapterNotFound):
        await _adapter(client).read_secret("nope")


async def test_vault_bootstrap_token_short_lived() -> None:
    client = _fake_hvac()
    assert await _adapter(client).issue_bootstrap_token("leaf-01") == "bootstrap-1"
    _, kwargs = client.auth.token.create.call_args
    assert kwargs["ttl"] == "15m"
    assert kwargs["policies"] == ["bootstrap"]


async def test_vault_no_role_id_raises_auth() -> None:
    client = _fake_hvac()
    a = VaultAdapter(AdapterConfig(name="vault"), addr="https://vault.example", client=client)
    with pytest.raises(AdapterAuthError):
        await a.read_secret("x")
