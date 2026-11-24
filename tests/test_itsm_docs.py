"""ITSM API calls checked against the vendors' documented endpoints and formats."""

from __future__ import annotations

import httpx
import pytest
import respx
from httpx import Response

from vireonet.adapters.base import AdapterConfig
from vireonet.adapters.servicenow import ServiceNowAdapter
from vireonet.itsm.base import Attachment, ItsmError
from vireonet.itsm.bmc_helix import BmcHelixItsm
from vireonet.itsm.freshservice import FreshserviceItsm
from vireonet.itsm.jira import JiraItsm

SN = "https://snow.example"
ITEM = {
    "number": {"value": "RITM0001234", "display_value": "RITM0001234"},
    "short_description": {"value": "Push", "display_value": "Push"},
    "state": {"value": "1", "display_value": "Open"},
    "sys_id": {"value": "abc123", "display_value": "abc123"},
}


def T(handler):  # type: ignore[no-untyped-def]
    return httpx.MockTransport(handler)


def _sn() -> ServiceNowAdapter:
    return ServiceNowAdapter(
        AdapterConfig(name="servicenow", base_url=SN, max_retries=0),
        auth_mode="basic",
        username="svc",
        password="secret",
    )


# --- ServiceNow -------------------------------------------------------------------------


@respx.mock
async def test_servicenow_attach_uses_multipart_upload() -> None:
    respx.get(url__regex=r".*/table/sc_req_item\?.*").mock(
        return_value=Response(200, json={"result": [ITEM]})
    )
    respx.get(url__regex=r".*sc_item_option_mtom.*").mock(
        return_value=Response(200, json={"result": []})
    )
    upload = respx.post(f"{SN}/api/now/attachment/upload").mock(
        return_value=Response(201, json={"result": {"sys_id": "att1"}})
    )
    assert await _sn().attach_file("RITM0001234", "out.xlsx", b"XLSXDATA") == "att1"
    req = upload.calls[0].request
    assert req.url.path == "/api/now/attachment/upload"
    assert req.headers["Content-Type"].startswith("multipart/form-data")
    body = req.content.decode("latin-1")
    assert 'name="table_name"\r\n\r\nsc_req_item' in body
    assert 'name="table_sys_id"\r\n\r\nabc123' in body
    assert 'name="uploadFile"; filename="out.xlsx"' in body
    assert "Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" in body
    assert "XLSXDATA" in body
    # The fields come before the file part.
    assert body.index("table_sys_id") < body.index("uploadFile")


@respx.mock
async def test_servicenow_variable_names_from_display_value() -> None:
    respx.get(url__regex=r".*/table/sc_req_item\?.*").mock(
        return_value=Response(200, json={"result": [ITEM]})
    )
    respx.get(url__regex=r".*sc_item_option_mtom.*").mock(
        return_value=Response(200, json={"result": [{"sc_item_option": {"value": "opt1"}}]})
    )
    opt = respx.get(url__regex=r".*/table/sc_item_option/opt1.*").mock(
        return_value=Response(
            200,
            json={
                "result": {
                    "item_option_new": {"value": "q-sysid", "display_value": "vlan_id"},
                    "value": "100",
                }
            },
        )
    )
    req = await _sn().get_request("RITM0001234")
    assert req.variables == {"vlan_id": "100"}
    assert opt.calls[0].request.url.params["sysparm_display_value"] == "all"


# --- Jira -------------------------------------------------------------------------------


async def test_jira_dc_download_via_absolute_content_url() -> None:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["Authorization"] == "Bearer pat-1"
        seen.append(str(req.url))
        if req.url.path == "/rest/api/2/issue/NET-1":
            return httpx.Response(
                200,
                json={
                    "fields": {
                        "attachment": [
                            {
                                "id": "10001",
                                "filename": "push.xlsx",
                                "size": 4,
                                "mimeType": "x",
                                "content": "https://jira.local/secure/attachment/10001/push.xlsx",
                            }
                        ]
                    }
                },
            )
        if req.url.path == "/secure/attachment/10001/push.xlsx":
            return httpx.Response(200, content=b"XLSX")
        return httpx.Response(404)

    j = JiraItsm("https://jira.local", pat="pat-1", transport=T(handler))
    atts = await j.list_attachments("NET-1")
    assert atts[0].url == "https://jira.local/secure/attachment/10001/push.xlsx"
    assert await j.download_attachment("NET-1", atts[0]) == b"XLSX"
    assert seen[-1] == "https://jira.local/secure/attachment/10001/push.xlsx"
    assert not any("/attachment/content/" in u for u in seen)


