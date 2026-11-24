"""TextFSM: parse show-command output into rows.

Two template sources, tried in order:
1. Your templates: VIREONET_TEXTFSM_USER_DIR (default policies/textfsm),
   with an ntc-style "index" file (Template, Hostname, Platform, Command).
2. The bundled ntc-templates library (~1000 templates, Apache-2.0).

Platforms are VireoNet names (ios, nxos, eos, junos, ...) or ntc names
(cisco_ios, juniper_junos, ...); VireoNet names are mapped.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import textfsm
from textfsm import clitable

# VireoNet platform -> ntc-templates platforms, most specific first.
NTC_PLATFORMS: dict[str, list[str]] = {
    "ios": ["cisco_ios"],
    "ios-xe": ["cisco_xe", "cisco_ios"],
    "nxos": ["cisco_nxos"],
    "eos": ["arista_eos"],
    "junos": ["juniper_junos"],
    "fortios": ["fortinet"],
    "panos": ["paloalto_panos"],
    "sros": ["alcatel_sros", "nokia_sros"],
    "srlinux": ["nokia_srl"],
    "exos": ["extreme_exos"],
}


class TextFsmError(ValueError):
    pass


@dataclass
class ParseResult:
    template: str
    source: str  # "user" | "ntc" | "inline"
    platform: str
    headers: list[str]
    rows: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "template": self.template,
            "source": self.source,
            "platform": self.platform,
            "headers": self.headers,
            "rows": self.rows,
        }


def ntc_dir() -> Path:
    import ntc_templates

    return Path(ntc_templates.__file__).parent / "templates"


def ntc_platforms(platform: str) -> list[str]:
    p = platform.strip().lower()
    return NTC_PLATFORMS.get(p, [p])


def _rows(headers: list[str], table: Any) -> list[dict[str, Any]]:
    keys = [h.lower() for h in headers]
    return [dict(zip(keys, list(r), strict=False)) for r in table]


def _read_index(directory: Path) -> list[dict[str, str]]:
    path = directory / "index"
    if not path.exists():
        return []
    lines = [
        line
        for line in path.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    reader = csv.reader(io.StringIO("\n".join(lines)), skipinitialspace=True)
    rows = list(reader)
    if not rows:
        return []
    header = [h.strip() for h in rows[0]]
    return [dict(zip(header, [c.strip() for c in r], strict=False)) for r in rows[1:] if r]


@lru_cache(maxsize=8)
def _cli_table(directory: str) -> clitable.CliTable:
    return clitable.CliTable("index", directory)


def _parse_with(
    directory: Path, platform: str, command: str, output: str
) -> tuple[str, list[str], list[dict[str, Any]]] | None:
    if not (directory / "index").exists():
        return None
    table = _cli_table(str(directory))
    try:
        table.ParseCmd(output, {"Command": command, "Platform": platform})
    except clitable.CliTableError:
        return None
    entry = table.index.GetRowMatch({"Command": command, "Platform": platform})
    name = str(table.index.index[entry]["Template"]) if entry else ""
    return name, list(table.header), _rows(list(table.header), table)


def parse(
    platform: str,
    command: str,
    output: str,
    *,
    template_text: str | None = None,
    user_dir: str | Path = "policies/textfsm",
) -> ParseResult:
    """Rows from `output`. An inline template (a draft from the UI) wins;
    then your templates; then ntc-templates."""
    if template_text is not None:
        fsm = compile_template(template_text)
        result = fsm.ParseText(output)
        return ParseResult(
            "<inline>", "inline", platform, list(fsm.header), _rows(list(fsm.header), result)
        )
    for ntc_platform in ntc_platforms(platform):
        for source, directory in (("user", Path(user_dir)), ("ntc", ntc_dir())):
            found = _parse_with(directory, ntc_platform, command, output)
            if found is not None:
                name, headers, rows = found
                return ParseResult(name, source, ntc_platform, headers, rows)
    raise TextFsmError(
        f"no TextFSM template for {command!r} on {platform} ({', '.join(ntc_platforms(platform))})"
    )


def compile_template(text: str) -> textfsm.TextFSM:
    try:
        return textfsm.TextFSM(io.StringIO(text))
    except textfsm.TextFSMTemplateError as exc:
        raise TextFsmError(f"template does not compile: {exc}") from exc


def validate(text: str) -> dict[str, Any]:
    try:
        fsm = compile_template(text)
    except TextFsmError as exc:
        return {"valid": False, "errors": [str(exc)], "values": []}
    return {"valid": True, "errors": [], "values": list(fsm.header), "states": list(fsm.states)}


def list_templates(
    platform: str | None = None,
    query: str | None = None,
    *,
    user_dir: str | Path = "policies/textfsm",
    limit: int = 200,
) -> dict[str, Any]:
    wanted = set(ntc_platforms(platform)) if platform else None
    q = (query or "").lower()
    out: list[dict[str, str]] = []
    total = 0
    for source, directory in (("user", Path(user_dir)), ("ntc", ntc_dir())):
        for row in _read_index(directory):
            plat = row.get("Platform", "")
            if wanted and plat not in wanted:
                continue
            name = row.get("Template", "")
            command = re.sub(r"\[\[|\]\]", "", row.get("Command", ""))
            if q and q not in name.lower() and q not in command.lower():
                continue
            total += 1
            if len(out) < limit:
                out.append(
                    {"template": name, "platform": plat, "command": command, "source": source}
                )
    return {"total": total, "templates": out}


def read_template(source: str, name: str, *, user_dir: str | Path = "policies/textfsm") -> str:
    if not re.fullmatch(r"[\w.-]+\.textfsm", name):
        raise TextFsmError("invalid template name")
    base = Path(user_dir) if source == "user" else ntc_dir()
    path = base / name
    if not path.is_file():
        raise TextFsmError(f"template {name} not found in {source}")
    return path.read_text()
