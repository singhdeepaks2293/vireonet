from __future__ import annotations

import respx
from httpx import Response

from tests.contract_suite import check_servicenow
from vireonet.adapters.base import AdapterConfig
from vireonet.adapters.servicenow import ServiceNowAdapter

BASE = "https://snow.example"
ITEM = {
    "number": "RITM0001234",
    "short_description": "Add VLAN 100",
    "state": {"value": "1", "display_value": "Open"},
    "requested_for": {"value": "alice"},
    "sys_id": {"value": "abc123"},
}
EMPTY = {"result": []}


def _adapter() -> ServiceNowAdapter:
    return ServiceNowAdapter(
        AdapterConfig(name="servicenow", base_url=BASE, max_retries=0),
        auth_mode="basic",
        username="svc",
        password="secret",
    )


@respx.mock
async def test_servicenow_real_contract() -> None:
    # health_check: GET sc_req_item?limit=1
    # GET sequence in check_servicenow: health(1) + get_request(1) +
    # add_work_note#1 (get_request + update_state's get_request = 2) +
    # add_work_note#2 (dedup, no HTTP) + missing get_request(1) = 5 GETs.
    respx.get(url__regex=r".*sc_req_item.*").mock(
        side_effect=[
            Response(200, json={"result": [ITEM]}),
            Response(200, json={"result": [ITEM]}),
            Response(200, json={"result": [ITEM]}),
            Response(200, json={"result": [ITEM]}),
            Response(200, json=EMPTY),
        ]
    )
    respx.get(url__regex=r".*sc_item_option_mtom.*").mock(
        return_value=Response(200, json={"result": []})
    )
    # PATCH update_state (called by add_work_note via update_state)
    respx.patch(url__regex=r".*sc_req_item/abc123.*").mock(
        return_value=Response(200, json={"result": ITEM})
    )
    await check_servicenow(_adapter())


@respx.mock
async def test_servicenow_real_404_maps_to_not_found() -> None:
    respx.get(url__regex=r".*sc_req_item.*").mock(
        return_value=Response(404, json={"error": {"message": "no record"}})
    )
    a = _adapter()
    import pytest

    from vireonet.adapters.errors import AdapterNotFound

    with pytest.raises(AdapterNotFound):
        await a.get_request("RITM0001234")


@respx.mock
async def test_servicenow_oauth_token_fetched_once() -> None:
    respx.post(f"{BASE}/oauth_token.do").mock(
        return_value=Response(200, json={"access_token": "tok-1", "expires_in": 3600})
    )
    respx.get(url__regex=r".*sc_req_item.*").mock(
        return_value=Response(200, json={"result": [ITEM]})
    )
    respx.get(url__regex=r".*sc_item_option_mtom.*").mock(
        return_value=Response(200, json={"result": []})
    )
    a = ServiceNowAdapter(
        AdapterConfig(name="servicenow", base_url=BASE, max_retries=0),
        auth_mode="oauth",
        client_id="id",
        client_secret="sec",
    )
    req = await a.get_request("RITM0001234")
    assert req.ticket == "RITM0001234"
    assert a._token == "tok-1"
