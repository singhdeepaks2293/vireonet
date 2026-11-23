"""Real config diffs: apply an intent to a stored running config and diff the two.

The diff is always running config vs candidate config. Nothing here renders
the intent as additions — that was the old placeholder behaviour.
"""

from __future__ import annotations

import difflib
import hashlib
from typing import Any


def normalize_config(content: str) -> str:
    """CRLF -> LF, strip trailing whitespace per line, drop trailing blanks."""
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def config_hash(content: str) -> str:
    """SHA-256 of the normalized config, so CRLF/trailing-space variants hash equal."""
    return hashlib.sha256(normalize_config(content).encode("utf-8")).hexdigest()


def config_lines(content: str) -> list[str]:
    """Normalized config as a list of lines (no trailing empty element)."""
    normalized = normalize_config(content)
    return normalized.removesuffix("\n").split("\n") if normalized else []


def _split_lines(content: str) -> list[str]:
    return config_lines(content)


def _block_span(lines: list[str], start: int) -> tuple[int, int]:
    """Span of the config block starting at `start`: header plus indented lines."""
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if line and not line.startswith((" ", "\t")):
            break
        end += 1
    return start, end


def _find_block(lines: list[str], header: str) -> int | None:
    for i, line in enumerate(lines):
        if line.strip() == header:
            return i
    return None


def _set_block_line(lines: list[str], start: int, end: int, new_line: str, match: str) -> None:
    """Replace the first line in lines[start:end] whose stripped text starts
    with `match`, else insert `new_line` at the end of the block."""
    for i in range(start + 1, end):
        if lines[i].strip().startswith(match):
            lines[i] = new_line
            return
    lines.insert(end, new_line)


def _apply_vlan_add(lines: list[str], intent: dict[str, Any]) -> None:
    vlan_id = intent["vlan_id"]
    vlan_name = intent.get("vlan_name", "")
    header = f"vlan {vlan_id}"
    idx = _find_block(lines, header)
    if idx is None:
        lines.extend([header, f" name {vlan_name}"])
    else:
        _, end = _block_span(lines, idx)
        _set_block_line(lines, idx, end, f" name {vlan_name}", "name ")
    for iface in intent.get("interfaces", []):
        iface_header = f"interface {iface}"
        idx = _find_block(lines, iface_header)
        if idx is None:
            lines.extend(
                [iface_header, " switchport mode access", f" switchport access vlan {vlan_id}"]
            )
            continue
        _, end = _block_span(lines, idx)
        if not any(lines[i].strip() == "switchport mode access" for i in range(idx + 1, end)):
            lines.insert(end, " switchport mode access")
            end += 1
        _set_block_line(
            lines, idx, end, f" switchport access vlan {vlan_id}", "switchport access vlan"
        )


def _apply_vlan_remove(lines: list[str], intent: dict[str, Any]) -> None:
    vlan_id = intent["vlan_id"]
    idx = _find_block(lines, f"vlan {vlan_id}")
    if idx is not None:
        _, end = _block_span(lines, idx)
        del lines[idx:end]
    target = f"switchport access vlan {vlan_id}"
    lines[:] = [line for line in lines if line.strip() != target]


def _apply_acl_entry_add(lines: list[str], intent: dict[str, Any]) -> None:
    entry = f"{intent['action']} {intent['protocol']} {intent['source']} {intent['destination']}"
    if intent.get("destination_port") is not None:
        entry += f" eq {intent['destination_port']}"
    header = f"ip access-list extended {intent['acl_name']}"
    idx = _find_block(lines, header)
    if idx is None:
        lines.extend([header, f" {entry}"])
        return
    _, end = _block_span(lines, idx)
    if not any(lines[i].strip() == entry for i in range(idx + 1, end)):
        lines.insert(end, f" {entry}")


def _apply_acl_entry_remove(lines: list[str], intent: dict[str, Any]) -> None:
    idx = _find_block(lines, f"ip access-list extended {intent['acl_name']}")
    if idx is None:
        return
    _, end = _block_span(lines, idx)
    prefix = f"{intent['sequence']} "
    kept = [line for line in lines[idx + 1 : end] if not line.strip().startswith(prefix)]
    lines[idx + 1 : end] = kept


def _apply_static_route_add(lines: list[str], intent: dict[str, Any]) -> None:
    line = f"ip route {intent['prefix']} {intent['next_hop']} {intent.get('admin_distance', 1)}"
    if not any(existing.strip() == line for existing in lines):
        lines.append(line)


def _apply_static_route_remove(lines: list[str], intent: dict[str, Any]) -> None:
    prefix = f"ip route {intent['prefix']} {intent['next_hop']}"
    lines[:] = [
        line
        for line in lines
        if not (line.strip() == prefix or line.strip().startswith(prefix + " "))
    ]


