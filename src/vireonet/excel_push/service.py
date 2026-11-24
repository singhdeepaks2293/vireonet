"""Excel config push: workbook storage, intake (upload or ITSM ticket) and
the pre-check write-back (running configs into the backup sheet, attached
back to the ticket)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vireonet.excel_push.workbook import (
    PushWorkbook,
    WorkbookError,
    now_str,
    parse_workbook,
    write_backups,
)
from vireonet.itsm.base import ItsmError, ItsmProvider
from vireonet.models import ExcelWorkbook


def is_workbook_name(name: str) -> bool:
    return name.lower().endswith((".xlsx", ".xlsm"))


class ExcelPushService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        itsm: ItsmProvider | None = None,
        max_bytes: int = 2_000_000,
        max_device_bytes: int = 200_000,
    ) -> None:
        self._sessions = sessions
        self.itsm = itsm
        self.max_bytes = max_bytes
        self.max_device_bytes = max_device_bytes

    def parse(self, content: bytes) -> PushWorkbook:
        return parse_workbook(
            content, max_bytes=self.max_bytes, max_device_bytes=self.max_device_bytes
        )

    async def store(
        self,
        ticket: str,
        filename: str,
        content: bytes,
        *,
        kind: str = "original",
        source: str = "upload",
    ) -> str:
        async with self._sessions() as s:
            row = ExcelWorkbook(
                ticket_number=ticket, filename=filename, kind=kind, source=source, content=content
            )
            s.add(row)
            await s.commit()
            return str(row.id)

    async def get(self, workbook_id: str) -> ExcelWorkbook | None:
        async with self._sessions() as s:
            return await s.get(ExcelWorkbook, uuid.UUID(workbook_id))

    async def latest(self, ticket: str) -> ExcelWorkbook | None:
        """The updated workbook when one exists, else the original."""
        async with self._sessions() as s:
            rows = (
                (
                    await s.execute(
                        select(ExcelWorkbook).where(ExcelWorkbook.ticket_number == ticket)
                    )
                )
                .scalars()
                .all()
            )
        if not rows:
            return None
        rows = sorted(rows, key=lambda r: (r.kind == "updated", r.created_at or 0))
        return rows[-1]

    async def fetch_from_ticket(self, ticket: str) -> tuple[str, bytes, list[str]]:
        """(filename, content, notes): the workbook attached to the ticket.

        Picks the newest .xlsx/.xlsm attachment that has a "config" sheet.
        """
        if self.itsm is None:
            raise ItsmError("no ITSM provider configured (VIREONET_ITSM_PROVIDER)")
        await self.itsm.get_ticket(ticket)
        atts = [a for a in await self.itsm.list_attachments(ticket) if is_workbook_name(a.filename)]
        if not atts:
            raise WorkbookError([f"ticket {ticket} has no .xlsx attachment"])
        notes: list[str] = []
        for att in reversed(atts):  # newest last in most tools
            content = await self.itsm.download_attachment(ticket, att)
            try:
                self.parse(content)
            except WorkbookError as exc:
                notes.append(f"{att.filename}: skipped ({exc.errors[0]})")
                continue
            return att.filename, content, notes
        raise WorkbookError([f"no usable workbook on {ticket}"] + notes)

    async def write_back(
        self, ticket: str, workbook_id: str, running: dict[str, str]
    ) -> dict[str, Any]:
        """Fill the backup sheet with pre-change running configs, store the
        updated copy, and attach it to the ticket (best effort)."""
        original = await self.get(workbook_id)
        if original is None:
            return {"ok": False, "error": f"workbook {workbook_id} not found"}
        taken = now_str()
        updated = write_backups(original.content, {d: (cfg, taken) for d, cfg in running.items()})
        name = original.filename.rsplit(".", 1)[0] + "-with-backup.xlsx"
        updated_id = await self.store(ticket, name, updated, kind="updated", source=original.source)
        result: dict[str, Any] = {"ok": True, "updated_workbook_id": updated_id, "filename": name}
        if self.itsm is not None:
            try:
                result["itsm_attachment"] = await self.itsm.attach_file(ticket, name, updated)
                await self.itsm.add_comment(
                    ticket,
                    f"VireoNet: pre-change running config of {len(running)} device(s) recorded in {name} (sheet 'backup').",
                )
            except Exception as exc:  # noqa: BLE001 — the push continues; say why it was not attached
                result["itsm_error"] = str(exc)
        return result
