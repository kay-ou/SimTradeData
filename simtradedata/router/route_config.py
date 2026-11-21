"""Route table and fetcher registry for SmartRouter."""

# Dotted import paths for lazy instantiation.
FETCHER_REGISTRY = {
    "etf_pcf": "simtradedata.fetchers.etf_pcf_fetcher.EtfPcfFetcher",
    "mootdx": "simtradedata.fetchers.mootdx_unified_fetcher.MootdxUnifiedFetcher",
    "eastmoney": "simtradedata.fetchers.eastmoney_fetcher.EastMoneyFetcher",
    "baostock": "simtradedata.fetchers.unified_fetcher.UnifiedDataFetcher",
    "yfinance": "simtradedata.fetchers.yfinance_fetcher.YFinanceFetcher",
}

# data_type -> market -> [source priority list]
DEFAULT_ROUTE_TABLE = {
    "etf_pcf": {"cn": ["etf_pcf"]},
    "daily_bars": {
        "cn": ["mootdx", "eastmoney", "baostock"],
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
    "blocks": {"cn": ["mootdx"]},
    "f10": {"cn": ["mootdx"]},
    "history_transactions": {"cn": ["mootdx"]},
    "history_minutes": {"cn": ["mootdx"]},
}
