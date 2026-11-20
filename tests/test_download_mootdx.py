from contextlib import nullcontext
from pathlib import Path

import pandas as pd
import pytest

import scripts.download_mootdx as download_mootdx
from scripts.download_mootdx import MootdxDownloader, _latest_stock_coverage
from simtradedata.writers.duckdb_writer import DuckDBWriter


@pytest.fixture(autouse=True)
def global_data_sources(monkeypatch):
    class Router:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get_trade_calendar(self, *_args):
            return pd.DataFrame({"calendar_date": ["2026-06-25"], "is_trading_day": ["1"]})

        def get_index_data(self, *_args):
            return pd.DataFrame({"close": [1.0]})

    monkeypatch.setattr(download_mootdx, "SmartRouter", Router)


class FakeWriter:
    def __init__(self):
        self.exrights_written = None

    def get_max_date(self, table, _symbol):
        if table == "stocks":
            return "2026-06-23"
        if table == "exrights":
            return None
        return None

    def write_market_data(self, *_args):
        raise AssertionError("market data should not be written")

    def write_exrights(self, symbol, exrights):
        self.exrights_written = (symbol, exrights)


class FakeUnifiedFetcher:
    def __init__(self):
        self.xdxr_called = False

    def fetch_daily_data(self, *_args):
        raise KeyError("datetime")

    def fetch_xdxr(self, _symbol):
        self.xdxr_called = True
        return pd.DataFrame(
            {
                "category": [1],
                "year": [2026],
                "month": [6],
                "day": [18],
                "songzhuangu": [0.0],
                "peigu": [0.0],
                "peigujia": [0.0],
                "fenhong": [1.0],
            }
        )


def test_download_stock_data_still_fetches_xdxr_when_ohlcv_fails():
    downloader = object.__new__(MootdxDownloader)
    downloader.writer = FakeWriter()
    downloader.unified_fetcher = FakeUnifiedFetcher()
    downloader.failed_stocks = []
    downloader._reconnect = lambda: None

    downloaded = downloader.download_stock_data(
        "600000.SS", "2026-01-01", "2026-06-25"
    )

    assert downloaded is True
    assert downloader.unified_fetcher.xdxr_called is True
    assert downloader.writer.exrights_written is not None


def test_download_stock_data_can_skip_ohlcv_and_fetch_xdxr():
    downloader = object.__new__(MootdxDownloader)
    downloader.writer = FakeWriter()
    downloader.unified_fetcher = FakeUnifiedFetcher()
    downloader.failed_stocks = []
    downloader._reconnect = lambda: None

    downloaded = downloader.download_stock_data(
        "600000.SS",
        "2026-01-01",
        "2026-06-25",
        include_ohlcv=False,
    )

    assert downloaded is True
    assert downloader.unified_fetcher.xdxr_called is True
    assert downloader.writer.exrights_written is not None


def test_default_download_repairs_missing_and_sparse_exrights_without_ohlcv(
    monkeypatch, tmp_path
):
    symbols = [f"00000{i}.SZ" for i in range(1, 6)]
    fetched = []
    batches = []
    db_path = tmp_path / "cn.duckdb"

    class Fetcher:
        def login(self):
            pass

        def logout(self):
            pass

        def fetch_stock_list(self):
            return symbols

        def fetch_daily_data(self, *args):
            pytest.fail("OHLCV must remain skipped")

        def fetch_xdxr(self, symbol):
            fetched.append(symbol)
            return pd.DataFrame({
                "category": [1, 1, 1],
                "year": [2026, 2026, 2026],
                "month": [1, 1, 1],
                "day": [1, 2, 3],
                "songzhuangu": [0.0, 0.0, 0.0],
                "peigu": [0.0, 0.0, 0.0],
                "peigujia": [0.0, 0.0, 0.0],
                "fenhong": [1.0, 1.0, 1.0],
            })

    class Downloader(MootdxDownloader):
        def __init__(self, db_path, **kwargs):
            existing = Path(db_path).exists()
            self.writer = DuckDBWriter(db_path)
            self.unified_fetcher = Fetcher()
            self.failed_stocks = []
            # The global-data fixture has no benchmark date; this test covers XDXR.
            self.writer.write_benchmark = lambda df: None
            if not existing:
                for count, symbol in enumerate(symbols):
                    self.writer.conn.executemany(
                        "INSERT INTO stocks (symbol, date) VALUES (?, ?)",
                        [(symbol, "2026-01-05"), (symbol, "2026-01-06")],
                    )
                    if count:
                        self.writer.write_exrights(symbol, pd.DataFrame({
                            "date": pd.date_range("2026-01-01", periods=count),
                            "bonus_ps": [0.1] * count,
                        }))
                self.writer.write_exrights("999999.SZ", pd.DataFrame({
                    "date": ["2026-01-01"], "bonus_ps": [0.1],
                }))

        def download_batch(self, batch, start, end, pbar, include_ohlcv=True):
            batches.append((list(batch), include_ohlcv))
            successes = 0
            for symbol in batch:
                successes += self.download_stock_data(
                    symbol, start, end, include_ohlcv=include_ohlcv
                )
                pbar.update(1)
            return successes

    monkeypatch.setattr(download_mootdx, "ProcessLock", lambda path: nullcontext())
    monkeypatch.setattr(download_mootdx, "MootdxDownloader", Downloader)
    monkeypatch.setattr(download_mootdx, "DEFAULT_DB_PATH", str(db_path))

    download_mootdx.download_all_data(skip_fundamentals=True, skip_bonus_fix=True)
    assert fetched == symbols[:3]
    assert batches == [(symbols[:3], False)]

    download_mootdx.download_all_data(skip_fundamentals=True, skip_bonus_fix=True)
    assert fetched == symbols[:3]
    assert batches == [(symbols[:3], False)]
    with DuckDBWriter(str(db_path)) as stored:
        assert stored.conn.execute(
            "SELECT symbol, COUNT(*) FROM exrights GROUP BY symbol ORDER BY symbol"
        ).fetchall() == [
            (symbols[0], 3), (symbols[1], 3), (symbols[2], 3),
            (symbols[3], 3), (symbols[4], 4), ("999999.SZ", 1),
        ]
        assert stored.conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0] == 10


