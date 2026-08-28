"""Tests for ETF support in TDX binary importer."""

import importlib
import struct

import pytest
import pandas as pd

import scripts.import_tdx_day as import_tdx_day

from scripts.import_tdx_day import (
    RECORD_FORMAT,
    is_stock_code,
    parse_tdx_day_file,
)


def _make_record(date_int, open_p, high, low, close, amount, volume):
    """Build a single TDX binary record for testing."""
    return struct.pack(RECORD_FORMAT, date_int, open_p, high, low, close, amount, volume, 0)


@pytest.mark.unit
class TestIsStockCodeEtf:
    """is_stock_code should accept ETF filenames."""

    def test_sh_etf(self):
        assert is_stock_code("sh510050.day") is True

    def test_sz_etf(self):
        assert is_stock_code("sz159919.day") is True

    def test_sh_lof(self):
        assert is_stock_code("sh500018.day") is True

    def test_sz_lof(self):
        assert is_stock_code("sz161039.day") is True

    def test_sh_stock(self):
        assert is_stock_code("sh600000.day") is True

    def test_sz_stock(self):
        assert is_stock_code("sz000001.day") is True

    def test_sh_index_rejected(self):
        assert is_stock_code("sh000001.day") is False

    def test_sz_index_rejected(self):
        assert is_stock_code("sz399001.day") is False


@pytest.mark.unit
class TestParseTdxDayFileEtf:
    """parse_tdx_day_file should apply correct divisor for ETFs."""

    def test_stock_default_divisor(self):
        data = _make_record(20250101, 1050, 1100, 1000, 1080, 1e8, 500000)
        df = parse_tdx_day_file(data)
        assert len(df) == 1
        assert df.iloc[0]["close"] == pytest.approx(10.80)
        assert df.iloc[0]["open"] == pytest.approx(10.50)

    def test_etf_divisor_1000(self):
        data = _make_record(20250101, 1050, 1100, 1000, 1080, 1e8, 500000)
        df = parse_tdx_day_file(data, price_divisor=1000.0)
        assert len(df) == 1
        assert df.iloc[0]["close"] == pytest.approx(1.080)
        assert df.iloc[0]["open"] == pytest.approx(1.050)

@pytest.mark.unit
class TestParseTdxDayFileHistoryStart:
    """parse_tdx_day_file honors the optional SIMTRADE_CN_HISTORY_START trim."""

    @pytest.fixture(autouse=True)
    def _cleanup_env(self, monkeypatch):
        yield
        monkeypatch.delenv("SIMTRADE_CN_HISTORY_START", raising=False)
        importlib.reload(import_tdx_day)

    def test_full_history_without_env(self, monkeypatch):
        monkeypatch.delenv("SIMTRADE_CN_HISTORY_START", raising=False)
        importlib.reload(import_tdx_day)
        data = _make_record(19901219, 100, 110, 90, 105, 1e7, 100000)
        df = import_tdx_day.parse_tdx_day_file(data)
        assert len(df) == 1
        assert df.iloc[0]["date"] == pd.Timestamp("1990-12-19")

    def test_pre_history_rows_skipped(self, monkeypatch):
        monkeypatch.setenv("SIMTRADE_CN_HISTORY_START", "2005-04-08")
        importlib.reload(import_tdx_day)
        data = (
            _make_record(19901219, 100, 110, 90, 105, 1e7, 100000)
            + _make_record(20050407, 100, 110, 90, 105, 1e7, 100000)
            + _make_record(20050408, 100, 110, 90, 105, 1e7, 100000)
        )
        df = import_tdx_day.parse_tdx_day_file(data)
        assert len(df) == 1
        assert df.iloc[0]["date"] == pd.Timestamp("2005-04-08")

    def test_post_history_rows_kept(self, monkeypatch):
        monkeypatch.setenv("SIMTRADE_CN_HISTORY_START", "2005-04-08")
        importlib.reload(import_tdx_day)
        data = _make_record(20260101, 100, 110, 90, 105, 1e7, 100000)
        df = import_tdx_day.parse_tdx_day_file(data)
        assert len(df) == 1
        assert df.iloc[0]["date"] == pd.Timestamp("2026-01-01")
