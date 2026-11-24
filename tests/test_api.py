from __future__ import annotations

from fastapi.testclient import TestClient

from vireonet.api.security import CurrentUser, get_current_user
from vireonet.config import Settings
from vireonet.main import create_app


def test_healthz_and_adapter_health() -> None:
    app = create_app(Settings(adapter_mode="fake"))
    client = TestClient(app)
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/adapters/health").status_code == 401  # needs a login now
    app.dependency_overrides[get_current_user] = lambda: CurrentUser("u", "v", "viewer")
    body = client.get("/adapters/health").json()
    names = {a["name"] for a in body["adapters"]}
    assert names == {
        "servicenow",
        "netbox",
        "awx",
        "gitlab",
        "batfish",
        "vault",
        "eveng",
        "containerlab",
        "dhcp",
        "notification",
        "monitoring",
    }
    assert all(a["status"] == "ok" for a in body["adapters"])
