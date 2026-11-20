"""Default daily ingestion must not fall back to Mootdx OHLCV."""

import sys

import pytest

from scripts import download


def setup_download(monkeypatch, args, tdx_ok=True):
    calls = []
    monkeypatch.setattr(sys, "argv", ["download.py", *args])
    monkeypatch.setattr(download, "print_data_status", lambda: None)
    monkeypatch.setattr(
        download, "run_tdx_download_and_import", lambda: calls.append("tdx") or tdx_ok
    )
    monkeypatch.setattr(
        download,
        "run_mootdx_download",
        lambda **kwargs: calls.append(("mootdx", kwargs)) or True,
    )
    monkeypatch.setattr(
        download,
        "run_baostock_download",
        lambda **kwargs: calls.append("baostock") or True,
    )
    return calls


def test_default_download_uses_tdx_and_mootdx_only_for_extras(monkeypatch):
    calls = setup_download(monkeypatch, [])
    download.main()
    assert calls[0] == "tdx"
    assert calls[1][0] == "mootdx"
    assert calls[1][1]["skip_ohlcv"] is True
    assert calls[2] == "baostock"


def test_failed_primary_does_not_enable_mootdx_daily_fallback(monkeypatch):
    calls = setup_download(monkeypatch, [], tdx_ok=False)
    with pytest.raises(SystemExit) as error:
        download.main()
    assert error.value.code == 1
    assert calls[1][1]["skip_ohlcv"] is True


def test_explicit_mootdx_source_is_extras_only(monkeypatch):
    calls = setup_download(monkeypatch, ["--source", "mootdx"])
    download.main()
    assert len(calls) == 1
    assert calls[0][1]["skip_ohlcv"] is True


def test_local_tdx_source_does_not_trigger_network_package_download(monkeypatch):
    calls = setup_download(monkeypatch, ["--tdx-source", "snapshot.zip"])
    monkeypatch.setattr(
        download, "run_tdx_import", lambda path: calls.append(("import", path)) or True
    )
    download.main()
    assert calls[0] == ("import", "snapshot.zip")
    assert "tdx" not in calls
    assert calls[1][1]["skip_ohlcv"] is True
