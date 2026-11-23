from __future__ import annotations

from fastapi.testclient import TestClient

from vireonet.config import Settings
from vireonet.main import create_app


def test_healthz_and_adapter_health() -> None:
    client = TestClient(create_app(Settings(adapter_mode="fake")))
    assert client.get("/healthz").json() == {"status": "ok"}
    body = client.get("/adapters/health").json()
    names = {a["name"] for a in body["adapters"]}
    assert names == {
        "servicenow", "netbox", "awx", "gitlab", "batfish",
        "vault", "eveng", "dhcp", "notification", "monitoring",
    }
    assert all(a["status"] == "ok" for a in body["adapters"])
