"""Financial catalog and metadata must remain usable when daily bars fail."""

from types import SimpleNamespace

import pandas as pd

from simtradedata.fetchers.mootdx_unified_fetcher import MootdxUnifiedFetcher


def test_financial_data_needs_no_quote_connection():
    fetcher = object.__new__(MootdxUnifiedFetcher)

    def unavailable():
        raise ConnectionError("quote server unavailable")

    expected = pd.DataFrame({"symbol": ["600000.SS"], "end_date": ["2026-09-30"]})
    fetcher._quotes_fetcher = SimpleNamespace(login=unavailable, logout=lambda: None)
    fetcher._affair_fetcher = SimpleNamespace(
        fetch_fundamentals_for_quarter=lambda year, quarter: expected
    )
    with fetcher:
        assert fetcher.fetch_fundamentals_for_quarter(2026, 3) is expected
