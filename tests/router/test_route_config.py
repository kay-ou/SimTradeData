from simtradedata.router.route_config import (
    DEFAULT_ROUTE_TABLE,
    FETCHER_REGISTRY,
)


class TestRouteConfig:
    def test_all_sources_in_route_table_are_registered(self):
        """Every source name referenced in the route table must exist in FETCHER_REGISTRY."""
        for data_type, markets in DEFAULT_ROUTE_TABLE.items():
            for market, sources in markets.items():
                for source in sources:
                    assert source in FETCHER_REGISTRY, (
                        f"Source '{source}' in {data_type}/{market} "
                        f"not found in FETCHER_REGISTRY"
                    )

    def test_daily_bars_cn_has_fallback(self):
        sources = DEFAULT_ROUTE_TABLE["daily_bars"]["cn"]
        assert len(sources) >= 2, "daily_bars/cn should have fallback sources"
        assert sources == ["eastmoney", "baostock"]

    def test_mootdx_keeps_non_daily_interfaces(self):
        for data_type in [
            "xdxr",
            "fundamentals",
            "stock_list",
            "realtime_quotes",
            "minute_bars",
        ]:
            assert DEFAULT_ROUTE_TABLE[data_type]["cn"] == ["mootdx"]

    def test_calendar_and_daily_index_do_not_depend_on_mootdx(self):
        assert DEFAULT_ROUTE_TABLE["trade_calendar"]["cn"] == ["baostock"]
        assert DEFAULT_ROUTE_TABLE["index_data"]["cn"] == ["baostock"]

    def test_daily_bars_us_uses_yfinance(self):
        sources = DEFAULT_ROUTE_TABLE["daily_bars"]["us"]
        assert sources == ["yfinance"]

    def test_money_flow_only_eastmoney(self):
        sources = DEFAULT_ROUTE_TABLE["money_flow"]["cn"]
        assert sources == ["eastmoney"]

    def test_fetcher_registry_has_import_paths(self):
        for name, path in FETCHER_REGISTRY.items():
            assert "." in path, f"Registry entry '{name}' must be a dotted import path"
            parts = path.rsplit(".", 1)
            assert len(parts) == 2, f"Registry entry '{name}' must be module.ClassName"
