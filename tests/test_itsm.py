"""ITSM providers against mocked HTTP APIs, plus the fake and the factory."""

from __future__ import annotations

import json

import httpx
import pytest

from vireonet.config import Settings
from vireonet.itsm.base import Attachment, ItsmError
from vireonet.itsm.bmc_helix import BmcHelixItsm
from vireonet.itsm.factory import build_itsm
from vireonet.itsm.fake import FakeItsm
from vireonet.itsm.freshservice import FreshserviceItsm
from vireonet.itsm.jira import JiraItsm
from vireonet.itsm.servicenow import ServiceNowItsm


def T(handler):  # type: ignore[no-untyped-def]
    return httpx.MockTransport(handler)


# --- Jira -------------------------------------------------------------------------


async def test_jira_ticket_attachments_comment_and_close() -> None:
    calls: list[tuple[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path))
        assert req.headers["Authorization"].startswith("Basic ")  # Cloud: email + API token
        p = req.url.path
        if p == "/rest/api/2/issue/NET-42" and req.method == "GET":
            return httpx.Response(
                200,
                json={
                    "fields": {
                        "summary": "Push config",
                        "status": {"name": "To Do"},
                        "reporter": {"displayName": "Bob"},
                        "attachment": [
                            {
                                "id": "10001",
                                "filename": "push.xlsx",
                                "size": 6000,
                                "mimeType": "x",
                                "content": "https://acme.atlassian.net/rest/api/2/attachment/content/10001",
                            }
                        ],
                    }
                },
            )
        if p == "/rest/api/2/attachment/content/10001":
            return httpx.Response(200, content=b"XLSX")
        if p == "/rest/api/2/issue/NET-42/attachments":
            assert req.headers["X-Atlassian-Token"] == "no-check"
            return httpx.Response(200, json=[{"id": "10002"}])
        if p == "/rest/api/2/issue/NET-42/comment":
            return httpx.Response(201, json={})
        if p == "/rest/api/2/issue/NET-42/transitions" and req.method == "GET":
            return httpx.Response(200, json={"transitions": [{"id": "31", "name": "Done"}]})
        if p == "/rest/api/2/issue/NET-42/transitions":
            assert json.loads(req.content) == {"transition": {"id": "31"}}
            return httpx.Response(204)
        return httpx.Response(404)

    j = JiraItsm("https://acme.atlassian.net", email="a@b.c", api_token="t", transport=T(handler))
    t = await j.get_ticket("NET-42")
    assert (t.summary, t.status, t.requester) == ("Push config", "To Do", "Bob")
    atts = await j.list_attachments("NET-42")
    assert atts[0].filename == "push.xlsx"
    assert await j.download_attachment("NET-42", atts[0]) == b"XLSX"
    assert await j.attach_file("NET-42", "out.xlsx", b"x") == "10002"
    await j.close("NET-42", "done by VireoNet")
    assert ("POST", "/rest/api/2/issue/NET-42/transitions") in calls


async def test_jira_data_center_uses_pat_and_reports_missing_transition() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["Authorization"] == "Bearer pat-1"
        if req.url.path.endswith("/transitions"):
            return httpx.Response(200, json={"transitions": [{"id": "1", "name": "Resolve"}]})
        return httpx.Response(201, json={})

    j = JiraItsm("https://jira.local", pat="pat-1", transport=T(handler))
    with pytest.raises(ItsmError, match="no transition named 'Done'"):
        await j.close("NET-1", "x")
    with pytest.raises(ItsmError, match="email"):
        JiraItsm("https://jira.local")


# --- Freshservice -------------------------------------------------------------------


async def test_freshservice() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "s3.example":  # pre-signed URL: no API key sent
            assert "Authorization" not in req.headers
            return httpx.Response(200, content=b"XLSX")
        assert req.headers["Authorization"].startswith("Basic ")
        if req.url.path == "/api/v2/tickets/42" and req.method == "GET":
            return httpx.Response(
                200,
                json={
                    "ticket": {
                        "subject": "Push",
                        "status": 2,
                        "requester_id": 7,
                        "attachments": [
                            {
                                "id": 900,
                                "name": "push.xlsx",
                                "size": 10,
                                "content_type": "x",
                                "attachment_url": "https://s3.example/push.xlsx",
                            }
                        ],
                    }
                },
            )
        if req.url.path == "/api/v2/tickets/42/notes":
            assert json.loads(req.content)["private"] is True
            return httpx.Response(201, json={})
        if req.url.path == "/api/v2/tickets/42" and req.method == "PUT":
            return httpx.Response(200, json={"ticket": {"attachments": [{"id": 901}]}})
        return httpx.Response(404)

    f = FreshserviceItsm("https://acme.freshservice.com", api_key="k", transport=T(handler))
    t = await f.get_ticket("INC-42")
    assert (t.key, t.status) == ("INC-42", "open")
    atts = await f.list_attachments("#42")
    assert await f.download_attachment("42", atts[0]) == b"XLSX"
    assert await f.attach_file("42", "o.xlsx", b"x") == "901"
    await f.add_comment("42", "hi")


