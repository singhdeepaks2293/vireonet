from __future__ import annotations

import respx
from httpx import Response

from vireonet.adapters.base import AdapterConfig
from vireonet.adapters.notification import NotificationAdapter


def _adapter(**kw) -> NotificationAdapter:  # type: ignore[no-untyped-def]
    return NotificationAdapter(AdapterConfig(name="notification", max_retries=0), **kw)


@respx.mock
async def test_slack_webhook_posts() -> None:
    route = respx.post("https://hooks.slack.example/x").mock(return_value=Response(200, json={"ok": True}))
    assert await _adapter(slack_webhook_url="https://hooks.slack.example/x").send("slack", "Approval needed", "body", "RITM1")
    assert route.called


@respx.mock
async def test_teams_webhook_posts() -> None:
    route = respx.post("https://teams.example/x").mock(return_value=Response(200, json={}))
    assert await _adapter(teams_webhook_url="https://teams.example/x").send("teams", "Title", "body", "RITM1")
    assert route.called


async def test_unconfigured_channel_returns_false_not_raise() -> None:
    assert await _adapter().send("slack", "Title", "body", "RITM1") is False
    assert await _adapter().send("email", "Title", "body", "RITM1") is False


async def test_email_via_mocked_smtp(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    sent: list = []

    class FakeSMTP:
        def __init__(self, *a, **k):  # type: ignore[no-untyped-def]
            pass

        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *a):  # type: ignore[no-untyped-def]
            return False

        def starttls(self):  # type: ignore[no-untyped-def]
            pass

        def login(self, *a):  # type: ignore[no-untyped-def]
            pass

        def send_message(self, msg):  # type: ignore[no-untyped-def]
            sent.append(msg)

    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    assert await _adapter(smtp_host="smtp.example", email_to="ops@example.com").send("email", "Title", "body", "RITM1")
    assert sent and "RITM1" in str(sent[0]["Subject"])
