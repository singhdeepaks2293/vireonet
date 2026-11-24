"""Stage-1 hardening: auth on ZTP/health/metrics, webhook HMAC, login lockout,
lock renewal, the per-wave health gate and the apply-time window recheck."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from vireonet.config import Settings
from vireonet.db import Base
from vireonet.execution.fake_engine import FakeExecutionEngine
from vireonet.execution.fake_network import FakeNetwork
from vireonet.main import create_app
from vireonet.models import DeviceLock
from vireonet.policy import PolicyEngine
from vireonet.safety import DeviceLockService
from vireonet.temporal.activities import ApplyInput, CheckInput, WindowCheckInput

from .test_automation_window import INTENT, _acts

MEM = "sqlite+aiosqlite:///:memory:"


# --- API auth ---------------------------------------------------------------------------


def test_health_endpoints_need_a_login() -> None:
    app = create_app(Settings(adapter_mode="fake", database_url=MEM))
    c = TestClient(app)
    assert c.get("/adapters/health").status_code == 401
    assert c.get("/system/health").status_code == 401
    assert c.get("/healthz").status_code == 200  # liveness stays open for probes


def test_metrics_token() -> None:
    open_app = TestClient(create_app(Settings(adapter_mode="fake", database_url=MEM)))
    assert open_app.get("/metrics").status_code == 200
    c = TestClient(create_app(Settings(adapter_mode="fake", database_url=MEM, metrics_token="m1")))
    assert c.get("/metrics").status_code == 401
    assert c.get("/metrics", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.get("/metrics", headers={"Authorization": "Bearer m1"}).status_code == 200


def test_ztp_registration_is_never_anonymous() -> None:
    app = create_app(Settings(adapter_mode="fake", database_url=MEM, ztp_registration_token="z1"))
    c = TestClient(app)
    body = {"serial": "FOC1", "hostname": "leaf-99", "site": "dc1", "platform": "ios"}
    assert c.post("/ztp/devices", json=body).status_code == 401
    assert (
        c.post("/ztp/devices", json=body, headers={"X-VireoNet-ZTP-Token": "bad"}).status_code
        == 401
    )
    ok = c.post("/ztp/devices", json=body, headers={"X-VireoNet-ZTP-Token": "z1"})
    assert ok.status_code != 401, ok.text
    assert c.get("/ztp/devices/FOC1").status_code == 401


def test_hmac_with_an_empty_secret_is_refused() -> None:
    # Only an api_token configured: an attacker could sign with the empty key.
    app = create_app(Settings(adapter_mode="fake", database_url=MEM, api_token="tok"))
    c = TestClient(app)
    body = b'{"number": "RITM0005001"}'
    forged = hmac.new(b"", body, hashlib.sha256).hexdigest()
    r = c.post(
        "/webhooks/servicenow",
        content=body,
        headers={"X-VireoNet-Signature": f"sha256={forged}", "Content-Type": "application/json"},
    )
    assert r.status_code == 401 and "WEBHOOK_SECRET" in r.json()["detail"]
    wrong = c.post(
        "/webhooks/servicenow",
        content=body,
        headers={"Authorization": "Bearer x", "Content-Type": "application/json"},
    )
    assert wrong.status_code == 401


def test_login_lockout_after_repeated_failures(tmp_path) -> None:
    import asyncio

    from vireonet.api import auth

    url = f"sqlite+aiosqlite:///{tmp_path}/u.db"

    async def init() -> None:
        e = create_async_engine(url)
        async with e.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await e.dispose()

    asyncio.run(init())
    auth._FAILURES.clear()
    c = TestClient(
        create_app(Settings(adapter_mode="fake", database_url=url, login_max_failures=3, dev=True))
    )
    codes = [
        c.post("/auth/login", json={"username": "Mallory", "password": "x"}).status_code
        for _ in range(4)
    ]
    assert codes[:3] != [429, 429, 429] and codes[3] == 429
    # Per username, case-insensitive; other users are unaffected.
    assert c.post("/auth/login", json={"username": "mallory", "password": "x"}).status_code == 429
    assert c.post("/auth/login", json={"username": "bob", "password": "x"}).status_code != 429
    auth._FAILURES.clear()


# --- Locks ------------------------------------------------------------------------------------


async def test_renew_all_only_touches_this_tickets_locks(tmp_path) -> None:
    db = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/l.db")
    async with db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(db, expire_on_commit=False)
    locks = DeviceLockService(factory, ttl_seconds=600)
    await locks.acquire("leaf-01", "CHG1")
    await locks.acquire("leaf-02", "CHG2")
    async with factory() as s:
        for row in (await s.execute(DeviceLock.__table__.select())).fetchall():
            await s.execute(
                DeviceLock.__table__.update()
                .where(DeviceLock.device == row.device)
                .values(expires_at=datetime.now(UTC) + timedelta(seconds=5))
            )
        await s.commit()
    assert await locks.renew_all(["leaf-01", "leaf-02"], "CHG1") == 1
    async with factory() as s:
        rows = {
            r.device: r.expires_at.replace(tzinfo=UTC)
            if r.expires_at.tzinfo is None
            else r.expires_at
            for r in (await s.execute(DeviceLock.__table__.select())).fetchall()
        }
    now = datetime.now(UTC)
    assert rows["leaf-01"] > now + timedelta(seconds=500)
    assert rows["leaf-02"] < now + timedelta(seconds=10)
    await db.dispose()


# --- Wave health gate -------------------------------------------------------------------------


async def test_failed_wave_health_stops_the_rollout(tmp_path) -> None:
    engine = FakeExecutionEngine(network=FakeNetwork())
    acts, cid, _f, db = await _acts(tmp_path, engine)
    await acts.pre_check_change(CheckInput(change_id=cid, intent=INTENT, kind="pre"))

    async def unhealthy(intent, wave):  # type: ignore[no-untyped-def]
        return {"wave": list(wave), "ok": False, "failures": ["vlan_active: VLAN 310 missing"]}

    acts._wave_health = unhealthy  # type: ignore[method-assign]
    res = await acts.apply_change(ApplyInput(change_id=cid, intent=INTENT))
    assert res["ok"] is False
    assert "wave health check failed" in res["failures"][0]
    assert res["evidence"]["wave_health"][0]["ok"] is False
    await db.dispose()


async def test_healthy_wave_is_recorded_in_evidence(tmp_path) -> None:
    engine = FakeExecutionEngine(network=FakeNetwork())
    acts, cid, _f, db = await _acts(tmp_path, engine)
    await acts.pre_check_change(CheckInput(change_id=cid, intent=INTENT, kind="pre"))
    res = await acts.apply_change(ApplyInput(change_id=cid, intent=INTENT))
    assert res["ok"] is True, res
    assert all(w["ok"] for w in res["evidence"]["wave_health"])
    await db.dispose()


# --- Apply-time policy window -----------------------------------------------------------------


async def test_policy_window_check(tmp_path) -> None:
    engine = FakeExecutionEngine(network=FakeNetwork())
    acts, cid, _f, db = await _acts(tmp_path, engine)
    now = datetime.now(UTC)
    acts._policy = PolicyEngine(
        change_window_days={(now + timedelta(days=1)).strftime("%a")},  # tomorrow only
        change_window_start="00:00",
        change_window_end="23:59",
        change_window_tz="UTC",
    )
    shut = await acts.policy_window_check(WindowCheckInput(change_id=cid, intent=INTENT))
    assert shut["ok"] is False and "outside change window" in shut["reason"]
    emergency = await acts.policy_window_check(
        WindowCheckInput(change_id=cid, intent=INTENT, emergency=True)
    )
    assert emergency["ok"] is True
    acts._policy = PolicyEngine.default()
    assert (await acts.policy_window_check(WindowCheckInput(change_id=cid, intent=INTENT)))["ok"]
    await db.dispose()


async def test_workflow_aborts_when_approval_lands_outside_the_window(tmp_path) -> None:
    from .test_automation_window import _reasons, _run

    engine = FakeExecutionEngine(network=FakeNetwork())
    acts, cid, factory, db = await _acts(tmp_path, engine)
    # Inside the window at validation, outside by the time apply would start.
    acts._policy.window_denial = lambda now=None: "outside change window: Sat not allowed"  # type: ignore[method-assign]
    result = await _run(acts, cid, skip_ci=True, pre_approved=True)
    assert result == "rejected"
    reason = (await _reasons(factory, cid))["rejected"]
    assert "outside change window" in reason and "nothing was pushed" in reason
    assert not [c for c in engine.calls if c.step == "apply"]
    await db.dispose()


def test_manual_images_are_served_with_strict_names() -> None:
    c = TestClient(create_app(Settings(adapter_mode="fake", database_url=MEM)))
    ok = c.get("/help/images/flows-run.png")
    assert ok.status_code == 200 and ok.headers["content-type"] == "image/png"
    for bad in ("../USER_MANUAL.md", "x.svg", "..%2Fsecrets.png", "Flows.png"):
        assert c.get(f"/help/images/{bad}").status_code == 404
