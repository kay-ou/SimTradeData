import pytest

from simtradedata.router.exceptions import NoSourceAvailable
from simtradedata.router.smart_router import SmartRouter


class TestDetectMarket:
    def test_shanghai_ss(self):
        router = SmartRouter()
        assert router._detect_market("600000.SS") == "cn"

    def test_shenzhen_sz(self):
        router = SmartRouter()
        assert router._detect_market("000001.SZ") == "cn"

    def test_us(self):
        router = SmartRouter()
        assert router._detect_market("AAPL.US") == "us"

    def test_unknown_suffix_raises(self):
        router = SmartRouter()
        with pytest.raises(ValueError, match="Unknown market suffix"):
            router._detect_market("1234.XX")


class TestResolveSources:
    def test_daily_bars_cn(self):
        router = SmartRouter()
        sources = router._resolve_sources("daily_bars", "cn")
        assert sources == ["mootdx", "eastmoney", "baostock"]

    def test_daily_bars_us(self):
        router = SmartRouter()
        sources = router._resolve_sources("daily_bars", "us")
        assert sources == ["yfinance"]

    def test_no_source_raises(self):
        router = SmartRouter()
        with pytest.raises(NoSourceAvailable):
            router._resolve_sources("minute_bars", "us")

    def test_unknown_data_type_raises(self):
        router = SmartRouter()
        with pytest.raises(NoSourceAvailable):
            router._resolve_sources("nonexistent", "cn")

    def test_custom_config(self):
        custom = {"daily_bars": {"cn": ["eastmoney"]}}
        router = SmartRouter(config=custom)
        assert router._resolve_sources("daily_bars", "cn") == ["eastmoney"]


class TestPublicAPISignatures:
    """Verify all public API methods exist and are callable."""

    METHODS = [
        "get_daily_bars", "get_xdxr",
        "get_money_flow", "get_lhb", "get_margin",
        "get_stock_list", "get_trade_calendar", "get_index_data",
        "get_realtime_quotes", "get_minute_bars",
        "get_fundamentals", "get_valuation",
    ]

    def test_all_methods_exist(self):
        router = SmartRouter()
        for method_name in self.METHODS:
            assert hasattr(router, method_name), f"Missing method: {method_name}"
            assert callable(getattr(router, method_name))


class TestModuleExports:
    def test_import_from_package(self):
        from simtradedata.router import SmartRouter, DataSourceError, NoSourceAvailable
        assert SmartRouter is not None
        assert issubclass(NoSourceAvailable, DataSourceError)
