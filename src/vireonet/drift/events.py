"""Event-triggered drift scans (D3).

POST /drift/events is authenticated by a shared token or an HMAC
signature — never by JWT — and fails closed: with no secret configured
the endpoint returns 401. Repeated events for the same device inside
the debounce window collapse into one scan.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException

DEBOUNCE_WINDOW = timedelta(minutes=5)


def verify_event_token(headers: Mapping[str, str], body: bytes, secret: str) -> None:
    """Verify a drift-event call. Raises HTTPException(401) on failure.

    Accepts either:
    - X-VireoNet-Event-Token: the shared secret, compared in constant time.
    - X-VireoNet-Signature: HMAC-SHA256 over the raw body, optionally
      prefixed with ``sha256=``.
    No secret configured is fail-closed: every call is rejected.
    """
    if not secret:
        raise HTTPException(
            status_code=401, detail="drift event authentication required: no secret configured"
        )
    token = headers.get("X-VireoNet-Event-Token", "")
    if token and hmac.compare_digest(token, secret):
        return
    signature = headers.get("X-VireoNet-Signature", "")
    if signature:
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if hmac.compare_digest(signature.removeprefix("sha256="), expected):
            return
        raise HTTPException(status_code=401, detail="bad drift event signature")
    raise HTTPException(status_code=401, detail="drift event authentication required")


class DebouncedScanner:
    """Collapse repeated scan triggers for one device into one scan.

    In-memory device -> last scan time; a trigger inside the 5-minute
    window is debounced (should_scan returns False and records nothing).
    This is a process-local guard for event storms, not durable state.
    """

    def __init__(self, window: timedelta = DEBOUNCE_WINDOW) -> None:
        self._window = window
        self._last_scan: dict[str, datetime] = {}

    def should_scan(self, device: str, now: datetime | None = None) -> bool:
        at = now or datetime.now(UTC)
        last = self._last_scan.get(device)
        if last is not None and at - last < self._window:
            return False
        self._last_scan[device] = at
        return True
