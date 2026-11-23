from __future__ import annotations

from fastapi.testclient import TestClient

from vireonet.config import Settings
from vireonet.main import create_app
from vireonet.observability import get_ticket, record_change, set_ticket
from vireonet.structlog_config import redact


def test_metrics_endpoint_exposes_vireonet_metrics() -> None:
    record_change("closed")
    client = TestClient(
        create_app(Settings(adapter_mode="fake", database_url="sqlite+aiosqlite:///:memory:"))
    )
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "vireonet_changes_total" in resp.text
    assert "vireonet_adapter_calls_total" in resp.text


def test_correlation_id_contextvar() -> None:
    set_ticket("RITM1234")
    assert get_ticket() == "RITM1234"


def test_redact_masks_secrets() -> None:
    assert redact({"token": "secret", "ticket": "RITM1"}) == {"token": "***", "ticket": "RITM1"}
    assert redact({"nested": {"password": "x"}}) == {"nested": {"password": "***"}}
