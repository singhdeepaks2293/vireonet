from __future__ import annotations

from pathlib import Path

import yaml


def test_compose_has_required_services() -> None:
    data = yaml.safe_load(Path("docker-compose.yml").read_text())
    services = data["services"]
    assert {"postgres", "temporal", "temporal-ui", "api", "worker"} <= set(services)
    assert services["api"]["ports"] == ["8000:8000"]
    assert services["temporal"]["ports"] == ["7233:7233"]
    assert "postgres-data" in data["volumes"]
    # API and worker must wait for healthy Postgres + Temporal.
    assert services["api"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert services["worker"]["depends_on"]["temporal"]["condition"] == "service_healthy"


def test_dockerfile_exists_and_exposes_8000() -> None:
    text = Path("Dockerfile").read_text()
    assert "EXPOSE 8000" in text and "uvicorn" in text
