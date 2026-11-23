from __future__ import annotations

from fastapi.testclient import TestClient

from vireonet.config import Settings
from vireonet.main import create_app
from vireonet.temporal.client import FakeTemporalClient


def _client():  # type: ignore[no-untyped-def]
    temporal = FakeTemporalClient()
    # ZTP service needs a real DB; use a file DB via the app's session factory
    # override is complex in TestClient, so this test stubs the service.
    app = create_app(Settings(adapter_mode="fake", database_url="sqlite+aiosqlite:///:memory:"), temporal_client=temporal)

    class StubZtpService:
        async def verify_token(self, serial, token):  # type: ignore[no-untyped-def]
            return False  # no device registered, so every token is invalid

    app.state.ztp_service = StubZtpService()
    return app, temporal


def test_callback_bad_token_401() -> None:
    app, _ = _client()
    # No device registered, so any token fails. The endpoint must not leak
    # whether the serial exists.
    resp = TestClient(app).post("/ztp/callback", json={"token": "bad", "serial": "FOC1", "model": "C9300", "platform": "ios-xe"})
    assert resp.status_code == 401  # bad token, no device registered
