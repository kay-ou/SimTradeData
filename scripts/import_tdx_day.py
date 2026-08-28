# -*- coding: utf-8 -*-
"""
Import TDX (通达信) daily data from ZIP files or extracted directories.

Data source: https://www.tdx.com.cn/article/vipdata.html
Download: 沪深日线 (hsjday.zip) / 北交所日线 (bjday.zip)

Features:
1. Parse TDX binary .day format (32 bytes per record)
2. Auto-incremental: only imports data newer than existing records
3. Batch processing with transaction support
4. Supports ZIP files with backslash paths (Windows format)

Usage:
    # Import from ZIP file
    poetry run python scripts/import_tdx_day.py hsjday.zip

    # Import from extracted directory
    poetry run python scripts/import_tdx_day.py /path/to/tdx/vipdoc/

    # Full reimport (ignore existing data)
    poetry run python scripts/import_tdx_day.py hsjday.zip --full

    # Specify database path
    poetry run python scripts/import_tdx_day.py hsjday.zip --db data/cn.duckdb
"""

import argparse
import logging
import os
import struct
import zipfile
from pathlib import Path
from typing import Iterator, Optional, Tuple

import pandas as pd
from tqdm import tqdm

from simtradedata.utils.code_utils import is_etf_code
from simtradedata.writers.duckdb_writer import DEFAULT_DB_PATH, DuckDBWriter

# Configuration
LOG_FILE = "data/import_tdx_day.log"
BATCH_SIZE = 50  # Number of stocks per transaction

# Optional per-deployment history trim (YYYYMMDD): only enforced when the
# deployment sets SIMTRADE_CN_HISTORY_START; unset imports the full history.
_env_history_start = os.environ.get("SIMTRADE_CN_HISTORY_START")
CN_HISTORY_START_INT = int(_env_history_start.replace("-", "")) if _env_history_start else None

# TDX binary format constants
RECORD_SIZE = 32  # bytes per record
RECORD_FORMAT = "<IIIIIfII"  # date, open, high, low, close, amount, volume, reserved

# Ensure log directory exists
Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    filemode="w",
)
logger = logging.getLogger(__name__)

# Also log to console
console = logging.StreamHandler()
console.setLevel(logging.WARNING)
logger.addHandler(console)


def parse_tdx_day_file(data: bytes, price_divisor: float = 100.0) -> pd.DataFrame:
    """
    Parse TDX binary .day file content.

    Format: 32 bytes per record
    - date: uint32 (YYYYMMDD)
    - open: uint32 (price in fen, divide by 100)
    - high: uint32
    - low: uint32
    - close: uint32
    - amount: float32 (turnover in yuan)
    - volume: uint32 (shares)
    - reserved: uint32

    Args:
        data: Raw binary content of .day file
        price_divisor: Divisor for raw prices. 100.0 for stocks, 1000.0 for ETFs.

    Returns:
        DataFrame with columns: date, open, high, low, close, volume, money
    """
    if len(data) < RECORD_SIZE:
        return pd.DataFrame()

    num_records = len(data) // RECORD_SIZE
    records = []

    for i in range(num_records):
        offset = i * RECORD_SIZE
        record = data[offset : offset + RECORD_SIZE]

        try:
            date_int, open_p, high, low, close, amount, volume, _ = struct.unpack(
                RECORD_FORMAT, record
            )

            # Parse date
            year = date_int // 10000
            month = (date_int % 10000) // 100
            day = date_int % 100

            # Skip invalid dates
            if year < 1990 or year > 2100 or month < 1 or month > 12 or day < 1 or day > 31:
                continue

            # Skip rows before the configured history start (when set)
            if CN_HISTORY_START_INT is not None and date_int < CN_HISTORY_START_INT:
                continue

            # Convert prices from fen to yuan
            records.append(
                {
                    "date": f"{year:04d}-{month:02d}-{day:02d}",
                    "open": open_p / price_divisor,
                    "high": high / price_divisor,
                    "low": low / price_divisor,
                    "close": close / price_divisor,
                    "volume": volume,
                    "money": amount,
                }
            )
        except struct.error:
            continue

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    return df


def iter_day_files_from_zip(
    zip_path: Path, wanted: Optional[set[str]] = None
) -> Iterator[Tuple[str, bytes]]:
    """
    Iterate over .day files in a ZIP archive.

    Handles Windows-style backslash paths in ZIP files.

    Args:
        zip_path: Path to ZIP file
        wanted: If given, only yield PTrade codes in this set
            (filters before decompressing each member)

    Yields:
        Tuples of (filename, file_content)
    """
    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            # Normalize path separators
            normalized = name.replace("\\", "/")

            # Only process .day files in lday directories
            if not normalized.endswith(".day"):
                continue
            if "/lday/" not in normalized:
                continue

            # Extract filename (e.g., sh600000.day)
            filename = normalized.split("/")[-1]

            if wanted is not None and filename_to_ptrade_code(filename) not in wanted:
                continue

            yield filename, zf.read(name)


