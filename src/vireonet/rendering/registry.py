from __future__ import annotations

import re
from typing import Any

from jinja2 import TemplateError

from vireonet.rendering.base import RenderedConfig, UnsupportedChange
from vireonet.rendering.builtin import BUILTIN
from vireonet.rendering.platforms import normalise_platform
from vireonet.rendering.templates import RenderTemplates


class Renderer:
    """Intent + device platform -> config. User templates win over built-ins."""

    def __init__(self, templates: RenderTemplates | None = None) -> None:
        self.templates = templates or RenderTemplates.empty()

    def supported_platforms(self) -> list[str]:
        return sorted(set(BUILTIN) | self.templates.platforms())

    def render(self, intent: Any, device: str, platform: str) -> RenderedConfig:
        plat = normalise_platform(platform)
        if intent.change_type == "config_push":
            return self._passthrough(intent, device, plat)
        _assert_no_control_chars(intent.model_dump(), device)
        if not plat:
            raise UnsupportedChange(f"{device}: platform unknown (set the platform in NetBox)")
        spec = self.templates.get(intent.change_type, plat)
        if spec is not None:
            variables = {
                "intent": intent.model_dump(),
                "device": device,
                "platform": plat,
                "ticket": intent.ticket_number,
            }
            try:
                lines = RenderTemplates.render(spec, variables)
            except TemplateError as exc:
                raise UnsupportedChange(f"{device}: render template error: {exc}") from exc
            rendered = RenderedConfig(device, plat, lines, spec.format, "template")
        elif plat in BUILTIN:
            fn, fmt = BUILTIN[plat]
            rendered = RenderedConfig(device, plat, fn(intent, device), fmt)  # type: ignore[arg-type]
        elif plat in self.templates.platforms():
            raise UnsupportedChange(
                f"{device}: no render template for {intent.change_type} on {plat!r} "
                "(add templates."
                + intent.change_type
                + "."
                + plat
                + " in policies/render_templates.yaml)"
            )
        else:
            raise UnsupportedChange(
                f"{device}: no renderer for platform {plat!r} "
                f"(built-in: {sorted(BUILTIN)}; add one in policies/render_templates.yaml)"
            )
        _assert_single_lines(rendered)
        return rendered

    @staticmethod
    def _passthrough(intent: Any, device: str, plat: str) -> RenderedConfig:
        """Excel config push: the device's config block as written, no rendering.

        Format is "set" when every line is a set/delete line (Junos, PAN-OS,
        SR Linux style), else CLI.
        """
        lines = [
            line.rstrip() for line in intent.configs.get(device, "").splitlines() if line.strip()
        ]
        if not lines:
            raise UnsupportedChange(f"{device}: no config for this device in the workbook")
        fmt = "set" if all(re.match(r"^\s*(set|delete)\s", line) for line in lines) else "cli"
        rendered = RenderedConfig(device, plat or "unknown", lines, fmt, "excel")  # type: ignore[arg-type]
        _assert_single_lines(rendered)
        return rendered

    def render_all(self, intent: Any, platforms: dict[str, str]) -> dict[str, RenderedConfig]:
        return {d: self.render(intent, d, platforms.get(d, "")) for d in intent.devices}


def _assert_no_control_chars(value: Any, device: str, path: str = "intent") -> None:
    """Refuse intent values carrying newlines or other control characters.

    Checked before rendering, because a template renders to text that is
    then split into lines: a newline inside a description would otherwise
    become a separate, injected config line that looks perfectly valid.
    """
    if isinstance(value, dict):
        for k, v in value.items():
            _assert_no_control_chars(v, device, f"{path}.{k}")
    elif isinstance(value, list | tuple):
        for n, v in enumerate(value):
            _assert_no_control_chars(v, device, f"{path}[{n}]")
    elif isinstance(value, str) and any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise UnsupportedChange(f"{device}: {path} contains a control character: {value!r}")


def _assert_single_lines(rendered: RenderedConfig) -> None:
    """A value with a newline (description, VLAN name...) could inject extra
    config lines. Refuse it rather than push it."""
    for line in rendered.lines:
        if "\n" in line or "\r" in line or any(ord(c) < 32 and c != "\t" for c in line):
            raise UnsupportedChange(
                f"{rendered.device}: rendered line contains a control character: {line!r}"
            )
