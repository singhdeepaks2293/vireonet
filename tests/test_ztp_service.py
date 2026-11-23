from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from vireonet.adapters.fakes import FakeNetBoxAdapter
from vireonet.db import Base
from vireonet.ztp.service import ZtpService


async def _service(tmp_path):  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return ZtpService(factory, netbox=FakeNetBoxAdapter(), token_ttl_seconds=900), engine


async def test_register_is_idempotent_by_serial(tmp_path) -> None:
    svc, engine = await _service(tmp_path)
    d1, created1 = await svc.register_device(
        hostname="leaf-01",
        serial="FOC1234A",
        mac_address="aa:bb:cc:dd:ee:ff",
        model="C9300",
        platform="ios-xe",
        site="dc1",
    )
    d2, created2 = await svc.register_device(
        hostname="leaf-01",
        serial="FOC1234A",
        mac_address="aa:bb:cc:dd:ee:ff",
        model="C9300",
        platform="ios-xe",
        site="dc1",
    )
    assert created1 and not created2 and d1.id == d2.id
    await engine.dispose()


async def test_token_single_use(tmp_path) -> None:
    svc, engine = await _service(tmp_path)
    await svc.register_device(
        hostname="leaf-01",
        serial="FOC1234A",
        mac_address="aa:bb:cc:dd:ee:ff",
        model="C9300",
        platform="ios-xe",
        site="dc1",
    )
    token = await svc.issue_token("FOC1234A")
    assert await svc.verify_token("FOC1234A", token) is True
    assert await svc.verify_token("FOC1234A", token) is False  # second use fails
    assert await svc.verify_token("FOC1234A", "wrong") is False
    await engine.dispose()


async def test_identity_pass_and_fail(tmp_path) -> None:
    svc, engine = await _service(tmp_path)
    await svc.register_device(
        hostname="leaf-01",
        serial="FOC1234A",
        mac_address="aa:bb:cc:dd:ee:ff",
        model="C9300-48P",
        platform="ios-xe",
        site="dc1",
    )
    # Fake NetBox leaf-01 is C9300-48P / FOC1234A, so this passes.
    ok, errors = await svc.verify_identity(serial="FOC1234A", model="c9300-48p", platform="ios-xe")
    assert ok, errors
    ok2, errors2 = await svc.verify_identity(serial="FOC1234A", model="C9500", platform="ios-xe")
    assert not ok2 and any("model" in e.lower() for e in errors2)
    await engine.dispose()
