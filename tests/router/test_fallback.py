"""Integration tests for SmartRouter fallback behavior.

Uses real (stub) fetcher subclasses instead of mocks,
per project convention (CLAUDE.md: no mocks except external systems).
"""
import pandas as pd
import pytest
from types import SimpleNamespace

from simtradedata.router.exceptions import DataSourceError, NoSourceAvailable
from simtradedata.router.smart_router import SmartRouter


class StubFetcherOK:
    """Stub fetcher that returns valid data."""

    source_name = "stub_ok"

    def login(self):
        pass

    def logout(self):
        pass

    def fetch_daily_data(self, symbol, start_date, end_date):
        return pd.DataFrame({
            "date": ["2024-01-02"],
            "open": [10.0], "high": [11.0], "low": [9.0],
            "close": [10.5], "volume": [1000], "amount": [10500.0],
        })


class StubFetcherEmpty:
    """Stub fetcher that returns empty DataFrame."""

    source_name = "stub_empty"

    def login(self):
        pass

    def logout(self):
        pass

    def fetch_daily_data(self, symbol, start_date, end_date):
        return pd.DataFrame()


class StubFetcherFail:
    """Stub fetcher that raises an exception."""

    source_name = "stub_fail"

    def login(self):
        pass

    def logout(self):
        pass

    def fetch_daily_data(self, symbol, start_date, end_date):
        raise ConnectionError("stub connection error")


def _stub_fetch_from(fetcher, source_name):
    """Generic fetch_from for testing _try_fetch directly."""
    return fetcher.fetch_daily_data("600000.SS", "2024-01-01", "2024-12-31")


@pytest.mark.parametrize("first_result", ["error", "empty"])
def test_default_daily_query_falls_back_from_mootdx_to_baostock(first_result):
    calls = []

    def mootdx(*args):
        calls.append("mootdx")
        if first_result == "error":
            raise ConnectionError("TDX source unavailable")
        return pd.DataFrame()

    def eastmoney(*args):
        calls.append("eastmoney")
        raise ConnectionError("HTTP source unavailable")

    def baostock(*args):
        calls.append("baostock")
        return StubFetcherOK().fetch_daily_data(*args)

    router = SmartRouter()
    router._fetchers.update({
        "mootdx": SimpleNamespace(fetch_daily_data=mootdx),
        "eastmoney": SimpleNamespace(fetch_daily_bars=eastmoney),
        "baostock": SimpleNamespace(fetch_unified_daily_data=baostock),
    })
    result = router.get_daily_bars("600000.SS", "2024-01-01", "2024-01-02")
    assert calls == ["mootdx", "eastmoney", "baostock"]
    assert result.iloc[0]["close"] == 10.5


def test_default_daily_query_uses_mootdx_before_http_sources():
    router = SmartRouter()
    router._fetchers.update({
        "mootdx": StubFetcherOK(),
        "eastmoney": SimpleNamespace(
            fetch_daily_bars=lambda *args: pytest.fail("Unexpected fallback")
        ),
        "baostock": SimpleNamespace(
            fetch_unified_daily_data=lambda *args: pytest.fail("Unexpected fallback")
        ),
    })
    result = router.get_daily_bars("510300.SS", "2024-01-01", "2024-01-02")
    assert result.iloc[0]["close"] == 10.5


def test_default_daily_query_uses_eastmoney_when_mootdx_is_empty():
    router = SmartRouter()
    router._fetchers.update({
        "mootdx": StubFetcherEmpty(),
        "eastmoney": SimpleNamespace(
            fetch_daily_bars=StubFetcherOK().fetch_daily_data
        ),
        "baostock": SimpleNamespace(
            fetch_unified_daily_data=lambda *args: pytest.fail("Unexpected fallback")
        ),
    })
    result = router.get_daily_bars("510300.SS", "2024-01-01", "2024-01-02")
    assert result.iloc[0]["close"] == 10.5


