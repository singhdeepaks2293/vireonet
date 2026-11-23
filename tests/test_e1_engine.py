"""E1: engine contract against Fake and respx-mocked AWX, mapping, failures."""

from __future__ import annotations

import pytest
import respx
from httpx import Response
from pydantic import ValidationError

from tests.contract_engine import check_engine
from vireonet.adapters.awx import AwxAdapter
from vireonet.adapters.base import AdapterConfig
from vireonet.execution.awx_engine import AwxEngine
from vireonet.execution.fake_engine import FakeExecutionEngine
from vireonet.execution.mapping import ExecutionMapping
from vireonet.execution.types import DeviceRef, StepRequest

BASE = "https://awx.example"


def _awx_adapter() -> AwxAdapter:
    return AwxAdapter(AdapterConfig(name="awx", base_url=BASE, max_retries=0), token="tok")


def _mock_template(name: str, template_id: int) -> None:
    respx.get(url__regex=rf".*/api/v2/job_templates/\?name={name}.*").mock(
        return_value=Response(200, json={"results": [{"id": template_id}], "next": None})
    )


def _mock_job(job_id: int, stdout: str = "PLAY ok\n") -> None:
    respx.post(url__regex=r".*/api/v2/job_templates/\d+/launch/.*").mock(
        return_value=Response(201, json={"job": job_id})
    )
    # launch_job's get_job + poll_job's get_job = 2 GETs per launch; the
    # contract test launches twice (backup, dry-run), so provide 4.
    # The "$" anchor keeps this from also matching the stdout URL.
    respx.get(url__regex=rf".*/api/v2/jobs/{job_id}/$").mock(
        side_effect=[
            Response(200, json={"id": job_id, "name": "backup", "status": "running"}),
            Response(200, json={"id": job_id, "name": "backup", "status": "successful"}),
            Response(200, json={"id": job_id, "name": "backup", "status": "running"}),
            Response(200, json={"id": job_id, "name": "backup", "status": "successful"}),
        ]
    )
    respx.get(url__regex=rf".*/api/v2/jobs/{job_id}/stdout/.*").mock(
        return_value=Response(200, text=stdout)
    )


def _request(**overrides: object) -> StepRequest:
    base: dict[str, object] = {
        "ticket_number": "RITM1",
        "device": DeviceRef(name="leaf-01", platform="iosxe"),
        "step": "backup",
    }
    base.update(overrides)
    return StepRequest(**base)  # type: ignore[arg-type]


async def test_contract_against_fake() -> None:
    await check_engine(FakeExecutionEngine())


@respx.mock
async def test_contract_against_awx_engine() -> None:
    # AwxEngine has a working python branch (E2), so the E1 python
    # rejection in check_engine does not apply to it; the Fake contract
    # above is where the rejection is enforced.
    respx.get(url__regex=r".*/api/v2/ping/.*").mock(return_value=Response(200, json={}))
    _mock_template("backup", 10)
    _mock_template("dry-run", 11)
    _mock_job(42)
    engine = AwxEngine(_awx_adapter(), ExecutionMapping.default(), base_url=BASE)
    await check_engine(engine, expect_python_error=False)


@respx.mock
async def test_awx_engine_backup_returns_stdout_tail() -> None:
    _mock_template("backup", 10)
    _mock_job(42, stdout="line1\nline2\n")
    engine = AwxEngine(_awx_adapter(), ExecutionMapping.default(), base_url=BASE)
    result = await engine.backup(_request())
    assert result.ok
    assert result.job_id == 42
    assert result.log_ref == f"{BASE}/#/jobs/playbook/42"
    assert result.output["stdout_tail"] == "line1\nline2\n"
    assert result.evidence["template"] == "backup"


async def test_fake_failure_injection() -> None:
    engine = FakeExecutionEngine(fail_steps={"apply"})
    assert (await engine.backup(_request())).ok
    failed = await engine.apply(_request(step="apply", dry_run=False))
    assert not failed.ok
    assert failed.status.value == "failed"
    assert [c.step for c in engine.calls] == ["backup", "apply"]


def test_step_request_rejects_secret_extra_vars() -> None:
    for key in ("api_key", "device_password", "my_token", "private_key_file", "SECRET"):
        with pytest.raises(ValidationError, match="must not contain secrets"):
            _request(extra_vars={key: "x"})
    # Non-secret keys are fine.
    assert _request(extra_vars={"vlan_id": "100"}).extra_vars == {"vlan_id": "100"}
