"""Execution template mapping (E1): policies/execution.yaml -> AWX template.

A mapping entry matches on (change_type, platform, job_type); the most
specific match wins over the entries that define the requested step: an
exact change_type beats "*", an exact platform beats "*". Entries that do
not define the step do not participate (so a ztp_device entry that only
defines ``apply`` never shadows the default ``backup`` mapping).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

_DEFAULT_YAML = """\
# Execution mapping: which AWX job template runs each engine step.
# Most-specific match wins: exact change_type/platform beats "*".
# job_type "playbook" runs an Ansible playbook template; job_type "python"
# runs ansible/playbooks/run_python.yml, which executes a Python runner.
mappings:
  - change_type: "*"
    platform: "*"
    job_type: playbook
    steps:
      backup: backup
      pre_check: pre-check
      dry_run: dry-run
      apply: apply
      post_check: post-check
      rollback: rollback
      collect_state: backup
  - change_type: ztp_device
    platform: "*"
    job_type: playbook
    steps:
      apply: ztp-day1
  - change_type: "*"
    platform: nxos
    job_type: playbook
    steps:
      apply:
        template: apply
        extra_vars:
          platform_family: nxos
  - change_type: "*"
    platform: "*"
    job_type: python
    steps:
      run_python: run-python
"""


class MappingEntry(BaseModel):
    change_type: str = "*"
    platform: str = "*"
    job_type: str = "playbook"
    steps: dict[str, Any] = {}

    def step_config(self, step: str) -> tuple[str, dict[str, str]] | None:
        """(template, extra_vars) for ``step``, or None if not defined here."""
        raw = self.steps.get(step)
        if raw is None:
            return None
        if isinstance(raw, str):
            return raw, {}
        if isinstance(raw, dict):
            template = raw.get("template")
            if not isinstance(template, str) or not template:
                return None
            extra = raw.get("extra_vars") or {}
            if not isinstance(extra, dict):
                return None
            return template, {str(k): str(v) for k, v in extra.items()}
        return None

    def specificity(self, change_type: str, platform: str) -> int:
        """Higher wins: exact change_type counts more than exact platform."""
        score = 0
        if self.change_type == change_type:
            score += 2
        elif self.change_type != "*":
            return -1
        if self.platform == platform:
            score += 1
        elif self.platform != "*":
            return -1
        return score


class ExecutionMapping(BaseModel):
    mappings: list[MappingEntry] = []

    @classmethod
    def default(cls) -> ExecutionMapping:
        return cls.from_yaml_text(_DEFAULT_YAML)

    @classmethod
    def from_yaml_text(cls, text: str) -> ExecutionMapping:
        data = yaml.safe_load(text) or {}
        mappings = data.get("mappings") or []
        return cls(mappings=[MappingEntry(**m) for m in mappings])

    @classmethod
    def from_yaml(cls, path: str | Path) -> ExecutionMapping:
        return cls.from_yaml_text(Path(path).read_text(encoding="utf-8"))

    def template_for(
        self, step: str, change_type: str, platform: str, job_type: str = "playbook"
    ) -> tuple[str, dict[str, str]]:
        """Most-specific (template, extra_vars) for this step, or ValueError."""
        best: tuple[str, dict[str, str]] | None = None
        best_score = -1
        for entry in self.mappings:
            if entry.job_type != job_type:
                continue
            score = entry.specificity(change_type, platform)
            if score < 0:
                continue
            config = entry.step_config(step)
            if config is None:
                continue
            if score > best_score:
                best, best_score = config, score
        if best is None:
            raise ValueError(
                f"no execution template for step={step} change_type={change_type} "
                f"platform={platform} job_type={job_type}"
            )
        return best

    def all_templates(self) -> set[str]:
        templates: set[str] = set()
        for entry in self.mappings:
            for step in entry.steps:
                config = entry.step_config(step)
                if config is not None:
                    templates.add(config[0])
        return templates
