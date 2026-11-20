"""Tests for the request monitor module: stats tracking and health probing."""

import pytest

from simtradedata.resilience.monitor import RequestMonitor


@pytest.mark.unit
class TestRequestStats:
    """Tests for request statistics recording and retrieval."""

    def test_empty_stats(self):
        monitor = RequestMonitor()
        stats = monitor.get_stats("mootdx")
        assert stats["total"] == 0
        assert stats["success_rate"] == 0.0
        assert stats["avg_response_time"] == 0.0
        assert stats["min_response_time"] == 0.0
        assert stats["max_response_time"] == 0.0

    def test_record_success(self):
        monitor = RequestMonitor()
        monitor.record_request("mootdx", success=True, response_time=1.0)
        stats = monitor.get_stats("mootdx")
        assert stats["total"] == 1
        assert stats["successful"] == 1
        assert stats["success_rate"] == 1.0

    def test_record_failure(self):
        monitor = RequestMonitor()
        monitor.record_request(
            "mootdx", success=False, response_time=0.5, error="timeout",
        )
        stats = monitor.get_stats("mootdx")
        assert stats["total"] == 1
        assert stats["failed"] == 1
        assert stats["success_rate"] == 0.0

    def test_mixed_requests(self):
        monitor = RequestMonitor()
        monitor.record_request("mootdx", success=True, response_time=1.0)
        monitor.record_request("mootdx", success=True, response_time=2.0)
        monitor.record_request(
            "mootdx", success=False, response_time=3.0, error="server error",
        )
        stats = monitor.get_stats("mootdx")
        assert stats["total"] == 3
        assert stats["successful"] == 2
        assert stats["failed"] == 1
        assert stats["success_rate"] == pytest.approx(2 / 3)
        assert stats["avg_response_time"] == pytest.approx(2.0)
        assert stats["min_response_time"] == pytest.approx(1.0)
        assert stats["max_response_time"] == pytest.approx(3.0)

    def test_sources_are_independent(self):
        monitor = RequestMonitor()
        monitor.record_request("mootdx", success=True, response_time=1.0)
        monitor.record_request("yfinance", success=False, response_time=2.0)
        mootdx_stats = monitor.get_stats("mootdx")
        yfinance_stats = monitor.get_stats("yfinance")
        assert mootdx_stats["total"] == 1
        assert mootdx_stats["successful"] == 1
        assert yfinance_stats["total"] == 1
        assert yfinance_stats["failed"] == 1

    def test_get_all_stats(self):
        monitor = RequestMonitor()
        monitor.record_request("mootdx", success=True, response_time=1.0)
        monitor.record_request("yfinance", success=True, response_time=2.0)
        all_stats = monitor.get_all_stats()
        assert "mootdx" in all_stats
        assert "yfinance" in all_stats
        assert all_stats["mootdx"]["total"] == 1
        assert all_stats["yfinance"]["total"] == 1

    def test_stats_snapshot_stays_independent_of_later_requests(self):
        monitor = RequestMonitor()
        monitor.record_request("mootdx", success=True, response_time=2.0)
        snapshot = monitor.get_stats("mootdx")
        monitor.record_request("mootdx", success=False, response_time=0.0)
        current = monitor.get_stats("mootdx")

        assert snapshot["total"] == 1
        assert snapshot["min_response_time"] == snapshot["max_response_time"] == 2.0
        assert current["total"] == 2
        assert current["avg_response_time"] == 1.0
        assert current["min_response_time"] == 0.0
        assert current["max_response_time"] == 2.0


@pytest.mark.unit
class TestHealthProbe:
    """Tests for health probe registration and execution."""

    def test_register_and_probe(self):
        monitor = RequestMonitor()
        monitor.register_probe("mootdx", lambda: True)
        assert monitor.probe("mootdx") is True

    def test_probe_failure(self):
        monitor = RequestMonitor()
        monitor.register_probe("mootdx", lambda: False)
        assert monitor.probe("mootdx") is False

    def test_probe_all(self):
        monitor = RequestMonitor()
        monitor.register_probe("mootdx", lambda: True)
        monitor.register_probe("yfinance", lambda: False)
        results = monitor.probe_all()
        assert results["mootdx"] is True
        assert results["yfinance"] is False

    def test_probe_exception_counts_as_failure(self):
        def bad_probe():
            raise RuntimeError("connection refused")

        monitor = RequestMonitor()
        monitor.register_probe("mootdx", bad_probe)
        assert monitor.probe("mootdx") is False
