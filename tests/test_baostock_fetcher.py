"""Tests for BaoStock fetcher session handling."""

import importlib
from types import SimpleNamespace

import pandas as pd
import pytest
from scripts.download import run_baostock_download
from simtradedata.fetchers.baostock_fetcher import BaoStockFetcher


def reset_baostock_state():
    BaoStockFetcher._bs_logged_in = False
    BaoStockFetcher._bs_login_count = 0


def test_login_retries_transient_baostock_failure(monkeypatch):
    reset_baostock_state()
    retry_module = importlib.import_module("simtradedata.resilience.retry")
    monkeypatch.setattr(retry_module.time, "sleep", lambda _: None)

    attempts = []

    def fake_login():
        attempts.append(1)
        if len(attempts) == 1:
            return SimpleNamespace(error_code="100010", error_msg="网络接收错误。")
        return SimpleNamespace(error_code="0", error_msg="success")

    monkeypatch.setattr("simtradedata.fetchers.baostock_fetcher.bs.login", fake_login)

    fetcher = BaoStockFetcher()
    fetcher.login()

    assert len(attempts) == 2
    assert BaoStockFetcher._bs_logged_in is True
    assert BaoStockFetcher._bs_login_count == 1

    reset_baostock_state()


def test_baostock_phase_keeps_index_constituents_enabled(monkeypatch):
    captured = {}

    def fake_download_all_data(**kwargs):
        captured.update(kwargs)

    monkeypatch.setitem(
        __import__("sys").modules,
        "download_efficient",
        SimpleNamespace(download_all_data=fake_download_all_data),
    )

    assert run_baostock_download(valuation_only=True) is True
    assert captured["skip_fundamentals"] is True
    assert captured["skip_metadata"] is False
    assert captured["valuation_only"] is True


@pytest.mark.parametrize("ratio,expected", [(0.6, 150.0), (0.0, 0.0), (1.0, None)])
def test_debt_equity_is_not_the_asset_liability_ratio(monkeypatch, ratio, expected):
    module = importlib.import_module("simtradedata.fetchers.baostock_fetcher")
    raw = pd.DataFrame({
        "code": ["sh.600000"], "statDate": ["2026-09-30"],
        "pubDate": ["2026-10-30"], "liabilityToAsset": [str(ratio)],
    })
    for name in [
        "query_profit_data", "query_growth_data", "query_operation_data",
        "query_cash_flow_data",
    ]:
        monkeypatch.setattr(module.bs, name, lambda **kwargs: SimpleNamespace(error_code="empty"))
    monkeypatch.setattr(module.bs, "query_balance_data", lambda **kwargs: SimpleNamespace(error_code="0", data=raw))
    monkeypatch.setattr(module, "read_baostock_result", lambda result: result.data)
    result = BaoStockFetcher().fetch_quarterly_fundamentals("600000.SS", 2026, 3)
    value = result.iloc[0]["debt_equity_ratio"]
    assert "_liability_to_asset" not in result.columns
    if expected is None:
        assert pd.isna(value)
    else:
        assert value == pytest.approx(expected)
