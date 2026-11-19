"""BaoStock pagination must not depend on DataFrame.append()."""

from contextlib import nullcontext

import pandas as pd
import pytest
from baostock.data.resultset import ResultData

from simtradedata.fetchers import baostock_fetcher
from simtradedata.fetchers.unified_fetcher import UnifiedDataFetcher


class PagedResult(ResultData):
    def __init__(self, fields, pages):
        super().__init__()
        self.error_code = "0"
        self.fields = fields
        self.pages = iter(pages)
        self.data = next(self.pages, [])

    def next(self):
        if self.cur_row_num >= len(self.data):
            self.data = next(self.pages, [])
            self.cur_row_num = 0
        return bool(self.data)


def test_read_result_collects_all_pages_without_dataframe_append():
    rs = PagedResult(["date", "peTTM", "isST"], [
        [["2026-06-01", "10", "0"]],
        [["2026-06-02", "11", "1"]],
    ])
    df = baostock_fetcher.read_baostock_result(rs)
    assert df.to_dict("records") == [
        {"date": "2026-06-01", "peTTM": "10", "isST": "0"},
        {"date": "2026-06-02", "peTTM": "11", "isST": "1"},
    ]


def test_read_result_preserves_empty_columns():
    df = baostock_fetcher.read_baostock_result(PagedResult(["date", "isST"], []))
    assert df.empty
    assert list(df.columns) == ["date", "isST"]


def test_read_result_rejects_failed_later_page():
    rs = PagedResult(["date"], [[["2026-06-01"]]])
    original_next = rs.next

    def next_with_failure():
        if rs.cur_row_num:
            rs.error_code = "100010"
            rs.error_msg = "接收数据异常"
            return False
        return original_next()

    rs.next = next_with_failure
    with pytest.raises(RuntimeError, match="接收数据异常"):
        baostock_fetcher.read_baostock_result(rs)


def test_unified_daily_data_reads_valuation_and_status_across_pages(monkeypatch):
    rs = PagedResult(["date", "peTTM", "isST", "tradestatus"], [
        [["2026-06-01", "10", "0", "1"]],
        [["2026-06-02", "11", "1", "0"]],
    ])
    monkeypatch.setattr(baostock_fetcher.bs, "query_history_k_data_plus", lambda *a, **k: rs)
    fetcher = UnifiedDataFetcher()
    monkeypatch.setattr(fetcher, "_resilient_operation", lambda: nullcontext(True))
    df = fetcher.fetch_unified_daily_data("000001.SZ", "2026-06-01", "2026-06-02")
    assert df["date"].tolist() == list(pd.to_datetime(["2026-06-01", "2026-06-02"]))
    assert df["peTTM"].tolist() == [10, 11]
    assert df["isST"].tolist() == [0, 1]
    assert df["tradestatus"].tolist() == [1, 0]
