"""Tests for the retry module: error classification and retry decorator."""

import pytest

from simtradedata.resilience.retry import (
    RetryConfig,
    is_retryable,
    retry,
)


@pytest.mark.unit
class TestErrorClassification:
    """Tests for the is_retryable() error classification function."""

    def test_timeout_is_retryable(self):
        assert is_retryable(TimeoutError("request timed out")) is True

    def test_connection_error_is_retryable(self):
        assert is_retryable(ConnectionError("connection failed")) is True

    @pytest.mark.parametrize(
        "error_type",
        [ConnectionResetError, ConnectionAbortedError, ConnectionRefusedError],
    )
    def test_connection_subclasses_are_retryable_without_message(self, error_type):
        assert is_retryable(error_type()) is True

    def test_non_retryable_type_takes_precedence_over_connection_error(self):
        class InvalidConnection(ValueError, ConnectionError):
            pass

        assert is_retryable(InvalidConnection("connection refused")) is False

    def test_value_error_not_retryable(self):
        assert is_retryable(ValueError("invalid value")) is False

    def test_key_error_not_retryable(self):
        assert is_retryable(KeyError("missing key")) is False

    def test_type_error_not_retryable(self):
        assert is_retryable(TypeError("wrong type")) is False

    def test_oserror_with_timeout_message_is_retryable(self):
        # OSError is not in either list, so keyword matching should apply.
        assert is_retryable(OSError("operation timed out")) is True

    def test_generic_exception_not_retryable(self):
        # Generic Exception without retryable keywords should not retry.
        assert is_retryable(Exception("something went wrong")) is False


@pytest.mark.unit
class TestRetryDecorator:
    """Tests for the retry() decorator factory."""

    def test_success_on_first_try(self):
        call_count = 0

        @retry(config=RetryConfig(max_retries=3))
        def succeed():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = succeed()
        assert result == "ok"
        assert call_count == 1

    def test_retries_on_retryable_error(self, monkeypatch):
        # Patch time.sleep to avoid real delays.
        monkeypatch.setattr("time.sleep", lambda _: None)
        call_count = 0

        @retry(config=RetryConfig(max_retries=3))
        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("connection lost")
            return "recovered"

        result = flaky()
        assert result == "recovered"
        assert call_count == 3

    def test_no_retry_on_non_retryable_error(self, monkeypatch):
        monkeypatch.setattr("time.sleep", lambda _: None)
        call_count = 0

        @retry(config=RetryConfig(max_retries=3))
        def bad_input():
            nonlocal call_count
            call_count += 1
            raise ValueError("invalid input")

        with pytest.raises(ValueError, match="invalid input"):
            bad_input()
        assert call_count == 1

    def test_raises_after_max_retries(self, monkeypatch):
        monkeypatch.setattr("time.sleep", lambda _: None)
        call_count = 0

        @retry(config=RetryConfig(max_retries=3))
        def always_fails():
            nonlocal call_count
            call_count += 1
            raise TimeoutError("request timed out")

        with pytest.raises(TimeoutError, match="request timed out"):
            always_fails()
        assert call_count == 3

    def test_backoff_increases_delay(self, monkeypatch):
        recorded_delays = []
        monkeypatch.setattr(
            "time.sleep", lambda d: recorded_delays.append(d)
        )

        config = RetryConfig(
            max_retries=4,
            base_delay=1.0,
            backoff_factor=2.0,
            jitter=0.0,
        )
        call_count = 0

        @retry(config=config)
        def always_timeout():
            nonlocal call_count
            call_count += 1
            raise TimeoutError("request timed out")

        with pytest.raises(TimeoutError):
            always_timeout()

        # With jitter=0 and base_delay=1.0, backoff_factor=2.0:
        # attempt 0 -> delay = 1.0 * 2^0 = 1.0
        # attempt 1 -> delay = 1.0 * 2^1 = 2.0
        # attempt 2 -> delay = 1.0 * 2^2 = 4.0
        assert recorded_delays == [1.0, 2.0, 4.0]
