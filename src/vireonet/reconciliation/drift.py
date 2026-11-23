"""Drift record store (E3, extended D1/D3).

Create-only in E3; D1/D3 add the queries the scan pipeline needs:
dedup lookup, last_seen touch, scan-run rows, and the active-change
check that suppresses a new unmanaged record while a change is in
progress on the device.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vireonet.change_state import ChangeState
from vireonet.drift.models import DriftScan
from vireonet.models import Change, DriftRecord

# A drift found while a change in one of these states touches the device
# is linked to that change's ticket and does NOT become a new unmanaged
# record until the change completes (D3 attribution).
ACTIVE_CHANGE_STATES: frozenset[str] = frozenset(
    {
        ChangeState.RECEIVED.value,
        ChangeState.VALIDATED.value,
        ChangeState.PLANNED.value,
        ChangeState.CI_PASSED.value,
        ChangeState.APPROVED.value,
        ChangeState.PRE_CHECKED.value,
        ChangeState.APPLIED.value,
        ChangeState.POST_CHECKED.value,
    }
)


class DriftStore:
    """Persistence for drift records and scan runs."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def create(
        self,
        device: str,
        kind: str,
        severity: str,
        diff: str,
        baseline_hash: str | None,
        running_hash: str | None,
        linked_ticket: str | None,
        *,
        status: str = "detected",
        baseline_ref: str | None = None,
        attributed_actor: str | None = None,
        attributed_at: datetime | None = None,
    ) -> str:
        async with self._sessions() as session:
            row = DriftRecord(
                device=device,
                kind=kind,
                severity=severity,
                status=status,
                diff=diff,
                baseline_hash=baseline_hash,
                running_hash=running_hash,
                linked_ticket=linked_ticket,
                baseline_ref=baseline_ref,
                attributed_actor=attributed_actor,
                attributed_at=attributed_at,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return str(row.id)

    async def find_existing(
        self, device: str, kind: str, running_hash: str | None
    ) -> DriftRecord | None:
        """An open record for the same device/kind/running hash, if any."""
        async with self._sessions() as session:
            return (
                await session.execute(
                    select(DriftRecord).where(
                        DriftRecord.device == device,
                        DriftRecord.kind == kind,
                        DriftRecord.running_hash == running_hash,
                        DriftRecord.status != "resolved",
                    )
                )
            ).scalar_one_or_none()

    async def touch(self, record_id: str) -> None:
        """Bump last_seen on a repeat finding (dedup: no new record)."""
        import uuid

        async with self._sessions() as session:
            row = await session.get(DriftRecord, uuid.UUID(str(record_id)))
            if row is not None:
                row.last_seen = datetime.now(UTC)
                await session.commit()

    async def find_active_change(self, device: str) -> str | None:
        """Ticket of an in-progress change on this device, if any."""
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(Change).where(Change.state.in_(sorted(ACTIVE_CHANGE_STATES)))
                    )
                )
                .scalars()
                .all()
            )
            for change in rows:
                if device in list(change.devices or []):
                    return str(change.ticket_number)
            return None

    async def create_scan(self, site: str) -> str:
        async with self._sessions() as session:
            row = DriftScan(site=site, status="running")
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return str(row.id)

    async def finish_scan(
        self,
        scan_id: str,
        *,
        status: str,
        devices_scanned: int,
        devices_with_drift: int,
        devices_failed: int,
    ) -> None:
        import uuid

        async with self._sessions() as session:
            row = await session.get(DriftScan, uuid.UUID(str(scan_id)))
            if row is None:
                return
            row.status = status
            row.finished_at = datetime.now(UTC)
            row.devices_scanned = devices_scanned
            row.devices_with_drift = devices_with_drift
            row.devices_failed = devices_failed
            await session.commit()

    async def get_scan(self, scan_id: str) -> DriftScan | None:
        import uuid

        async with self._sessions() as session:
            return await session.get(DriftScan, uuid.UUID(str(scan_id)))

    async def list_records(
        self, *, device: str | None = None, status: str | None = None, limit: int = 100
    ) -> list[DriftRecord]:
        async with self._sessions() as session:
            query = select(DriftRecord).order_by(DriftRecord.last_seen.desc()).limit(limit)
            if device:
                query = query.where(DriftRecord.device == device)
            if status:
                query = query.where(DriftRecord.status == status)
            return list((await session.execute(query)).scalars().all())

    async def get_record(self, record_id: str) -> DriftRecord | None:
        import uuid

        async with self._sessions() as session:
            return await session.get(DriftRecord, uuid.UUID(str(record_id)))

    async def record_decision(
        self,
        record_id: str,
        *,
        action: str,
        actor: str,
        reason: str = "",
        ticket_ref: str = "",
        expiry: datetime | None = None,
    ) -> str:
        """Append a DriftDecision row (append-only; never edits a record)."""
        import uuid

        from vireonet.drift.models import DriftDecision

        async with self._sessions() as session:
            row = DriftDecision(
                record_id=uuid.UUID(str(record_id)),
                action=action,
                actor=actor,
                reason=reason,
                ticket_ref=ticket_ref,
                expiry=expiry,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return str(row.id)
