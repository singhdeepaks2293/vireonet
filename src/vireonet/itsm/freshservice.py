"""Freshservice (API v2, basic auth with the API key as username)."""

from __future__ import annotations

from typing import Any

import httpx

from vireonet.itsm.base import Attachment, HttpItsm, ItsmError, Ticket

_STATUS = {2: "open", 3: "pending", 4: "resolved", 5: "closed"}


class FreshserviceItsm(HttpItsm):
    name = "freshservice"

    def __init__(self, base_url: str, *, api_key: str, **kw: Any) -> None:
        super().__init__(base_url, auth=(api_key, "X"), **kw)

    @staticmethod
    def _id(key: str) -> str:
        """'INC-42', '#42' or '42' -> '42'."""
        return "".join(ch for ch in key if ch.isdigit()) or key

    async def health(self) -> dict[str, Any]:
        # /api/v2/agents/me is Freshdesk-only; listing one ticket checks the key.
        await self._send("GET", "/api/v2/tickets", params={"per_page": 1})
        return {"backend": self.name, "status": "ok"}

    async def _ticket(self, key: str) -> dict[str, Any]:
        return dict((await self._send("GET", f"/api/v2/tickets/{self._id(key)}")).get("ticket", {}))

    async def get_ticket(self, key: str) -> Ticket:
        t = await self._ticket(key)
        return Ticket(
            key,
            str(t.get("subject", "")),
            _STATUS.get(int(t.get("status") or 0), str(t.get("status"))),
            str(t.get("requester_id", "")),
            # planned_start_date / planned_end_date are standard fields on
            # Freshservice Changes, not on tickets; on a ticket they are only
            # present when the instance defines them, otherwise "".
            {
                "planned_start": str(t.get("planned_start_date") or ""),
                "planned_end": str(t.get("planned_end_date") or ""),
            },
        )

    async def list_attachments(self, key: str) -> list[Attachment]:
        return [
            Attachment(
                str(a["id"]),
                str(a.get("name", "")),
                int(a.get("size") or 0),
                str(a.get("content_type", "")),
                str(a.get("attachment_url") or ""),
            )
            for a in (await self._ticket(key)).get("attachments") or []
        ]

    async def download_attachment(self, key: str, attachment: Attachment) -> bytes:
        # attachment_url is a pre-signed (S3) URL: fetch it without the API key
        # (an extra Authorization header makes the signed request fail).
        if not attachment.url:
            raise ItsmError(
                f"{self.name}: attachment {attachment.id} has no attachment_url "
                "(list the ticket's attachments first)"
            )
        kw = {k: v for k, v in self._kw.items() if k not in ("auth", "headers")}
        try:
            async with httpx.AsyncClient(follow_redirects=True, **kw) as client:
                resp = await client.get(attachment.url)
        except httpx.HTTPError as exc:
            raise ItsmError(f"{self.name}: {exc.__class__.__name__}: {exc}") from exc
        if resp.status_code >= 400:
            raise ItsmError(
                f"{self.name}: attachment {attachment.id} download failed "
                f"(HTTP {resp.status_code}; the pre-signed URL may have expired)"
            )
        return bytes(resp.content)

    async def attach_file(self, key: str, filename: str, content: bytes) -> str:
        data = await self._send(
            "PUT", f"/api/v2/tickets/{self._id(key)}", files={"attachments[]": (filename, content)}
        )
        atts = (data.get("ticket") or {}).get("attachments") or []
        return str(atts[-1]["id"]) if atts else ""

    async def add_comment(self, key: str, text: str) -> None:
        await self._send(
            "POST", f"/api/v2/tickets/{self._id(key)}/notes", json={"body": text, "private": True}
        )

    async def close(self, key: str, note: str) -> None:
        await self.add_comment(key, note)
        await self._send("PUT", f"/api/v2/tickets/{self._id(key)}", json={"status": 5})