def iter_day_files_from_dir(dir_path: Path) -> Iterator[Tuple[str, bytes]]:
    """
    Iterate over .day files in a directory structure.

    Expected structure:
    - dir_path/sh/lday/*.day
    - dir_path/sz/lday/*.day
    - dir_path/bj/lday/*.day

    Args:
        dir_path: Path to root directory

    Yields:
        Tuples of (filename, file_content)
    """
    for market in ["sh", "sz", "bj"]:
        lday_dir = dir_path / market / "lday"
        if not lday_dir.exists():
            continue

        for day_file in lday_dir.glob("*.day"):
            yield day_file.name, day_file.read_bytes()


def filename_to_ptrade_code(filename: str) -> str:
    """
    Convert TDX filename to PTrade code.

    Args:
        filename: e.g., 'sh600000.day', 'sz000001.day', 'bj430017.day'

    Returns:
        PTrade code, e.g., '600000.SS', '000001.SZ', '430017.BJ'
    """
    # Remove .day extension
    base = filename.replace(".day", "")

    # Extract market and code
    market = base[:2]  # sh, sz, bj
    code = base[2:]  # 600000, 000001, etc.

    # Map to PTrade suffix
    suffix_map = {"sh": "SS", "sz": "SZ", "bj": "BJ"}
    suffix = suffix_map.get(market, "")

    if not suffix:
        return None

    return f"{code}.{suffix}"


def is_stock_code(filename: str) -> bool:
    """
    Check if filename represents a stock or ETF (not index/bond/warrant).

    Args:
        filename: e.g., 'sh600000.day', 'sz159919.day'

    Returns:
        True if it's a stock or ETF code
    """
    base = filename.replace(".day", "")
    market = base[:2]
    code = base[2:]

    if len(code) != 6:
        return False

    # ETF/LOF/fund prefixes (both markets)
    etf_prefixes = ("15", "16", "50", "51", "52", "56", "58", "59")
    if code[:2] in etf_prefixes:
        return True

    # Stock prefixes by market
    if market == "sh":
        return code[0] == "6"
    elif market == "sz":
        return code[:2] in ("00", "30")
    elif market == "bj":
        return code[:2] in ("43", "83", "87", "92")

    return False


