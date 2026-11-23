"""Durable store for running-config versions and per-change diffs (F1)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vireonet.diff import compute_diff, config_hash, config_lines, normalize_config
from vireonet.models import ChangeDiff, DeviceConfigVersion


class ConfigStore:
    """Running configs versioned per device; diffs stored per change+device.

    Versions dedupe by normalized content hash: storing an unchanged config
    returns the existing version row instead of creating a new one.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def _latest(self, session: AsyncSession, device: str) -> DeviceConfigVersion | None:
        return (
            await session.execute(
                select(DeviceConfigVersion)
                .where(DeviceConfigVersion.device == device)
                .order_by(desc(DeviceConfigVersion.version))
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _get(
        self, session: AsyncSession, device: str, version: int
    ) -> DeviceConfigVersion | None:
        return (
            await session.execute(
                select(DeviceConfigVersion).where(
                    DeviceConfigVersion.device == device, DeviceConfigVersion.version == version
                )
            )
        ).scalar_one_or_none()

    async def store_running(
        self,
        device: str,
        content: str,
        ticket_number: str | None = None,
        source: str = "awx-backup",
    ) -> DeviceConfigVersion:
        normalized = normalize_config(content)
        digest = config_hash(normalized)
        async with self._sessions() as session:
            latest = await self._latest(session, device)
            if latest is not None and latest.content_hash == digest:
                return latest
            row = DeviceConfigVersion(
                device=device,
                version=(latest.version + 1) if latest is not None else 1,
                content=normalized,
                content_hash=digest,
                ticket_number=ticket_number,
                source=source,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def latest(self, device: str) -> DeviceConfigVersion | None:
        async with self._sessions() as session:
            return await self._latest(session, device)

    async def get_version(self, device: str, version: int) -> DeviceConfigVersion | None:
        async with self._sessions() as session:
            return await self._get(session, device, version)

    async def history(self, device: str, limit: int = 50) -> list[dict[str, Any]]:
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(DeviceConfigVersion)
                        .where(DeviceConfigVersion.device == device)
                        .order_by(desc(DeviceConfigVersion.version))
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            return [
                {
                    "device": r.device,
                    "version": r.version,
                    "content_hash": r.content_hash,
                    "ticket_number": r.ticket_number,
                    "source": r.source,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "lines": len(config_lines(r.content)),
                }
                for r in rows
            ]

    async def compare_versions(self, device: str, v_from: int, v_to: int) -> dict[str, Any]:
        async with self._sessions() as session:
            from_row = await self._get(session, device, v_from)
            to_row = await self._get(session, device, v_to)
        if from_row is None:
            raise ValueError(f"no config version {v_from} for {device}")
        if to_row is None:
            raise ValueError(f"no config version {v_to} for {device}")
        return compute_diff(device, from_row.content, to_row.content)

    @staticmethod
    def _coerce_id(change_id: uuid.UUID | str) -> uuid.UUID:
        return change_id if isinstance(change_id, uuid.UUID) else uuid.UUID(str(change_id))

    async def store_change_diff(
        self, change_id: uuid.UUID | str, device: str, running: str, candidate: str
    ) -> ChangeDiff:
        """Idempotent per change+device: a second store returns the existing row."""
        diff = compute_diff(device, running, candidate)
        pk = self._coerce_id(change_id)
        async with self._sessions() as session:
            existing = (
                await session.execute(
                    select(ChangeDiff).where(
                        ChangeDiff.change_id == pk, ChangeDiff.device == device
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing
            latest = await self._latest(session, device)
            running_version_id = (
                latest.id
                if latest is not None and latest.content_hash == diff["running_hash"]
                else None
            )
            row = ChangeDiff(
                change_id=pk,
                device=device,
                running_version_id=running_version_id,
                candidate_hash=str(diff["candidate_hash"]),
                diff_text="\n".join(str(line) for line in diff["unified"]),
                diff_lines=list(diff["lines"]),
                added=int(diff["added"]),
                removed=int(diff["removed"]),
                is_empty=bool(diff["is_empty"]),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def baseline_matches(
        self, change_id: uuid.UUID | str, device: str, running_version_id: uuid.UUID
    ) -> bool:
        """True only when the stored approval diff references this exact running version."""
        pk = self._coerce_id(change_id)
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(ChangeDiff).where(
                        ChangeDiff.change_id == pk, ChangeDiff.device == device
                    )
                )
            ).scalar_one_or_none()
            return row is not None and row.running_version_id == running_version_id

    async def diffs_for_change(self, change_id: uuid.UUID | str) -> list[ChangeDiff]:
        pk = self._coerce_id(change_id)
        async with self._sessions() as session:
            return list(
                (
                    await session.execute(
                        select(ChangeDiff)
                        .where(ChangeDiff.change_id == pk)
                        .order_by(ChangeDiff.device)
                    )
                )
                .scalars()
                .all()
            )