def _apply_interface_set(lines: list[str], intent: dict[str, Any]) -> None:
    header = f"interface {intent['interface']}"
    idx = _find_block(lines, header)
    if idx is None:
        lines.append(header)
        idx = len(lines) - 1
    _, end = _block_span(lines, idx)
    if intent.get("description") is not None:
        _set_block_line(lines, idx, end, f" description {intent['description']}", "description ")
        _, end = _block_span(lines, idx)
    if intent.get("enabled") is False:
        for i in range(idx + 1, end):
            if lines[i].strip() == "no shutdown":
                lines[i] = " shutdown"
                break
        else:
            if not any(lines[i].strip() == "shutdown" for i in range(idx + 1, end)):
                lines.insert(end, " shutdown")
        _, end = _block_span(lines, idx)
    elif intent.get("enabled") is True:
        for i in range(idx + 1, end):
            if lines[i].strip() == "shutdown":
                lines[i] = " no shutdown"
                break
        else:
            if not any(lines[i].strip() == "no shutdown" for i in range(idx + 1, end)):
                lines.insert(end, " no shutdown")
        _, end = _block_span(lines, idx)
    if intent.get("mtu") is not None:
        _set_block_line(lines, idx, end, f" mtu {intent['mtu']}", "mtu ")


def _apply_ztp_device(lines: list[str], intent: dict[str, Any]) -> None:
    devices = intent.get("devices") or []
    if not devices:
        return
    hostname = f"hostname {devices[0]}"
    for i, line in enumerate(lines):
        if line.strip().startswith("hostname "):
            lines[i] = hostname
            return
    lines.insert(0, hostname)


_APPLIERS = {
    "vlan_add": _apply_vlan_add,
    "vlan_remove": _apply_vlan_remove,
    "acl_entry_add": _apply_acl_entry_add,
    "acl_entry_remove": _apply_acl_entry_remove,
    "static_route_add": _apply_static_route_add,
    "static_route_remove": _apply_static_route_remove,
    "interface_set": _apply_interface_set,
    "ztp_device": _apply_ztp_device,
}


def apply_intent_to_config(running: str, intent: dict[str, Any]) -> str:
    """Line-based applier: intent applied to the running config -> candidate config.

    Unknown change types are a no-op: the candidate is the running config.
    """
    lines = _split_lines(running)
    applier = _APPLIERS.get(str(intent.get("change_type", "")))
    if applier is not None:
        applier(lines, intent)
    return normalize_config("\n".join(lines))


def compute_diff(device: str, running: str, candidate: str) -> dict[str, Any]:
    """Running vs candidate diff: unified text plus structured per-line rows."""
    running_lines = _split_lines(running)
    candidate_lines = _split_lines(candidate)
    unified = list(
        difflib.unified_diff(
            running_lines,
            candidate_lines,
            fromfile=f"a/{device}.cfg",
            tofile=f"b/{device}.cfg",
            lineterm="",
        )
    )
    rows: list[dict[str, Any]] = []
    added = removed = 0
    old_no = new_no = 0
    matcher = difflib.SequenceMatcher(None, running_lines, candidate_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                old_no += 1
                new_no += 1
                rows.append(
                    {
                        "old_line": old_no,
                        "new_line": new_no,
                        "type": "context",
                        "text": running_lines[i1 + k],
                    }
                )
        elif tag == "delete":
            for k in range(i1, i2):
                old_no += 1
                removed += 1
                rows.append(
                    {
                        "old_line": old_no,
                        "new_line": None,
                        "type": "remove",
                        "text": running_lines[k],
                    }
                )
        elif tag == "insert":
            for k in range(j1, j2):
                new_no += 1
                added += 1
                rows.append(
                    {
                        "old_line": None,
                        "new_line": new_no,
                        "type": "add",
                        "text": candidate_lines[k],
                    }
                )
        elif tag == "replace":
            for k in range(i1, i2):
                old_no += 1
                removed += 1
                rows.append(
                    {
                        "old_line": old_no,
                        "new_line": None,
                        "type": "remove",
                        "text": running_lines[k],
                    }
                )
            for k in range(j1, j2):
                new_no += 1
                added += 1
                rows.append(
                    {
                        "old_line": None,
                        "new_line": new_no,
                        "type": "add",
                        "text": candidate_lines[k],
                    }
                )
    return {
        "device": device,
        "unified": unified,
        "lines": rows,
        "added": added,
        "removed": removed,
        "is_empty": added == 0 and removed == 0,
        "running_hash": config_hash(running),
        "candidate_hash": config_hash(candidate),
    }
