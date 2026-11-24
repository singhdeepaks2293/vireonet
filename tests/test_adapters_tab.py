"""Every adapter VireoNet has must be visible on the Adapters tab
(/adapters/settings). Adding an adapter without a row fails this test."""

from __future__ import annotations

from fastapi.testclient import TestClient

from vireonet.adapters.fakes import ALL_FAKE_ADAPTERS
from vireonet.adapters.git.registry import DEFAULT_BASE_URLS
from vireonet.api.adapters import ITSM_PROVIDERS, TELEMETRY_BACKENDS
from vireonet.api.security import CurrentUser, get_current_user
from vireonet.config import Settings
from vireonet.controllers.vendors import VENDORS
from vireonet.flows.sources import NORMALISERS, TRIGGER_FOR
from vireonet.logsearch import LOG_TYPES
from vireonet.main import create_app


def _rows(**settings: object) -> dict[str, dict[str, object]]:
    app = create_app(
        Settings(
            **{"adapter_mode": "fake", "database_url": "sqlite+aiosqlite:///:memory:", **settings}
        )
    )
    app.dependency_overrides[get_current_user] = lambda: CurrentUser("u", "v", "viewer")
    return {r["name"]: r for r in TestClient(app).get("/adapters/settings").json()}


def test_every_adapter_family_has_a_row() -> None:
    rows = _rows()
    expected = (
        set(ALL_FAKE_ADAPTERS)
        | {f"itsm: {p}" for p in ITSM_PROVIDERS}
        | {f"controller: {v}" for v in VENDORS}
        | {f"logs: {t}" for t in LOG_TYPES}
        | {f"git: {g}" for g in DEFAULT_BASE_URLS}
        | {f"inbound: {s}" for s in NORMALISERS if s not in TRIGGER_FOR}
        | {"inbound: kafka", "logs: fake", "itsm: fake"}
    )
    missing = sorted(expected - set(rows))
    assert not missing, f"adapters missing from the Adapters tab: {missing}"
    telemetry = [r for r in rows.values() if r["category"] == "telemetry"]
    assert len(telemetry) >= len(TELEMETRY_BACKENDS)
    categories = {r["category"] for r in rows.values()}
    assert {"core", "itsm", "telemetry", "controller", "logs", "git", "inbound"} <= categories


def test_rows_never_leak_secrets() -> None:
    rows = _rows(
        log_backends='[{"name":"es","type":"elasticsearch","url":"https://user:pw@es.example:9200/path?x=1","token":"SECRET"}]'
    )
    es = rows["logs: es"]
    assert es["endpoint"] == "https://es.example:9200" and "SECRET" not in str(rows)
