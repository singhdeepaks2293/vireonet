"""Jira Software / Jira Service Management (REST API v2).

Cloud: basic auth with account email + API token. Data Center / Server:
a personal access token (Bearer). Closing a ticket runs the workflow
transition named by `close_transition` (default "Done").
"""

from __future__ import annotations

from typing import Any

from vireonet.itsm.base import Attachment, HttpItsm, ItsmError, Ticket


class JiraItsm(HttpItsm):
    name = "jira"

    def __init__(
        self,
        base_url: str,
        *,
        email: str = "",
        api_token: str = "",
        pat: str = "",
        close_transition: str = "Done",
        planned_start_field: str = "",
        planned_end_field: str = "",
        **kw: Any,
    ) -> None:
        # Jira has no standard change window: name the custom fields
        # (e.g. Jira Service Management "Planned start" = customfield_10042).
        self.planned_fields = (planned_start_field, planned_end_field)
        if pat:
            super().__init__(base_url, headers={"Authorization": f"Bearer {pat}"}, **kw)
        elif email and api_token:
            super().__init__(base_url, auth=(email, api_token), **kw)
        else:
            raise ItsmError("jira: set an email + API token (Cloud) or a personal access token")
        self.close_transition = close_transition

    async def health(self) -> dict[str, Any]:
        me = await self._send("GET", "/rest/api/2/myself")
        return {"backend": self.name, "status": "ok", "detail": me.get("displayName", "")}

    async def _issue(self, key: str) -> dict[str, Any]:
        return dict(
            await self._send(
                "GET",
                f"/rest/api/2/issue/{key}",
                params={
                    "fields": ",".join(
                        ["summary", "status", "reporter", "attachment"]
                        + [f for f in self.planned_fields if f]
                    )
                },
            )
        )

    async def get_ticket(self, key: str) -> Ticket:
        f = (await self._issue(key)).get("fields", {})
        return Ticket(
            key=key,
            summary=str(f.get("summary", "")),
            status=str((f.get("status") or {}).get("name", "")),
            requester=str((f.get("reporter") or {}).get("displayName", "")),
            fields={
                "planned_start": str(f.get(self.planned_fields[0]) or "")
                if self.planned_fields[0]
                else "",
                "planned_end": str(f.get(self.planned_fields[1]) or "")
                if self.planned_fields[1]
                else "",
            },
        )

    async def list_attachments(self, key: str) -> list[Attachment]:
        f = (await self._issue(key)).get("fields", {})
        return [
            Attachment(
                str(a["id"]),
                str(a.get("filename", "")),
                int(a.get("size") or 0),
                str(a.get("mimeType", "")),
                str(a.get("content", "")),
            )
            for a in f.get("attachment") or []
        ]

    async def download_attachment(self, key: str, attachment: Attachment) -> bytes:
        # The attachment's "content" URL works on Jira Cloud and Data Center
        # (/rest/api/2/attachment/content/{id} exists on Cloud only).
        url = attachment.url
        if not url:
            meta = await self._send("GET", f"/rest/api/2/attachment/{attachment.id}")
            url = str(meta.get("content", ""))
        if not url:
            raise ItsmError(f"{self.name}: attachment {attachment.id} has no content URL")
        return bytes(await self._send("GET", url, raw=True, follow_redirects=True))

    async def attach_file(self, key: str, filename: str, content: bytes) -> str:
        data = await self._send(
            "POST",
            f"/rest/api/2/issue/{key}/attachments",
            headers={"X-Atlassian-Token": "no-check"},
            files={"file": (filename, content)},
        )
        return str(data[0]["id"]) if isinstance(data, list) and data else ""

    async def add_comment(self, key: str, text: str) -> None:
        await self._send("POST", f"/rest/api/2/issue/{key}/comment", json={"body": text})

    async def close(self, key: str, note: str) -> None:
        await self.add_comment(key, note)
        transitions = (await self._send("GET", f"/rest/api/2/issue/{key}/transitions")).get(
            "transitions", []
        )
        match = next(
            (
                t
                for t in transitions
                if str(t.get("name", "")).lower() == self.close_transition.lower()
            ),
            None,
        )
        if match is None:
            names = [t.get("name") for t in transitions]
            raise ItsmError(
                f"jira: no transition named {self.close_transition!r} on {key} (have {names})"
            )
        await self._send(
            "POST", f"/rest/api/2/issue/{key}/transitions", json={"transition": {"id": match["id"]}}
        )
