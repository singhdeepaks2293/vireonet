"""E2 runner contract: types round-trip, dry-run makes no change, secrets
never appear in output, secret extra_vars are rejected."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest
from vireonet_runners.common import redact
from vireonet_runners.netmiko_runner import run_netmiko_step
from vireonet_runners.types import RunnerRequest, RunnerResult


def test_runner_request_to_result_round_trip() -> None:
    req = RunnerRequest(
        ticket_number="RITM1",
        device="leaf-01",
        platform="iosxe",
        mgmt_ip="10.0.0.11",
        step="apply",
        change_type="vlan_add",
        rendered_config="vlan 100\n name web\n",
        dry_run=False,
    )
    result = RunnerResult(
        status="successful",
        step=req.step,
        device=req.device,
        output={"changed": True},
        declared_changes=[
            {
                "object_type": "vlan",
                "identifier": "100",
                "field": "name",
                "old_value": "",
                "new_value": "web",
                "config_lines": ["vlan 100", " name web"],
            }
        ],
    )
    assert RunnerResult(**result.model_dump()) == result
    assert RunnerRequest(**req.model_dump()) == req


def test_dry_run_makes_no_change(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[list[str]] = []

    class FakeConn:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def send_command(self, command: str, **kwargs: Any) -> str:
            return "ok"

        def send_config_set(self, lines: list[str]) -> str:
            sent.append(list(lines))
            return "ok"

        def save_config(self) -> str:
            return "saved"

        def disconnect(self) -> None:
            pass

    mod = types.ModuleType("netmiko")
    mod.ConnectHandler = FakeConn  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "netmiko", mod)
    result = run_netmiko_step(
        {
            "ticket_number": "RITM1",
            "device": "leaf-01",
            "platform": "ios",
            "step": "apply",
            "rendered_config": "vlan 100\n name web\n",
            "dry_run": True,
            "extra_vars": {},
        }
    )
    assert result["status"] == "successful"
    assert sent == []


def test_secrets_redacted_and_absent_from_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVICE_PASSWORD", "s3cret-pw")
    monkeypatch.setenv("VIREONET_DEVICE_PASSWORD", "s3cret-pw")
    assert redact('password is "s3cret-pw" here') == 'password is "***" here'
    assert redact('{"password": "s3cret-pw"}') == '{"password": "***"}'
    assert "s3cret-pw" not in redact("plain s3cret-pw text")
    # emit() itself never adds secrets; the redacted block is clean.
    assert "s3cret-pw" not in redact('{"output": {"x": "s3cret-pw"}}')


def test_request_with_password_in_extra_vars_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="must not contain secrets"):
        RunnerRequest(
            ticket_number="RITM1",
            device="leaf-01",
            platform="ios",
            step="backup",
            extra_vars={"device_password": "x"},
        )
