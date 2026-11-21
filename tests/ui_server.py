"""Isolated browser-test server with a temporary database and local ZIP."""

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


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="simtrade-ui-") as directory:
        seed(directory)
        main(["--data-dir", directory, "--port", "18765"])
