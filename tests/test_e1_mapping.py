"""E1: execution mapping — most-specific match, errors, template validation."""

from __future__ import annotations

import pytest

from vireonet.execution.awx_engine import AwxEngine
from vireonet.execution.mapping import ExecutionMapping


def _mapping() -> ExecutionMapping:
    return ExecutionMapping.from_yaml("policies/execution.yaml")


def test_default_mapping_matches_yaml_file() -> None:
    assert ExecutionMapping.default().all_templates() == _mapping().all_templates()


def test_ztp_device_apply_uses_ztp_day1() -> None:
    template, extra = _mapping().template_for("apply", "ztp_device", "ios-xe")
    assert template == "ztp-day1"
    assert extra == {}


def test_nxos_apply_uses_apply_with_platform_family() -> None:
    template, extra = _mapping().template_for("apply", "vlan_add", "nxos")
    assert template == "apply"
    assert extra == {"platform_family": "nxos"}


def test_backup_falls_back_to_default_for_ztp() -> None:
    # The ztp_device entry only defines apply; backup falls through to "*".
    template, _ = _mapping().template_for("backup", "ztp_device", "ios-xe")
    assert template == "backup"


def test_generic_backup() -> None:
    template, _ = _mapping().template_for("backup", "vlan_add", "ios-xe")
    assert template == "backup"


def test_unknown_step_raises_value_error() -> None:
    with pytest.raises(ValueError, match="no execution template"):
        _mapping().template_for("no_such_step", "vlan_add", "ios-xe")


def test_python_mapping() -> None:
    template, _ = _mapping().template_for("run_python", "vlan_add", "iosxe", job_type="python")
    assert template == "run-python"


async def test_validate_templates_reports_missing() -> None:
    class StubAwx:
        def __init__(self) -> None:
            self.checked: list[str] = []

        async def template_exists(self, name: str) -> bool:
            self.checked.append(name)
            return name != "rollback"

    engine = AwxEngine(StubAwx(), ExecutionMapping.default())  # type: ignore[arg-type]
    assert await engine.validate_templates() == ["rollback"]


async def test_validate_templates_skips_adapters_without_template_exists() -> None:
    class BareAwx:
        pass

    engine = AwxEngine(BareAwx(), ExecutionMapping.default())  # type: ignore[arg-type]
    assert await engine.validate_templates() == []
