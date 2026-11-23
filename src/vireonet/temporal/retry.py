from __future__ import annotations

from datetime import timedelta

from temporalio.common import RetryPolicy

# Temporal retries by ApplicationError type, which for activities is the
# exception class name. Auth / not-found / conflict / validation must never
# be retried — retrying them only burns quota and hides a real failure.
NON_RETRYABLE_ERROR_TYPES = [
    "AdapterAuthError",
    "AdapterNotFound",
    "AdapterConflict",
    "AdapterValidationError",
    "ValueError",
]
RETRYABLE_ERROR_TYPES = ["AdapterTimeout", "AdapterUnavailable", "AdapterRateLimited"]


def retry_policy(*, maximum_attempts: int = 5) -> RetryPolicy:
    return RetryPolicy(
        initial_interval=timedelta(seconds=1),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(seconds=60),
        maximum_attempts=maximum_attempts,
        non_retryable_error_types=NON_RETRYABLE_ERROR_TYPES,
    )


def is_retryable_error_type(error_type: str) -> bool:
    return error_type in RETRYABLE_ERROR_TYPES
