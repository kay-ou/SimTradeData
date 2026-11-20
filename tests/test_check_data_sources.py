"""Offline regression tests for scheduled data-source probes."""

import json
import subprocess
from types import SimpleNamespace

import pandas as pd
import pytest

from scripts import check_data_sources as checks


def bars(date="2024-07-03"):
    return pd.DataFrame(
        {
            "date": [date],
            "open": [10.0],
            "high": [12.0],
            "low": [9.0],
            "close": [11.0],
            "volume": [100],
            "amount": [1100],
        }
    )


@pytest.mark.parametrize(
    "market,now,expected",
    [
        ("cn", "2024-02-15T16:00:00Z", "2024-02-08"),  # Spring Festival
        ("cn", "2024-02-19T08:00:00Z", "2024-02-08"),  # update grace
        ("cn", "2024-02-19T15:00:00Z", "2024-02-19"),
        ("us", "2024-07-04T12:00:00Z", "2024-07-03"),  # July 4
        ("us", "2024-07-06T12:00:00Z", "2024-07-05"),  # weekend
        ("us", "2024-03-11T21:00:00Z", "2024-03-08"),  # DST + grace
        ("us", "2024-03-11T23:00:00Z", "2024-03-11"),
    ],
)
def test_latest_due_session(market, now, expected):
    assert checks.latest_due_session(market, pd.Timestamp(now)) == expected


def test_calendar_out_of_range_fails_closed():
    with pytest.raises(ValueError):
        checks.latest_due_session("cn", pd.Timestamp("2100-01-01T00:00:00Z"))


def test_valid_data_and_stale_data():
    assert checks.validate_bars(bars(), "2024-07-03", "2024-07-05")["status"] == "ok"
    assert checks.validate_bars(bars(), "2024-07-05", "2024-07-05")["status"] == "stale"


def test_empty_and_changed_schema():
    assert (
        checks.validate_bars(pd.DataFrame(), "2024-07-03", "2024-07-05")["status"]
        == "empty"
    )
    result = checks.validate_bars(
        bars().drop(columns="volume"), "2024-07-03", "2024-07-05"
    )
    assert result["status"] == "schema_error"
    assert "volume" in result["detail"]


@pytest.mark.parametrize(
    "column,value",
    [
        ("close", -1),
        ("close", 13),
        ("high", 8),
        ("volume", -1),
        ("amount", -1),
        ("open", float("inf")),
        ("close", None),
        ("date", "invalid"),
        ("date", "2024-07-06"),
    ],
)
def test_invalid_data(column, value):
    df = bars()
    df[column] = value
    assert (
        checks.validate_bars(df, "2024-07-03", "2024-07-05")["status"] == "invalid_data"
    )


def test_duplicate_dates():
    assert (
        checks.validate_bars(pd.concat([bars(), bars()]), "2024-07-03", "2024-07-05")[
            "status"
        ]
        == "invalid_data"
    )


def test_yahoo_date_index_and_no_traded_amount():
    df = bars().drop(columns="amount").set_index("date")
    df.index = pd.DatetimeIndex(df.index)
    assert (
        checks.validate_bars(df, "2024-07-03", "2024-07-05", require_amount=False)[
            "status"
        ]
        == "ok"
    )
    assert (
        checks.validate_bars(df, "2024-07-03", "2024-07-05")["status"] == "schema_error"
    )


def test_yahoo_missing_date_is_still_a_schema_error():
    df = bars().drop(columns=["date", "amount"])
    assert (
        checks.validate_bars(df, "2024-07-03", "2024-07-05", require_amount=False)[
            "status"
        ]
        == "schema_error"
    )


def test_probe_cannot_fall_back(monkeypatch):
    configs = []

    class Router:
        def __init__(self, config):
            configs.append(config)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get_daily_bars(self, *args):
            return bars()

    monkeypatch.setattr(checks, "SmartRouter", Router)
    result = checks.probe("eastmoney", "2024-07-03", "2024-07-05")
    assert result["status"] == "ok"
    assert configs == [{"daily_bars": {"cn": ["eastmoney"]}}]


