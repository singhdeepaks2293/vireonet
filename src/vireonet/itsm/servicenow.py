"""ServiceNow, over the existing ServiceNow adapter (OAuth or basic auth)."""

from __future__ import annotations

from typing import Any

from vireonet.itsm.base import Attachment, ItsmError, Ticket


class ServiceNowItsm:
    name = "servicenow"

    def __init__(self, adapter: Any) -> None:
        self._sn = adapter

    async def health(self) -> dict[str, Any]:
        h = await self._sn.health_check()
        return {"backend": self.name, "status": h.status, "detail": getattr(h, "detail", "")}

    async def get_ticket(self, key: str) -> Ticket:
        try:
            r = await self._sn.get_request(key)
        except Exception as exc:  # noqa: BLE001 — adapter errors become ITSM errors
            raise ItsmError(f"servicenow: {exc}") from exc
        fields = {
            **dict(r.variables),
            "state_value": r.state,
            "planned_start": r.planned_start,
            "planned_end": r.planned_end,
        }
        return Ticket(
            r.ticket, r.short_description, r.state_display or r.state, r.requested_by, fields
        )

    async def list_attachments(self, key: str) -> list[Attachment]:
        rows = await self._sn.list_attachments(key)
        return [
            Attachment(r["id"], r["filename"], r.get("size", 0), r.get("content_type", ""))
            for r in rows
        ]

    async def download_attachment(self, key: str, attachment: Attachment) -> bytes:
        return bytes(await self._sn.download_attachment(attachment.id))

    async def attach_file(self, key: str, filename: str, content: bytes) -> str:
        return str(await self._sn.attach_file(key, filename, content))

    async def add_comment(self, key: str, text: str) -> None:
        await self._sn.add_work_note(key, text, idempotency_key=f"{key}:{hash(text)}")

    async def close(self, key: str, note: str) -> None:
        await self._sn.close(key, note)
