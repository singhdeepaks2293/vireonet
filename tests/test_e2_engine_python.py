"""E2: AwxEngine python branch — VIREONET-RESULT via stdout markers and
via set_stats artifacts, and the no-result error."""

from __future__ import annotations

import json

import pytest
import respx
from httpx import Response

from vireonet.adapters.awx import AwxAdapter
from vireonet.adapters.base import AdapterConfig
from vireonet.adapters.errors import AdapterValidationError
from vireonet.execution.awx_engine import AwxEngine
from vireonet.execution.mapping import ExecutionMapping
from vireonet.execution.types import DeviceRef, StepRequest

BASE = "https://awx.example"

RESULT = {
    "status": "successful",
    "step": "apply",
    "device": "leaf-01",
    "output": {"changed": True},
    "declared_changes": [
        {
            "object_type": "vlan",
            "identifier": "100",
            "field": "name",
            "old_value": "",
            "new_value": "web",
            "config_lines": ["vlan 100", " name web"],
        }
    ],
    "error": None,
    "duration_seconds": 1.5,
}


def _adapter() -> AwxAdapter:
    return AwxAdapter(AdapterConfig(name="awx", base_url=BASE, max_retries=0), token="tok")


def _mock_run_python(job_id: int, stdout: str, events: list[dict]) -> None:
    respx.get(url__regex=r".*/api/v2/job_templates/\?name=run-python.*").mock(
        return_value=Response(200, json={"results": [{"id": 20}], "next": None})
    )
    respx.post(url__regex=r".*/api/v2/job_templates/20/launch/.*").mock(
        return_value=Response(201, json={"job": job_id})
    )
    respx.get(url__regex=rf".*/api/v2/jobs/{job_id}/$").mock(
        side_effect=[
            Response(200, json={"id": job_id, "name": "run-python", "status": "running"}),
            Response(200, json={"id": job_id, "name": "run-python", "status": "successful"}),
        ]
    )
    respx.get(url__regex=rf".*/api/v2/jobs/{job_id}/stdout/.*").mock(
        return_value=Response(200, text=stdout)
    )
    respx.get(url__regex=rf".*/api/v2/jobs/{job_id}/job_events/.*").mock(
        return_value=Response(200, json={"results": events, "next": None})
    )


def _request() -> StepRequest:
    return StepRequest(
        ticket_number="RITM1",
        device=DeviceRef(name="leaf-01", platform="iosxe"),
        step="apply",
        change_type="vlan_add",
        job_type="python",
        dry_run=False,
    )


def _engine() -> AwxEngine:
    return AwxEngine(_adapter(), ExecutionMapping.default(), base_url=BASE)


@respx.mock
async def test_python_result_from_stdout_markers() -> None:
    stdout = (
        "PLAY [run_python] ***\n"
        "VIREONET-RESULT-BEGIN\n" + json.dumps(RESULT) + "\nVIREONET-RESULT-END\n"
    )
    _mock_run_python(9, stdout, events=[])
    result = await _engine().apply(_request())
    assert result.ok
    assert result.job_id == 9
    assert len(result.declared_changes) == 1
    assert result.declared_changes[0].object_type == "vlan"
    assert result.declared_changes[0].identifier == "100"
    assert result.declared_changes[0].new_value == "web"
    assert result.output == {"changed": True}
    assert result.evidence["template"] == "run-python"


@respx.mock
async def test_python_result_from_set_stats_artifacts() -> None:
    # No markers in stdout; the result comes from the set_stats artifact.
    _mock_run_python(
        9, "PLAY ok, no markers here\n", events=[{"event_data": {"vireonet_result": RESULT}}]
    )
    result = await _engine().apply(_request())
    assert result.ok
    assert len(result.declared_changes) == 1
    assert result.declared_changes[0].identifier == "100"


@respx.mock
async def test_python_no_result_raises() -> None:
    _mock_run_python(9, "PLAY ok, no markers here\n", events=[])
    with pytest.raises(AdapterValidationError, match="no VIREONET-RESULT"):
        await _engine().apply(_request())
