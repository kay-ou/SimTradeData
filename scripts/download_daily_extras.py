# -*- coding: utf-8 -*-
"""
Download complementary daily data from EastMoney.

Fetches data not available from mootdx:
- Money flow — per-stock daily net inflows by order size
- LHB (Dragon Tiger Board) — stock appearances on the billboard
- Margin trading — margin balance data

LHB API only retains ~30 days of data, so this should run regularly.

Usage:
    uv run python scripts/download_daily_extras.py
    uv run python scripts/download_daily_extras.py --days 7
"""

import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path

from tqdm import tqdm

from simtradedata.fetchers.eastmoney_fetcher import EastMoneyFetcher
from simtradedata.writers.duckdb_writer import DEFAULT_DB_PATH, DuckDBWriter

LOG_FILE = "data/download_daily_extras.log"

Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=LOG_FILE, level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s", filemode="w",
)
logger = logging.getLogger(__name__)


def download_extras(days: int = 30, db_path: str = DEFAULT_DB_PATH):
    """Download complementary data for recent trading days."""
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    print("=" * 60)
    print("SimTradeData Daily Extras (EastMoney)")
    print("=" * 60)
    print(f"Date range: {start_date} ~ {end_date}")
    print(f"Database: {db_path}")

    writer = DuckDBWriter(db_path=str(db_path))
    fetcher = EastMoneyFetcher()
    fetcher.login()

    try:
        # 1. LHB — batch fetch for entire date range
        print("\n[1/3] Fetching LHB (Dragon Tiger Board)...")
        try:
            lhb_df = fetcher.fetch_lhb(start_date, end_date)
            if not lhb_df.empty:
                writer.write_lhb(lhb_df)
                print(f"  {len(lhb_df)} records written")
            else:
                print("  No LHB data")
        except Exception as e:
            logger.error(f"LHB fetch failed: {e}")
            print(f"  Failed: {e}")

        # 2. Get stock list from DB for per-stock APIs
        stock_symbols = writer.conn.execute(
            "SELECT DISTINCT symbol FROM stocks ORDER BY symbol"
        ).fetchall()
        stock_list = [row[0] for row in stock_symbols]
        print(f"\nStock pool: {len(stock_list)} symbols")

        # 3. Money flow — per stock
        print("\n[2/3] Fetching money flow...")
        mf_success = 0
        with tqdm(total=len(stock_list), desc="Money flow", unit="stock", ncols=80) as pbar:
            for symbol in stock_list:
                try:
                    mf_df = fetcher.fetch_money_flow(symbol, start_date, end_date)
                    if not mf_df.empty:
                        writer.write_money_flow(symbol, mf_df)
                        mf_success += 1
                except Exception as e:
                    logger.warning(f"Money flow failed for {symbol}: {e}")
                finally:
                    pbar.update(1)
        print(f"  {mf_success}/{len(stock_list)} stocks with data")

        # 4. Margin trading — per stock
        print("\n[3/3] Fetching margin trading...")
        margin_success = 0
        with tqdm(total=len(stock_list), desc="Margin", unit="stock", ncols=80) as pbar:
            for symbol in stock_list:
                try:
                    mg_df = fetcher.fetch_margin(symbol, start_date, end_date)
                    if not mg_df.empty:
                        writer.write_margin_trading(symbol, mg_df)
                        margin_success += 1
                except Exception as e:
                    logger.warning(f"Margin failed for {symbol}: {e}")
                finally:
                    pbar.update(1)
        print(f"  {margin_success}/{len(stock_list)} stocks with data")

    finally:
        writer.close()
        fetcher.logout()

    print("\n" + "=" * 60)
    print("Daily Extras Download Complete!")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Download daily extras from EastMoney"
    )
    parser.add_argument(
        "--days", type=int, default=30,
        help="Number of days to fetch (default: 30)",
    )
    parser.add_argument(
        "--db", type=str, default=DEFAULT_DB_PATH,
        help="Database path",
    )
    args = parser.parse_args()
    download_extras(days=args.days, db_path=args.db)