class TdxDayImporter:
    """Import TDX daily data into DuckDB database."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH, full_import: bool = False):
        """
        Initialize importer.

        Args:
            db_path: Path to DuckDB database
            full_import: If True, import all data regardless of existing records
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.writer = DuckDBWriter(db_path=str(self.db_path))
        self.full_import = full_import

        self.stats = {
            "files_processed": 0,
            "files_skipped": 0,
            "records_imported": 0,
            "records_skipped": 0,
            "records_backfilled": 0,
        }

    def get_existing_date_range(self, symbol: str) -> tuple:
        """
        Get (min_date, max_date) for a symbol.

        Returns:
            Tuple of (min_date_str, max_date_str), or (None, None) if not exists.
        """
        if self.full_import:
            return None, None
        min_date = self.writer.get_min_date("stocks", symbol)
        max_date = self.writer.get_max_date("stocks", symbol)
        return min_date, max_date

    def import_stock(self, symbol: str, df: pd.DataFrame) -> int:
        """
        Import data for a single stock.

        Handles both:
        - New data (after existing MAX date)
        - Historical backfill (before existing MIN date)

        Args:
            symbol: PTrade format code
            df: DataFrame with OHLCV data

        Returns:
            Number of records imported
        """
        if df.empty:
            return 0

        # Check existing data range for incremental import
        min_date, max_date = self.get_existing_date_range(symbol)

        if min_date and max_date:
            min_dt = pd.to_datetime(min_date)
            max_dt = pd.to_datetime(max_date)

            # Import all records outside existing range plus gap-fill within range.
            # INSERT OR REPLACE handles duplicates safely via PRIMARY KEY.
            df_backfill = df[df["date"] < min_dt]
            df_new = df[df["date"] > max_dt]
            df_mid = df[(df["date"] >= min_dt) & (df["date"] <= max_dt)]

            if df_backfill.empty and df_new.empty:
                # No new data outside existing range, but still check for gaps
                if df_mid.empty:
                    self.stats["records_skipped"] += 1
                    return 0
                # Gap-fill: records within range not previously imported
                self.stats["records_backfilled"] += len(df_mid)
                df = df_mid
            else:
                if not df_backfill.empty:
                    self.stats["records_backfilled"] += len(df_backfill)
                if not df_mid.empty:
                    self.stats["records_backfilled"] += len(df_mid)
                df = pd.concat([df_backfill, df_mid, df_new], ignore_index=True)

        # Set date as index for writer
        df = df.set_index("date")

        # Write to database
        self.writer.write_market_data(symbol, df)
        self.stats["records_imported"] += len(df)

        return len(df)

    def import_from_source(self, source_path: Path) -> dict:
        """
        Import from ZIP file or directory.

        Args:
            source_path: Path to ZIP file or directory

        Returns:
            Statistics dict
        """
        # Determine source type
        if source_path.is_file() and source_path.suffix.lower() == ".zip":
            file_iter = iter_day_files_from_zip(source_path)
            # Count total files
            with zipfile.ZipFile(source_path, "r") as zf:
                total_files = sum(
                    1
                    for n in zf.namelist()
                    if n.endswith(".day") and "lday" in n.replace("\\", "/")
                )
        elif source_path.is_dir():
            # Count files first
            total_files = sum(
                1
                for market in ["sh", "sz", "bj"]
                for _ in (source_path / market / "lday").glob("*.day")
                if (source_path / market / "lday").exists()
            )
            file_iter = iter_day_files_from_dir(source_path)
        else:
            raise ValueError(f"Invalid source: {source_path}")

        print(f"Found {total_files} .day files")
        print(f"Mode: {'Full import' if self.full_import else 'Incremental'}")
        print("=" * 60)

        # Process in batches
        batch = []
        batch_data = []

        with tqdm(total=total_files, desc="Importing", unit="file", ncols=100) as pbar:
            for filename, data in file_iter:
                pbar.update(1)

                # Skip non-stock files
                if not is_stock_code(filename):
                    self.stats["files_skipped"] += 1
                    continue

                # Convert to PTrade code
                symbol = filename_to_ptrade_code(filename)
                if not symbol:
                    self.stats["files_skipped"] += 1
                    continue

                # Parse data
                # Determine price divisor: 1000 for ETF/LOF, 100 for stocks
                bare_code = filename.replace(".day", "")[2:]
                divisor = 1000.0 if is_etf_code(bare_code) else 100.0
                df = parse_tdx_day_file(data, price_divisor=divisor)
                if df.empty:
                    self.stats["files_skipped"] += 1
                    continue

                batch.append(symbol)
                batch_data.append(df)

                # Process batch
                if len(batch) >= BATCH_SIZE:
                    self._process_batch(batch, batch_data)
                    batch = []
                    batch_data = []

            # Process remaining
            if batch:
                self._process_batch(batch, batch_data)

        return self.stats

    def _process_batch(self, symbols: list, dataframes: list):
        """Process a batch of stocks in a single transaction."""
        self.writer.begin()
        try:
            for symbol, df in zip(symbols, dataframes):
                try:
                    self.import_stock(symbol, df)
                    self.stats["files_processed"] += 1
                except Exception as e:
                    logger.warning(f"Failed to import {symbol}: {e}")
                    self.stats["files_skipped"] += 1

            self.writer.commit()
        except Exception as e:
            logger.error(f"Batch commit failed: {e}")
            self.writer.rollback()
            raise

    def close(self):
        """Close database connection."""
        self.writer.close()


def main():
    parser = argparse.ArgumentParser(
        description="Import TDX daily data from ZIP or directory",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Import from ZIP file (incremental)
    poetry run python scripts/import_tdx_day.py hsjday.zip

    # Full reimport from ZIP
    poetry run python scripts/import_tdx_day.py hsjday.zip --full

    # Import from extracted directory
    poetry run python scripts/import_tdx_day.py ~/tdx/vipdoc/

Data source:
    https://www.tdx.com.cn/article/vipdata.html
    Download: 沪深日线 (hsjday.zip)
        """,
    )
    parser.add_argument(
        "source",
        type=str,
        help="Path to ZIP file or extracted directory",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=DEFAULT_DB_PATH,
        help=f"Database path (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Full import (ignore existing data, reimport all)",
    )

    args = parser.parse_args()

    source_path = Path(args.source)
    if not source_path.exists():
        print(f"Error: Source not found: {source_path}")
        return 1

    print("=" * 60)
    print("TDX Daily Data Import")
    print("=" * 60)
    print(f"Source: {source_path}")
    print(f"Database: {args.db}")
    print()

    importer = TdxDayImporter(db_path=args.db, full_import=args.full)

    try:
        stats = importer.import_from_source(source_path)

        print()
        print("=" * 60)
        print("Import Complete")
        print("=" * 60)
        print(f"Files processed: {stats['files_processed']}")
        print(f"Files skipped: {stats['files_skipped']}")
        print(f"Records imported: {stats['records_imported']}")

        if stats["records_backfilled"] > 0:
            print(f"  - Backfilled (historical): {stats['records_backfilled']}")

        if stats["records_skipped"] > 0:
            print(f"Records skipped (up to date): {stats['records_skipped']}")

    finally:
        importer.close()

    return 0


if __name__ == "__main__":
    exit(main())
