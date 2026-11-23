#!/usr/bin/env python3
"""Read-only daily-bar probes, isolated by source with a hard process timeout."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd

from simtradedata.router import SmartRouter

SOURCES = {
    "mootdx": ("cn", "600000.SS"),
    "eastmoney": ("cn", "600000.SS"),
    "baostock": ("cn", "600000.SS"),
    "yfinance": ("us", "AAPL.US"),
    "tdx_package": ("cn", "600000.SS"),
}
DEFAULT_SOURCES = ["mootdx", "eastmoney", "baostock", "yfinance", "tdx_package"]
CALENDARS = {"cn": "XSHG", "us": "XNYS"}
# Allow CN end-of-day processing and US daily-bar publication to finish.
UPDATE_GRACE_HOURS = {"cn": 6, "us": 2}


def latest_due_session(market: str, now: pd.Timestamp) -> str:
    import exchange_calendars as xc

    calendar = xc.get_calendar(CALENDARS[market])
    local_date = now.tz_convert(calendar.tz).date()
    if not calendar.first_session.date() <= local_date <= calendar.last_session.date():
        raise ValueError(f"{CALENDARS[market]} calendar does not cover {local_date}")
    schedule = calendar.schedule.loc[
        str(local_date - pd.Timedelta(days=35)) : str(local_date)
    ]
    due = schedule[
        schedule["close"] + pd.Timedelta(hours=UPDATE_GRACE_HOURS[market]) <= now
    ]
    if due.empty:
        raise ValueError(f"No completed session in {CALENDARS[market]} calendar")
    return due.index[-1].strftime("%Y-%m-%d")


def validate_bars(
    df: pd.DataFrame, expected: str | None, end: str, *, require_amount: bool = True
) -> dict:
    if df.empty:
        return {"status": "empty", "detail": "No daily bars returned"}
    required = {"open", "high", "low", "close", "volume"}
    if require_amount:
        required.add("amount")
    if not isinstance(df.index, pd.DatetimeIndex):
        required.add("date")
    missing = required - set(df.columns)
    if missing:
        return {
            "status": "schema_error",
            "detail": f"Missing fields: {sorted(missing)}",
        }
    raw_dates = (
        df["date"] if "date" in df.columns else pd.Series(df.index, index=df.index)
    )
    dates = pd.to_datetime(raw_dates, errors="coerce")
    numeric_cols = ["open", "high", "low", "close", "volume"]
    nonnegative_cols = ["volume"]
    if "amount" in df.columns:
        numeric_cols.append("amount")
        nonnegative_cols.append("amount")
    values = df[numeric_cols].apply(pd.to_numeric, errors="coerce")
    valid = (
        dates.notna().all()
        and not dates.dt.normalize().duplicated().any()
        and (dates.dt.strftime("%Y-%m-%d") <= end).all()
        and np.isfinite(values.to_numpy()).all()
        and (values[["open", "high", "low", "close"]] > 0).all().all()
        and (values[nonnegative_cols] >= 0).all().all()
        and (values["high"] >= values[["open", "low", "close"]].max(axis=1)).all()
        and (values["low"] <= values[["open", "high", "close"]].min(axis=1)).all()
    )
    if not valid:
        return {
            "status": "invalid_data",
            "detail": "Invalid dates, duplicate bars, or invalid OHLCV values",
        }
    latest = dates.max().strftime("%Y-%m-%d")
    return {
        "status": "stale" if expected and latest < expected else "ok",
        "latest_date": latest,
        "rows": len(df),
        "detail": f"latest={latest}, expected={expected or 'calendar unavailable'}",
    }


def _source_report(source: str, expected: str | None, result: dict, started: float) -> dict:
    market, symbol = SOURCES[source]
    return {
        **result,
        "source": source,
        "market": market,
        "symbol": symbol,
        "expected_date": expected,
        "freshness_checked": expected is not None
        and result["status"] in {"ok", "stale"},
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def probe(source: str, expected: str | None, end: str) -> dict:
    market, symbol = SOURCES[source]
    start = (pd.Timestamp(end) - pd.Timedelta(days=35)).strftime("%Y-%m-%d")
    started = time.monotonic()
    try:
        if source == 'tdx_package':
            from simtradedata.fetchers.tdx_daily_package import fetch_daily_package
            if not expected:
                raise ValueError('Trading calendar unavailable for daily package verification')
            frame = fetch_daily_package(expected).rename(columns={'money':'amount'})
            samples = []
            today = (pd.Timestamp(end) - pd.Timedelta(days=1)).strftime('%Y-%m-%d')
            for code in [symbol, '159919.SZ', '510300.SS']:
                data = frame[frame['symbol'] == code]
                sample = validate_bars(data, expected, today)
                active = data['volume'] > 0
                if sample['status'] == 'ok' and active.any():
                    average = data.loc[active,'amount'] / data.loc[active,'volume']
                    if not average.between(data.loc[active,'low'] * .99, data.loc[active,'high'] * 1.01).all():
                        sample = {'status':'invalid_data', 'detail':'Daily package price/volume/amount units disagree'}
                samples.append({'symbol':code, **sample})
            failed = next((sample for sample in samples if sample['status'] != 'ok'), None)
            result = {**(failed or samples[0]), 'samples':samples}
            return _source_report(source, expected, result, started)
        # One source only: the production adapters run without fallback.
        with SmartRouter(config={"daily_bars": {market: [source]}}) as router:
            df = router.get_daily_bars(symbol, start, end)
            today = (pd.Timestamp(end) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            # Yahoo uses a date index and does not expose actual traded amount.
            result = validate_bars(df, expected, today, require_amount=source != "yfinance")
            if source == "mootdx" and result["status"] == "ok":
                samples = []
                for etf in ["510300.SS", "159915.SZ"]:
                    data = router.get_daily_bars(etf, start, end)
                    sample = validate_bars(data, expected, today)
                    if sample["status"] == "ok":
                        active = pd.to_numeric(data["volume"]) > 0
                        average = (
                            pd.to_numeric(data.loc[active, "amount"])
                            / pd.to_numeric(data.loc[active, "volume"])
                        )
                        if not average.between(
                            pd.to_numeric(data.loc[active, "low"]) * 0.99,
                            pd.to_numeric(data.loc[active, "high"]) * 1.01,
                        ).all():
                            sample = {
                                "status": "invalid_data",
                                "detail": "ETF price/volume units disagree with traded amount",
                            }
                    samples.append({"symbol": etf, **sample})
                    if sample["status"] != "ok":
                        result = {**sample, "failing_symbol": etf}
                        break
                result["etf_samples"] = samples
    except Exception as error:
        message = str(error)
        lower = message.lower()
        source_error = error.__cause__ if error.__cause__ is not None else error
        if "429" in lower or "rate limit" in lower or "too many requests" in lower:
            status = "rate_limited"
        elif (
            isinstance(source_error, TimeoutError)
            or "timeout" in lower
            or "timed out" in lower
        ):
            status = "timeout"
        elif isinstance(source_error, (KeyError, ValueError, TypeError)):
            status = "schema_error"
        else:
            status = "unavailable"
        result = {"status": status, "detail": f"{type(error).__name__}: {message}"}
    return _source_report(source, expected, result, started)


def run_source(
    source: str, expected: str | None, end: str, timeout: int, attempts: int
) -> dict:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="simtrade-probe-") as directory:
        output = Path(directory) / "result.json"
        for attempt in range(1, attempts + 1):
            output.unlink(missing_ok=True)
            command = [
                sys.executable,
                "-m",
                "scripts.check_data_sources",
                "--worker",
                source,
                "--end-date",
                end,
                "--json-output",
                str(output),
            ]
            if expected:
                command.extend(["--expected-date", expected])
            try:
                completed = subprocess.run(
                    command,
                    cwd=Path(__file__).resolve().parents[1],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
                if completed.returncode != 0 or not output.exists():
                    result = {
                        "status": "unavailable",
                        "detail": "Probe process failed; " + completed.stderr[-1000:],
                    }
                else:
                    result = json.loads(output.read_text(encoding="utf-8"))
            except subprocess.TimeoutExpired:
                result = {
                    "status": "timeout",
                    "detail": f"Probe exceeded {timeout}s process limit",
                }
            if result["status"] not in {
                "timeout",
                "unavailable",
                "rate_limited",
                "empty",
            }:
                break
            if attempt < attempts:
                time.sleep(2)
    return _source_report(source, expected, {**result, "attempts": attempt}, started)


def write_json(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", nargs="+", choices=SOURCES, default=DEFAULT_SOURCES)
    parser.add_argument(
        "--timeout", type=int, default=90, help="Hard seconds per attempt"
    )
    parser.add_argument("--attempts", type=int, choices=[1, 2, 3], default=2)
    parser.add_argument(
        "--json-output", type=Path, default=Path("logs/validation/data_sources.json")
    )
    parser.add_argument("--worker", choices=SOURCES, help=argparse.SUPPRESS)
    parser.add_argument("--expected-date", help=argparse.SUPPRESS)
    parser.add_argument("--end-date", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.worker:
        write_json(
            args.json_output, probe(args.worker, args.expected_date, args.end_date)
        )
        return 0

    now = pd.Timestamp.now(tz="UTC")
    expected, calendar_errors = {}, {}
    for market in dict.fromkeys(SOURCES[source][0] for source in args.sources):
        try:
            expected[market] = latest_due_session(market, now)
        except Exception as error:
            expected[market] = None
            calendar_errors[market] = str(error)
    results = []
    for source in dict.fromkeys(args.sources):
        market = SOURCES[source][0]
        timezone = "Asia/Shanghai" if market == "cn" else "America/New_York"
        # Query tomorrow too: yfinance treats end_date as exclusive.
        end = (now.tz_convert(timezone) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        result = run_source(source, expected[market], end, args.timeout, args.attempts)
        results.append(result)
        print(f"{source}: {result['status']} — {result.get('detail', '')}", flush=True)
    ok = not calendar_errors and all(result["status"] == "ok" for result in results)
    report = {
        "checked_at": now.isoformat(),
        "ok": ok,
        "calendar_errors": calendar_errors,
        "results": results,
    }
    write_json(args.json_output, report)
    for market, error in calendar_errors.items():
        print(f"{market} calendar_error: {error}", file=sys.stderr)
    print(f"Report: {args.json_output}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
