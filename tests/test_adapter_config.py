"""Adapters → Configure: overrides saved from the UI, secrets encrypted and
never returned, applied on top of the environment; Gitea tab; docs API."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from vireonet.adapter_config import apply_overrides, decrypt, encrypt, is_secret, load_overrides
from vireonet.api.security import CurrentUser, get_current_user
from vireonet.config import Settings
from vireonet.db import Base
from vireonet.main import create_app


def make(tmp_path, **kw: Any):  # type: ignore[no-untyped-def]
    url = f"sqlite+aiosqlite:///{tmp_path}/cfg.db"

    async def init() -> None:
        e = create_async_engine(url)
        async with e.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        await e.dispose()

    asyncio.run(init())
    app = create_app(
        Settings(**{"adapter_mode": "fake", "database_url": url, "jwt_secret": "j" * 40, **kw})
    )
    client = TestClient(app)

    def as_role(role: str) -> TestClient:
        app.dependency_overrides[get_current_user] = lambda: CurrentUser("u", role, role)
        return client

    return app, as_role, url


def test_secret_detection_and_encryption() -> None:
    assert (
        is_secret("netbox_token")
        and is_secret("servicenow_client_secret")
        and is_secret("kafka_password")
    )
    assert not is_secret("netbox_base_url") and not is_secret("git_token_secret")
    tok = encrypt("s3cr3t", "k" * 40)
    assert (
        "s3cr3t" not in tok and decrypt(tok, "k" * 40) == "s3cr3t" and decrypt(tok, "other") is None
    )


def test_admin_only_get_save_and_secrets_are_never_returned(tmp_path) -> None:
    app, as_role, url = make(tmp_path)
    assert as_role("approver").get("/adapters/config").status_code == 403
    cfg = as_role("admin").get("/adapters/config").json()
    netbox = next(g for g in cfg["groups"] if g["id"] == "netbox")
    token = next(f for f in netbox["fields"] if f["name"] == "netbox_token")
    assert token["kind"] == "secret" and token["value"] is None
    assert {g["id"] for g in cfg["groups"]} >= {
        "servicenow",
        "itsm",
        "netbox",
        "awx",
        "telemetry",
        "controllers",
        "logs",
        "kafka",
        "flows",
        "gitea",
    }
    r = as_role("admin").put(
        "/adapters/config",
        json={
            "values": {
                "netbox_base_url": "https://nb.example",
                "netbox_token": "nb-SECRET",
                "log_export_to": "fake, es",
            }
        },
    )
    assert r.status_code == 200 and r.json()["restart_needed"] is True
    assert "nb-SECRET" not in as_role("admin").get("/adapters/config").text
    again = as_role("admin").get("/adapters/config").json()
    nb = {f["name"]: f for f in next(g for g in again["groups"] if g["id"] == "netbox")["fields"]}
    assert nb["netbox_base_url"]["source"] == "ui" and nb["netbox_token"]["source"] == "ui"

    async def stored() -> dict[str, Any]:
        e = create_async_engine(url)
        out = await load_overrides(async_sessionmaker(e, expire_on_commit=False), "j" * 40)
        raw = (await e.connect()).exec_driver_sql(
            "select value from adapter_overrides where key='netbox_token'"
        )
        row = (await raw).fetchone() if hasattr(raw, "__await__") else None
        await e.dispose()
        return {"over": out, "raw": row}

    data = asyncio.run(stored())
    assert data["over"]["netbox_token"] == "nb-SECRET" and data["over"]["log_export_to"] == [
        "fake",
        "es",
    ]
    applied = apply_overrides(Settings(adapter_mode="fake"), data["over"])
    assert applied.netbox_base_url == "https://nb.example" and applied.log_export_to == [
        "fake",
        "es",
    ]
    # Empty secret keeps the stored one; clear resets to the environment.
    as_role("admin").put(
        "/adapters/config", json={"values": {"netbox_token": ""}, "clear": ["netbox_base_url"]}
    )
    assert asyncio.run(stored())["over"].get("netbox_base_url") is None
    assert asyncio.run(stored())["over"]["netbox_token"] == "nb-SECRET"


def test_validation_and_test_connection(tmp_path) -> None:
    _app, as_role, _url = make(tmp_path)
    admin = as_role("admin")
    assert (
        admin.put("/adapters/config", json={"values": {"jwt_secret": "x"}}).status_code == 422
    )  # not an adapter setting
    assert (
        admin.put("/adapters/config", json={"values": {"controllers": "{not json"}}).status_code
        == 422
    )
    assert (
        admin.put("/adapters/config", json={"values": {"flow_script_runner": "nope"}}).status_code
        == 422
    )
    ok = admin.post("/adapters/config/test", json={"group": "logs", "values": {}}).json()
    assert ok["ok"] is True  # fake log backend
    bad = admin.post(
        "/adapters/config/test",
        json={
            "group": "logs",
            "values": {
                "log_backends": '[{"name":"es","type":"elasticsearch","url":"http://127.0.0.1:1"}]'
            },
        },
    ).json()
    assert bad["ok"] is False


def test_gitea_info_and_docs_api(tmp_path) -> None:
    app, as_role, _ = make(tmp_path, git_provider="gitea", git_base_url="http://gitea:3000/api/v1")

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v1/version":
            return httpx.Response(200, json={"version": "1.22.6"})
        if req.url.path == "/api/v1/repos/search":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "data": [
                        {
                            "full_name": "netops/config-leaf-01",
                            "html_url": "http://gitea:3000/netops/config-leaf-01",
                        }
                    ],
                },
            )
        return httpx.Response(404)

    app.state.gitea_transport = httpx.MockTransport(h)
    info = as_role("viewer").get("/gitea/info").json()
    assert (
        info["installed"]
        and info["version"] == "1.22.6"
        and info["ui_url"] == "http://gitea:3000"
        and info["repos"][0]["name"] == "netops/config-leaf-01"
    )
    app.state.gitea_transport = httpx.MockTransport(lambda r: httpx.Response(502))
    assert as_role("viewer").get("/gitea/info").json()["installed"] is False
    docs = {d["name"] for d in as_role("viewer").get("/help/docs").json()["items"]}
    assert {"USER_MANUAL", "ADAPTERS", "SDLC"} <= docs
    assert "VireoNet" in as_role("viewer").get("/help/docs/ADAPTERS").json()["markdown"]
    assert as_role("viewer").get("/help/docs/..%2Fpyproject").status_code == 404
