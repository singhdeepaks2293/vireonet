"""Per-platform ignore handling for drift normalisation (D1).

normalize_config already exists in vireonet.diff — reuse it, do not
reimplement it. This module adds only what diff.py does not know about:
volatile lines (timestamps, banners, uptime, secret hashes) that change
on every collection and must never count as drift, and interface-block
ordering, which is order-insensitive on the device.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from vireonet.diff import normalize_config

DEFAULT_IGNORE_PATH = "policies/drift_ignore.yaml"

# Generic volatile-line rules, applied on every platform before the
# platform-specific ignore file. Secret hashes are replaced, not dropped,
# so a line disappearing entirely is still visible as a change.
_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(secret 5 )\S+"), r"\1<redacted>"),
    (re.compile(r"(secret 9 )\S+"), r"\1<redacted>"),
    (re.compile(r"(password 7 )\S+"), r"\1<redacted>"),
    (re.compile(r"(password 5 )\S+"), r"\1<redacted>"),
]


def load_ignore_rules(path: str | Path = DEFAULT_IGNORE_PATH) -> dict[str, list[str]]:
    """Load {platform: [regex, ...]} from the ignore YAML. Missing file -> {}.

    The file shape is ``{platform: [pattern, ...]}`` with an optional
    ``"*"`` key whose patterns apply to every platform.
    """
    file = Path(path)
    if not file.exists():
        return {}
    raw: Any = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"drift ignore file {path} must be a mapping platform -> patterns")
    out: dict[str, list[str]] = {}
    for platform, patterns in raw.items():
        if not isinstance(patterns, list):
            raise ValueError(f"drift ignore patterns for {platform} must be a list")
        out[str(platform)] = [str(p) for p in patterns]
    return out


def _compile(rules: dict[str, list[str]], platform: str) -> list[re.Pattern[str]]:
    patterns = list(rules.get("*", [])) + list(rules.get(platform, []))
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern))
        except re.error:
            # A bad operator pattern must not break scanning; skip it.
            continue
    return compiled


def strip_ignored(config: str, platform: str, rules: dict[str, list[str]]) -> str:
    """Drop lines matching a platform ignore pattern; redact secret hashes.

    Applies line by line to the already-normalized config (CRLF -> LF,
    trailing whitespace stripped, by normalize_config).
    """
    compiled = _compile(rules, platform)
    kept: list[str] = []
    for line in normalize_config(config).split("\n"):
        if line == "" and not kept:
            continue
        if any(rx.search(line) for rx in compiled):
            continue
        redacted = line
        for pattern, replacement in _SECRET_PATTERNS:
            redacted = pattern.sub(replacement, redacted)
        kept.append(redacted)
    text = "\n".join(kept)
    return normalize_config(text)


def sort_interface_blocks(config: str) -> str:
    """Sort top-level ``interface`` blocks by interface name before hashing.

    Approximation, documented: only blocks whose header line starts with
    ``interface `` are reordered; every other line keeps its relative
    order, and indented lines stay attached to their header. Devices do
    not guarantee interface order in ``show running-config``, so without
    this a reordered dump hashes as drift.
    """
    normalized = normalize_config(config)
    lines = normalized.removesuffix("\n").split("\n") if normalized else []
    # Split into segments: each segment is either an interface block
    # (header + indented lines) or a run of non-interface lines. A
    # segment's sort key is the interface name, or "" for non-interface
    # segments, which never move.
    segments: list[tuple[str, list[str]]] = []  # (sort key, lines)
    current: list[str] | None = None
    current_key = ""

    def flush() -> None:
        nonlocal current, current_key
        if current is not None:
            segments.append((current_key, current))
        current, current_key = None, ""

    for line in lines:
        if line.startswith("interface "):
            flush()
            current = [line]
            current_key = line[len("interface ") :].strip()
        elif line in ("!", ""):
            # Block separator: belongs to the block above it (IOS
            # separates blocks with "!"), so it moves with that block
            # and does not break a run of interface segments.
            if current is None:
                segments.append(("", [line]))
            else:
                current.append(line)
        elif line and not line.startswith((" ", "\t")):
            # Non-interface top-level line: its own segment.
            flush()
            segments.append(("", [line]))
        else:
            if current is None:
                current = [line]
                current_key = ""
            else:
                current.append(line)
    flush()
    # Sort each maximal run of consecutive interface segments by
    # interface name; non-interface segments stay exactly where they are.
    out: list[str] = []
    i = 0
    while i < len(segments):
        if segments[i][0] == "":
            out.extend(segments[i][1])
            i += 1
            continue
        j = i
        while j < len(segments) and segments[j][0] != "":
            j += 1
        for _, block in sorted(segments[i:j], key=lambda item: item[0]):
            out.extend(block)
        i = j
    return normalize_config("\n".join(out)) if out else ""


def normalize_for_drift(
    config: str, platform: str, rules: dict[str, list[str]] | None = None
) -> str:
    """Full drift normalisation: normalize, strip ignored, sort interfaces."""
    stripped = strip_ignored(config, platform, rules or {})
    return sort_interface_blocks(stripped)
