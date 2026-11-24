"""In-memory ITSM for fake/demo mode. Seeded with demo tickets, one of
which carries an Excel config workbook so the config push can be tried
end to end without a real ITSM tool."""

from __future__ import annotations

import base64
import itertools
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from vireonet.excel_push.window import iso
from vireonet.itsm.base import XLSX_TYPE, Attachment, ItsmError, Ticket


@dataclass
class _Ticket:
    ticket: Ticket
    attachments: dict[str, tuple[Attachment, bytes]] = field(default_factory=dict)
    comments: list[str] = field(default_factory=list)


class FakeItsm:
    name = "fake"

    def __init__(self, seed_demo: bool = True, *, sessions: Any = None) -> None:
        """`sessions` (an async session factory) makes what VireoNet does to
        tickets (comments, closure, attached workbooks) shared between the API
        and the worker through the demo_state table. Without it, state is
        per process."""
        self.tickets: dict[str, _Ticket] = {}
        self._ids = itertools.count(1)
        self._sessions = sessions
        if seed_demo:
            from vireonet.excel_push.workbook import demo_workbook

            self.add_ticket(
                Ticket(
                    "RITM0005001", "Push VLAN 300 to leaf-01 and leaf-03 (Excel)", "open", "alice"
                )
            )
            self.attach_bytes("RITM0005001", "vlan300-config.xlsx", demo_workbook())
            self.add_ticket(
                Ticket("NET-42", "Jira-style key: interface descriptions", "open", "bob")
            )
            # ServiceNow-style Change Requests in "Implement" (raw -1), for the
            # end-to-end state trigger. Windows are relative to start-up time.
            now = datetime.now(UTC)
            for key, summary, start, end in (
                (
                    "CHG0030001",
                    "Excel push, CAB approved, window open now",
                    now - timedelta(minutes=1),
                    now + timedelta(hours=4),
                ),
                (
                    "CHG0030002",
                    "Excel push, window opens in 3 minutes",
                    now + timedelta(minutes=3),
                    now + timedelta(hours=4),
                ),
                ("CHG0030003", "Excel push, no planned window on the ticket", None, None),
                (
                    "CHG0030004",
                    "Excel push, window already over",
                    now - timedelta(hours=3),
                    now - timedelta(hours=1),
                ),
            ):
                fields = {
                    "state_value": "-1",
                    "site": "dc1",
                    "planned_start": iso(start),
                    "planned_end": iso(end),
                }
                self.add_ticket(Ticket(key, summary, "Implement", "carol", fields))
                self.attach_bytes(key, f"{key.lower()}-config.xlsx", demo_workbook())
            # Controller upgrade demos (stage 3): Arista via CloudVision, Palo Alto via Panorama.
            for key, summary in (
                ("CHG0030006", "OS UPGRADE spine-01 to 4.32.1F"),
                ("CHG0030007", "OS UPGRADE fw-02 to 11.1.2"),
            ):
                self.add_ticket(
                    Ticket(
                        key,
                        summary,
                        "Implement",
                        "carol",
                        {
                            "state_value": "-1",
                            "site": "dc1",
                            "category": "network",
                            "priority": "3",
                        },
                    )
                )
            # Workflow builder demo: no workbook, a flow handles it (ITSM
            # trigger "summary matches OS UPGRADE <device> to <version>").
            self.add_ticket(
                Ticket(
                    "CHG0030005",
                    "OS UPGRADE leaf-01 to 17.9.4",
                    "Implement",
                    "carol",
                    {
                        "state_value": "-1",
                        "site": "dc1",
                        "category": "network",
                        "priority": "3",
                        "planned_start": iso(now - timedelta(minutes=1)),
                        "planned_end": iso(now + timedelta(hours=4)),
                    },
                )
            )

    def add_ticket(self, ticket: Ticket) -> None:
        self.tickets[ticket.key] = _Ticket(ticket)

    def attach_bytes(
        self, key: str, filename: str, content: bytes, *, att_id: str | None = None
    ) -> str:
        t = self._get(key)
        att_id = att_id or f"att-{next(self._ids)}"
        ctype = XLSX_TYPE if filename.endswith(".xlsx") else "application/octet-stream"
        t.attachments[att_id] = (Attachment(att_id, filename, len(content), ctype), content)
        return att_id

    def _get(self, key: str) -> _Ticket:
        if key not in self.tickets:
            raise ItsmError(f"fake: ticket {key} not found")
        return self.tickets[key]

    # --- shared overlay (demo_state table) -------------------------------------------

    async def _load(self, key: str) -> _Ticket:
        t = self._get(key)
        if self._sessions is None:
            return t
        from vireonet.models import DemoState

        async with self._sessions() as session:
            row = await session.get(DemoState, ("fake_itsm", key))
        if row is not None:
            v = row.value or {}
            t.ticket.status = str(v.get("status", t.ticket.status))
            if v.get("state_value") is not None:
                t.ticket.fields["state_value"] = v["state_value"]
            t.comments = list(v.get("comments", []))
            for a in v.get("attachments", []):
                if a["id"] not in t.attachments:
                    content = base64.b64decode(a["b64"])
                    t.attachments[a["id"]] = (
                        Attachment(a["id"], a["filename"], len(content), a.get("content_type", "")),
                        content,
                    )
        return t

    async def _save(self, key: str, t: _Ticket, added: list[str] | None = None) -> None:
        if self._sessions is None:
            return
        from vireonet.models import DemoState

        async with self._sessions() as session:
            row = await session.get(DemoState, ("fake_itsm", key))
            value = dict(row.value) if row is not None else {"attachments": []}
            value["status"] = t.ticket.status
            value["state_value"] = t.ticket.fields.get("state_value")
            value["comments"] = list(t.comments)
            atts = list(value.get("attachments", []))
            for att_id in added or []:
                a, content = t.attachments[att_id]
                atts.append(
                    {
                        "id": att_id,
                        "filename": a.filename,
                        "content_type": a.content_type,
                        "b64": base64.b64encode(content).decode(),
                    }
                )
            value["attachments"] = atts
            if row is None:
                session.add(DemoState(namespace="fake_itsm", key=key, value=value))
            else:
                row.value = value
            await session.commit()

    async def health(self) -> dict[str, Any]:
        shared = " (shared through the database)" if self._sessions is not None else ""
        return {
            "backend": self.name,
            "status": "ok",
            "detail": f"{len(self.tickets)} demo tickets{shared}",
        }

    async def get_ticket(self, key: str) -> Ticket:
        return (await self._load(key)).ticket

    async def comments(self, key: str) -> list[str]:
        return list((await self._load(key)).comments)

    async def list_attachments(self, key: str) -> list[Attachment]:
        return [a for a, _ in (await self._load(key)).attachments.values()]

    async def download_attachment(self, key: str, attachment: Attachment) -> bytes:
        return (await self._load(key)).attachments[attachment.id][1]

    async def attach_file(self, key: str, filename: str, content: bytes) -> str:
        t = await self._load(key)
        # Unique across processes, so API and worker never collide.
        att_id = f"att-{uuid.uuid4().hex[:10]}" if self._sessions is not None else None
        att_id = self.attach_bytes(key, filename, content, att_id=att_id)
        await self._save(key, t, [att_id])
        return att_id

    async def add_comment(self, key: str, text: str) -> None:
        t = await self._load(key)
        t.comments.append(text)
        await self._save(key, t)

    async def close(self, key: str, note: str) -> None:
        t = await self._load(key)
        t.comments.append(note)
        t.ticket.status = "closed"
        if "state_value" in t.ticket.fields:  # ServiceNow raw state: 3 = Closed
            t.ticket.fields["state_value"] = "3"
        await self._save(key, t)
