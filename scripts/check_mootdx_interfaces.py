#!/usr/bin/env python3
"""Probe retained Mootdx interfaces separately, without daily-bar login gates."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.check_data_sources import latest_due_session, write_json
from simtradedata.fetchers.mootdx_affair_fetcher import MootdxAffairFetcher
from simtradedata.fetchers.mootdx_unified_fetcher import MootdxUnifiedFetcher

INTERFACES = [
    "stock_list",
    "xdxr",
    "realtime_quotes",
    "minute_bars",
    "financial_catalog",
    "fundamentals",
]
REQUIRED = {
    "xdxr": {"category", "year", "month", "day"},
    "realtime_quotes": {"code", "price"},
    "minute_bars": {"open", "high", "low", "close", "volume"},
    "fundamentals": {"code", "end_date", "basic_eps", "total_assets"},
}


def validate_result(name: str, data) -> dict:
    if len(data) == 0:
        return {"status": "empty", "detail": "No records returned"}
    if name == "financial_catalog":
        if not all(
            isinstance(row, dict)
            and re.fullmatch(r"gpcw\d{8}\.zip", row.get("filename", ""))
            for row in data
        ):
            return {
                "status": "schema_error",
                "detail": "Unexpected financial catalog records",
            }
        return {
            "status": "ok",
            "rows": len(data),
            "scope": "catalog only; ZIP contents not verified",
        }
    if name == "stock_list":
        if not all(
            isinstance(symbol, str) and re.fullmatch(r"\d{6}\.(SS|SZ|BJ)", symbol)
            for symbol in data
        ):
            return {"status": "schema_error", "detail": "Invalid stock symbols"}
        return {"status": "ok", "rows": len(data)}
    if not isinstance(data, pd.DataFrame):
        return {"status": "schema_error", "detail": "Expected DataFrame"}
    missing = REQUIRED[name] - set(data.columns)
    if missing:
        return {
            "status": "schema_error",
            "detail": f"Missing fields: {sorted(missing)}",
        }
    if name == "fundamentals":
        sample = data[data["code"].astype(str) == "600000"]
        if sample.empty:
            return {
                "status": "empty",
                "detail": "Representative stock missing from financial report",
            }
        dates = pd.to_datetime(sample["end_date"], errors="coerce")
        values = sample[["basic_eps", "total_assets"]].apply(pd.to_numeric, errors="coerce")
        valid = dates.notna().all() and np.isfinite(values).all().all()
    elif name == "realtime_quotes":
        price = pd.to_numeric(data["price"], errors="coerce")
        valid = np.isfinite(price).all() and (price > 0).all()
    elif name == "xdxr":
        dates = pd.to_datetime(data[["year", "month", "day"]], errors="coerce")
        valid = dates.notna().all()
    else:
        values = data[list(REQUIRED[name])].apply(pd.to_numeric, errors="coerce")
        raw_dates = data["datetime"] if "datetime" in data.columns else data.index
        if "datetime" not in data.columns and not isinstance(
            data.index, pd.DatetimeIndex
        ):
            return {"status": "schema_error", "detail": "Missing minute timestamps"}
        dates = pd.to_datetime(raw_dates, errors="coerce")
        valid = (
            np.isfinite(values).all().all()
            and dates.notna().all()
            and not pd.Index(dates).has_duplicates
            and (
                pd.Index(dates).strftime("%Y-%m-%d")
                <= pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y-%m-%d")
            ).all()
            and (values[["open", "high", "low", "close"]] > 0).all().all()
            and (values["volume"] >= 0).all()
            and (values["high"] >= values[["open", "low", "close"]].max(axis=1)).all()
            and (values["low"] <= values[["open", "high", "close"]].min(axis=1)).all()
        )
        if valid:
            expected = latest_due_session("cn", pd.Timestamp.now(tz="UTC"))
            latest = dates.max().strftime("%Y-%m-%d")
            return {
                "status": "ok" if latest >= expected else "stale",
                "rows": len(data),
                "latest_date": latest,
                "expected_date": expected,
            }
    return {"status": "ok" if valid else "invalid_data", "rows": len(data)}


def probe_interface(name: str) -> dict:
    try:
        if name in {"financial_catalog", "fundamentals"}:
            with tempfile.TemporaryDirectory(prefix="mootdx-catalog-") as directory:
                fetcher = MootdxAffairFetcher(download_dir=directory)
                reports = fetcher.list_available_reports()
                if name == "financial_catalog":
                    data = reports
                else:
                    today = pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y%m%d")
                    filenames = [
                        row["filename"]
                        for row in reports
                        if re.fullmatch(
                            r"gpcw\d{4}(0331|0630|0930|1231)\.zip",
                            row.get("filename", ""),
                        )
                        and row["filename"][4:12] <= today
                    ]
                    if not filenames:
                        raise ValueError("No completed-quarter report available")
                    filename = max(filenames)
                    year, month = int(filename[4:8]), int(filename[8:10])
                    data = fetcher.fetch_fundamentals_for_quarter(
                        year, month // 3, fields=["basic_eps", "total_assets"]
                    )
        else:
            with MootdxUnifiedFetcher() as fetcher:
                if name == "stock_list":
                    data = fetcher.fetch_stock_list()
                elif name == "xdxr":
                    data = fetcher.fetch_xdxr("600000.SS")
                elif name == "realtime_quotes":
                    data = fetcher.fetch_realtime_quotes(["600000.SS"])
                else:
                    data = fetcher.fetch_minute_bars(
                        "600000.SS", frequency=0, offset=50
                    )
        result = validate_result(name, data)
        if name == "fundamentals":
            result.update(
                report_file=filename,
                scope="Latest available quarter; representative stock content checked",
            )
    except Exception as error:
        result = {"status": "unavailable", "detail": f"{type(error).__name__}: {error}"}
    return {"source": "mootdx", "interface": name, **result}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--interfaces", nargs="+", choices=INTERFACES, default=INTERFACES
    )
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument(
        "--json-output",
        type=Path,
        default=Path("logs/validation/mootdx-interfaces.json"),
    )
    parser.add_argument("--worker", choices=INTERFACES, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.worker:
        write_json(args.json_output, probe_interface(args.worker))
        return 0
    results = []
    with tempfile.TemporaryDirectory(prefix="mootdx-interfaces-") as directory:
        for name in dict.fromkeys(args.interfaces):
            output = Path(directory) / f"{name}.json"
            try:
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "scripts.check_mootdx_interfaces",
                        "--worker",
                        name,
                        "--json-output",
                        str(output),
                    ],
                    cwd=Path(__file__).resolve().parents[1],
                    capture_output=True,
                    text=True,
                    timeout=args.timeout,
                    check=False,
                    env={**os.environ, "TMPDIR": directory},
                )
                result = (
                    json.loads(output.read_text())
                    if completed.returncode == 0 and output.exists()
                    else {"status": "unavailable", "detail": completed.stderr[-1000:]}
                )
            except subprocess.TimeoutExpired:
                result = {
                    "status": "timeout",
                    "detail": f"Exceeded {args.timeout}s process limit",
                }
            result = {"source": "mootdx", "interface": name, **result}
            results.append(result)
            print(f"{name}: {result['status']}", flush=True)
    ok = all(result["status"] == "ok" for result in results)
    write_json(
        args.json_output,
        {
            "checked_at": pd.Timestamp.now(tz="UTC").isoformat(),
            "ok": ok,
            "results": results,
        },
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
