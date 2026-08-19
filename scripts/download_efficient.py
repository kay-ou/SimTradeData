# -*- coding: utf-8 -*-
"""
Efficient BaoStock data download program with DuckDB storage

Features:
1. Auto-incremental: queries MAX(date) to determine start_date per symbol
2. Auto-dedup: uses INSERT OR REPLACE with PRIMARY KEY constraints
3. Batch transaction: commits once per batch for better performance

Output: DuckDB database (data/cn.duckdb)
Export to Parquet: use scripts/export_parquet.py
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import baostock as bs
import pandas as pd
from tqdm import tqdm

from simtradedata.config.field_mappings import BENCHMARK_CONFIG
from simtradedata.fetchers.baostock_fetcher import BaoStockFetcher
from simtradedata.fetchers.unified_fetcher import UnifiedDataFetcher
from simtradedata.processors.data_splitter import DataSplitter
from simtradedata.utils.process_lock import ProcessLock
from simtradedata.writers.duckdb_writer import DEFAULT_DB_PATH, DuckDBWriter

# Configuration
OUTPUT_DIR = "data"
LOG_FILE = "data/download_efficient.log"
LOCK_FILE = "data/.download.lock"

# Date range configuration
START_DATE = "2015-01-01"
END_DATE = None  # None means use current date

# Batch configuration
BATCH_SIZE = 20

# Ensure log directory exists
Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    filemode="w",
)
logger = logging.getLogger(__name__)


class EfficientBaoStockDownloader:
    """Efficient BaoStock data downloader with DuckDB storage"""

    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        skip_fundamentals: bool = False,
        skip_metadata: bool = False,
        valuation_only: bool = False,
    ):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.unified_fetcher = UnifiedDataFetcher()
        self.standard_fetcher = BaoStockFetcher()
        self.data_splitter = DataSplitter()
        self.writer = DuckDBWriter(db_path=str(self.db_path))

        self.skip_fundamentals = skip_fundamentals
        self.skip_metadata = skip_metadata
        self.valuation_only = valuation_only

        self.status_cache = {}
        self.failed_stocks = []

    def get_incremental_start_date(self, symbol: str) -> str:
        """
        Get incremental start date for a symbol.
        Returns next day after MAX(date), or START_DATE if no data.
        """
        table = "valuation" if self.valuation_only else "stocks"
        max_date = self.writer.get_max_date(table, symbol)
        if max_date:
            next_day = datetime.strptime(max_date, "%Y-%m-%d") + timedelta(days=1)
            return next_day.strftime("%Y-%m-%d")
        return START_DATE

    def download_stock_data(
        self, symbol: str, start_date: str, end_date: str
    ) -> dict:
        """Download all data for a single stock with auto-incremental logic"""
        try:
            # Auto-incremental: determine actual start date
            actual_start = self.get_incremental_start_date(symbol)
            if actual_start > start_date:
                start_date = actual_start

            # BaoStock date ranges are inclusive; query the target day when
            # the per-symbol cursor is exactly equal to the requested end date.
            if start_date > end_date:
                return None

            unified_df = self.unified_fetcher.fetch_unified_daily_data(
                symbol, start_date, end_date
            )

            if unified_df.empty:
                logger.warning(f"No data for {symbol}")
                return None

            split_data = self.data_splitter.split_data(unified_df)

            # In valuation-only mode, skip market data and related downloads
            if self.valuation_only:
                updated = False

                # Write valuation data
                valuation_data = split_data.get("valuation")
                if valuation_data is not None and not valuation_data.empty:
                    self.writer.write_valuation(symbol, valuation_data)
                    updated = True

                # Cache status data
                status_data = split_data.get("status")
                if status_data is not None and not status_data.empty:
                    self.status_cache[symbol] = status_data
                    updated = True

                if updated:
                    return {"stock_code": symbol}
                return None

            # Full mode: write market data
            if "market" in split_data:
                self.writer.write_market_data(symbol, split_data["market"])

            valuation_data = split_data.get("valuation")

            # Cache status data
            if "status" in split_data:
                self.status_cache[symbol] = split_data["status"]


            # Download dividend (ex-rights) data
            try:
                start_year = int(start_date[:4])
                end_year = int(end_date[:4])
                dividend_df = self.standard_fetcher.fetch_dividend_data_range(
                    symbol, start_year, end_year
                )
                if not dividend_df.empty:
                    self.writer.write_exrights(symbol, dividend_df)
            except Exception as e:
                logger.warning(f"Failed to fetch dividend for {symbol}: {e}")

            # Download basic info (skip for incremental updates)
            basic_info = {}
            is_incremental = actual_start > START_DATE
            if not self.skip_metadata and not is_incremental:
                try:
                    basic_df = self.standard_fetcher.fetch_stock_basic(symbol)
                    if not basic_df.empty:
                        basic_info = {
                            "status": basic_df["status"].values[0],
                            "ipoDate": basic_df["ipoDate"].values[0],
                            "outDate": basic_df["outDate"].values[0],
                            "type": basic_df["type"].values[0],
                            "code_name": basic_df["code_name"].values[0],
                        }
                except Exception as e:
                    logger.warning(f"Failed to fetch basic info for {symbol}: {e}")

            # Download industry info (skip for incremental updates)
            industry_info = {}
            if not self.skip_metadata and not is_incremental:
                try:
                    industry_df = self.standard_fetcher.fetch_stock_industry(symbol)
                    if not industry_df.empty:
                        industry_info = {
                            "industry": industry_df["industry"].values[0],
                            "industryClassification": industry_df[
                                "industryClassification"
                            ].values[0],
                        }
                except Exception as e:
                    logger.warning(f"Failed to fetch industry for {symbol}: {e}")

            # Write valuation data (raw data only)
            if valuation_data is not None and not valuation_data.empty:
                self.writer.write_valuation(symbol, valuation_data)

            # Only return metadata for new stocks (not incremental updates)
            if is_incremental:
                return None

            return {
                "stock_code": symbol,
                "stock_name": basic_info.get("code_name", ""),
                "listed_date": basic_info.get("ipoDate", ""),
                "de_listed_date": basic_info.get("outDate", ""),
                "security_type": basic_info.get("type", ""),
                "listing_status": basic_info.get("status", ""),
                "blocks": json.dumps(industry_info, ensure_ascii=False)
                if industry_info
                else None,
            }

        except Exception as e:
            logger.error(f"Failed to download {symbol}: {e}")
            self.failed_stocks.append(symbol)
            return None

    def download_batch(
        self, stock_batch: list, start_date: str, end_date: str, pbar=None
    ) -> list:
        """Download data for a batch of stocks in a single transaction"""
        metadata_list = []

        self.writer.begin()
        try:
            for stock in stock_batch:
                try:
                    metadata = self.download_stock_data(stock, start_date, end_date)
                    if metadata:
                        metadata_list.append(metadata)
                except Exception as e:
                    logger.error(f"Exception downloading {stock}: {e}")
                finally:
                    if pbar:
                        pbar.update(1)

            self.writer.commit()
        except Exception:
            self.writer.rollback()
            raise

        return metadata_list

    def aggregate_and_write_status(self) -> None:
        """
        Aggregate status_cache and write to stock_status table

        Transforms per-symbol status data into per-date aggregated format:
        - For each date, collect all symbols that are ST
        - For each date, collect all symbols that are HALT (tradestatus=0)
        """
        if not self.status_cache:
            logger.info("No status data to aggregate")
            return

        # Collect all status data into a single DataFrame
        all_status = []
        for symbol, status_df in self.status_cache.items():
            if status_df is None or status_df.empty:
                continue
            df = status_df.copy()
            df["symbol"] = symbol
            all_status.append(df)

        if not all_status:
            logger.info("No valid status data to aggregate")
            return

        combined = pd.concat(all_status, ignore_index=True)

        # Ensure date column exists
        if "date" not in combined.columns:
            logger.warning("No date column in status data")
            return

        # Convert isST and tradestatus to int
        if "isST" in combined.columns:
            combined["isST"] = pd.to_numeric(combined["isST"], errors="coerce").fillna(0)
        if "tradestatus" in combined.columns:
            combined["tradestatus"] = pd.to_numeric(
                combined["tradestatus"], errors="coerce"
            ).fillna(1)

        combined["_date_str"] = pd.to_datetime(combined["date"]).dt.strftime("%Y%m%d")

        if "isST" in combined.columns:
            st_data = combined[combined["isST"] == 1]
            for date_str, day_data in st_data.groupby("_date_str", sort=False):
                self.writer.write_stock_status(date_str, "ST", day_data["symbol"].tolist())

        if "tradestatus" in combined.columns:
            halt_data = combined[combined["tradestatus"] == 0]
            for date_str, day_data in halt_data.groupby("_date_str", sort=False):
                self.writer.write_stock_status(
                    date_str, "HALT", day_data["symbol"].tolist()
                )

        logger.info(f"Aggregated status data for {len(combined['date'].unique())} dates")

    def refresh_daily_stock_status(self, target_date: str) -> None:
        """Refresh target-day ST/HALT status from BaoStock's daily stock pool."""
        from simtradedata.utils.code_utils import convert_to_ptrade_code

        rs = bs.query_all_stock(day=target_date)
        if rs.error_code != "0":
            logger.warning("Failed to refresh stock status %s: %s", target_date, rs.error_msg)
            return

        stocks_df = rs.get_data()
        if stocks_df.empty:
            logger.warning("No stock status data for %s", target_date)
            return

        date_key = target_date.replace("-", "")
        if "code" not in stocks_df.columns:
            logger.warning("No code column in stock status data for %s", target_date)
            return

        stocks_df = stocks_df.copy()
        stocks_df["symbol"] = stocks_df["code"].map(
            lambda code: convert_to_ptrade_code(code, "baostock")
        )

        if "tradeStatus" in stocks_df.columns:
            halted = stocks_df[stocks_df["tradeStatus"].astype(str) == "0"][
                "symbol"
            ].dropna().tolist()
            self.writer.write_stock_status(date_key, "HALT", halted)

        if "code_name" in stocks_df.columns:
            st_symbols = stocks_df[
                stocks_df["code_name"].astype(str).str.contains("ST", na=False)
            ]["symbol"].dropna().tolist()
            self.writer.write_stock_status(date_key, "ST", st_symbols)

    def download_fundamentals_by_quarter(
        self, stock_pool: list, start_date: str, end_date: str
    ) -> None:
        """Download fundamentals organized by quarter with incremental detection.

        Iterates quarter -> stocks (instead of stock -> quarters), enabling:
        1. Skip entirely completed quarters via fundamentals_progress table
        2. Skip individual symbol+quarter pairs already in DB
        3. Better progress tracking and error recovery
        """
        from simtradedata.utils.sampling import quarter_end_date
        from simtradedata.utils.ttm_calculator import get_quarters_in_range

        quarters = get_quarters_in_range(start_date, end_date)
        if not quarters:
            print("  No quarters in date range")
            return

        completed_quarters = self.writer.get_completed_fundamental_quarters()
        pending_quarters = [
            (y, q) for y, q in quarters if (y, q) not in completed_quarters
        ]

        if not pending_quarters:
            print("  All quarters already completed")
            return

        print(f"  Total quarters: {len(quarters)}, "
              f"completed: {len(quarters) - len(pending_quarters)}, "
              f"pending: {len(pending_quarters)}")

        for qi, (year, quarter) in enumerate(pending_quarters, 1):
            q_end = quarter_end_date(year, quarter)
            print(f"\n  Quarter {qi}/{len(pending_quarters)}: "
                  f"{year}Q{quarter} (end: {q_end})")

            # Batch process stocks for this quarter
            batches = [
                stock_pool[i : i + BATCH_SIZE]
                for i in range(0, len(stock_pool), BATCH_SIZE)
            ]

            success_count = 0
            skip_count = 0

            with tqdm(
                total=len(stock_pool),
                desc=f"  {year}Q{quarter}",
                unit="stock",
                ncols=100,
            ) as pbar:
                for batch in batches:
                    self.writer.begin()
                    try:
                        for symbol in batch:
                            try:
                                if self.writer.has_fundamental(symbol, q_end):
                                    skip_count += 1
                                    continue

                                fund_df = (
                                    self.standard_fetcher
                                    .fetch_quarterly_fundamentals(
                                        symbol, year, quarter
                                    )
                                )

                                if not fund_df.empty:
                                    if "end_date" in fund_df.columns:
                                        fund_df = fund_df.sort_values("end_date")
                                        fund_df = fund_df.set_index("end_date")
                                    self.writer.write_fundamentals(
                                        symbol, fund_df
                                    )
                                    success_count += 1
                            except Exception as e:
                                logger.warning(
                                    f"Failed fundamentals {symbol} "
                                    f"{year}Q{quarter}: {e}"
                                )
                            finally:
                                pbar.update(1)

                        self.writer.commit()
                    except Exception:
                        self.writer.rollback()
                        raise

            self.writer.mark_fundamental_quarter_completed(
                year, quarter, success_count
            )
            print(f"    Completed: {success_count} new, {skip_count} skipped")


