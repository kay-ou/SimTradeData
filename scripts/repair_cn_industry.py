# -*- coding: utf-8 -*-
"""
Backfill CN stock_metadata.blocks (ZJHHY) from BaoStock query_stock_industry.

The desktop low-code industry filter expects blocks JSON with a ZJHHY entry:
    {"ZJHHY": [["J66", "货币金融服务"]]}
BaoStock returns industry strings like "J66货币金融服务", which this script
parses and writes into stock_metadata.blocks. Existing blocks are merged
(their ZJHHY entry is replaced, other keys preserved).

One-time repair job; not part of the daily download path.

Usage:
    poetry run python scripts/repair_cn_industry.py
    poetry run python scripts/repair_cn_industry.py --db data/cn.duckdb
"""

import argparse
import json
import logging
import re
from datetime import date

import duckdb
import pandas as pd
from tqdm import tqdm

from scripts.check_integrity import _is_cn_symbol
from simtradedata.config.field_mappings import BLOCKS_COVERAGE_FLOOR
from simtradedata.fetchers.baostock_fetcher import BaoStockFetcher
from simtradedata.utils.process_lock import ProcessLock
from simtradedata.writers.duckdb_writer import DEFAULT_DB_PATH

LOCK_FILE = "data/.repair_cn_industry.lock"
BATCH_SIZE = 100
FALLBACK_QUERY_DATE = "2025-06-30"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# BaoStock industry format: "J66货币金融服务" -> ("J66", "货币金融服务")
_INDUSTRY_RE = re.compile(r"^([A-Z]\d{2})(.*)$")


def parse_zjhhy(industry_text) -> tuple[str, str] | None:
    if not isinstance(industry_text, str):
        return None
    match = _INDUSTRY_RE.match(industry_text.strip())
    if not match or not match.group(2):
        return None
    return match.group(1), match.group(2)


def build_blocks(existing: str | None, code: str, label: str) -> str:
    zjhhy = {"ZJHHY": [[code, label]]}
    if not existing:
        return json.dumps(zjhhy, ensure_ascii=False)
    try:
        merged = json.loads(existing)
    except json.JSONDecodeError:
        merged = {}
    if not isinstance(merged, dict):
        merged = {}
    merged["ZJHHY"] = [[code, label]]
    return json.dumps(merged, ensure_ascii=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill CN industry blocks")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="DuckDB database path")
    args = parser.parse_args()

    with ProcessLock(LOCK_FILE):
        # read_only=False for duckdb < 1.0 compatibility (read_write kwarg is newer)
        con = duckdb.connect(args.db, read_only=False)
        try:
            symbols = [
                row[0]
                for row in con.execute(
                    "SELECT DISTINCT symbol FROM stocks ORDER BY symbol"
                ).fetchall()
            ]
            symbols = [s for s in symbols if _is_cn_symbol(s)]
            if not symbols:
                logger.error("No CN symbols found in stocks table")
                return 1

            todo = [
                row[0]
                for row in con.execute(
                    """
                    SELECT DISTINCT s.symbol
                    FROM stocks s
                    LEFT JOIN stock_metadata m ON s.symbol = m.symbol
                    WHERE m.symbol IS NULL OR m.blocks IS NULL
                       OR m.blocks NOT LIKE '%ZJHHY%'
                    ORDER BY s.symbol
                    """
                ).fetchall()
            ]
            todo = [s for s in todo if _is_cn_symbol(s)]
            logger.info(
                "%d CN symbols, %d already have ZJHHY, %d to fetch",
                len(symbols),
                len(symbols) - len(todo),
                len(todo),
            )

            existing = {
                row[0]: row[1]
                for row in con.execute(
                    "SELECT symbol, blocks FROM stock_metadata WHERE blocks IS NOT NULL"
                ).fetchall()
            }

            fetcher = BaoStockFetcher()
            fetcher.login()

            updates: list[tuple[str, str]] = []

            def flush() -> None:
                if not updates:
                    return
                batch = pd.DataFrame(updates, columns=["symbol", "blocks"])
                con.register("_blocks_batch", batch)
                try:
                    con.execute(
                        "UPDATE stock_metadata AS m "
                        "SET blocks = b.blocks "
                        "FROM _blocks_batch AS b "
                        "WHERE m.symbol = b.symbol"
                    )
                finally:
                    con.unregister("_blocks_batch")
                logger.info("Flushed %d blocks", len(updates))
                updates.clear()

            def fetch_one(symbol: str) -> str | None:
                for query_date in (date.today().isoformat(), FALLBACK_QUERY_DATE):
                    df = fetcher.fetch_stock_industry(symbol, date=query_date)
                    if df.empty:
                        continue
                    parsed = parse_zjhhy(df["industry"].values[0])
                    if parsed:
                        code, label = parsed
                        return build_blocks(existing.get(symbol), code, label)
                return None

            def backfill(items: list[str], desc: str) -> list[tuple[str, str]]:
                failures: list[tuple[str, str]] = []
                for symbol in tqdm(items, desc=desc):
                    try:
                        blocks = fetch_one(symbol)
                    except Exception as exc:  # noqa: BLE001 - report and continue
                        failures.append((symbol, str(exc)))
                        continue
                    if blocks:
                        updates.append((symbol, blocks))
                        if len(updates) >= BATCH_SIZE:
                            flush()
                flush()
                return failures

            try:
                failed = backfill(todo, "backfill blocks")

                # Retry failures once (transient baostock errors)
                if failed:
                    logger.info("Retrying %d failed symbols once", len(failed))
                    failed = backfill([symbol for symbol, _ in failed], "retry")
            finally:
                fetcher.logout()

            total, non_null = con.execute(
                "SELECT COUNT(*), COUNT(blocks) FROM stock_metadata"
            ).fetchone()
            zjhhy_count = con.execute(
                "SELECT COUNT(*) FROM stock_metadata "
                "WHERE blocks IS NOT NULL AND blocks LIKE '%ZJHHY%'"
            ).fetchone()[0]
            coverage = (zjhhy_count / total) if total else 0.0
            print(
                f"blocks backfill complete: {zjhhy_count}/{total} with ZJHHY "
                f"({coverage:.1%}), {len(failed)} failures"
            )
            if failed:
                for symbol, error in failed[:10]:
                    print(f"  failed: {symbol}: {error}")
            if coverage < BLOCKS_COVERAGE_FLOOR:
                logger.error(
                    "ZJHHY coverage %.1%% below target %.0f%%",
                    coverage * 100,
                    BLOCKS_COVERAGE_FLOOR * 100,
                )
                return 1
            return 0
        finally:
            con.close()


if __name__ == "__main__":
    raise SystemExit(main())
