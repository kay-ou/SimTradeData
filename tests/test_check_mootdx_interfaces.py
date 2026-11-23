"""Retained interfaces must be assessed independently of daily availability."""

import json
import subprocess
from types import SimpleNamespace

import pandas as pd
import pytest

from scripts import check_mootdx_interfaces as checks
from scripts import check_data_sources


def test_daily_monitor_includes_restored_mootdx():
    assert check_data_sources.DEFAULT_SOURCES == [
        "mootdx", "eastmoney", "baostock", "yfinance", "tdx_package",
    ]


def test_catalog_probe_does_not_login_quotes(monkeypatch):
    monkeypatch.setattr(
        checks, "MootdxUnifiedFetcher", lambda: pytest.fail("Catalog cannot use quotes")
    )
    monkeypatch.setattr(
        checks,
        "MootdxAffairFetcher",
        lambda **kwargs: SimpleNamespace(
            list_available_reports=lambda: [{"filename": "gpcw20260930.zip"}]
        ),
    )
    result = checks.probe_interface("financial_catalog")
    assert result["status"] == "ok"
    assert "contents not verified" in result["scope"]


def test_financial_content_is_downloaded_and_checked_independently(monkeypatch):
    calls = []
    frame = pd.DataFrame(
        {
            "code": ["600000"],
            "end_date": ["2026-09-30"],
            "basic_eps": [1.0],
            "total_assets": [100.0],
            "good_will": [0.0],
            "administration_expense": [5.0],
            "total_operating_revenue": [50.0],
            "diluted_eps": [1.0],
        }
    )

    def fetch(year, quarter, **kwargs):
        calls.append((year, quarter))
        assert kwargs["fields"] == checks.FUNDAMENTAL_PROBE_FIELDS
        return frame

    monkeypatch.setattr(
        checks,
        "MootdxUnifiedFetcher",
        lambda: pytest.fail("Financial data cannot use quotes"),
    )
    monkeypatch.setattr(
        checks,
        "MootdxAffairFetcher",
        lambda **kwargs: SimpleNamespace(
            list_available_reports=lambda: [{"filename": "gpcw20260930.zip"}],
            fetch_fundamentals_for_quarter=fetch,
        ),
    )
    assert checks.probe_interface("fundamentals")["status"] == "ok"
    assert calls == [(2026, 3)]


@pytest.mark.parametrize(
    "interface,data,status",
    [
        ("stock_list", [], "empty"),
        ("stock_list", ["bad"], "schema_error"),
        ("stock_list", ["600000.SS"], "ok"),
        ("financial_catalog", [{"filename": "invalid.zip"}], "schema_error"),
        ("xdxr", pd.DataFrame({"year": [2026]}), "schema_error"),
        (
            "realtime_quotes",
            pd.DataFrame({"code": ["600000"], "price": [None]}),
            "invalid_data",
        ),
        ("realtime_quotes", pd.DataFrame({"code": ["600000"], "price": [10]}), "ok"),
    ],
)
def test_result_validation(interface, data, status):
    assert checks.validate_result(interface, data)["status"] == status


@pytest.mark.parametrize("name,data,status", [
    ("snapshot", {"600000.SS": {"last_px": 10}}, "invalid_data"),
    ("blocks", pd.DataFrame({"filename": ["block.dat"], "blockname": ["测试"], "block_type": [2], "code": ["bad"]}), "invalid_data"),
    ("blocks", pd.DataFrame({"filename": ["block.dat"] * 2, "blockname": ["测试"] * 2, "block_type": [2] * 2, "code": ["600000"] * 2}), "invalid_data"),
    ("f10", pd.DataFrame({"text": [None]}), "invalid_data"),
    ("history_transactions", pd.DataFrame({"time": ["09:31"], "price": [10], "vol": [2], "buyorsell": [5]}), "ok"),
    ("history_minutes", pd.DataFrame({"price": [10], "vol": [-2]}), "invalid_data"),
])
def test_extra_interface_health_rejects_invalid_content(name, data, status):
    assert checks.validate_result(name, data)["status"] == status


def test_minute_bars_allow_multiple_rows_in_same_day(monkeypatch):
    monkeypatch.setattr(checks, "latest_due_session", lambda *args: "2026-06-18")
    frame = pd.DataFrame(
        {
            "open": [10, 10],
            "high": [11, 11],
            "low": [9, 9],
            "close": [10, 10],
            "volume": [100, 100],
        },
        index=pd.to_datetime(["2026-06-18 10:00", "2026-06-18 10:05"]),
    )
    assert checks.validate_result("minute_bars", frame)["status"] == "ok"
    monkeypatch.setattr(checks, "latest_due_session", lambda *args: "2026-06-19")
    assert checks.validate_result("minute_bars", frame)["status"] == "stale"


def test_hung_interface_does_not_hide_other_results(monkeypatch, tmp_path):
    def run(command, **kwargs):
        interface = command[command.index("--worker") + 1]
        if interface == "xdxr":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        checks.write_json(__import__("pathlib").Path(command[-1]), {"status": "ok"})
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(checks.subprocess, "run", run)
    output = tmp_path / "report.json"
    assert (
        checks.main(
            ["--interfaces", "xdxr", "financial_catalog", "--json-output", str(output)]
        )
        == 1
    )
    report = json.loads(output.read_text())
    assert [result["status"] for result in report["results"]] == ["timeout", "ok"]
