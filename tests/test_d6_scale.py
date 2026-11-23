"""D6: drift scale (500 devices under the concurrency limit) + settings validation."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from vireonet.config import Settings, validate_drift_settings
from vireonet.drift.collector import DriftCollector
from vireonet.execution.fake_engine import FakeExecutionEngine
from vireonet.execution.types import StepResult, StepStatus
from vireonet.main import create_app
from vireonet.safety import DeviceLockService


def _good_settings(**overrides: object) -> Settings:
    kwargs: dict[str, object] = {
        "adapter_mode": "fake",
        "drift_max_concurrent": 5,
        "drift_cron": "0 2 * * *",
        "drift_exception_max_days": 30,
        "drift_emergency_deadline_hours": 24,
        "drift_incident_severity_threshold": "high",
    }
    kwargs.update(overrides)
    return Settings(**kwargs)  # type: ignore[arg-type]


# -- settings validation --------------------------------------------------------


def test_drift_settings_defaults_are_valid() -> None:
    assert validate_drift_settings(_good_settings()) == []


@pytest.mark.parametrize("bad", [0, 51, -1])
def test_drift_settings_concurrency_range(bad: int) -> None:
    problems = validate_drift_settings(_good_settings(drift_max_concurrent=bad))
    assert any("drift_max_concurrent" in p for p in problems)


def test_drift_settings_bad_cron_and_threshold() -> None:
    assert any(
        "drift_cron" in p for p in validate_drift_settings(_good_settings(drift_cron="not a cron"))
    )
    assert any(
        "drift_incident_severity_threshold" in p
        for p in validate_drift_settings(_good_settings(drift_incident_severity_threshold="urgent"))
    )
    assert any(
        "drift_exception_max_days" in p
        for p in validate_drift_settings(_good_settings(drift_exception_max_days=0))
    )
    assert any(
        "drift_emergency_deadline_hours" in p
        for p in validate_drift_settings(_good_settings(drift_emergency_deadline_hours=0))
    )


def test_drift_settings_missing_ignore_file(tmp_path: Path) -> None:
    problems = validate_drift_settings(
        _good_settings(drift_ignore_path=str(tmp_path / "missing.yaml"))
    )
    assert any("ignore-rule" in p for p in problems)


def test_drift_settings_unknown_severity_section(tmp_path: Path) -> None:
    policy = tmp_path / "drift.yaml"
    policy.write_text(
        "protected_devices: []\ncritical_sections: [acl, bogus_section]\n"
        "high_sections: [interfaces]\nmanaged_sections: [interfaces]\n",
        encoding="utf-8",
    )
    problems = validate_drift_settings(_good_settings(drift_severity_policy=str(policy)))
    assert any("bogus_section" in p for p in problems)


def test_drift_settings_compliance_rules_must_have_string_context(tmp_path: Path) -> None:
    rules = tmp_path / "compliance.yaml"
    rules.write_text(
        "rules:\n  - name: no-telnet\n    context:\n      device_role: 42\n",
        encoding="utf-8",
    )
    problems = validate_drift_settings(_good_settings(drift_compliance_rules=str(rules)))
    assert any("must be strings" in p for p in problems)
    rules.write_text(
        "rules:\n  - name: no-telnet\n    context:\n      device_role: core\n",
        encoding="utf-8",
    )
    assert validate_drift_settings(_good_settings(drift_compliance_rules=str(rules))) == []
    assert any(
        "compliance-rule file not found" in p
        for p in validate_drift_settings(
            _good_settings(drift_compliance_rules=str(tmp_path / "nope.yaml"))
        )
    )


def test_fake_mode_warns_but_starts_with_bad_drift_settings() -> None:
    # Real mode refuses; fake mode only warns (see main.create_app).
    app = create_app(_good_settings(drift_max_concurrent=99))
    assert app is not None


# -- scale ----------------------------------------------------------------------


class TrackingEngine(FakeExecutionEngine):
    """Fake engine that records peak in-flight collect_state calls."""

    def __init__(self) -> None:
        super().__init__()
        self.in_flight = 0
        self.peak_in_flight = 0

    async def collect_state(self, req):  # type: ignore[no-untyped-def]
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0.001)
            return StepResult(
                status=StepStatus.SUCCESSFUL,
                step="collect_state",
                device=req.device.name,
                job_id=1,
                output={"running_config": f"hostname {req.device.name}\n"},
            )
        finally:
            self.in_flight -= 1


async def test_500_devices_scanned_under_concurrency_limit(engine: AsyncEngine) -> None:
    settings = _good_settings(drift_max_concurrent=5)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    locks = DeviceLockService(factory)
    engine_fake = TrackingEngine()
    collector = DriftCollector(engine_fake, locks, max_concurrent=settings.drift_max_concurrent)
    specs = [
        {"device": f"leaf-{i:03d}", "platform": "ios", "mgmt_ip": f"10.0.0.{i % 250 + 1}"}
        for i in range(500)
    ]
    results = await collector.collect_many(specs, "RITM-SCALE")
    assert len(results) == 500
    assert all(isinstance(v, str) and v.startswith("hostname leaf-") for v in results.values())
    assert engine_fake.peak_in_flight <= 5, (
        f"peak in-flight {engine_fake.peak_in_flight} exceeded limit 5"
    )
    assert engine_fake.peak_in_flight > 1, "test would be vacuous without real concurrency"


async def test_change_lock_not_starved_while_scan_holds_other_locks(
    engine: AsyncEngine,
) -> None:
    """A scan holding locks on devices A..J must not starve a change on K.

    Device locks are per device: acquiring K while A..J are held must
    complete well within a timeout (here: immediately, asserted via
    wait_for with a 5s budget).
    """
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    locks = DeviceLockService(factory)
    for i in range(10):
        await locks.acquire(f"leaf-{i:03d}", "DRIFT-SCAN")
    acquired = await asyncio.wait_for(locks.acquire("leaf-999", "RITM-CHANGE"), timeout=5.0)
    assert acquired is True
    assert await locks.is_locked("leaf-999") == "RITM-CHANGE"
    await locks.release_all([f"leaf-{i:03d}" for i in range(10)], "DRIFT-SCAN")
    await locks.release("leaf-999", "RITM-CHANGE")