async def test_jira_download_falls_back_to_attachment_metadata() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/rest/api/2/attachment/10001":
            return httpx.Response(
                200, json={"content": "https://jira.local/secure/attachment/10001/a.xlsx"}
            )
        if req.url.path == "/secure/attachment/10001/a.xlsx":
            return httpx.Response(200, content=b"XLSX")
        return httpx.Response(404)

    j = JiraItsm("https://jira.local", pat="pat-1", transport=T(handler))
    assert await j.download_attachment("NET-1", Attachment("10001", "a.xlsx")) == b"XLSX"


# --- Freshservice ------------------------------------------------------------------------


async def test_freshservice_health_lists_one_ticket() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path == "/api/v2/tickets":
            return httpx.Response(200, json={"tickets": []})
        return httpx.Response(404)

    f = FreshserviceItsm("https://acme.freshservice.com", api_key="k", transport=T(handler))
    assert (await f.health())["status"] == "ok"
    assert seen[0].url.path == "/api/v2/tickets"
    assert seen[0].url.params["per_page"] == "1"
    assert seen[0].headers["Authorization"].startswith("Basic ")


async def test_freshservice_presigned_download_has_no_auth_header() -> None:
    seen: list[httpx.Request] = []
    url = "https://s3.amazonaws.com/fs/push.xlsx?X-Amz-Signature=abc"

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path == "/api/v2/tickets/42":
            return httpx.Response(
                200,
                json={
                    "ticket": {
                        "attachments": [
                            {"id": 900, "name": "push.xlsx", "size": 4, "attachment_url": url}
                        ]
                    }
                },
            )
        if req.url.host == "s3.amazonaws.com":
            return httpx.Response(200, content=b"XLSX")
        return httpx.Response(404)

    f = FreshserviceItsm("https://acme.freshservice.com", api_key="k", transport=T(handler))
    atts = await f.list_attachments("42")
    assert atts[0].url == url
    assert await f.download_attachment("42", atts[0]) == b"XLSX"
    s3 = seen[-1]
    assert s3.url.host == "s3.amazonaws.com"
    assert "Authorization" not in s3.headers
    assert s3.url.params["X-Amz-Signature"] == "abc"


async def test_freshservice_download_without_url_raises() -> None:
    f = FreshserviceItsm(
        "https://acme.freshservice.com",
        api_key="k",
        transport=T(lambda req: httpx.Response(500)),
    )
    with pytest.raises(ItsmError, match="no attachment_url"):
        await f.download_attachment("42", Attachment("900", "push.xlsx"))


# --- BMC Helix ---------------------------------------------------------------------------


async def test_bmc_helix_relogs_in_on_401_and_retries_once() -> None:
    logins: list[int] = []
    tokens = iter(["OLD", "NEW"])

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/jwt/login":
            logins.append(1)
            return httpx.Response(200, content=next(tokens).encode())
        if req.headers["Authorization"] == "AR-JWT OLD":
            return httpx.Response(401)
        assert req.headers["Authorization"] == "AR-JWT NEW"
        return httpx.Response(
            200, json={"entries": [{"values": {"Description": "Push", "Status": "Assigned"}}]}
        )

    b = BmcHelixItsm("https://helix.local", username="u", password="p", transport=T(handler))
    await b.health()  # caches the OLD (now expired) JWT
    t = await b.get_ticket("INC000000000123")
    assert t.status == "Assigned"
    assert len(logins) == 2


async def test_bmc_helix_persistent_401_is_an_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/jwt/login":
            return httpx.Response(200, content=b"JWT")
        return httpx.Response(401)

    b = BmcHelixItsm("https://helix.local", username="u", password="p", transport=T(handler))
    with pytest.raises(ItsmError, match="authentication failed"):
        await b.get_ticket("INC1")


async def test_bmc_helix_attachment_part_is_octet_stream() -> None:
    bodies: list[bytes] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/jwt/login":
            return httpx.Response(200, content=b"JWT")
        bodies.append(req.content)
        return httpx.Response(201)

    b = BmcHelixItsm("https://helix.local", username="u", password="p", transport=T(handler))
    assert await b.attach_file("INC1", "out.xlsx", b"XLSX") == "out.xlsx"
    body = bodies[0].decode("latin-1")
    part = body[body.index('name="attach-z2AF Work Log01"') :]
    assert "Content-Type: application/octet-stream" in part
    assert 'name="entry"' in body and "Content-Type: application/json" in body
