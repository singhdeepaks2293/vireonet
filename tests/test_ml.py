"""ML tab: algorithms checked against known ground truth, data sources, API."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from vireonet.api.security import CurrentUser, get_current_user
from vireonet.config import Settings
from vireonet.db import Base
from vireonet.main import create_app
from vireonet.ml.datasets import MIN_REAL_CHANGES, load_changes, load_traffic, synthetic_changes
from vireonet.ml.rl import DEVICE_BUCKETS, RISK_BUCKETS, recommend_wave, train_wave_policy
from vireonet.ml.supervised import (
    REGRESSORS,
    classify_change_risk,
    regress_change_duration,
    regress_traffic,
)
from vireonet.ml.unsupervised import cluster_interfaces, detect_anomalies
from vireonet.models import AuditLog, Change
from vireonet.state_machine import ChangeState
from vireonet.telemetry.fake import FakeTelemetry
from vireonet.topology.demo import DEMO_LINKS, demo_interfaces

INTERFACES = sorted(
    {(a, ai) for a, ai, _, _ in DEMO_LINKS} | {(b, bi) for _, _, b, bi in DEMO_LINKS}
)


def _series(now: datetime, hours: int = 24):  # type: ignore[no-untyped-def]
    fake = FakeTelemetry(interfaces_for=demo_interfaces, now=lambda: now)
    series, _ = asyncio.run(load_traffic(fake, INTERFACES, minutes=hours * 60, step_seconds=600))
    truth = {
        (s.device, s.interface) for s in series if fake.profile(s.device, s.interface)["anomalous"]
    }
    return series, truth


@pytest.mark.parametrize("algorithm", ["isolation_forest", "zscore"])
@pytest.mark.parametrize("offset_h", [0, 7, 19, 40])
def test_anomaly_detection_finds_injected_bursts_without_false_alarms(
    algorithm: str, offset_h: int
) -> None:
    series, truth = _series(datetime(2026, 11, 23, 16, 40, tzinfo=UTC) + timedelta(hours=offset_h))
    result = detect_anomalies(series, algorithm)  # type: ignore[arg-type]
    flagged = {(r["device"], r["interface"]) for r in result["results"] if r["anomalies"]}
    assert truth and flagged == truth


def test_kmeans_separates_busy_and_quiet_interfaces() -> None:
    series, _ = _series(datetime(2026, 11, 23, 12, tzinfo=UTC))
    result = cluster_interfaces(series)
    assert 2 <= result["k"] <= 6 and result["silhouette"] > 0.2
    assert sum(c["size"] for c in result["clusters"]) == len(series)
    peaks = [max(c["center"]["peak_in"], c["center"]["peak_out"]) for c in result["clusters"]]
    assert peaks == sorted(peaks, reverse=True)
    fixed = cluster_interfaces(series, k=3)
    assert fixed["k"] == 3 and len(fixed["clusters"]) == 3
    with pytest.raises(ValueError):
        cluster_interfaces(series[:2])


def test_classifiers_beat_chance_and_tune_thresholds() -> None:
    result = classify_change_risk(synthetic_changes())
    assert {m["algorithm"] for m in result["models"]} == {
        "gradient_boosting",
        "random_forest",
        "logistic_regression",
    }
    for m in result["models"]:
        assert m["roc_auc"] > 0.55 and m["recall"] > 0.3  # failures are not ignored
        assert 0 < m["threshold"] < 1
    assert result["source"] == "synthetic" and result["notes"]


def test_all_regression_algorithms_and_duration_is_learnable() -> None:
    result = regress_change_duration(synthetic_changes())
    assert {m["algorithm"] for m in result["models"]} == set(REGRESSORS)
    rmses = [m["rmse"] for m in result["models"]]
    assert rmses == sorted(rmses)
    assert result["models"][0]["r2"] > 0.6


def test_traffic_forecast() -> None:
    series, _ = _series(datetime(2026, 11, 23, 12, tzinfo=UTC), hours=48)
    result = regress_traffic(
        series, horizon_steps=6, algorithms=["ridge", "random_forest", "gradient_boosting"]
    )
    assert result["models"][0]["r2"] > 0.6


def test_q_learning_policy_is_cautious_with_risk() -> None:
    trained = train_wave_policy(episodes=40000)
    by_bucket = {(p["devices"], p["risk"]): p["wave_size"] for p in trained["policy"]}
    for dlo, dhi in DEVICE_BUCKETS[1:]:
        waves = [by_bucket[(f"{dlo}-{dhi}", f"{lo:.0%}-{hi:.0%}")] for lo, hi in RISK_BUCKETS]
        assert waves == sorted(waves, reverse=True), waves  # more risk -> smaller (or equal) waves
        assert all(w <= dhi for w in waves)
    assert recommend_wave(trained, devices=3, risk=0.001) <= 3
    curve = [c["mean_reward"] for c in trained["learning_curve"]]
    assert curve[-1] >= curve[0]


async def test_change_dataset_uses_the_database_when_it_has_enough_history(tmp_path) -> None:
    db = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/ml.db")
    async with db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(db, expire_on_commit=False)
    assert (await load_changes(factory)).source == "synthetic"
    async with factory() as s:
        for n in range(MIN_REAL_CHANGES + 5):
            cid = uuid.uuid4()
            failed = n % 5 == 0
            created = datetime(2026, 11, 1, tzinfo=UTC) + timedelta(hours=n)
            s.add(
                Change(
                    id=cid,
                    ticket_number=f"RITM{n}",
                    change_type="vlan_add",
                    state=ChangeState.ROLLED_BACK if failed else ChangeState.CLOSED,
                    requester="a",
                    intent={},
                    devices=[f"leaf-{n % 3}"] * (1 + n % 4),
                    created_at=created,
                )
            )
            s.add(
                AuditLog(
                    change_id=cid,
                    from_state="ci_passed",
                    to_state="approved",
                    actor="t",
                    created_at=created,
                )
            )
            s.add(
                AuditLog(
                    change_id=cid,
                    from_state="post_checked",
                    to_state="rolled_back" if failed else "closed",
                    actor="t",
                    created_at=created + timedelta(minutes=10 + n % 7),
                )
            )
        await s.commit()
    data = await load_changes(factory)
    assert data.source == "database" and data.rows == MIN_REAL_CHANGES + 5
    assert 0.1 < data.failed.mean() < 0.3
    assert data.duration_min.min() >= 10
    await db.dispose()


def _client() -> TestClient:
    app = create_app(
        Settings(adapter_mode="fake", dev=True, database_url="sqlite+aiosqlite:///:memory:")
    )
    app.dependency_overrides[get_current_user] = lambda: CurrentUser("u", "v", "viewer")
    return TestClient(app)


def test_ml_api_end_to_end_in_fake_mode() -> None:
    c = _client()
    assert c.get("/ml/overview").json()["advisory"] is True
    anomalies = c.post("/ml/anomalies", json={}).json()
    assert anomalies["source"] == "fake" and anomalies["anomalous_interfaces"] >= 1
    assert c.post("/ml/clusters", json={"k": 3}).json()["k"] == 3
    assert (
        c.post("/ml/classification", json={"algorithms": ["gradient_boosting"]}).json()["best"]
        == "gradient_boosting"
    )
    reg = c.post(
        "/ml/regression", json={"target": "change_duration", "algorithms": ["linear", "svr"]}
    ).json()
    assert {m["algorithm"] for m in reg["models"]} == {"linear", "svr"}
    rl = c.post("/ml/reinforcement", json={"episodes": 5000}).json()
    assert rl["advisory"] is True and len(rl["policy"]) == 16
    risky = c.post(
        "/ml/risk",
        json={
            "change_type": "static_route_remove",
            "devices": 40,
            "platforms": 3,
            "hour": 2,
            "emergency": True,
        },
    ).json()
    safe = c.post("/ml/risk", json={"change_type": "vlan_add", "devices": 2, "hour": 11}).json()
    assert risky["failure_probability"] > safe["failure_probability"]
    assert risky["recommended_wave_size"] <= 8


def test_ml_api_validation() -> None:
    c = _client()
    assert c.post("/ml/classification", json={"algorithms": ["magic"]}).status_code == 422
    assert c.post("/ml/risk", json={"change_type": "nope", "devices": 1}).status_code == 422
    assert c.post("/ml/anomalies", json={"algorithm": "nope"}).status_code == 422


def test_ml_needs_telemetry_for_traffic_models() -> None:
    app = create_app(
        Settings(adapter_mode="fake", dev=True, database_url="sqlite+aiosqlite:///:memory:")
    )
    app.state.telemetry = None
    app.dependency_overrides[get_current_user] = lambda: CurrentUser("u", "v", "viewer")
    resp = TestClient(app).post("/ml/anomalies", json={})
    assert resp.status_code == 503 and "TELEMETRY_BACKEND" in resp.json()["detail"]
