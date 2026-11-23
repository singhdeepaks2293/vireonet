from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import yaml


def test_all_playbooks_are_valid_yaml_with_hosts_and_tasks() -> None:
    for path in sorted(Path("ansible/playbooks").glob("*.yml")):
        data = yaml.safe_load(path.read_text())
        assert isinstance(data, list) and data, path
        for play in data:
            assert "hosts" in play and "tasks" in play, path
            assert play["tasks"], path


def test_job_templates_reference_existing_playbooks() -> None:
    data = yaml.safe_load(Path("ansible/awx/job_templates.yaml").read_text())
    for t in data["job_templates"]:
        assert Path(t["playbook"]).exists(), t
        assert t["name"] in {
            "backup",
            "pre-check",
            "dry-run",
            "apply",
            "post-check",
            "rollback",
            "ztp-day1",
            "dhcp-reload",
            "run-python",
        }


def test_ansible_syntax_check_if_available() -> None:
    if shutil.which("ansible-playbook") is None:
        import pytest

        pytest.skip("ansible-playbook not installed in this sandbox")
    for path in sorted(Path("ansible/playbooks").glob("*.yml")):
        result = subprocess.run(
            ["ansible-playbook", "--syntax-check", str(path)],
            capture_output=True,
            text=True,
            timeout=60,
        )  # noqa: S603,S607
        assert result.returncode == 0, f"{path}: {result.stdout} {result.stderr}"
