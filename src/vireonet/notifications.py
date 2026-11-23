from __future__ import annotations

from vireonet.adapters.protocols import NotificationProtocol


class NotificationService:
    """Four events, plain wording, no exclamation marks. Channel is configured,
    not chosen per call — the caller passes the ticket and the reason."""

    def __init__(self, adapter: NotificationProtocol, *, channel: str = "slack") -> None:
        self._adapter = adapter
        self._channel = channel

    async def approval_needed(self, ticket: str, change_type: str, devices: list[str]) -> bool:
        return await self._adapter.send(
            self._channel, f"Approval needed: {ticket}",
            f"Change {change_type} on {', '.join(devices)} passed CI and is waiting for approval.", ticket,
        )

    async def change_failed(self, ticket: str, reason: str) -> bool:
        return await self._adapter.send(
            self._channel, f"Change failed: {ticket}", f"Change failed: {reason}", ticket,
        )

    async def rolled_back(self, ticket: str, reason: str) -> bool:
        return await self._adapter.send(
            self._channel, f"Rolled back: {ticket}", f"Change was rolled back: {reason}", ticket,
        )

    async def change_completed(self, ticket: str) -> bool:
        return await self._adapter.send(
            self._channel, f"Change completed: {ticket}", "Change applied, post-checked and closed.", ticket,
        )