def download_all_data(
    skip_fundamentals=False,
    skip_metadata=False,
    start_date=None,
    valuation_only=False,
):
    """
    Main download function with auto-incremental logic.

    Each symbol automatically starts from MAX(date)+1, no manual resume needed.
    """
    with ProcessLock(LOCK_FILE):
        print("=" * 70)
        print("SimTradeData Download (DuckDB Storage)")
        print("=" * 70)
        print("Mode: Auto-incremental (queries MAX(date) per symbol)")

        if valuation_only:
            print("Valuation-only mode: downloading PE/PB/PS/PCF/turnover + status only")
        if skip_fundamentals:
            print("Fundamentals: Skipped")
        if skip_metadata:
            print("Metadata: Skipped")

        print("=" * 70)

        # Date range
        end_date = (
            datetime.now().date()
            if END_DATE is None
            else datetime.strptime(END_DATE, "%Y-%m-%d").date()
        )

        use_start_date = start_date or START_DATE
        start_date_obj = datetime.strptime(use_start_date, "%Y-%m-%d").date()

        start_date_str = start_date_obj.strftime("%Y-%m-%d")
        end_date_str = end_date.strftime("%Y-%m-%d")

        print(f"\nDate range: {start_date_str} ~ {end_date_str}")
        print("(Each symbol starts from its MAX(date)+1 automatically)")

        # Initialize downloader
        db_path = Path(DEFAULT_DB_PATH)
        downloader = EfficientBaoStockDownloader(
            db_path=str(db_path),
            skip_fundamentals=skip_fundamentals,
            skip_metadata=skip_metadata,
            valuation_only=valuation_only,
        )
        downloader.unified_fetcher.login()
        downloader.standard_fetcher.login()

        try:
            # Get stock pool - merge from multiple sources
            from simtradedata.utils.sampling import (
                generate_monthly_end_dates,
                generate_monthly_start_dates,
            )
            from simtradedata.utils.code_utils import convert_to_ptrade_code

            def is_a_share_stock(code: str) -> bool:
                """Filter to only A-share stocks (exclude ETF, index, bonds)"""
                symbol = code.split('.')[0] if '.' in code else code
                if len(symbol) != 6:
                    return False
                prefix = symbol[:3]
                # Shanghai: 600/601/603/605 (main), 688/689 (STAR)
                # Shenzhen: 000/001/002/003 (main), 300/301/302 (ChiNext)
                valid_prefixes = {
                    '600', '601', '603', '605', '688', '689',  # SH
                    '000', '001', '002', '003', '300', '301', '302',  # SZ
                }
                return prefix in valid_prefixes

            sample_dates = generate_monthly_start_dates(
                START_DATE, end_date.strftime("%Y-%m-%d")
            )

            # Check cached data
            sampled_dates = set(downloader.writer.get_sampled_dates())
            cached_pool = downloader.writer.get_stock_pool()

            # Also get stocks already in database (from TDX import)
            existing_stocks = set(downloader.writer.get_existing_stocks("stocks"))

            # Filter to only unsampled dates
            new_dates = [d for d in sample_dates if d.date() not in sampled_dates]

            if cached_pool and not new_dates:
                # Merge cached pool with existing stocks
                all_stocks = set(cached_pool) | existing_stocks
                # Filter to A-share stocks only
                stock_pool = sorted([s for s in all_stocks if is_a_share_stock(s)])
                print(f"\nStock pool: {len(stock_pool)} A-shares")
                print(f"  (from stock_pool: {len(cached_pool)}, from TDX import: {len(existing_stocks)})")
            else:
                # Sample new dates (or all if first run)
                dates_to_sample = new_dates if cached_pool else sample_dates
                desc = f"Sampling {len(dates_to_sample)} new dates" if cached_pool else "Sampling stock pool"
                print(f"\n{desc}...")

                all_stocks = set(cached_pool) if cached_pool else set()
                for date_obj in tqdm(dates_to_sample, desc=desc):
                    date_str = date_obj.strftime("%Y-%m-%d")
                    try:
                        rs = bs.query_all_stock(day=date_str)
                        if rs.error_code == "0":
                            stocks_df = rs.get_data()
                            if not stocks_df.empty:
                                codes = [convert_to_ptrade_code(c, "baostock")
                                         for c in stocks_df["code"].tolist()]
                                all_stocks.update(codes)
                                downloader.writer.update_stock_pool(codes, date_obj.date())
                        downloader.writer.add_sampled_date(date_obj.date())
                    except Exception as e:
                        logger.error(f"Failed to sample {date_str}: {e}")

                # Merge with existing stocks from TDX import
                all_stocks |= existing_stocks
                # Filter to A-share stocks only
                stock_pool = sorted([s for s in all_stocks if is_a_share_stock(s)])
                print(f"Total A-share stocks: {len(stock_pool)}")

            # Download in batches
            batches = [
                stock_pool[i : i + BATCH_SIZE]
                for i in range(0, len(stock_pool), BATCH_SIZE)
            ]

            # Check if data is already up to date
            # Only skip if ALL stocks have been covered AND there are no new trading days
            check_table = "valuation" if valuation_only else "stocks"
            skip_stock_download = False

            # Count how many stocks in pool are missing from the target table
            existing_symbols = set(downloader.writer.get_existing_stocks(check_table))
            missing_count = len([s for s in stock_pool if s not in existing_symbols])

            if missing_count > 0:
                print(f"\n{missing_count} stocks missing from {check_table} table, will download")
            else:
                # All stocks exist, check if there are new trading days
                global_max_date = downloader.writer.get_max_date(check_table)
                if global_max_date:
                    test_start = (datetime.strptime(global_max_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
                    if test_start > end_date_str:
                        print(f"\n{check_table.capitalize()} data already up to date (max_date: {global_max_date})")
                        skip_stock_download = True
                    else:
                        try:
                            test_df = downloader.unified_fetcher.fetch_unified_daily_data(
                                "000001.SZ", test_start, end_date_str
                            )
                            if test_df.empty:
                                print(f"\n{check_table.capitalize()} data already up to date (max_date: {global_max_date})")
                                print("No new trading days since last update, skipping stock download.")
                                skip_stock_download = True
                        except Exception:
                            pass  # If check fails, proceed with download

            if skip_stock_download:
                all_metadata = []
                success = 0
                skipped = len(stock_pool)
            else:
                print(f"\nProcessing {len(stock_pool)} stocks in {len(batches)} batches...")
                print(f"Batch size: {BATCH_SIZE}")
                print("Note: Each symbol auto-detects its incremental start date")
                print("=" * 60)

                all_metadata = []
                success = 0
                skipped = 0

                # Use a single progress bar for total stocks with more info
                with tqdm(
                    total=len(stock_pool),
                    desc="Downloading stocks",
                    unit="stock",
                    ncols=100,
                    bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]"
                ) as pbar:
                    for batch in batches:
                        try:
                            metadata_list = downloader.download_batch(
                                batch, start_date_str, end_date_str, pbar
                            )
                            all_metadata.extend(metadata_list)
                            success += len(metadata_list)
                            skipped += len(batch) - len(metadata_list)
                        except Exception as e:
                            logger.error(f"Batch failed: {e}")
                            pbar.update(len(batch))

                print("=" * 60)
                print(f"Download complete: {success} updated, {skipped} skipped/failed")

            # Save metadata (skip in valuation-only mode)
            if all_metadata and not valuation_only:
                print("\nSaving stock metadata...")
                meta_df = pd.DataFrame(all_metadata)
                meta_df.set_index("stock_code", inplace=True)
                meta_df = meta_df.sort_index()
                downloader.writer.write_stock_metadata(meta_df)

            # Aggregate and save stock status (ST/HALT) - only if we have new data
            if not skip_stock_download and downloader.status_cache:
                print("\nAggregating stock status...")
                try:
                    downloader.aggregate_and_write_status()
                    print("  Stock status saved")
                except Exception as e:
                    logger.error(f"Failed to aggregate stock status: {e}")

            # Download quarterly fundamentals (skip in valuation-only mode)
            if not skip_fundamentals and not valuation_only:
                print("\nDownloading quarterly fundamentals...")
                try:
                    downloader.download_fundamentals_by_quarter(
                        stock_pool, start_date_str, end_date_str
                    )
                except Exception as e:
                    logger.error(f"Failed to download fundamentals: {e}")

            # Download global data
            print("\nDownloading global data...")

            print("  Daily stock status...")
            try:
                downloader.refresh_daily_stock_status(end_date_str)
                print("    Done")
            except Exception as e:
                logger.error(f"Failed to refresh daily stock status: {e}")

            # Trading calendar (skip in valuation-only mode)
            if not valuation_only:
                print("  Trading calendar...")
                try:
                    trade_cal = downloader.standard_fetcher.fetch_trade_calendar(
                        start_date_str, end_date_str
                    )
                    if not trade_cal.empty:
                        trade_days = trade_cal[trade_cal["is_trading_day"] == "1"]
                        trade_days = trade_days.rename(
                            columns={"calendar_date": "trade_date"}
                        )
                        downloader.writer.write_trade_days(trade_days)
                        print(f"    {len(trade_days)} days")
                except Exception as e:
                    logger.error(f"Failed to download trading calendar: {e}")

            # Benchmark index (skip in valuation-only mode)
            if not valuation_only:
                BENCHMARK_INDEX = BENCHMARK_CONFIG["default_index"]
                print(f"  Benchmark index ({BENCHMARK_INDEX})...")
                try:
                    benchmark_df = downloader.unified_fetcher.fetch_index_data(
                        BENCHMARK_INDEX, start_date_str, end_date_str
                    )
                    if not benchmark_df.empty:
                        downloader.writer.write_benchmark(benchmark_df)
                        print(f"    {len(benchmark_df)} days")
                except Exception as e:
                    logger.error(f"Failed to download benchmark: {e}")

            # Index constituents (use month-end sampling to match PTrade)
            # Check existing data to skip already downloaded dates
            print("  Index constituents...")
            try:
                # Get existing index constituent dates
                existing_dates = set()
                try:
                    result = downloader.writer.conn.execute(
                        "SELECT DISTINCT date FROM index_constituents"
                    ).fetchall()
                    existing_dates = {row[0] for row in result}
                except Exception:
                    pass

                index_sample_dates = generate_monthly_end_dates(
                    START_DATE, end_date.strftime("%Y-%m-%d")
                )

                # Filter to only new dates
                new_dates = [d for d in index_sample_dates if d.strftime("%Y%m%d") not in existing_dates]

                if not new_dates:
                    print(f"    All {len(index_sample_dates)} dates already downloaded")
                else:
                    print(f"    Downloading {len(new_dates)} new dates (skipping {len(existing_dates)} existing)...")
                    for date_obj in new_dates:
                        date_str = date_obj.strftime("%Y%m%d")

                        for index_code in ["000016.SS", "000300.SS", "000905.SS"]:
                            try:
                                stocks_df = downloader.standard_fetcher.fetch_index_stocks(
                                    index_code, date_obj.strftime("%Y-%m-%d")
                                )
                                if not stocks_df.empty:
                                    from simtradedata.utils.code_utils import (
                                        convert_to_ptrade_code,
                                    )

                                    ptrade_codes = [
                                        convert_to_ptrade_code(code, "baostock")
                                        for code in stocks_df["code"].tolist()
                                    ]
                                    downloader.writer.write_index_constituents(
                                        date_str, index_code, ptrade_codes
                                    )
                            except Exception as e:
                                logger.warning(f"Index {index_code} {date_str}: {e}")

                    print(f"    Done ({len(new_dates)} dates)")
            except Exception as e:
                logger.error(f"Failed to download index constituents: {e}")

        finally:
            try:
                downloader.writer.close()
            except Exception as e:
                logger.warning("Failed to close writer: %s", e)
            try:
                downloader.unified_fetcher.logout()
            except Exception as e:
                logger.warning("Failed to logout unified_fetcher: %s", e)
            try:
                downloader.standard_fetcher.logout()
            except Exception as e:
                logger.warning("Failed to logout standard_fetcher: %s", e)

        # Summary
        print("\n" + "=" * 70)
        print("Download Complete!")
        print("=" * 70)

        db_file = Path(DEFAULT_DB_PATH)
        if db_file.exists():
            db_size = db_file.stat().st_size / (1024 * 1024)
            print(f"\nDatabase: {db_file}")
            print(f"Size: {db_size:.1f} MB")

        print("\nTo export to Parquet format:")
        print("  poetry run python scripts/export_parquet.py")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Download BaoStock data to DuckDB (auto-incremental)"
    )
    parser.add_argument(
        "--skip-fundamentals",
        action="store_true",
        help="Skip quarterly fundamentals download",
    )
    parser.add_argument(
        "--skip-metadata",
        action="store_true",
        help="Skip stock basic info and industry classification",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="Override default start date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--valuation-only",
        action="store_true",
        help="Only download valuation (PE/PB/PS/PCF/turnover) + status + index constituents",
    )

    args = parser.parse_args()

    download_all_data(
        skip_fundamentals=args.skip_fundamentals,
        skip_metadata=args.skip_metadata,
        start_date=args.start_date,
        valuation_only=args.valuation_only,
    )
