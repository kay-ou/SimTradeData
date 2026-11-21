"""Check official ETF PCF freshness and optionally archive dated Parquet samples."""

import argparse
from pathlib import Path

import pandas as pd

from scripts.check_data_sources import latest_due_session, write_json
from simtradedata.fetchers.etf_pcf_fetcher import EtfPcfFetcher


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=["510300.SS", "159919.SZ"])
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--date", help="Exact historical trading date (YYYY-MM-DD), primarily SZSE")
    parser.add_argument("--archive-dir", type=Path)
    parser.add_argument("--json-output", type=Path, default=Path("logs/validation/etf-pcf.json"))
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    expected = pd.Timestamp(args.date).strftime("%Y-%m-%d") if args.date else latest_due_session("cn", pd.Timestamp.now(tz="UTC"))
    results = []
    with EtfPcfFetcher(timeout=args.timeout) as fetcher:
        for symbol in args.symbols:
            try:
                data = fetcher.fetch_pcf(symbol, args.date) if args.date else fetcher.fetch_pcf(symbol)
                if data.empty:
                    raise ValueError("No PCF rows")
                date = data.iloc[0]["date"]
                result = {"symbol": symbol, "status": "ok" if date >= expected else "stale",
                          "date": date, "expected_date": expected, "rows": len(data),
                          "securities": int(data["symbol"].notna().sum()),
                          "cash_rows": int(data["is_cash_component"].sum())}
                if args.archive_dir:
                    target = args.archive_dir / symbol / f"{date}.parquet"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    data.to_parquet(target, index=False)
                    result["archive"] = str(target)
            except Exception as error:
                result = {"symbol": symbol, "status": "unavailable", "detail": f"{type(error).__name__}: {error}"}
            results.append(result)
            print(f"{symbol}: {result['status']}", flush=True)
    ok = all(result["status"] == "ok" for result in results)
    write_json(args.json_output, {"source": "etf_pcf", "checked_at": pd.Timestamp.now(tz="UTC").isoformat(),
                                  "ok": ok, "results": results})
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
