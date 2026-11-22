"""Run real UI update operations with deterministic, offline market sources."""

from pathlib import Path

import pandas as pd

from tests.ui_server import etfs
from simtradedata.fetchers.mootdx_fetcher import MootdxFetcher
from simtradedata.web import etf_catalogue, operations, etf_listing
from simtradedata.writers.duckdb_writer import DuckDBWriter

etf_catalogue.load_etfs = etfs
etf_listing.fetch_etf_listing_info = lambda *args: None
MootdxFetcher.__enter__ = lambda self: self
MootdxFetcher.__exit__ = lambda *args: None


def bars(self, symbol, start, end):
    dates = pd.date_range(start, end, freq='B')
    return pd.DataFrame({'date': dates, 'open': 1., 'high': 1.1, 'low': .9,
                         'close': 1., 'volume': 100., 'amount': 100.})


def calendar(data_dir, plan):
    with DuckDBWriter(str(Path(data_dir) / 'cn.duckdb')) as writer:
        for day in pd.date_range(plan['request']['start_date'], plan['request']['end_date']):
            writer.conn.execute('INSERT OR REPLACE INTO exchange_calendar VALUES (?,?)', [day.date(), day.weekday() < 5])
            if day.weekday() < 5:
                writer.conn.execute('INSERT OR IGNORE INTO trade_days VALUES (?)', [day.date()])
    return {'written': 0, 'failed_units': []}


MootdxFetcher.fetch_daily_bars = bars
operations.update_calendar = calendar

if __name__ == '__main__':
    raise SystemExit(operations.main())
