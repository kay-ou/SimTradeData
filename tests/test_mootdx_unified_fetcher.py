from types import SimpleNamespace

import pandas as pd
import pytest

from simtradedata.fetchers.mootdx_unified_fetcher import MootdxUnifiedFetcher


def test_daily_data_preserves_available_fields_order_and_copy():
    df = pd.DataFrame(
        {"close": [11.0], "extra": [99], "date": ["2026-06-18"], "open": [10.0]}
    )
    fetcher = object.__new__(MootdxUnifiedFetcher)
    fetcher._quotes_fetcher = SimpleNamespace(fetch_daily_bars=lambda *args: df)

    result = fetcher.fetch_daily_data("600000.SS", "2026-06-18", "2026-06-25")

    assert result.columns.tolist() == ["date", "open", "close"]
    assert result["close"].tolist() == [11.0]
    result.loc[0, "open"] = 20.0
    assert df.loc[0, "open"] == 10.0


def test_daily_data_still_requires_date():
    fetcher = object.__new__(MootdxUnifiedFetcher)
    fetcher._quotes_fetcher = SimpleNamespace(
        fetch_daily_bars=lambda *args: pd.DataFrame({"close": [11.0]})
    )
    with pytest.raises(KeyError, match="date"):
        fetcher.fetch_daily_data("600000.SS", "2026-06-18", "2026-06-25")


def test_stock_list_preserves_stock_etf_filter_and_conversion():
    fetcher = object.__new__(MootdxUnifiedFetcher)
    fetcher._quotes_fetcher = SimpleNamespace(
        fetch_stock_list=lambda: pd.DataFrame(
            {"code": ["600000", "000001", "510300", "159915", "110001", "", "12345"]}
        )
    )

    assert MootdxUnifiedFetcher.fetch_stock_list.nocache(fetcher) == [
        "000001.SZ",
        "159915.SZ",
        "510300.SS",
        "600000.SS",
    ]
