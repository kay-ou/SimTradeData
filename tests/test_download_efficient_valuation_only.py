import pandas as pd

import scripts.download_efficient as download_efficient
from scripts.download_efficient import EfficientBaoStockDownloader


class FakeWriter:
    def __init__(self):
        self.valuation_writes = []
        self.status_writes = []

    def get_max_date(self, table, symbol=None):
        return None

    def begin(self):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass

    def write_valuation(self, symbol, df):
        self.valuation_writes.append((symbol, df.copy()))

    def write_stock_status(self, date, status_type, symbols):
        self.status_writes.append((date, status_type, symbols))


class FakeFetcher:
    def fetch_unified_daily_data(self, symbol, start_date, end_date):
        return pd.DataFrame(
            {
                "date": ["2026-06-24"],
                "peTTM": [10.0],
                "isST": [0],
                "tradestatus": [1],
            }
        )


class FakeSplitter:
    def __init__(self, split_data):
        self._split_data = split_data

    def split_data(self, df):
        return self._split_data


def make_valuation_only_downloader(split_data):
    downloader = EfficientBaoStockDownloader.__new__(EfficientBaoStockDownloader)
    downloader.valuation_only = True
    downloader.status_cache = {}
    downloader.failed_stocks = []
    downloader.writer = FakeWriter()
    downloader.unified_fetcher = FakeFetcher()
    downloader.data_splitter = FakeSplitter(split_data)
    return downloader


def test_valuation_only_batch_counts_written_valuation_as_updated():
    valuation = pd.DataFrame(
        {"pe_ttm": [10.0]},
        index=pd.to_datetime(["2026-06-24"]),
    )
    status = pd.DataFrame(
        {"date": ["2026-06-24"], "isST": [0], "tradestatus": [1]}
    )
    downloader = make_valuation_only_downloader(
        {"valuation": valuation, "status": status}
    )

    result = downloader.download_batch(
        ["000001.SZ"],
        "2015-01-01",
        "2026-06-24",
    )

    assert result == [{"stock_code": "000001.SZ"}]
    assert len(downloader.writer.valuation_writes) == 1
    assert downloader.status_cache["000001.SZ"].equals(status)


def test_valuation_only_downloads_single_missing_target_day():
    valuation = pd.DataFrame(
        {"pe_ttm": [10.0]},
        index=pd.to_datetime(["2026-06-24"]),
    )
    downloader = make_valuation_only_downloader(
        {"valuation": valuation, "status": pd.DataFrame()}
    )
    downloader.writer.get_max_date = lambda table, symbol=None: "2026-06-23"

    result = downloader.download_stock_data(
        "000001.SZ",
        "2015-01-01",
        "2026-06-24",
    )

    assert result == {"stock_code": "000001.SZ"}
    assert len(downloader.writer.valuation_writes) == 1


def test_valuation_only_batch_does_not_count_empty_split_as_updated():
    downloader = make_valuation_only_downloader(
        {
            "valuation": pd.DataFrame(),
            "status": pd.DataFrame(),
        }
    )

    result = downloader.download_batch(
        ["000001.SZ"],
        "2015-01-01",
        "2026-06-24",
    )

    assert result == []
    assert downloader.writer.valuation_writes == []
    assert downloader.status_cache == {}


def test_status_aggregation_groups_symbols_by_date_once():
    downloader = make_valuation_only_downloader({})
    downloader.status_cache = {
        "000001.SZ": pd.DataFrame(
            {
                "date": ["2026-06-23", "2026-06-24"],
                "isST": ["1", "0"],
                "tradestatus": ["1", "0"],
            }
        ),
        "600000.SS": pd.DataFrame(
            {
                "date": ["2026-06-23", "2026-06-24"],
                "isST": ["1", "0"],
                "tradestatus": ["0", "1"],
            }
        ),
    }

    downloader.aggregate_and_write_status()

    assert downloader.writer.status_writes == [
        ("20260623", "ST", ["000001.SZ", "600000.SS"]),
        ("20260624", "HALT", ["000001.SZ"]),
        ("20260623", "HALT", ["600000.SS"]),
    ]


def test_refresh_daily_stock_status_writes_target_day_halt_and_st(monkeypatch):
    class FakeResult:
        error_code = "0"
        error_msg = ""

        def get_data(self):
            return pd.DataFrame(
                {
                    "code": ["sz.000524", "sz.000638", "sh.600000"],
                    "tradeStatus": ["0", "1", "1"],
                    "code_name": ["岭南控股", "*ST万方", "浦发银行"],
                }
            )

    monkeypatch.setattr(
        download_efficient.bs,
        "query_all_stock",
        lambda day: FakeResult(),
    )
    downloader = make_valuation_only_downloader({})

    downloader.refresh_daily_stock_status("2026-06-25")

    assert downloader.writer.status_writes == [
        ("20260625", "HALT", ["000524.SZ"]),
        ("20260625", "ST", ["000638.SZ"]),
    ]


def test_refresh_daily_stock_status_clears_empty_target_day_status(monkeypatch):
    class FakeResult:
        error_code = "0"
        error_msg = ""

        def get_data(self):
            return pd.DataFrame(
                {
                    "code": ["sz.000524", "sh.600000"],
                    "tradeStatus": ["1", "1"],
                    "code_name": ["岭南控股", "浦发银行"],
                }
            )

    monkeypatch.setattr(
        download_efficient.bs,
        "query_all_stock",
        lambda day: FakeResult(),
    )
    downloader = make_valuation_only_downloader({})

    downloader.refresh_daily_stock_status("2026-06-25")

    assert downloader.writer.status_writes == [
        ("20260625", "HALT", []),
        ("20260625", "ST", []),
    ]
