from __future__ import annotations

from vireonet.temporal.retry import (
    NON_RETRYABLE_ERROR_TYPES,
    RETRYABLE_ERROR_TYPES,
    is_retryable_error_type,
    retry_policy,
)


def test_auth_and_validation_never_retried() -> None:
    assert "AdapterAuthError" in NON_RETRYABLE_ERROR_TYPES
    assert "AdapterValidationError" in NON_RETRYABLE_ERROR_TYPES
    assert "AdapterNotFound" in NON_RETRYABLE_ERROR_TYPES
    assert "AdapterTimeout" not in NON_RETRYABLE_ERROR_TYPES
    assert is_retryable_error_type("AdapterTimeout") is True
    assert is_retryable_error_type("AdapterAuthError") is False


def test_retry_policy_carries_non_retryable_list() -> None:
    p = retry_policy(maximum_attempts=3)
    assert p.maximum_attempts == 3
    assert set(p.non_retryable_error_types or []) >= set(NON_RETRYABLE_ERROR_TYPES)
    assert RETRYABLE_ERROR_TYPES  # documents the retryable set
