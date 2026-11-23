"""Script manifest (E3): which custom scripts may run, at which SHA.

Only a script listed here, at exactly the listed SHA, may execute — and
only for its allowed change types and platforms. The manifest is edited
only by admins, via merge request.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


class ScriptEntry(BaseModel):
    name: str
    repo: str
    ref: str = "main"
    sha: str
    allowed_change_types: list[str] = []
    allowed_platforms: list[str] = []
    writes: list[str] = []


class ScriptManifest(BaseModel):
    scripts: list[ScriptEntry] = []

    @classmethod
    def from_yaml(cls, path: str | Path) -> ScriptManifest:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls(scripts=[ScriptEntry(**s) for s in data.get("scripts") or []])

    def get(self, name: str) -> ScriptEntry | None:
        for entry in self.scripts:
            if entry.name == name:
                return entry
        return None

    def check(self, name: str, sha: str, change_type: str, platform: str) -> ScriptEntry:
        """Validate a run request. Raises ValueError before anything runs."""
        entry = self.get(name)
        if entry is None:
            raise ValueError(f"script {name} is not listed in the script manifest")
        if entry.sha != sha:
            raise ValueError(
                f"script {name}: SHA mismatch (manifest {entry.sha}, requested {sha}); "
                "only the listed SHA may run"
            )
        if change_type not in entry.allowed_change_types:
            raise ValueError(f"script {name} is not allowed for change type {change_type}")
        if platform not in entry.allowed_platforms:
            raise ValueError(f"script {name} is not allowed for platform {platform}")
        return entry