@pytest.mark.parametrize(
    "error,status",
    [
        (TimeoutError("timed out"), "timeout"),
        (ConnectionError("HTTP 429"), "rate_limited"),
        (KeyError("new_field"), "schema_error"),
        (ConnectionError("connection refused"), "unavailable"),
    ],
)
def test_probe_errors(monkeypatch, error, status):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(checks, "SmartRouter", fail)
    result = checks.probe("eastmoney", "2024-07-03", "2024-07-05")
    assert result["status"] == status
    assert result["freshness_checked"] is False


@pytest.mark.parametrize(
    "error,status,attempts",
    [
        (KeyError("close"), "schema_error", 1),
        (ValueError("invalid field"), "schema_error", 1),
        (TypeError("unexpected field type"), "schema_error", 1),
        (TimeoutError(), "timeout", 2),
        (ConnectionError("HTTP 429"), "rate_limited", 2),
        (ConnectionError("connection refused"), "unavailable", 2),
    ],
)
def test_router_wrapped_errors_keep_classification_and_retry_policy(
    monkeypatch, error, status, attempts
):
    def fail(*args):
        raise error

    fetcher = SimpleNamespace(fetch_daily_bars=fail)
    monkeypatch.setattr(checks.SmartRouter, "_get_fetcher", lambda *args: fetcher)

    def run(command, **kwargs):
        from pathlib import Path

        output = Path(command[command.index("--json-output") + 1])
        result = checks.probe("eastmoney", "2024-07-03", "2024-07-05")
        checks.write_json(output, result)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(checks.subprocess, "run", run)
    monkeypatch.setattr(checks.time, "sleep", lambda _: None)
    result = checks.run_source("eastmoney", "2024-07-03", "2024-07-05", 1, 2)
    assert result["status"] == status
    assert result["attempts"] == attempts
    assert result["freshness_checked"] is False
    assert result["detail"].startswith("DataSourceError:")


def test_hung_source_retries_and_reports_timeout(monkeypatch):
    calls = []

    def hung(*args, **kwargs):
        calls.append(kwargs["timeout"])
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(checks.subprocess, "run", hung)
    monkeypatch.setattr(checks.time, "sleep", lambda _: None)
    result = checks.run_source("mootdx", "2024-07-03", "2024-07-05", 1, 2)
    assert result["status"] == "timeout"
    assert result["attempts"] == 2
    assert calls == [1, 1]
    assert result["source"] == "mootdx"
    assert result["market"] == "cn"
    assert result["symbol"] == "600000.SS"
    assert result["expected_date"] == "2024-07-03"
    assert result["freshness_checked"] is False
    assert result["elapsed_seconds"] >= 0


def test_transient_failure_can_recover(monkeypatch, tmp_path):
    results = iter([{"status": "unavailable", "detail": "offline"}, {"status": "ok"}])

    def run(command, **kwargs):
        output = command[command.index("--json-output") + 1]
        from pathlib import Path

        Path(output).write_text(json.dumps(next(results)))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(checks.subprocess, "run", run)
    monkeypatch.setattr(checks.time, "sleep", lambda _: None)
    assert (
        checks.run_source("mootdx", "2024-07-03", "2024-07-05", 1, 2)["attempts"] == 2
    )


def test_failed_calendar_still_checks_availability_and_returns_failure(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        checks,
        "latest_due_session",
        lambda *args: (_ for _ in ()).throw(ValueError("expired calendar")),
    )
    monkeypatch.setattr(
        checks,
        "run_source",
        lambda *args: {
            "status": "ok",
            "source": args[0],
            "freshness_checked": False,
        },
    )
    output = tmp_path / "report.json"
    assert checks.main(["--sources", "eastmoney", "--json-output", str(output)]) == 1
    report = json.loads(output.read_text())
    assert report["calendar_errors"]["cn"] == "expired calendar"
    assert report["results"][0]["freshness_checked"] is False
