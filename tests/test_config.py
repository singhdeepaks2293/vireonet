from __future__ import annotations

from vireonet.config import Settings


def test_defaults_are_fake_mode() -> None:
    s = Settings()
    assert s.adapter_mode == "fake"
    assert s.temporal_address == "localhost:7233"


def test_env_override(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("VIREONET_ADAPTER_MODE", "real")
    monkeypatch.setenv("VIREONET_LOG_LEVEL", "DEBUG")
    s = Settings()
    assert s.adapter_mode == "real"
    assert s.log_level == "DEBUG"