class TestTryFetch:
    """Test the core _try_fetch fallback mechanism."""

    def _make_router(self, source_names):
        config = {"daily_bars": {"cn": source_names}}
        return SmartRouter(config=config)

    def _inject(self, router, name, fetcher):
        router._fetchers[name] = fetcher

    def test_first_source_succeeds(self):
        router = self._make_router(["s1"])
        self._inject(router, "s1", StubFetcherOK())
        df = router._try_fetch("daily_bars", _stub_fetch_from, symbol="600000.SS")
        assert not df.empty
        assert "close" in df.columns

    def test_fallback_on_failure(self):
        router = self._make_router(["s1", "s2"])
        self._inject(router, "s1", StubFetcherFail())
        self._inject(router, "s2", StubFetcherOK())
        df = router._try_fetch("daily_bars", _stub_fetch_from, symbol="600000.SS")
        assert not df.empty

    def test_fallback_on_empty(self):
        router = self._make_router(["s1", "s2"])
        self._inject(router, "s1", StubFetcherEmpty())
        self._inject(router, "s2", StubFetcherOK())
        df = router._try_fetch("daily_bars", _stub_fetch_from, symbol="600000.SS")
        assert not df.empty

    def test_all_empty_returns_empty(self):
        router = self._make_router(["s1"])
        self._inject(router, "s1", StubFetcherEmpty())
        df = router._try_fetch("daily_bars", _stub_fetch_from, symbol="600000.SS")
        assert df.empty

    def test_all_fail_raises(self):
        router = self._make_router(["s1"])
        self._inject(router, "s1", StubFetcherFail())
        with pytest.raises(DataSourceError, match="s1"):
            router._try_fetch("daily_bars", _stub_fetch_from, symbol="600000.SS")

    def test_no_source_for_market_raises(self):
        router = self._make_router(["s1"])
        self._inject(router, "s1", StubFetcherOK())
        with pytest.raises(NoSourceAvailable):
            router._try_fetch("daily_bars", _stub_fetch_from, symbol="AAPL.US")

    def test_circuit_breaker_skips_unhealthy(self):
        class StubWithOpenCB(StubFetcherFail):
            class _cb:
                @staticmethod
                def is_available():
                    return False
            _circuit_breaker = _cb()

        router = self._make_router(["s1", "s2"])
        self._inject(router, "s1", StubWithOpenCB())
        self._inject(router, "s2", StubFetcherOK())
        df = router._try_fetch("daily_bars", _stub_fetch_from, symbol="600000.SS")
        assert not df.empty


class StubFetcherFundamentals:
    """Stub fetcher with fundamentals support."""

    def login(self):
        pass

    def logout(self):
        pass

    def fetch_fundamentals_for_quarter(self, year, quarter):
        return pd.DataFrame({
            "end_date": [f"{year}-{quarter * 3:02d}-30"],
            "roe": [15.0],
        })

    def fetch_fundamentals(self, symbol):
        return pd.DataFrame({"date": ["2024-03-31"], "roe": [12.0]})


class StubFetcherValuation:
    """Stub fetcher with valuation support."""

    def login(self):
        pass

    def logout(self):
        pass

    def fetch_unified_daily_data(self, symbol, start_date, end_date):
        return pd.DataFrame({
            "date": ["2024-01-02"],
            "open": [10.0], "high": [11.0], "low": [9.0],
            "close": [10.5], "volume": [1000], "amount": [10500.0],
            "peTTM": [20.0], "pbMRQ": [3.5], "psTTM": [5.0],
            "pcfNcfTTM": [15.0], "turn": [1.2],
        })


class TestFundamentals:
    def test_quarterly_batch(self):
        config = {"fundamentals": {"cn": ["stub_fund"]}}
        router = SmartRouter(config=config)
        router._fetchers["stub_fund"] = StubFetcherFundamentals()
        df = router.get_fundamentals(year=2024, quarter=1)
        assert not df.empty
        assert "roe" in df.columns

    def test_per_stock(self):
        config = {"fundamentals": {"us": ["stub_fund"]}}
        router = SmartRouter(config=config)
        router._fetchers["stub_fund"] = StubFetcherFundamentals()
        df = router.get_fundamentals(symbol="AAPL.US")
        assert not df.empty

    def test_no_args_raises(self):
        router = SmartRouter()
        with pytest.raises(ValueError, match="Provide"):
            router.get_fundamentals()


class TestValuation:
    def test_baostock_extracts_valuation_cols(self):
        config = {"valuation": {"cn": ["baostock"]}}
        router = SmartRouter(config=config)
        router._fetchers["baostock"] = StubFetcherValuation()
        df = router.get_valuation("600000.SS", "2024-01-01", "2024-12-31")
        assert not df.empty
        assert "peTTM" in df.columns
        assert "open" not in df.columns
