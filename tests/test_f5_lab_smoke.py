"""Unit tests for the F5 lab runner (scripts/lab_e2e.py).

No live systems: Runner.api is replaced with a canned fake that returns
scripted responses in order, per (method, path). The fake mimics the real
API surface the runner talks to — POST /changes, GET /changes/{id},
GET /changes/{id}/detail, POST .../signals/approval, POST /ztp/devices,
POST /ztp/devices/{serial}/reservation, POST /ztp/callback,
GET /ztp/devices/{serial}, DELETE /ztp/devices/{serial}/reservation — so a
missing endpoint in the real API shows up here as an unexpected call.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import lab_e2e  # noqa: E402
from lab_e2e import Runner  # noqa: E402


class FakeResponse:
    """Minimal httpx.Response stand-in: .status_code, .json(), .text."""

    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self):  # type: ignore[no-untyped-def]
        return self._payload

    @property
    def text(self) -> str:
        return str(self._payload)


class FakeApi:
    """Canned responses, in order, per (method, path)."""

    def __init__(self, script: dict[tuple[str, str], list[FakeResponse]]) -> None:
        self._script = {key: list(responses) for key, responses in script.items()}
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, method: str, path: str, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append((method, path))
        queue = self._script.get((method, path))
        assert queue, f"unexpected API call {method} {path}"
        return queue.pop(0)


def _runner(
    monkeypatch: pytest.MonkeyPatch, script: dict[tuple[str, str], list[FakeResponse]]
) -> tuple[Runner, FakeApi]:
    runner = Runner(
        api_url="http://localhost:8000", token="lab-token", lab_name="lab", poll_seconds=0
    )
    fake = FakeApi(script)
    monkeypatch.setattr(runner, "api", fake)
    return runner, fake


def _real_prereq() -> dict[tuple[str, str], list[FakeResponse]]:
    return {
        ("GET", "/adapters/settings"): [
            FakeResponse(
                200,
                [
                    {"name": "netbox", "mode": "real"},
                    {"name": "awx", "mode": "real"},
                    {"name": "eveng", "mode": "real"},
                ],
            )
        ],
        ("GET", "/adapters/health"): [
            FakeResponse(
                200,
                {
                    "adapters": [
                        {"name": "netbox", "status": "ok"},
                        {"name": "awx", "status": "ok"},
                        {"name": "eveng", "status": "ok"},
                    ]
                },
            )
        ],
    }


def test_stage_records_pass_fail_and_counts(capsys: pytest.CaptureFixture[str]) -> None:
    runner = Runner(api_url="http://localhost:8000", token="t", lab_name="lab")
    assert runner.stage("a", True, "ok") is True
    assert runner.stage("b", False, "nope") is False
    assert runner.passed == 1
    assert runner.total == 2
    out = capsys.readouterr().out
    assert "PASS a: ok" in out
    assert "FAIL b: nope" in out


async def test_run_vlan_success(monkeypatch: pytest.MonkeyPatch) -> None:
    script = {
        **_real_prereq(),
        ("POST", "/changes"): [FakeResponse(202, {"id": "change-1", "state": "validated"})],
        ("GET", "/changes/change-1"): [
            FakeResponse(200, {"id": "change-1", "state": "ci_passed"}),
            FakeResponse(200, {"id": "change-1", "state": "applied"}),
            FakeResponse(200, {"id": "change-1", "state": "post_checked"}),
            FakeResponse(200, {"id": "change-1", "state": "closed"}),
        ],
        ("GET", "/changes/change-1/detail"): [
            FakeResponse(
                200,
                {
                    "diff": [
                        {"type": "context", "text": "# leaf-01"},
                        {"type": "add", "text": "vlan 100"},
                        {"type": "add", "text": " name web"},
                    ]
                },
            )
        ],
        ("POST", "/changes/change-1/signals/approval"): [FakeResponse(202, {"status": "accepted"})],
    }
    runner, _ = _runner(monkeypatch, script)
    assert await runner.run_vlan() is True
    assert [r.name for r in runner.results] == [
        "prereq",
        "change-create",
        "ci-batfish",
        "backup-diff",
        "approve",
        "apply",
        "post-check",
        "close",
    ]
    assert runner.passed == runner.total == 8


async def test_run_vlan_fails_when_ci_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    script = {
        **_real_prereq(),
        ("POST", "/changes"): [FakeResponse(202, {"id": "change-1", "state": "validated"})],
        ("GET", "/changes/change-1"): [FakeResponse(200, {"id": "change-1", "state": "ci_failed"})],
    }
    runner, fake = _runner(monkeypatch, script)
    assert await runner.run_vlan() is False
    failed = [r for r in runner.results if r.name == "ci-batfish"]
    assert len(failed) == 1 and failed[0].passed is False
    assert ("POST", "/changes/change-1/signals/approval") not in fake.calls


async def test_run_ztp_success(monkeypatch: pytest.MonkeyPatch) -> None:
    script = {
        ("POST", "/ztp/devices"): [
            FakeResponse(
                202,
                {
                    "serial": "FOC9999A",
                    "token": "tok-1",
                    "stage": "registered",
                    "ticket_number": "ZTP-FOC9999A",
                },
            )
        ],
        ("POST", "/ztp/devices/FOC9999A/reservation"): [
            FakeResponse(202, {"ip_address": "10.0.1.101", "already_created": False})
        ],
        ("GET", "/ztp/devices/FOC9999A"): [
            FakeResponse(200, {"stage": "identity_verified"}),
            FakeResponse(200, {"stage": "active"}),
        ],
        ("POST", "/ztp/callback"): [FakeResponse(202, {"status": "callback_accepted"})],
        ("DELETE", "/ztp/devices/FOC9999A/reservation"): [
            FakeResponse(200, {"removed": True, "verified": True})
        ],
    }
    runner, _ = _runner(monkeypatch, script)
    assert await runner.run_ztp() is True
    assert [r.name for r in runner.results] == [
        "ztp-register",
        "ztp-reservation",
        "ztp-callback",
        "ztp-identity",
        "ztp-day1",
        "ztp-cleanup",
    ]
    assert runner.passed == runner.total == 6


async def test_ztp_cleanup_fails_when_delete_called_before_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # DELETE reservation is legal only from stage "active" (F2 service rule),
    # so calling cleanup before the device is active must FAIL the stage
    # instead of silently claiming the reservation was removed.
    script = {
        ("DELETE", "/ztp/devices/FOC9999A/reservation"): [
            FakeResponse(
                409, {"detail": "remove_reservation is not legal from stage identity_verified"}
            )
        ],
    }
    runner, _ = _runner(monkeypatch, script)
    assert await runner._ztp_cleanup("FOC9999A") is False
    assert runner.results[-1].name == "ztp-cleanup"
    assert runner.results[-1].passed is False


async def test_ztp_cleanup_fails_when_removed_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    # A 200 with removed=False means no reservation was in the DHCP config;
    # that is a failure, not a clean state.
    script = {
        ("DELETE", "/ztp/devices/FOC9999A/reservation"): [
            FakeResponse(200, {"removed": False, "note": "no reservation for leaf-99"})
        ],
    }
    runner, _ = _runner(monkeypatch, script)
    assert await runner._ztp_cleanup("FOC9999A") is False
    assert runner.results[-1].name == "ztp-cleanup"
    assert runner.results[-1].passed is False


async def test_ztp_cleanup_removes_reservation(monkeypatch: pytest.MonkeyPatch) -> None:
    script = {
        ("DELETE", "/ztp/devices/FOC9999A/reservation"): [
            FakeResponse(200, {"removed": True, "verified": True})
        ],
    }
    runner, _ = _runner(monkeypatch, script)
    assert await runner._ztp_cleanup("FOC9999A") is True
    assert runner.results[-1].name == "ztp-cleanup"
    assert runner.results[-1].passed is True


def test_lab_e2e_module_exports() -> None:
    # Guard against accidental renames the tests above depend on.
    assert {
        "rejected",
        "ci_failed",
        "pre_check_failed",
        "failed",
        "rolled_back",
    } == lab_e2e.TERMINAL_CHANGE_FAILURES
    assert lab_e2e.VLAN_INTENT["ticket_number"] == "RITM-LAB-001"
    assert lab_e2e.ZTP_DEVICE["serial"] == "FOC9999A"
