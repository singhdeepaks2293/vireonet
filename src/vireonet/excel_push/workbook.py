"""The config-push workbook.

Sheet "config" (required): columns Device | Config. One row per device; the
Config cell holds the device's whole config block (multi-line). Rows with
an empty Device continue the previous device's block.

Sheet "backup" (optional input, always written back): columns Device |
Rollback config | Running config (pre-change) | Taken at.
- "Rollback config" is yours: what VireoNet pushes to undo the change if
  apply or post-check fails (preferred over Git and the AWX backup).
- "Running config (pre-change)" and "Taken at" are filled in by VireoNet at
  pre-check and the workbook is attached back to the ticket.

Header names are matched case-insensitively; extra columns and sheets are
ignored. Sheet names "Config" / "CONFIG" / "Backup" also work.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

HOSTNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
_CONFIG_HEADERS = {"device", "config"}
_BACKUP_COLS = ["Device", "Rollback config", "Running config (pre-change)", "Taken at"]


class WorkbookError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass
class PushWorkbook:
    configs: dict[str, str]
    rollback_configs: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> list[dict[str, Any]]:
        return [
            {
                "device": d,
                "config_lines": len([x for x in cfg.splitlines() if x.strip()]),
                "rollback_lines": len(
                    [x for x in self.rollback_configs.get(d, "").splitlines() if x.strip()]
                ),
                "config_preview": "\n".join(cfg.splitlines()[:12]),
            }
            for d, cfg in self.configs.items()
        ]


def _sheet(wb: Any, name: str) -> Any | None:
    for ws in wb.worksheets:
        if ws.title.strip().lower() == name:
            return ws
    return None


def _header_map(ws: Any) -> tuple[int, dict[str, int]]:
    """Row number of the header and column index by lower-cased name."""
    for row in ws.iter_rows(min_row=1, max_row=10):
        names = {str(c.value).strip().lower(): c.column - 1 for c in row if c.value is not None}
        if "device" in names:
            return row[0].row, names
    return 0, {}


def _blocks(
    ws: Any, value_col: str, errors: list[str], sheet: str, max_bytes: int
) -> dict[str, str]:
    header_row, cols = _header_map(ws)
    if not header_row or value_col not in cols:
        errors.append(f'sheet "{sheet}": needs header columns "Device" and "{value_col.title()}"')
        return {}
    out: dict[str, list[str]] = {}
    order: list[str] = []
    current: str | None = None
    for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
        dev_raw = row[cols["device"]] if cols["device"] < len(row) else None
        val_raw = row[cols[value_col]] if cols[value_col] < len(row) else None
        device = str(dev_raw).strip() if dev_raw not in (None, "") else ""
        value = "" if val_raw is None else str(val_raw)
        if device:
            if not HOSTNAME_RE.fullmatch(device):
                errors.append(f'sheet "{sheet}": invalid device name {device!r}')
                current = None
                continue
            if device in out:
                errors.append(
                    f'sheet "{sheet}": device {device} appears twice (one row per device)'
                )
                current = None
                continue
            current = device
            out[device] = []
            order.append(device)
        elif not value.strip():
            continue
        elif current is None:
            errors.append(f'sheet "{sheet}": config text with no device above it')
            continue
        out[current].extend(value.replace("\r\n", "\n").replace("\r", "\n").split("\n"))
    result = {}
    for d in order:
        text = "\n".join(line.rstrip() for line in out[d]).strip("\n")
        if any(ord(c) < 32 and c not in "\n\t" for c in text):
            errors.append(f'sheet "{sheet}": {d}: contains control characters')
        if len(text.encode()) > max_bytes:
            errors.append(f'sheet "{sheet}": {d}: config is larger than {max_bytes} bytes')
        result[d] = text
    return result


def parse_workbook(
    content: bytes, *, max_bytes: int = 2_000_000, max_device_bytes: int = 200_000
) -> PushWorkbook:
    """Parse and validate. Raises WorkbookError with every problem found."""
    if len(content) > max_bytes:
        raise WorkbookError([f"workbook is larger than {max_bytes} bytes"])
    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 — anything unreadable is a user error
        raise WorkbookError([f"not a readable .xlsx workbook: {exc.__class__.__name__}"]) from exc
    errors: list[str] = []
    warnings: list[str] = []
    cfg_ws = _sheet(wb, "config")
    if cfg_ws is None:
        raise WorkbookError(['no sheet named "config"'])
    configs = _blocks(cfg_ws, "config", errors, "config", max_device_bytes)
    for d, text in configs.items():
        if not text.strip():
            errors.append(f'sheet "config": {d}: config is empty')
    if not configs and not errors:
        errors.append('sheet "config": no devices found')
    rollback: dict[str, str] = {}
    bk_ws = _sheet(wb, "backup")
    if bk_ws is None:
        warnings.append('no "backup" sheet: rollback uses Git / the AWX pre-change backup')
    else:
        _, cols = _header_map(bk_ws)
        if "rollback config" in cols:
            rollback = {
                d: t
                for d, t in _blocks(
                    bk_ws, "rollback config", errors, "backup", max_device_bytes
                ).items()
                if t.strip()
            }
            unknown = sorted(set(rollback) - set(configs))
            if unknown:
                errors.append(
                    f'sheet "backup": rollback config for devices not in "config": {unknown}'
                )
        else:
            warnings.append(
                '"backup" sheet has no "Rollback config" column: rollback uses Git / the AWX backup'
            )
    for d in configs:
        if d not in rollback:
            warnings.append(
                f"{d}: no rollback config in the workbook; rollback uses Git / the AWX backup"
            )
    wb.close()
    if errors:
        raise WorkbookError(errors)
    return PushWorkbook(configs, rollback, warnings)


# --- Writing --------------------------------------------------------------------

_HEAD = Font(bold=True)
_FILL = PatternFill("solid", fgColor="DCF0FB")
_WRAP = Alignment(wrap_text=True, vertical="top")


def _style_header(ws: Any) -> None:
    for c in ws[1]:
        c.font, c.fill = _HEAD, _FILL
    ws.freeze_panes = "A2"


def build_workbook(
    configs: dict[str, str],
    rollback: dict[str, str] | None = None,
    running: dict[str, tuple[str, str]] | None = None,
) -> bytes:
    """A workbook with "config" and "backup" sheets (running: device -> (config, taken_at))."""
    wb = Workbook()
    ws = wb.active
    ws.title = "config"
    ws.append(["Device", "Config"])
    for d, cfg in configs.items():
        ws.append([d, cfg])
    _style_header(ws)
    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["B"].width = 90
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = _WRAP
    bk = wb.create_sheet("backup")
    bk.append(_BACKUP_COLS)
    for d in configs:
        run, taken = (running or {}).get(d, ("", ""))
        bk.append([d, (rollback or {}).get(d, ""), run, taken])
    _style_header(bk)
    for col, width in zip("ABCD", (18, 60, 90, 22), strict=True):
        bk.column_dimensions[col].width = width
    for row in bk.iter_rows(min_row=2):
        for c in row:
            c.alignment = _WRAP
    readme = wb.create_sheet("readme")
    for line in (
        ["VireoNet config push workbook"],
        [
            'Sheet "config": one row per device. Device = NetBox hostname. Config = the full config block to push (multi-line cell).'
        ],
        [
            'Sheet "backup": Rollback config = what to push to undo the change if it fails (optional).'
        ],
        [
            "VireoNet fills Running config (pre-change) and Taken at during pre-check and attaches the workbook back to the ticket."
        ],
        [
            "Every push goes through approval, locks, backup, dry-run, waves, post-checks and automatic rollback."
        ],
    ):
        readme.append(line)
    readme.column_dimensions["A"].width = 140
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def write_backups(original: bytes, running: dict[str, tuple[str, str]]) -> bytes:
    """The original workbook with the backup sheet's Running config / Taken at
    filled in (the sheet and columns are added when missing). Other sheets,
    and the user's Rollback config, are left as they were."""
    wb = load_workbook(io.BytesIO(original))
    bk = _sheet(wb, "backup")
    if bk is None:
        bk = wb.create_sheet("backup")
        bk.append(_BACKUP_COLS)
        _style_header(bk)
    header_row, cols = _header_map(bk)
    if not header_row:
        bk.insert_rows(1)
        for i, name in enumerate(_BACKUP_COLS, start=1):
            bk.cell(row=1, column=i, value=name)
        header_row, cols = _header_map(bk)
    for name in ("Rollback config", "Running config (pre-change)", "Taken at"):
        if name.lower() not in cols:
            col = bk.max_column + 1
            bk.cell(row=header_row, column=col, value=name).font = _HEAD
            cols[name.lower()] = col - 1
    rows = {
        str(bk.cell(row=r, column=cols["device"] + 1).value or "").strip(): r
        for r in range(header_row + 1, bk.max_row + 1)
    }
    for device, (config, taken) in running.items():
        r = rows.get(device)
        if r is None:
            r = bk.max_row + 1
            bk.cell(row=r, column=cols["device"] + 1, value=device)
        cell = bk.cell(row=r, column=cols["running config (pre-change)"] + 1, value=config)
        cell.alignment = _WRAP
        bk.cell(row=r, column=cols["taken at"] + 1, value=taken)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def demo_workbook() -> bytes:
    """Sample workbook for fake mode and the downloadable template."""
    return build_workbook(
        {
            "leaf-01": "vlan 300\n name excel-demo\ninterface GigabitEthernet1/0/30\n switchport mode access\n switchport access vlan 300",
            "leaf-03": "vlan 300\n   name excel-demo\ninterface Ethernet30\n   switchport access vlan 300",
        },
        {
            "leaf-01": "interface GigabitEthernet1/0/30\n no switchport access vlan 300\nno vlan 300",
            "leaf-03": "interface Ethernet30\n   no switchport access vlan 300\nno vlan 300",
        },
    )


def now_str() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
