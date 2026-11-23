"""Stdin/stdout contract for the python runners (E2).

- read_request(): one JSON object from stdin.
- redact(): replaces credential values and secret-like key values with ***.
- emit(): prints VIREONET-RESULT-BEGIN / JSON / VIREONET-RESULT-END.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

_BEGIN = "VIREONET-RESULT-BEGIN"
_END = "VIREONET-RESULT-END"

_SECRET_ENV_VARS = (
    "DEVICE_PASSWORD",
    "DEVICE_ENABLE",
    "VIREONET_DEVICE_PASSWORD",
    "VIREONET_DEVICE_ENABLE",
)
_SECRET_KEY_RE = re.compile(
    r'(?P<prefix>"(?:[^"]*(?:secret|token|password|private_key|api_key)[^"]*)"\s*:\s*")'
    r'[^"]*'
    r'(?P<suffix>")',
    re.IGNORECASE,
)


def read_request() -> dict[str, Any]:
    """One JSON object from stdin. Raises ValueError on empty/bad input."""
    raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("empty stdin: expected a JSON RunnerRequest")
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"stdin is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("stdin JSON must be an object")
    return parsed


def redact(text: str) -> str:
    """Replace credential values and secret-like JSON values with ***."""
    out = text
    for var in _SECRET_ENV_VARS:
        value = os.environ.get(var)
        if value:
            out = out.replace(value, "***")
    return _SECRET_KEY_RE.sub(r"\g<prefix>***\g<suffix>", out)


def emit(result: dict[str, Any]) -> None:
    print(_BEGIN)
    print(json.dumps(result))
    print(_END)
