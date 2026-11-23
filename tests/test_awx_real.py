from __future__ import annotations

import pytest
import respx
from httpx import Response

from vireonet.adapters.awx import AwxAdapter
from vireonet.adapters.base import AdapterConfig
from vireonet.adapters.errors import AdapterNotFound, AdapterValidationError

BASE = "https://awx.example"


def _adapter() -> AwxAdapter:
    return AwxAdapter(AdapterConfig(name="awx", base_url=BASE, max_retries=0), token="tok")


@respx.mock
async def test_awx_launch_and_poll() -> None:
    respx.get(url__regex=r".*/api/v2/job_templates/\?name=backup.*").mock(
        return_value=Response(200, json={"results": [{"id": 10}], "next": None})
    )
    respx.post(url__regex=r".*/api/v2/job_templates/10/launch/.*").mock(
        return_value=Response(201, json={"job": 42})
    )
    respx.get(url__regex=r".*/api/v2/jobs/42/.*").mock(
        side_effect=[
            Response(200, json={"id": 42, "name": "backup", "status": "running"}),
            Response(200, json={"id": 42, "name": "backup", "status": "successful"}),
        ]
    )
    a = _adapter()
    job = await a.launch_job("backup", {"ticket": "RITM1"})
    assert job.id == 42
    done = await a.poll_job(42, timeout_seconds=10, interval_seconds=0.01)
    assert done.status == "successful"


@respx.mock
async def test_awx_secret_extra_vars_rejected_before_http() -> None:
    a = _adapter()
    with pytest.raises(AdapterValidationError):
        await a.launch_job("backup", {"api_token": "x"})


@respx.mock
async def test_awx_template_not_found() -> None:
    respx.get(url__regex=r".*/api/v2/job_templates/.*").mock(
        return_value=Response(200, json={"results": [], "next": None})
    )
    with pytest.raises(AdapterNotFound):
        await _adapter().launch_job("nope", {})


@respx.mock
async def test_awx_contract_against_fake_template_list() -> None:
    respx.get(url__regex=r".*/api/v2/job_templates/.*").mock(
        return_value=Response(
            200, json={"results": [{"name": "backup"}, {"name": "apply"}], "next": None}
        )
    )
    assert await _adapter().list_templates() == ["backup", "apply"]
