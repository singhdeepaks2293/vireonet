"""BMC Helix ITSM / Remedy (AR System REST API, JWT login).

Incidents live in form "HPD:Help Desk" keyed by "Incident Number". Up to
three attachments are read from "z2AF Work Log01..03" on the work-info
form "HPD:WorkLog"; new files are added as a work-log entry.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from vireonet.itsm.base import Attachment, HttpItsm, ItsmAuthError, ItsmError, Ticket

_FORM = "HPD:Help Desk"
_WORKLOG = "HPD:WorkLog"


class BmcHelixItsm(HttpItsm):
    name = "bmc_helix"

    def __init__(self, base_url: str, *, username: str, password: str, **kw: Any) -> None:
        super().__init__(base_url, **kw)
        self._creds = (username, password)
        self._jwt = ""

    async def _auth(self) -> dict[str, str]:
        if not self._jwt:
            token = await self._send(
                "POST",
                "/api/jwt/login",
                data={"username": self._creds[0], "password": self._creds[1]},
                raw=True,
            )
            self._jwt = bytes(token).decode().strip()
        return {"Authorization": f"AR-JWT {self._jwt}"}

    async def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        """Authenticated request. The JWT expires after an hour: on a 401 log in
        again and retry once."""
        headers = await self._auth()  # a failed login is not retried
        try:
            return await self._send(method, path, headers=headers, **kwargs)
        except ItsmAuthError as exc:
            if exc.status_code != 401:
                raise
            self._jwt = ""
            return await self._send(method, path, headers=await self._auth(), **kwargs)

    async def _get(self, path: str, **params: Any) -> Any:
        return await self._call("GET", path, params=params or None)

    async def health(self) -> dict[str, Any]:
        await self._auth()
        return {"backend": self.name, "status": "ok"}

    async def get_ticket(self, key: str) -> Ticket:
        data = await self._get(
            f"/api/arsys/v1/entry/{quote(_FORM)}", q=f"'Incident Number'=\"{key}\""
        )
        entries = data.get("entries") or []
        if not entries:
            raise ItsmError(f"bmc_helix: incident {key} not found")
        v = entries[0].get("values", {})
        return Ticket(
            key,
            str(v.get("Description", "")),
            str(v.get("Status", "")),
            str(v.get("Submitter", "")),
            {
                "planned_start": str(v.get("Scheduled Start Date") or ""),
                "planned_end": str(v.get("Scheduled End Date") or ""),
            },
        )

    async def _worklogs(self, key: str) -> list[dict[str, Any]]:
        data = await self._get(
            f"/api/arsys/v1/entry/{quote(_WORKLOG)}", q=f"'Incident Number'=\"{key}\""
        )
        return list(data.get("entries") or [])

    async def list_attachments(self, key: str) -> list[Attachment]:
        out = []
        for entry in await self._worklogs(key):
            v = entry.get("values", {})
            for n in (1, 2, 3):
                field = f"z2AF Work Log0{n}"
                meta = v.get(field)
                if isinstance(meta, dict) and meta.get("name"):
                    out.append(
                        Attachment(
                            f"{v.get('Work Log ID')}|{field}",
                            str(meta["name"]),
                            int(meta.get("sizeBytes") or 0),
                        )
                    )
        return out

    async def download_attachment(self, key: str, attachment: Attachment) -> bytes:
        entry_id, field = attachment.id.split("|", 1)
        return bytes(
            await self._call(
                "GET",
                f"/api/arsys/v1/entry/{quote(_WORKLOG)}/{entry_id}/attach/{quote(field)}",
                raw=True,
            )
        )

    async def attach_file(self, key: str, filename: str, content: bytes) -> str:
        import json

        entry = {
            "values": {
                "Incident Number": key,
                "Description": f"VireoNet: {filename}",
                "Work Log Type": "General Information",
                "z2AF Work Log01": filename,
            }
        }
        await self._call(
            "POST",
            f"/api/arsys/v1/entry/{quote(_WORKLOG)}",
            files={
                "entry": (None, json.dumps(entry), "application/json"),
                "attach-z2AF Work Log01": (filename, content, "application/octet-stream"),
            },
        )
        return filename

    async def add_comment(self, key: str, text: str) -> None:
        await self._call(
            "POST",
            f"/api/arsys/v1/entry/{quote(_WORKLOG)}",
            json={
                "values": {
                    "Incident Number": key,
                    "Description": text[:100],
                    "Detailed Description": text,
                    "Work Log Type": "General Information",
                }
            },
        )

    async def close(self, key: str, note: str) -> None:
        await self.add_comment(key, note)
        data = await self._get(
            f"/api/arsys/v1/entry/{quote(_FORM)}", q=f"'Incident Number'=\"{key}\""
        )
        entries = data.get("entries") or []
        if not entries:
            raise ItsmError(f"bmc_helix: incident {key} not found")
        entry_id = (
            entries[0].get("values", {}).get("Entry ID")
            or entries[0].get("_links", {}).get("self", [{}])[0].get("href", "").rsplit("/", 1)[-1]
        )
        await self._call(
            "PUT",
            f"/api/arsys/v1/entry/{quote(_FORM)}/{entry_id}",
            json={
                "values": {
                    "Status": "Resolved",
                    "Resolution": note,
                    "Status_Reason": "No Further Action Required",
                }
            },
        )
