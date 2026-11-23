from __future__ import annotations


class AdapterError(Exception):
    """Base for all adapter failures. Workflows decide retry vs fail from the type."""

    retryable: bool = False

    def __init__(self, adapter: str, operation: str, message: str) -> None:
        super().__init__(f"{adapter}.{operation}: {message}")
        self.adapter = adapter
        self.operation = operation
        self.message = message


class AdapterAuthError(AdapterError):
    retryable = False  # Refresh once in BaseAdapter; a second failure is fatal.


class AdapterNotFound(AdapterError):
    retryable = False


class AdapterConflict(AdapterError):
    retryable = False


class AdapterRateLimited(AdapterError):
    retryable = True


class AdapterTimeout(AdapterError):
    retryable = True


class AdapterUnavailable(AdapterError):
    retryable = True


class AdapterValidationError(AdapterError):
    retryable = False
