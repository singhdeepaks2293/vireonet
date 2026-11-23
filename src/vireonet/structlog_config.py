from __future__ import annotations

import structlog

_SECRET_KEYS = {"password", "token", "secret", "api_key", "apikey", "authorization", "vault_token"}


def redact(value: object) -> object:
    """Recursively mask secret-looking keys. Never log full payloads."""
    if isinstance(value, dict):
        return {
            str(k): "***" if str(k).lower() in _SECRET_KEYS else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def configure_logging(level: str) -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
