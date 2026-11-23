"""Drift attribution (D3): who made the change, and is a change in flight?

Attribution is the seam, not the answer: per-platform AAA / syslog /
accounting-log attribution is UNCERTAIN — different platforms expose
"who changed the config" in different places, if at all — so this module
defines the provider interface and a honest UnknownAttribution default,
and leaves real per-platform attribution for a later milestone.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AttributionProvider(Protocol):
    async def attribute(self, device: str, running_config: str) -> dict[str, str | None]:
        """Return {"actor": str, "at": str | None} for the last change."""
        ...


class UnknownAttribution:
    """Default provider: attribution is unknown, and says so."""

    async def attribute(self, device: str, running_config: str) -> dict[str, str | None]:
        return {"actor": "unknown", "at": None}


def link_to_change(device: str, ticket: str, active_changes: list[Any]) -> bool:
    """True when ``ticket`` is an active change touching ``device``.

    ``active_changes`` are Change-like objects (or dicts) with a
    ticket_number and a devices list. Used by scan storage: a drift that
    appears while the linked change is in progress is attributed to it
    instead of being raised as a new unmanaged record.
    """
    for change in active_changes:
        if isinstance(change, dict):
            change_ticket = str(change.get("ticket_number", ""))
            devices = list(change.get("devices", []) or [])
        else:
            change_ticket = str(getattr(change, "ticket_number", ""))
            devices = list(getattr(change, "devices", []) or [])
        if change_ticket == ticket and device in devices:
            return True
    return False
