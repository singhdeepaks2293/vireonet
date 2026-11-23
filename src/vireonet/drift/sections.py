"""Section-aware config comparison (D2).

A config is split into named sections (interfaces, acl, routing, aaa,
ntp, snmp, other) so a change in a managed block can be treated
differently from a change in a block nobody manages. The parser behind
the split is an interface: CiscoconfparseParser uses ciscoconfparse when
installed; SimpleLineParser is the dependency-free fallback with the
same section rules.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from vireonet.diff import config_lines

MANAGED_SECTIONS_DEFAULT: frozenset[str] = frozenset({"interfaces", "acl", "routing", "aaa"})

SECTION_NAMES: tuple[str, ...] = ("interfaces", "acl", "routing", "aaa", "ntp", "snmp", "other")


def section_of_line(line: str) -> str:
    """Section for one top-level config line (indented lines inherit)."""
    stripped = line.strip()
    if stripped.startswith("interface "):
        return "interfaces"
    if "access-list" in stripped or stripped.startswith("ip access-list"):
        return "acl"
    if stripped.startswith(("ip route ", "ipv6 route ", "router ", "route-map ")):
        return "routing"
    if stripped.startswith(("aaa ", "username ")):
        return "aaa"
    if stripped.startswith("ntp "):
        return "ntp"
    if stripped.startswith("snmp-server"):
        return "snmp"
    return "other"


@runtime_checkable
class SectionParser(Protocol):
    def split(self, config: str) -> dict[str, list[str]]:
        """Section name -> lines belonging to it (headers included)."""


class SimpleLineParser:
    """Line-prefix parser: a top-level line starts a block in its section;
    indented lines belong to the current block's section."""

    def split(self, config: str) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {name: [] for name in SECTION_NAMES}
        current = "other"
        for line in config_lines(config):
            if line and not line.startswith((" ", "\t")):
                current = section_of_line(line)
            out[current].append(line)
        return {name: lines for name, lines in out.items() if lines}


class CiscoconfparseParser:
    """ciscoconfparse-backed parser (same section rules, same output).

    ciscoconfparse is an optional dependency: if it is not installed,
    constructing this parser raises ImportError and callers fall back to
    SimpleLineParser. The split itself is still line-based classification
    over the parsed objects, so both parsers agree on sections.
    """

    def __init__(self) -> None:
        try:
            from ciscoconfparse import CiscoConfParse  # noqa: F401
        except ImportError as exc:
            raise ImportError("ciscoconfparse is not installed; use SimpleLineParser") from exc

    def split(self, config: str) -> dict[str, list[str]]:
        from ciscoconfparse import CiscoConfParse

        lines = config_lines(config)
        parse = CiscoConfParse(lines)
        # Top-level objects, in file order; each object's text is its
        # header plus its indented children.
        seen: set[int] = set()
        out: dict[str, list[str]] = {name: [] for name in SECTION_NAMES}
        for obj in parse.objs:
            section = section_of_line(obj.text)
            for child in [obj, *obj.children]:
                if id(child) in seen:
                    continue
                seen.add(id(child))
                out[section].append(child.text)
        # Lines ciscoconfparse did not attach to any object fall through
        # to "other". Counted, not set-membership, so duplicate identical
        # lines are not swallowed.
        from collections import Counter

        covered = Counter(line for lines_list in out.values() for line in lines_list)
        for line in lines:
            if covered.get(line, 0) > 0:
                covered[line] -= 1
            else:
                out["other"].append(line)
        return {name: lines_list for name, lines_list in out.items() if lines_list}


def default_parser() -> SectionParser:
    """CiscoconfparseParser when the library is installed, else the simple one."""
    try:
        return CiscoconfparseParser()
    except ImportError:
        return SimpleLineParser()


def managed_sections(
    device: str, templates: set[str] | dict[str, object] | None = None
) -> set[str]:
    """Managed section names for a device.

    For D2 the caller passes the managed set in directly; ``templates``
    is accepted for the future per-template mapping (a dict is ignored
    for now and the default set is returned). ``device`` is part of the
    signature so a per-device mapping can land here without changing
    callers.
    """
    if isinstance(templates, set):
        return set(templates)
    return set(MANAGED_SECTIONS_DEFAULT)


def classify_section_diff(section: str, managed: set[str] | None = None) -> Literal["full", "low"]:
    """Severity class for a change in ``section``: managed -> "full", else "low"."""
    managed_set = set(managed) if managed is not None else set(MANAGED_SECTIONS_DEFAULT)
    return "full" if section in managed_set else "low"


def sections_in_diff(diff_lines: list[dict[str, object]]) -> set[str]:
    """Sections touched by added/removed diff rows (compute_diff "lines").

    Indented lines inherit their block's section: the current section is
    tracked from every row (context rows included) and updated whenever
    a top-level line appears, so a changed line inside an interface
    block counts as "interfaces" even though the header itself did not
    change.
    """
    sections: set[str] = set()
    current = "other"
    for row in diff_lines:
        text = str(row.get("text", ""))
        if text and not text.startswith((" ", "\t")):
            current = section_of_line(text)
        if row.get("type") in {"add", "remove"}:
            sections.add(current)
    return sections
