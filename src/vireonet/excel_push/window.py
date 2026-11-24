"""Planned change windows (from the ITSM ticket or the submitter)."""

from __future__ import annotations

from datetime import UTC, datetime


def parse_when(value: object) -> datetime | None:
    """ISO 8601, or ServiceNow's 'YYYY-MM-DD HH:MM:SS' (UTC value). Naive
    times are taken as UTC. Empty / unparseable -> None."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    for candidate in (text, text.replace(" ", "T", 1)):
        try:
            dt = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    for fmt in ("%d-%m-%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def iso(dt: datetime | None) -> str:
    return dt.astimezone(UTC).isoformat() if dt else ""
