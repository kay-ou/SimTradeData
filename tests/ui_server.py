"""Isolated browser-test server with a temporary database and local ZIP."""

import os
from contextlib import nullcontext
import struct
import tempfile
import zipfile
from pathlib import Path

import pandas as pd

from simtradedata.fetchers.etf_pcf_fetcher import parse_pcf
from simtradedata.web.server import main
from simtradedata.writers.duckdb_writer import DuckDBWriter


def seed(directory):
    directory = Path(directory)
    writer = DuckDBWriter(str(directory / "cn.duckdb"))
    writer.conn.execute("INSERT INTO trade_days VALUES ('2026-11-18'), ('2026-11-19'), ('2026-11-20')")
    writer.conn.execute("CREATE TABLE exchange_calendar(date DATE PRIMARY KEY, is_trading_day BOOLEAN NOT NULL)")
    writer.conn.execute("INSERT INTO exchange_calendar VALUES ('2026-11-18', true), ('2026-11-19', true), ('2026-11-20', true)")
    writer.write_market_data("600000.SS", pd.DataFrame(
        {"open": [10.0], "high": [11.0], "low": [9.0], "close": [10.5], "volume": [1000], "money": [10500.]},
        index=pd.to_datetime(["2026-11-19"]),
    ))
    writer.write_market_data("600000.SS", pd.DataFrame(
        {"open": [5.0], "high": [6.0], "low": [4.0], "close": [5.5], "volume": [1000], "money": [5500.]},
        index=pd.to_datetime(["2026-11-17"]),
    ))
    writer.close()
    cache = directory / "downloads"
    cache.mkdir()
    records = b"".join(struct.pack("<IIIIIfII", day, 1000, 1100, 900, 1050, 10500., 1000, 0)
                       for day in [20261117, 20261118, 20261119, 20261120, 20261121])
    with zipfile.ZipFile(cache / "hsjday.zip", "w") as archive:
        archive.writestr("vipdoc/sh/lday/sh600000.day", records)
    pcf = directory / "etf_pcf" / "159919.SZ"
    pcf.mkdir(parents=True)
    fixture = Path(__file__).parent / "fixtures" / "pcf" / "159919-20261120.xml"
    parse_pcf(fixture.read_bytes(), "159919.SZ").to_parquet(pcf / "2026-11-20.parquet", index=False)


def etfs():
    return ([{"symbol": symbol, "name": name, "short_name": name, "market": market,
              "daily_supported": True, "history_pcf_supported": market == "SZ",
              "pcf_support": "历史可按日期查询，连续覆盖待核验" if market == "SZ" else "目前仅最新PCF，历史尚未接入"}
             for symbol, name, market in [("159919.SZ", "嘉实沪深300ETF", "SZ"), ("159920.SZ", "华夏恒生ETF", "SZ"), ("510300.SS", "沪深300ETF", "SS")]], [])



if __name__ == "__main__":
    existing = os.environ.get("SIMTRADE_UI_FIXTURE_DIR")
    with nullcontext(existing) if existing else tempfile.TemporaryDirectory(prefix="simtrade-ui-") as directory:
        if not existing:
            seed(directory)
            # The UI fixture also contains a November 17 stock bar. Verify its
            # calendar day and the synthetic ETFs' November 18 listing bound.
            with DuckDBWriter(str(Path(directory) / 'cn.duckdb')) as writer:
                writer.conn.execute("INSERT INTO exchange_calendar VALUES ('2026-11-17',true)")
                writer.conn.execute("INSERT INTO trade_days VALUES ('2026-11-17')")
                for symbol in ['159919.SZ','159920.SZ','510300.SS']:
                    writer.conn.execute("INSERT INTO stock_metadata(symbol,listed_date) VALUES (?, '2026-11-18')", [symbol])
            os.environ["SIMTRADE_UI_FIXTURE_DIR"] = directory
        from simtradedata.web import source_health
        def healthy(_):
            return {"freshness": "current", "latest": "2026-11-20", "detail": "隔离测试样本有效"}
        source_health.daily_probe = source_health.financial_probe = source_health.pcf_probe = healthy
        from simtradedata.web import etf_catalogue
        etf_catalogue.load_etfs = etfs
        # Execute real operations with isolated source fixtures in the child process.
        from simtradedata.web import jobs
        original_popen = jobs.subprocess.Popen
        def isolated_popen(args, **kwargs):
            if '-m' in args and 'simtradedata.web.operations' in args:
                args = [value if value != 'simtradedata.web.operations' else 'tests.ui_worker' for value in args]
            return original_popen(args, **kwargs)
        jobs.subprocess.Popen = isolated_popen
        main(["--data-dir", directory, "--port", "18765"])
