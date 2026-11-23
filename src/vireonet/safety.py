from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vireonet.models import DeviceLock


class LockHeldError(ValueError):
    def __init__(self, device: str, holder: str) -> None:
        super().__init__(f"device {device} is locked by {holder}")
        self.device = device
        self.holder = holder


class DeviceLockService:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], *, ttl_seconds: int = 1800
    ) -> None:
        self._sessions = sessions
        self.ttl_seconds = ttl_seconds

    async def acquire(self, device: str, ticket: str, *, holder: str = "temporal") -> bool:
        """Acquire one lock. Expired locks are stolen; live locks raise LockHeldError."""
        now = datetime.now(UTC)
        async with self._sessions() as session:
            existing = (
                await session.execute(select(DeviceLock).where(DeviceLock.device == device))
            ).scalar_one_or_none()
            if existing is not None:
                if existing.expires_at.tzinfo is None:
                    existing_exp = existing.expires_at.replace(tzinfo=UTC)
                else:
                    existing_exp = existing.expires_at
                if existing_exp > now:
                    raise LockHeldError(device, existing.ticket_number)
                await session.delete(existing)
                await session.flush()
            session.add(
                DeviceLock(
                    device=device,
                    ticket_number=ticket,
                    holder=holder,
                    expires_at=now + timedelta(seconds=self.ttl_seconds),
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                raise LockHeldError(device, "another ticket") from None
            return True

    async def acquire_all(self, devices: list[str], ticket: str) -> list[str]:
        acquired: list[str] = []
        try:
            for device in sorted(devices):  # sorted order avoids deadlock between tickets
                await self.acquire(device, ticket)
                acquired.append(device)
        except LockHeldError:
            await self.release_all(acquired, ticket)
            raise
        return acquired

    async def renew_all(self, devices: list[str], ticket: str) -> bool:
        """Extend leases only when every requested lock is still owned by ticket.

        Returns False rather than recreating a missing/expired lock. A caller
        must stop and reconcile an uncertain execution if ownership is lost.
        """
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=self.ttl_seconds)
        async with self._sessions() as session:
            rows = list(
                (
                    await session.execute(
                        select(DeviceLock).where(
                            DeviceLock.device.in_(devices),
                            DeviceLock.ticket_number == ticket,
                        )
                    )
                ).scalars().all()
            )
            if len(rows) != len(set(devices)):
                return False
            for row in rows:
                current = row.expires_at
                current = current.replace(tzinfo=UTC) if current.tzinfo is None else current
                if current <= now:
                    return False
            for row in rows:
                row.expires_at = expires
            await session.commit()
            return True

    async def release(self, device: str, ticket: str) -> None:
        async with self._sessions() as session:
            await session.execute(
                delete(DeviceLock).where(
                    DeviceLock.device == device, DeviceLock.ticket_number == ticket
                )
            )
            await session.commit()

    async def release_any(self, device: str) -> bool:
        """Release a lock by device only (operator action from the UI). Returns True if a lock was held."""
        async with self._sessions() as session:
            row = (
                await session.execute(select(DeviceLock).where(DeviceLock.device == device))
            ).scalar_one_or_none()
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def release_all(self, devices: list[str], ticket: str) -> None:
        for device in devices:
            await self.release(device, ticket)

    async def list(self) -> list[DeviceLock]:
        """All locks, oldest acquired first. Includes expired locks not yet stolen."""
        async with self._sessions() as session:
            return list(
                (await session.execute(select(DeviceLock).order_by(DeviceLock.created_at)))
                .scalars()
                .all()
            )

    async def is_locked(self, device: str) -> str | None:
        async with self._sessions() as session:
            row = (
                await session.execute(select(DeviceLock).where(DeviceLock.device == device))
            ).scalar_one_or_none()
            if row is None:
                return None
            exp = (
                row.expires_at.replace(tzinfo=UTC)
                if row.expires_at.tzinfo is None
                else row.expires_at
            )
            return row.ticket_number if exp > datetime.now(UTC) else None


@dataclass
class WaveResult:
    completed_waves: list[list[str]] = field(default_factory=list)
    failed_devices: list[str] = field(default_factory=list)
    aborted: bool = False


async def run_waves(
    devices: list[str],
    func: Callable[[list[str]], Awaitable[list[str]]],
    *,
    wave_size: int = 2,
    abort_threshold: int = 1,
) -> WaveResult:
    """Run func per wave, sequentially. Abort remaining waves when a wave
    has >= abort_threshold failures. func returns the failed devices in the wave."""
    result = WaveResult()
    for i in range(0, len(devices), wave_size):
        wave = devices[i : i + wave_size]
        failed = await func(wave)
        result.failed_devices.extend(failed)
        if failed:
            if len(failed) >= abort_threshold:
                result.aborted = True
                return result
        else:
            result.completed_waves.append(wave)
    return result
