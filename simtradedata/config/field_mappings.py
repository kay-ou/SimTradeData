"""
Centralized field mapping configurations

This module contains all field mapping definitions from data sources to PTrade format.
Supports: BaoStock, mootdx
"""

# BaoStock -> PTrade field mappings

MARKET_FIELD_MAP = {
    "date": "date",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "preclose": "preclose",
    "volume": "volume",
    "amount": "money",  # BaoStock 'amount' -> PTrade 'money'
}

# Mootdx market data field mapping (mootdx -> PTrade)
MOOTDX_MARKET_FIELD_MAP = {
    "datetime": "date",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "vol": "volume",
    "amount": "money",
}

VALUATION_FIELD_MAP = {
    "peTTM": "pe_ttm",
    "pbMRQ": "pb",
    "psTTM": "ps_ttm",
    "pcfNcfTTM": "pcf",
    "turn": "turnover_rate",
}

FUNDAMENTAL_FIELD_MAP = {
    # From profit data
    "roeAvg": "roe",
    "roa": "roa",
    "npMargin": "net_profit_ratio",
    "gpMargin": "gross_income_ratio",
    # From balance data
    "currentRatio": "current_ratio",
    "quickRatio": "quick_ratio",
    "liabilityToAsset": "debt_equity_ratio",
    # From operation data
    "ARTurnRatio": "accounts_receivables_turnover_rate",
    "INVTurnRatio": "inventory_turnover_rate",
    "TATurnRatio": "total_asset_turnover_rate",
    "CATurnRatio": "current_assets_turnover_rate",
    # From growth data
    "YOYORev": "operating_revenue_grow_rate",
    "YOYNI": "net_profit_grow_rate",
    "YOYAsset": "total_asset_grow_rate",
    "YOYEPSBasic": "basic_eps_yoy",
    "YOYPNI": "np_parent_company_yoy",
    # From cash flow data
    "ebitToInterest": "interest_cover",
}

# Mootdx XDXR field mapping (for ex-rights/ex-dividend data)
MOOTDX_XDXR_FIELD_MAP = {
    "datetime": "date",
    "songzhuangu": "bonus_ps",      # Song zhuan gu (bonus shares)
    "peigu": "rationed_ps",          # Pei gu (rationed shares)
    "peigujia": "rationed_px",       # Pei gu price
    "fenhong": "dividend",           # Cash dividend
}

# Data routing configuration for DataSplitter
DATA_ROUTING = {
    'market': {
        'target_table': 'stocks',
        'fields': [
            'date', 'open', 'high', 'low', 'close', 'preclose', 'volume', 'amount',
        ],
        'rename': {
            'amount': 'money',
        },
    },
    'valuation': {
        'target_table': 'valuation',
        'fields': [
            'date', 'peTTM', 'pbMRQ', 'psTTM', 'pcfNcfTTM', 'turn',
        ],
        'rename': {
            'peTTM': 'pe_ttm',
            'pbMRQ': 'pb',
            'psTTM': 'ps_ttm',
            'pcfNcfTTM': 'pcf',
            'turn': 'turnover_rate',
        },
    },
    'status': {
        'target_table': None,  # Stored in memory
        'fields': ['date', 'isST', 'tradestatus'],
        'rename': {}
    }
}

# Benchmark configuration
BENCHMARK_CONFIG = {
    'default_index': '000300.SS',  # CSI300 (沪深300)
    'alternatives': {
        '000016.SS': 'SSE50',      # 上证50
        '000905.SS': 'CSI500',     # 中证500
        '000001.SS': 'SSE',        # 上证指数
        '399001.SZ': 'SZSE',       # 深证成指
        '399006.SZ': 'CYBZ',       # 创业板指
    }
}

# Benchmark history floor for CN exports: the exported benchmark must cover
# dates back to at least this value. TDX index bars are depth-limited, so a
# truncated export fails the gate instead of shipping silently.
# Value matches the documented package contract:
# docs/PTRADE_PARQUET_FORMAT.md (version.parquet start_date = 2015-01-01)
BENCHMARK_HISTORY_FLOOR = '2015-01-01'

# Minimum share of CN stock_metadata rows that must carry industry blocks,
# enforced by the pre-release integrity gate and the repair job's target.
BLOCKS_COVERAGE_FLOOR = 0.95


def benchmark_history_ok(start_text: str | None) -> bool:
    """True when the earliest benchmark date covers BENCHMARK_HISTORY_FLOOR."""
    return bool(start_text) and start_text <= BENCHMARK_HISTORY_FLOOR
