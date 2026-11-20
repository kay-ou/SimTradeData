"""Route table and fetcher registry for SmartRouter."""

# Dotted import paths for lazy instantiation.
FETCHER_REGISTRY = {
    "mootdx": "simtradedata.fetchers.mootdx_unified_fetcher.MootdxUnifiedFetcher",
    "eastmoney": "simtradedata.fetchers.eastmoney_fetcher.EastMoneyFetcher",
    "baostock": "simtradedata.fetchers.unified_fetcher.UnifiedDataFetcher",
    "yfinance": "simtradedata.fetchers.yfinance_fetcher.YFinanceFetcher",
}

# data_type -> market -> [source priority list]
DEFAULT_ROUTE_TABLE = {
    "daily_bars": {
        "cn": ["eastmoney", "baostock"],
        "us": ["yfinance"],
    },
    "xdxr": {
        "cn": ["mootdx"],
    },
    "fundamentals": {
        "cn": ["mootdx"],
        "us": ["yfinance"],
    },
    "valuation": {
        "cn": ["baostock"],
        "us": ["yfinance"],
    },
    "money_flow": {
        "cn": ["eastmoney"],
    },
    "lhb": {
        "cn": ["eastmoney"],
    },
    "margin": {
        "cn": ["eastmoney"],
    },
    "stock_list": {
        "cn": ["mootdx"],
        "us": ["yfinance"],
    },
    "trade_calendar": {
        "cn": ["baostock"],
    },
    "index_data": {
        "cn": ["baostock"],
    },
    "realtime_quotes": {
        "cn": ["mootdx"],
    },
    "minute_bars": {
        "cn": ["mootdx"],
    },
}
