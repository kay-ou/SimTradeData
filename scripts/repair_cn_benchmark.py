# -*- coding: utf-8 -*-
"""
Backfill CN benchmark index history from the TDX hsjday.zip package.

The daily mootdx fetch is depth-limited (offset=800), so the benchmark table
can silently lose its pre-2023 history. This one-time repair job imports the
full index day files (sh000300.day etc.) from the TDX vipdoc package into the
benchmark table and the stocks-table index rows.

Usage:
    poetry run python scripts/repair_cn_benchmark.py
    poetry run python scripts/repair_cn_benchmark.py --file data/downloads/hsjday.zip
    poetry run python scripts/repair_cn_benchmark.py --skip-download
"""

import argparse
import logging
from pathlib import Path

from scripts.download_tdx_day import (
    DOWNLOAD_DIR,
    DOWNLOAD_URL,
    download_file,
    needs_update,
    get_remote_file_info,
)
from scripts.import_tdx_day import (
    TdxDayImporter,
    filename_to_ptrade_code,
    iter_day_files_from_zip,
    parse_tdx_day_file,
)
from simtradedata.config.field_mappings import (
    BENCHMARK_CONFIG,
    BENCHMARK_HISTORY_FLOOR,
    benchmark_history_ok,
)
from simtradedata.utils.process_lock import ProcessLock
from simtradedata.writers.duckdb_writer import DEFAULT_DB_PATH

LOCK_FILE = "data/.repair_cn_benchmark.lock"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill CN benchmark index history")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="DuckDB database path")
    parser.add_argument("--file", default=None, help="Use existing hsjday.zip file")
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Do not download; fail if the zip is missing",
    )
    args = parser.parse_args()

    zip_path = Path(args.file) if args.file else DOWNLOAD_DIR / "hsjday.zip"

    with ProcessLock(LOCK_FILE):
        # needs_update returns True when the file is missing or stale
        if not args.skip_download and needs_update(
            zip_path, get_remote_file_info(DOWNLOAD_URL)
        ):
            print(f"Downloading {DOWNLOAD_URL} (~500MB)...")
            if not download_file(DOWNLOAD_URL, zip_path):
                logger.error("Download failed")
                return 1

        if not zip_path.exists():
            logger.error("Zip not found: %s (use --file or drop --skip-download)", zip_path)
            return 1

        indices = [
            BENCHMARK_CONFIG["default_index"],
            *BENCHMARK_CONFIG["alternatives"],
        ]
        wanted = set(indices)

        importer = TdxDayImporter(db_path=args.db)
        default_index = BENCHMARK_CONFIG["default_index"]
        imported: dict[str, int] = {}

        try:
            for filename, data in iter_day_files_from_zip(zip_path, wanted=wanted):
                code = filename_to_ptrade_code(filename)
                df = parse_tdx_day_file(data)
                if df.empty:
                    logger.warning("No records in %s", filename)
                    continue
                importer.import_stock(code, df)
                if code == default_index:
                    importer.writer.write_benchmark(df)
                imported[code] = len(df)
                logger.info("Imported %s: %d rows", code, len(df))

            missing = wanted - set(imported)
            if missing:
                logger.error("Index files missing from zip: %s", sorted(missing))
                return 1

            # Verify the benchmark table now covers the required history floor
            min_date, max_date, rows = importer.writer.conn.execute(
                "SELECT MIN(date)::VARCHAR, MAX(date)::VARCHAR, COUNT(*) FROM benchmark"
            ).fetchone()
        finally:
            importer.close()
        print(f"benchmark table: {min_date} -> {max_date} ({rows} rows)")
        if not benchmark_history_ok(min_date):
            logger.error(
                "benchmark still truncated: %s (required <= %s)",
                min_date,
                BENCHMARK_HISTORY_FLOOR,
            )
            return 1

        print("Benchmark history backfill complete.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
