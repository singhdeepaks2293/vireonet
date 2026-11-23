from __future__ import annotations

import hashlib
import hmac

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from vireonet.api.security import verify_github_webhook
from vireonet.change_state import ChangeState
from vireonet.config import Settings
from vireonet.state_machine import is_legal


def _request(body: bytes, signature: str) -> Request:
    sent = False

    async def receive() -> dict[str, object]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/webhooks/github",
            "raw_path": b"/webhooks/github",
            "query_string": b"",
            "headers": [(b"x-hub-signature-256", signature.encode("ascii"))],
            "client": ("127.0.0.1", 12345),
            "server": ("test", 443),
        },
        receive,
    )


@pytest.mark.asyncio
async def test_github_webhook_accepts_native_sha256_signature() -> None:
    secret = "test-webhook-secret"
    body = b'{"action":"opened"}'
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    request = _request(body, f"sha256={digest}")

    await verify_github_webhook(request, Settings(webhook_secret=secret, dev=False))


@pytest.mark.asyncio
async def test_github_webhook_rejects_invalid_signature() -> None:
    request = _request(b'{"action":"opened"}', "sha256=invalid")

    with pytest.raises(HTTPException) as exc:
        await verify_github_webhook(request, Settings(webhook_secret="secret", dev=False))

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_github_webhook_fails_closed_without_secret() -> None:
    request = _request(b"{}", "")

    with pytest.raises(HTTPException) as exc:
        await verify_github_webhook(request, Settings(webhook_secret="", dev=False))

    assert exc.value.status_code == 401


def test_closure_pending_is_recoverable_only_to_closed_or_failed() -> None:
    assert is_legal(ChangeState.RECONCILED, ChangeState.CLOSURE_PENDING)
    assert is_legal(ChangeState.CLOSURE_PENDING, ChangeState.CLOSED)
    assert is_legal(ChangeState.CLOSURE_PENDING, ChangeState.FAILED)
    assert not is_legal(ChangeState.CLOSURE_PENDING, ChangeState.APPLIED)
