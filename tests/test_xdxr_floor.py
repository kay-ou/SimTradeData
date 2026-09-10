"""XDXR conversion must drop pre-floor rows so sparse-refill cannot
re-introduce history the deployment has cut."""

import importlib

import pandas as pd
import pytest


def _make_xdxr_df():
    return pd.DataFrame(
        {
            "category": [1, 1, 1],
            "year": [1993, 2004, 2020],
            "month": [11, 1, 6],
            "day": [1, 2, 3],
            "songzhuangu": [10, 10, 10],
            "peigu": [0, 0, 0],
            "peigujia": [0, 0, 0],
            "fenhong": [1, 1, 1],
        }
    )


def _convert(xdxr_df):
    from scripts import download_mootdx

    downloader = download_mootdx.MootdxDownloader.__new__(
        download_mootdx.MootdxDownloader
    )
    return downloader._convert_xdxr_to_exrights(xdxr_df)


def test_xdxr_conversion_drops_pre_floor_rows(monkeypatch):
    import simtradedata.config.field_mappings as fm
    from scripts import download_mootdx

    monkeypatch.setenv("SIMTRADE_CN_HISTORY_START", "2005-05-09")
    importlib.reload(fm)
    importlib.reload(download_mootdx)

    result = _convert(_make_xdxr_df())

    assert list(result["date"]) == [pd.Timestamp("2020-06-03")]
    assert len(result) == 1


def test_xdxr_conversion_keeps_full_history_without_floor(monkeypatch):
    import simtradedata.config.field_mappings as fm
    from scripts import download_mootdx

    monkeypatch.delenv("SIMTRADE_CN_HISTORY_START", raising=False)
    importlib.reload(fm)
    importlib.reload(download_mootdx)

    result = _convert(_make_xdxr_df())

    assert len(result) == 3
