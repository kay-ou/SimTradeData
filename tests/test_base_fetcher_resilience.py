"""Tests for BaseFetcher resilience integration (cooldown, circuit breaker, monitor)."""

import pytest

from simtradedata.fetchers.base_fetcher import BaseFetcher
from simtradedata.resilience.circuit_breaker import CircuitBreaker, CircuitBreakerConfig


class DummyFetcher(BaseFetcher):
    """Minimal concrete subclass for testing BaseFetcher resilience."""

    source_name = "test_source"

    def _do_login(self):
        pass

    def _do_logout(self):
        pass


class FalseyTimeoutError(TimeoutError):
    def __bool__(self):
        return False


@pytest.mark.unit
class TestBaseFetcherResilience:
    """Tests for resilience integration in BaseFetcher._make_request."""

    def setup_method(self):
        self.fetcher = DummyFetcher()
        # Reset global singleton state so tests are independent.
        self.fetcher._cooldown._sources.clear()
        self.fetcher._monitor._stats.clear()
        self.fetcher._circuit_breaker = CircuitBreaker("test_source")

    def test_successful_request(self):
        result = self.fetcher._make_request(lambda: "ok")
        assert result == "ok"

    def test_records_success_in_monitor(self):
        self.fetcher._make_request(lambda: "ok")
        stats = self.fetcher._monitor.get_stats("test_source")
        assert stats["successful"] == 1

    def test_records_failure_in_monitor(self):
        with pytest.raises(ValueError):
            self.fetcher._make_request(self._raise_value_error)
        stats = self.fetcher._monitor.get_stats("test_source")
        assert stats["failed"] == 1

    def test_skips_when_in_cooldown(self):
        self.fetcher._cooldown.record_failure("test_source", "timeout")
        result = self.fetcher._make_request(lambda: "should not run")
        assert result is None

    def test_skips_when_circuit_open(self):
        for _ in range(5):
            self.fetcher._circuit_breaker.record_failure()
        result = self.fetcher._make_request(lambda: "should not run")
        assert result is None

    @pytest.mark.parametrize("use_context", [False, True])
    def test_request_paths_record_success_once(self, use_context):
        if use_context:
            with self.fetcher._resilient_operation() as available:
                assert available is True
        else:
            assert self.fetcher._make_request(lambda value: value, "ok") == "ok"

        stats = self.fetcher._monitor.get_stats("test_source")
        status = self.fetcher._cooldown.get_status("test_source")
        assert stats["total"] == stats["successful"] == 1
        assert stats["failed"] == 0
        assert status["total_requests"] == 1
        assert status["total_failures"] == status["consecutive_failures"] == 0
        assert status["is_in_cooldown"] is False

    @pytest.mark.parametrize("use_context", [False, True])
    @pytest.mark.parametrize(
        "error,error_type",
        [
            (TimeoutError("timed out"), "timeout"),
            (FalseyTimeoutError("timed out"), "timeout"),
            (ValueError("invalid data"), "default"),
        ],
    )
    def test_request_paths_record_failure_once_and_preserve_error(
        self, monkeypatch, caplog, use_context, error, error_type
    ):
        recorded_types = []
        record_failure = self.fetcher._cooldown.record_failure

        def record(source, category):
            recorded_types.append(category)
            record_failure(source, category)

        monkeypatch.setattr(self.fetcher._cooldown, "record_failure", record)
        self.fetcher._circuit_breaker = CircuitBreaker(
            "test_source", CircuitBreakerConfig(failure_threshold=1)
        )

        def fail():
            raise error

        with pytest.raises(type(error)) as caught:
            if use_context:
                with self.fetcher._resilient_operation() as available:
                    assert available is True
                    fail()
            else:
                self.fetcher._make_request(fail)

        assert caught.value is error
        assert recorded_types == [error_type]
        assert str(error) in caplog.text
        stats = self.fetcher._monitor.get_stats("test_source")
        status = self.fetcher._cooldown.get_status("test_source")
        assert stats["total"] == stats["failed"] == 1
        assert stats["successful"] == 0
        assert status["total_requests"] == status["total_failures"] == 1
        assert status["consecutive_failures"] == 1
        assert status["is_in_cooldown"] is True
        assert self.fetcher._circuit_breaker.is_available() is False

    # -- helpers --

    @staticmethod
    def _raise_value_error():
        raise ValueError("test error")
