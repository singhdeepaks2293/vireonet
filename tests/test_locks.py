from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from vireonet.db import Base
from vireonet.safety import DeviceLockService, LockHeldError


async def _service(tmp_path):  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return DeviceLockService(
        async_sessionmaker(engine, expire_on_commit=False), ttl_seconds=3600
    ), engine


async def test_acquire_release_and_conflict(tmp_path) -> None:
    svc, engine = await _service(tmp_path)
    assert await svc.acquire("leaf-01", "RITM1")
    assert await svc.is_locked("leaf-01") == "RITM1"
    with pytest.raises(LockHeldError):
        await svc.acquire("leaf-01", "RITM2")
    await svc.release("leaf-01", "RITM1")
    assert await svc.is_locked("leaf-01") is None
    await engine.dispose()


async def test_acquire_all_rolls_back_on_conflict(tmp_path) -> None:
    svc, engine = await _service(tmp_path)
    await svc.acquire("leaf-01", "RITM1")
    with pytest.raises(LockHeldError):
        await svc.acquire_all(["leaf-01", "leaf-02"], "RITM2")
    assert await svc.is_locked("leaf-02") is None  # leaf-02 was not left locked
    await engine.dispose()


async def test_expired_lock_is_stealable(tmp_path) -> None:
    svc, engine = await _service(tmp_path)
    svc.ttl_seconds = 0  # expires immediately
    await svc.acquire("leaf-01", "RITM1")
    assert await svc.acquire("leaf-01", "RITM2")  # stolen, no exception
    await engine.dispose()
