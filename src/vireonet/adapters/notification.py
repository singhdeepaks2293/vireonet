from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage
from typing import Any

import httpx

from vireonet.adapters.base import AdapterConfig, AdapterHealth, BaseAdapter, create_http_client
from vireonet.adapters.errors import AdapterUnavailable, AdapterValidationError


def _teams_payload(title: str, body: str) -> dict[str, Any]:
    """Teams Workflows ("Post to a channel when a webhook request is received") payload."""
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {"type": "TextBlock", "text": title, "weight": "Bolder", "wrap": True},
                        {"type": "TextBlock", "text": body, "wrap": True},
                    ],
                },
            }
        ],
    }


class NotificationAdapter(BaseAdapter):
    """Real notifications: SMTP email, Teams webhook, Slack webhook.

    A channel with no URL / no SMTP host configured returns False instead of
    raising — a missing webhook must not fail a change. Secrets are never logged.
    """

    def __init__(
        self,
        config: AdapterConfig,
        *,
        smtp_host: str = "",
        smtp_port: int = 587,
        smtp_username: str = "",
        smtp_password: str = "",
        email_from: str = "vireonet@example.com",
        email_to: str = "",
        teams_webhook_url: str = "",
        slack_webhook_url: str = "",
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(config, correlation_id=correlation_id)
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_username = smtp_username
        self.smtp_password = smtp_password
        self.email_from = email_from
        self.email_to = email_to
        self.teams_webhook_url = teams_webhook_url
        self.slack_webhook_url = slack_webhook_url

    def capabilities(self) -> list[str]:
        return ["email", "teams", "slack"]

    async def health_check(self) -> AdapterHealth:
        configured = bool(self.teams_webhook_url or self.slack_webhook_url or self.smtp_host)
        return AdapterHealth(
            name=self.name,
            status="ok" if configured else "down",
            capabilities=self.capabilities(),
            detail="" if configured else "no notification channel configured",
        )

    async def send(self, channel: str, title: str, body: str, ticket: str) -> bool:
        if channel == "email":
            return await self._send_email(title, body, ticket)
        if channel == "teams":
            # Office 365 connectors (MessageCard) are retired; Teams Workflows
            # webhooks take a message with an Adaptive Card attachment.
            return await self._send_webhook(
                self.teams_webhook_url, _teams_payload(title, f"{body}\nTicket: {ticket}")
            )
        if channel == "slack":
            return await self._send_webhook(
                self.slack_webhook_url, {"text": f"*{title}*\n{body}\nTicket: {ticket}"}
            )
        raise AdapterValidationError(self.name, "send", f"unknown channel {channel}")

    async def _send_webhook(self, url: str, payload: dict[str, Any]) -> bool:
        if not url:
            return False
        async with create_http_client(self.config, correlation_id=self.correlation_id) as client:
            try:
                resp = await client.post(url, json=payload)
            except httpx.HTTPError as exc:
                raise AdapterUnavailable(self.name, "send_webhook", str(exc)) from exc
            # Teams Workflows webhooks answer 202 Accepted; Slack answers 200.
            return 200 <= resp.status_code < 300

    async def _send_email(self, title: str, body: str, ticket: str) -> bool:
        if not self.smtp_host or not self.email_to:
            return False

        def _do() -> None:
            msg = EmailMessage()
            msg["Subject"] = f"[VireoNet {ticket}] {title}"
            msg["From"] = self.email_from
            msg["To"] = self.email_to
            msg.set_content(f"{body}\n\nTicket: {ticket}\n")
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10) as server:
                server.starttls()
                if self.smtp_username:
                    server.login(self.smtp_username, self.smtp_password)
                server.send_message(msg)

        await asyncio.to_thread(_do)
        return True
