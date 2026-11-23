from __future__ import annotations

import pytest
import respx
from httpx import Response

from vireonet.adapters.base import AdapterConfig
from vireonet.adapters.errors import AdapterValidationError
from vireonet.adapters.gitlab import GitLabAdapter

BASE = "https://gitlab.example"
PID = "123"


def _adapter() -> GitLabAdapter:
    return GitLabAdapter(
        AdapterConfig(name="gitlab", base_url=BASE, max_retries=0), token="tok", project_id=PID
    )


@respx.mock
async def test_gitlab_create_mr_and_merge() -> None:
    respx.post(url__regex=r".*/repository/branches.*").mock(
        return_value=Response(201, json={"name": "change/RITM1"})
    )
    respx.post(url__regex=r".*/repository/commits.*").mock(
        return_value=Response(201, json={"id": "abc"})
    )
    respx.post(url__regex=r".*/merge_requests$").mock(
        return_value=Response(
            201, json={"iid": 5, "title": "Add VLAN", "state": "opened", "web_url": f"{BASE}/mr/5"}
        )
    )
    respx.get(url__regex=r".*/merge_requests/5/pipelines.*").mock(
        return_value=Response(200, json=[{"status": "success"}])
    )
    respx.get(url__regex=r".*/merge_requests/5$").mock(
        return_value=Response(
            200, json={"iid": 5, "title": "Add VLAN", "state": "opened", "web_url": f"{BASE}/mr/5"}
        )
    )
    respx.put(url__regex=r".*/merge_requests/5/merge.*").mock(
        return_value=Response(
            200, json={"iid": 5, "title": "Add VLAN", "state": "merged", "web_url": f"{BASE}/mr/5"}
        )
    )
    a = _adapter()
    mr = await a.create_mr("change/RITM1", "Add VLAN", {"vlan.yml": "vlan: 100"})
    assert mr.iid == 5 and mr.pipeline_status == "success"
    merged = await a.merge_mr(5)
    assert merged.state == "merged"


@respx.mock
async def test_gitlab_merge_blocked_on_failed_pipeline() -> None:
    respx.get(url__regex=r".*/merge_requests/5$").mock(
        return_value=Response(200, json={"iid": 5, "title": "x", "state": "opened", "web_url": ""})
    )
    respx.get(url__regex=r".*/merge_requests/5/pipelines.*").mock(
        return_value=Response(200, json=[{"status": "failed"}])
    )
    with pytest.raises(AdapterValidationError):
        await _adapter().merge_mr(5)


@respx.mock
async def test_gitlab_read_file_decodes_base64() -> None:
    import base64

    content = base64.b64encode(b"vlan: 100").decode()
    respx.get(url__regex=r".*/repository/files/.*").mock(
        return_value=Response(200, json={"content": content})
    )
    assert await _adapter().read_file("vlan.yml") == "vlan: 100"