def test_latest_stock_coverage_counts_only_current_stock_pool_symbols():
    latest_count, total_count, ratio = _latest_stock_coverage(
        {"000001.SZ", "600000.SS", "510300.SS"},
        ["000001.SZ", "000002.SZ", "600000.SS"],
    )

    assert latest_count == 2
    assert total_count == 3
    assert ratio == 2 / 3


def test_skip_ohlcv_empty_stock_list_still_runs_remaining_phase(monkeypatch, tmp_path):
    class DummyLock:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class FakeResult:
        def fetchall(self):
            return []

    class FakeConn:
        def execute(self, *_args, **_kwargs):
            return FakeResult()

    class FakeWriter:
        def __init__(self):
            self.conn = FakeConn()
            self.trade_days_written = False
            self.benchmark_written = False

        def get_max_date(self, _table):
            return "2026-06-24"

        def write_trade_days(self, _df):
            self.trade_days_written = True

        def write_benchmark(self, _df):
            self.benchmark_written = True

        def close(self):
            pass

    class EmptyStockListFetcher:
        def __init__(self):
            self.logged_out = False

        def login(self):
            pass

        def logout(self):
            self.logged_out = True

        def fetch_stock_list(self):
            return []

        def fetch_trade_calendar(self, *_args):
            return pd.DataFrame(
                {
                    "calendar_date": ["2026-06-25"],
                    "is_trading_day": ["1"],
                }
            )

        def fetch_index_data(self, *_args):
            return pd.DataFrame({"close": [1.0]})

    class FakeDownloader:
        last = None

        def __init__(self, **_kwargs):
            FakeDownloader.last = self
            self.writer = FakeWriter()
            self.unified_fetcher = EmptyStockListFetcher()
            self.failed_stocks = []
            self.fixed_exrights = False
            self.downloaded_fundamentals = False

        def fix_exrights_precision(self):
            self.fixed_exrights = True

        def download_fundamentals_batch(self, *_args, **_kwargs):
            self.downloaded_fundamentals = True

    monkeypatch.setattr(download_mootdx, "ProcessLock", DummyLock)
    monkeypatch.setattr(download_mootdx, "MootdxDownloader", FakeDownloader)
    monkeypatch.setattr(download_mootdx, "DEFAULT_DB_PATH", str(tmp_path / "cn.duckdb"))

    download_mootdx.download_all_data(skip_ohlcv=True)

    downloader = FakeDownloader.last
    assert downloader is not None
    assert downloader.fixed_exrights is True
    assert downloader.downloaded_fundamentals is True
    assert downloader.writer.trade_days_written is True
    assert downloader.writer.benchmark_written is True


def test_skip_bonus_fix_does_not_call_baostock_correction(monkeypatch, tmp_path):
    class DummyLock:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class FakeResult:
        def fetchall(self):
            return []

        def fetchone(self):
            return [0]

    class FakeConn:
        def execute(self, *_args, **_kwargs):
            return FakeResult()

    class FakeWriter:
        def __init__(self):
            self.conn = FakeConn()

        def get_max_date(self, _table):
            return "2026-06-24"

        def write_trade_days(self, _df):
            pass

        def write_benchmark(self, _df):
            pass

        def close(self):
            pass

    class EmptyStockListFetcher:
        def login(self):
            pass

        def logout(self):
            pass

        def fetch_stock_list(self):
            return []

        def fetch_trade_calendar(self, *_args):
            return pd.DataFrame()

        def fetch_index_data(self, *_args):
            return pd.DataFrame()

    class FakeDownloader:
        last = None

        def __init__(self, **_kwargs):
            FakeDownloader.last = self
            self.writer = FakeWriter()
            self.unified_fetcher = EmptyStockListFetcher()
            self.failed_stocks = []
            self.fixed_exrights = False

        def fix_exrights_precision(self):
            self.fixed_exrights = True

        def download_fundamentals_batch(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr(download_mootdx, "ProcessLock", DummyLock)
    monkeypatch.setattr(download_mootdx, "MootdxDownloader", FakeDownloader)
    monkeypatch.setattr(download_mootdx, "DEFAULT_DB_PATH", str(tmp_path / "cn.duckdb"))

    download_mootdx.download_all_data(skip_ohlcv=True, skip_bonus_fix=True)

    assert FakeDownloader.last.fixed_exrights is False