# --- BMC Helix -------------------------------------------------------------------------


async def test_bmc_helix_jwt_login_and_worklog_attachments() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/jwt/login":
            return httpx.Response(200, content=b"JWT123")
        assert req.headers["Authorization"] == "AR-JWT JWT123"
        if "HPD%3AHelp%20Desk" in str(req.url.raw_path) or "HPD:Help Desk" in req.url.path:
            return httpx.Response(
                200,
                json={
                    "entries": [
                        {
                            "values": {
                                "Description": "Push",
                                "Status": "Assigned",
                                "Submitter": "carol",
                            }
                        }
                    ]
                },
            )
        if req.url.path.endswith("/attach/z2AF Work Log01"):
            return httpx.Response(200, content=b"XLSX")
        if "HPD:WorkLog" in req.url.path:
            return httpx.Response(
                200,
                json={
                    "entries": [
                        {
                            "values": {
                                "Work Log ID": "WLG1",
                                "z2AF Work Log01": {"name": "push.xlsx", "sizeBytes": 10},
                            }
                        }
                    ]
                },
            )
        return httpx.Response(404)

    b = BmcHelixItsm("https://helix.local", username="u", password="p", transport=T(handler))
    t = await b.get_ticket("INC000000000123")
    assert (t.summary, t.status) == ("Push", "Assigned")
    atts = await b.list_attachments("INC000000000123")
    assert atts == [Attachment("WLG1|z2AF Work Log01", "push.xlsx", 10)]
    assert await b.download_attachment("INC000000000123", atts[0]) == b"XLSX"


# --- ServiceNow wrapper, fake, factory ---------------------------------------------------


async def test_servicenow_wrapper_delegates() -> None:
    class SN:
        async def get_request(self, key):  # type: ignore[no-untyped-def]
            from vireonet.adapters.protocols import SNRequest

            return SNRequest(key, "Push", "open", "alice")

        async def list_attachments(self, key):  # type: ignore[no-untyped-def]
            return [{"id": "s1", "filename": "p.xlsx", "size": 3}]

        async def download_attachment(self, att_id):  # type: ignore[no-untyped-def]
            return b"XLSX"

    s = ServiceNowItsm(SN())
    assert (await s.get_ticket("RITM1")).requester == "alice"
    att = (await s.list_attachments("RITM1"))[0]
    assert await s.download_attachment("RITM1", att) == b"XLSX"


async def test_fake_itsm_has_a_demo_ticket_with_a_workbook() -> None:
    f = FakeItsm()
    atts = await f.list_attachments("RITM0005001")
    assert atts[0].filename.endswith(".xlsx")
    await f.attach_file("RITM0005001", "out.xlsx", b"x")
    await f.add_comment("RITM0005001", "note")
    assert len(await f.list_attachments("RITM0005001")) == 2
    with pytest.raises(ItsmError):
        await f.get_ticket("NOPE-1")


def test_factory_selection() -> None:
    assert isinstance(build_itsm(Settings(adapter_mode="fake")), FakeItsm)
    assert build_itsm(Settings(adapter_mode="real")) is None
    j = build_itsm(Settings(itsm_provider="jira", jira_base_url="https://j", jira_pat="p"))
    assert isinstance(j, JiraItsm)
    with pytest.raises(ItsmError):
        build_itsm(Settings(itsm_provider="servicenow"), adapters={})


async def test_fake_itsm_state_is_shared_through_the_database(tmp_path) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import vireonet.models  # noqa: F401  (registers the tables)
    from vireonet.db import Base

    db = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/s.db")
    async with db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(db, expire_on_commit=False)
    worker, api = FakeItsm(sessions=sessions), FakeItsm(sessions=sessions)
    await worker.add_comment("CHG0030001", "applied in wave 1")
    att = await worker.attach_file("CHG0030001", "updated.xlsx", b"NEW")
    await worker.close("CHG0030001", "closed by VireoNet")
    assert (await api.get_ticket("CHG0030001")).status == "closed"
    assert (await api.get_ticket("CHG0030001")).fields["state_value"] == "3"  # no longer Implement
    assert await api.comments("CHG0030001") == ["applied in wave 1", "closed by VireoNet"]
    files = {a.id: a for a in await api.list_attachments("CHG0030001")}
    assert await api.download_attachment("CHG0030001", files[att]) == b"NEW"
    assert len(files) == 2  # the seeded workbook plus the write-back
    assert (await FakeItsm().get_ticket("CHG0030001")).status == "Implement"  # unshared
    await db.dispose()
