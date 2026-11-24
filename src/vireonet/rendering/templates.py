"""User render templates (policies/render_templates.yaml).

Override a built-in renderer for one (change type, platform) pair, or add a
platform VireoNet does not ship (huawei-vrp, iosxr, ...), without code:

    version: 1
    templates:
      vlan_add:
        huawei-vrp:
          format: cli
          config: |
            vlan {{ intent.vlan_id }}
             description {{ intent.vlan_name }}
            {% for i in intent.interfaces %}
            interface {{ i }}
             port link-type access
             port default vlan {{ intent.vlan_id }}
            {% endfor %}

`config` is a sandboxed Jinja template (StrictUndefined). The output is
split into lines; blank lines are dropped. Variables: intent, device,
platform, ticket.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel, ValidationError

from vireonet.checks.templates import KNOWN_CHANGE_TYPES
from vireonet.rendering.filters import FILTERS, GLOBALS

_ENV = SandboxedEnvironment(
    undefined=StrictUndefined, autoescape=False, trim_blocks=True, lstrip_blocks=True
)
_ENV.filters.update(FILTERS)
_ENV.globals.update(GLOBALS)


class RenderTemplateError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


class TemplateSpec(BaseModel):
    model_config = {"extra": "forbid"}

    format: Literal["cli", "set"] = "cli"
    config: str


class RenderTemplatesFile(BaseModel):
    model_config = {"extra": "forbid"}

    version: Literal[1] = 1
    # change_type -> platform -> template
    templates: dict[str, dict[str, TemplateSpec]] = {}


class RenderTemplates:
    def __init__(self, data: RenderTemplatesFile, source: str = "<none>", text: str = "") -> None:
        self.data = data
        self.source = source
        self.text = text

    @classmethod
    def empty(cls) -> RenderTemplates:
        return cls(RenderTemplatesFile())

    @classmethod
    def load(cls, path: str | Path) -> RenderTemplates:
        p = Path(path)
        if not p.exists():
            return cls.empty()
        return cls.from_yaml_text(p.read_text(encoding="utf-8"), source=str(p))

    @classmethod
    def from_yaml_text(cls, text: str, source: str = "<text>") -> RenderTemplates:
        try:
            raw = yaml.safe_load(text) or {}
            data = RenderTemplatesFile.model_validate(raw)
        except yaml.YAMLError as exc:
            raise RenderTemplateError([f"YAML does not parse: {exc}"]) from exc
        except ValidationError as exc:
            raise RenderTemplateError(
                [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
            ) from exc
        errors: list[str] = []
        for ct, platforms in data.templates.items():
            if ct not in KNOWN_CHANGE_TYPES:
                errors.append(f"templates.{ct}: unknown change type")
            for platform, spec in platforms.items():
                try:
                    _ENV.parse(spec.config)
                except TemplateError as exc:
                    errors.append(f"templates.{ct}.{platform}.config: does not parse: {exc}")
        if errors:
            raise RenderTemplateError(errors)
        return cls(data, source, text)

    def get(self, change_type: str, platform: str) -> TemplateSpec | None:
        return self.data.templates.get(change_type, {}).get(platform)

    def platforms(self) -> set[str]:
        return {p for per in self.data.templates.values() for p in per}

    @staticmethod
    def render(spec: TemplateSpec, variables: dict[str, Any]) -> list[str]:
        text = _ENV.from_string(spec.config).render(**variables)
        return [line.rstrip() for line in text.splitlines() if line.strip()]
