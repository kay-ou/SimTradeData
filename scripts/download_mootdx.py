# -*- coding: utf-8 -*-
"""
Mootdx data download program with DuckDB storage

Complements download_efficient.py (BaoStock) by fetching data from mootdx:
- Daily K-line data (OHLCV)
- XDXR (ex-rights/ex-dividend) data
- Adjust factors (derived from hfq prices)
- Batch financial data (from Affair ZIP files)
- Trading calendar (derived from index data)
- Benchmark index data

Output: DuckDB database (data/cn.duckdb)
"""

import argparse
import logging
import socket
import warnings
from datetime import datetime, timedelta
from pathlib import Path

# Suppress FutureWarning from mootdx using deprecated pandas fillna(method=...)
warnings.filterwarnings("ignore", category=FutureWarning, module="mootdx")

import pandas as pd  # noqa: E402
from tqdm import tqdm  # noqa: E402

from simtradedata.config.field_mappings import (  # noqa: E402
    BENCHMARK_CONFIG,
    CN_HISTORY_START,
)
from simtradedata.fetchers.mootdx_unified_fetcher import MootdxUnifiedFetcher  # noqa: E402
from simtradedata.utils.process_lock import ProcessLock  # noqa: E402
from simtradedata.writers.duckdb_writer import DEFAULT_DB_PATH, DuckDBWriter  # noqa: E402

# Configuration
OUTPUT_DIR = "data"
LOG_FILE = "data/download_mootdx.log"
LOCK_FILE = "data/.download_mootdx.lock"

# Date range
START_DATE = "2015-01-01"
END_DATE = None  # None means current date

# Batch size for stock processing
BATCH_SIZE = 20
LATEST_COVERAGE_SKIP_RATIO = 0.95

# Ensure log directory exists
Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    filemode="w",
)
logger = logging.getLogger(__name__)

# Prevent infinite hangs on TDX server socket recv()
socket.setdefaulttimeout(30)


def _latest_stock_coverage(
    current_symbols: set[str], stock_pool: list[str]
) -> tuple[int, int, float]:
    stock_set = set(stock_pool)
    total = len(stock_set)
    if total == 0:
        return 0, 0, 0.0
    latest_count = len(stock_set & current_symbols)
    return latest_count, total, latest_count / total


class MootdxDownloader:
    """Mootdx data downloader with DuckDB storage"""

    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        skip_fundamentals: bool = False,
        download_dir: str = None,
    ):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.unified_fetcher = MootdxUnifiedFetcher(download_dir=download_dir)
        self.writer = DuckDBWriter(db_path=str(self.db_path))

        self.skip_fundamentals = skip_fundamentals
        self.download_dir = download_dir
        self.failed_stocks = []

    def _reconnect(self):
        """Reconnect to TDX server after connection error."""
        logger.warning("Reconnecting to TDX server...")
        try:
            self.unified_fetcher.logout()
        except Exception:
            pass
        try:
            self.unified_fetcher.login()
            logger.info("TDX server reconnected")
        except Exception as e:
            logger.error(f"Failed to reconnect: {e}")

    def get_incremental_start_date(self, symbol: str) -> str:
        """Get next date after MAX(date) for incremental updates."""
        max_date = self.writer.get_max_date("stocks", symbol)
        if max_date:
            next_day = datetime.strptime(max_date, "%Y-%m-%d") + timedelta(days=1)
            return next_day.strftime("%Y-%m-%d")
        return START_DATE

    def download_stock_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        include_ohlcv: bool = True,
    ) -> bool:
        """
        Download daily OHLCV + adjust factor + XDXR for a single stock.

        OHLCV uses incremental logic (skip if up to date).
        Adjust factor and XDXR are downloaded if missing for the symbol,
        even when OHLCV is already current (e.g. after TDX bulk import).

        Returns:
            True if any data was downloaded, False if all skipped/failed
        """
        try:
            downloaded = False

            # --- OHLCV: incremental ---
            actual_start = self.get_incremental_start_date(symbol)
            ohlcv_start = max(actual_start, start_date)

            if include_ohlcv and ohlcv_start <= end_date:
                try:
                    df = self.unified_fetcher.fetch_daily_data(
                        symbol, ohlcv_start, end_date
                    )

                    if not df.empty:
                        # Filter out empty rows (halted stocks return rows with all NaN)
                        price_cols = ["open", "high", "low", "close"]
                        available_cols = [c for c in price_cols if c in df.columns]
                        if available_cols:
                            df = df.dropna(subset=available_cols, how="all")

                        if not df.empty:
                            if "date" in df.columns:
                                market_df = df.set_index("date")
                            else:
                                market_df = df

                            if "amount" in market_df.columns:
                                market_df = market_df.rename(
                                    columns={"amount": "money"}
                                )

                            self.writer.write_market_data(symbol, market_df)
                            downloaded = True
                except (socket.timeout, ConnectionError):
                    logger.warning(
                        f"Connection error fetching OHLCV for {symbol}, reconnecting"
                    )
                    self._reconnect()
                except Exception as e:
                    logger.warning(f"Failed to fetch OHLCV for {symbol}: {e}")

            # --- XDXR: download if missing or suspiciously sparse for this symbol ---
            exr_max = self.writer.get_max_date("exrights", symbol)
            needs_xdxr = not exr_max
            if exr_max:
                exr_count = self.writer.conn.execute(
                    "SELECT COUNT(*) FROM exrights WHERE symbol = ?", [symbol]
                ).fetchone()[0]
                if exr_count < 3:
                    logger.info(
                        f"XDXR for {symbol} has only {exr_count} events (max_date={exr_max}), "
                        f"re-downloading to fill potential gaps"
                    )
                    needs_xdxr = True
            if needs_xdxr:
                try:
                    xdxr_df = self.unified_fetcher.fetch_xdxr(symbol)
                    if not xdxr_df.empty:
                        exrights = self._convert_xdxr_to_exrights(xdxr_df)
                        if not exrights.empty:
                            self.writer.write_exrights(symbol, exrights)
                            downloaded = True
                except (socket.timeout, ConnectionError):
                    logger.warning(f"Connection error fetching XDXR for {symbol}, reconnecting")
                    self._reconnect()
                except Exception as e:
                    logger.warning(f"Failed to fetch XDXR for {symbol}: {e}")

            return downloaded

        except (socket.timeout, ConnectionError):
            logger.warning(f"Connection error downloading {symbol}, reconnecting")
            self.failed_stocks.append(symbol)
            self._reconnect()
            return False
        except Exception as e:
            logger.error(f"Failed to download {symbol}: {e}")
            self.failed_stocks.append(symbol)
            return False

    def _convert_xdxr_to_exrights(self, xdxr_df: pd.DataFrame) -> pd.DataFrame:
        """
        Convert mootdx XDXR data to PTrade exrights format.

        mootdx XDXR fields (per 10 shares):
          songzhuangu = bonus + conversion shares
          peigu       = rights issue shares
          peigujia    = rights issue price (per share, NOT per 10)
          fenhong     = cash dividend

        PTrade exrights fields (per share):
          allotted_ps = songzhuangu / 10
          rationed_ps = peigu / 10
          rationed_px = peigujia (already per share)
          bonus_ps    = fenhong / 10
          dividend    = fenhong / 10

        Args:
            xdxr_df: Raw XDXR DataFrame from mootdx

        Returns:
            DataFrame with columns: date, allotted_ps, rationed_ps,
                                   rationed_px, bonus_ps, dividend
        """
        if xdxr_df.empty:
            return pd.DataFrame()

        # XDXR category: 1 = ex-rights/ex-dividend
        if "category" in xdxr_df.columns:
            xdxr_df = xdxr_df[xdxr_df["category"] == 1]

        if xdxr_df.empty:
            return pd.DataFrame()

        result = pd.DataFrame()

        # Construct date from year/month/day columns (mootdx format)
        if "year" in xdxr_df.columns and "month" in xdxr_df.columns and "day" in xdxr_df.columns:
            result["date"] = pd.to_datetime(
                xdxr_df[["year", "month", "day"]].astype(int)
            )
        elif "datetime" in xdxr_df.columns:
            result["date"] = pd.to_datetime(xdxr_df["datetime"])
        elif "date" in xdxr_df.columns:
            result["date"] = pd.to_datetime(xdxr_df["date"])
        else:
            return pd.DataFrame()

        # Allotted shares per share (songzhuangu / 10)
        result["allotted_ps"] = (
            pd.to_numeric(xdxr_df.get("songzhuangu", 0), errors="coerce").fillna(0.0) / 10.0
        )

        # Rationed shares per share (peigu / 10)
        result["rationed_ps"] = (
            pd.to_numeric(xdxr_df.get("peigu", 0), errors="coerce").fillna(0.0) / 10.0
        )

        # Rationed price (already per share)
        result["rationed_px"] = pd.to_numeric(
            xdxr_df.get("peigujia", 0), errors="coerce"
        ).fillna(0.0)

        # Cash dividend per share (fenhong / 10)
        fenhong_ps = (
            pd.to_numeric(xdxr_df.get("fenhong", 0), errors="coerce").fillna(0.0) / 10.0
        )
        result["bonus_ps"] = fenhong_ps
        result["dividend"] = fenhong_ps

        # Deployment floor: sparse-refill re-downloads full XDXR history, so drop
        # pre-floor rows here to match TDX import behavior.
        if CN_HISTORY_START:
            result = result[result["date"] >= pd.to_datetime(CN_HISTORY_START)]

        return result

    def download_batch(
        self,
        stock_batch: list,
        start_date: str,
        end_date: str,
        pbar=None,
        include_ohlcv: bool = True,
    ) -> int:
        """Download data for a batch of stocks in a single transaction."""
        success_count = 0

        self.writer.begin()
        try:
            for stock in stock_batch:
                try:
                    if self.download_stock_data(
                        stock, start_date, end_date, include_ohlcv=include_ohlcv
                    ):
                        success_count += 1
                except Exception as e:
                    logger.error(f"Exception downloading {stock}: {e}")
                finally:
                    if pbar:
                        pbar.update(1)

            self.writer.commit()
        except Exception:
            self.writer.rollback()
            raise

        return success_count

    # Number of recent quarters to always re-download (reports may still be
    # updated, and TDX server hashes are unreliable across distributed nodes)
    RECENT_QUARTERS_TO_REFRESH = 4

    def download_fundamentals_batch(
        self, start_date: str, end_date: str,
        refresh: bool = False,
    ) -> None:
        """
        Download batch financial data by quarter with incremental updates.

        Uses Affair API which downloads one ZIP per quarter containing
        all stocks' data - much more efficient than per-stock queries.

        Incremental strategy:
        - Recent quarters (last RECENT_QUARTERS_TO_REFRESH): always re-download
          because financial reports may be updated after initial publication.
        - Historical quarters: skip if already completed.
        - refresh=True: force re-download all quarters.

        TDX server hashes are NOT used for change detection because distributed
        TDX servers return inconsistent hashes for the same file.
        """
        from simtradedata.fetchers.mootdx_affair_fetcher import MootdxAffairFetcher
        from simtradedata.utils.ttm_calculator import get_quarters_in_range

        quarters = get_quarters_in_range(start_date, end_date)
        if not quarters:
            print("  No quarters in date range")
            return

        # Create affair fetcher (same download dir as unified_fetcher)
        affair_fetcher = MootdxAffairFetcher(download_dir=self.download_dir)

        print(f"  Total quarters: {len(quarters)}")
        if refresh:
            print("  Mode: refresh (re-download all quarters)")
        else:
            print(f"  Mode: incremental (refresh last {self.RECENT_QUARTERS_TO_REFRESH} quarters)")

        # Get already completed quarters
        completed_quarters = self.writer.get_completed_fundamental_quarters()

        # Batch fetch remote file info (for availability check)
        try:
            remote_files = affair_fetcher.list_available_reports()
            remote_hash_map = {f.get("filename"): f.get("hash") for f in remote_files}
        except Exception as e:
            logger.warning(f"Failed to fetch remote file list: {e}")
            remote_hash_map = {}

        # Determine which recent quarters should always be refreshed
        recent_quarters = set(quarters[-self.RECENT_QUARTERS_TO_REFRESH:])

        pending = []
        skipped = 0

        for year, quarter in quarters:
            filename = affair_fetcher.get_quarter_filename(year, quarter)
            remote_hash = remote_hash_map.get(filename)

            if remote_hash is None:
                # File not available on server
                logger.info(f"File {filename} not available on TDX server")
                continue

            if refresh:
                # Refresh mode: re-download everything
                pending.append((year, quarter, filename, remote_hash))
                continue

            is_recent = (year, quarter) in recent_quarters
            is_completed = (year, quarter) in completed_quarters

            if is_completed and not is_recent:
                # Historical completed quarter: skip
                skipped += 1
                continue

            # New quarter or recent quarter: download
            pending.append((year, quarter, filename, remote_hash))

        if not pending:
            print(f"  All {len(quarters)} quarters up-to-date ({skipped} completed)")
            return

        print(
            f"  Pending: {len(pending)} (recent: {len(recent_quarters)}), skipped: {skipped}"
        )

        for qi, (year, quarter, filename, remote_hash) in enumerate(pending, 1):
            print(f"\n  Quarter {qi}/{len(pending)}: {year}Q{quarter}")

            try:
                fund_df = self.unified_fetcher.fetch_fundamentals_for_quarter(
                    year, quarter
                )

                if fund_df.empty:
                    logger.warning(f"No fundamentals for {year}Q{quarter}")
                    # Still mark as completed (empty is valid state)
                    self.writer.mark_fundamental_quarter_completed(
                        year, quarter, 0,
                        filename=filename, file_hash=remote_hash or ""
                    )
                    continue

                # Write per-stock fundamentals (INSERT OR REPLACE overwrites
                # existing records safely, no need to delete old data first)
                success_count = 0
                if "code" in fund_df.columns:
                    self.writer.begin()
                    try:
                        for code, group in fund_df.groupby("code"):
                            try:
                                # Convert code to PTrade format
                                from simtradedata.utils.code_utils import (
                                    convert_to_ptrade_code,
                                )
                                ptrade_code = convert_to_ptrade_code(
                                    str(code), "qstock"
                                )

                                write_df = group.drop(columns=["code"])
                                if "end_date" in write_df.columns:
                                    write_df = write_df.sort_values("end_date")
                                    write_df = write_df.set_index("end_date")

                                self.writer.write_fundamentals(ptrade_code, write_df)
                                success_count += 1
                            except Exception as e:
                                logger.warning(
                                    f"Failed to write fundamentals for {code}: {e}"
                                )

                        # Record progress in same transaction
                        self.writer.mark_fundamental_quarter_completed(
                            year, quarter, success_count,
                            filename=filename, file_hash=remote_hash or ""
                        )
                        self.writer.commit()
                        print(f"    Completed: {success_count} stocks")
                    except Exception:
                        self.writer.rollback()
                        raise

            except Exception as e:
                logger.error(f"Failed to download fundamentals {year}Q{quarter}: {e}")

    def fix_exrights_precision(self) -> int:
        """Correct bonus_ps precision using exchange reference prices.

        On ex-dividend dates the exchange publishes a reference price (preclose)
        computed from the actual dividend.  This method fetches unadjusted daily
        data from baostock (adjustflag='3') whose preclose field IS the
        exchange reference price, then derives the precise dividend:

            bonus_ps = close_prev - preclose_ex * m + rationed_ps * rationed_px

        where m = 1 + allotted_ps + rationed_ps.

        Uses a tracking table (_bonus_ps_corrections) so that already-corrected
        symbols are skipped on incremental runs.

        Returns the number of events corrected.
        """
        import baostock as bs

        # Ensure tracking table exists
        self.writer.conn.execute("""
            CREATE TABLE IF NOT EXISTS _bonus_ps_corrections (
                symbol TEXT PRIMARY KEY,
                corrected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                fix_count INTEGER DEFAULT 0
            )
        """)

        # Find symbols with exrights data that haven't been corrected yet
        uncorrected = self.writer.conn.execute("""
            SELECT DISTINCT e.symbol
            FROM exrights e
            LEFT JOIN _bonus_ps_corrections c ON e.symbol = c.symbol
            WHERE c.symbol IS NULL
            ORDER BY e.symbol
        """).fetchdf()

        symbols_to_fix = uncorrected["symbol"].tolist()
        if not symbols_to_fix:
            print("  All exrights already corrected")
            return 0

        # Load exrights events for uncorrected symbols
        events = self.writer.conn.execute("""
            SELECT e.symbol, e.date, e.allotted_ps, e.rationed_ps,
                   e.rationed_px, e.bonus_ps
            FROM exrights e
            LEFT JOIN _bonus_ps_corrections c ON e.symbol = c.symbol
            WHERE c.symbol IS NULL
            ORDER BY e.symbol, e.date
        """).fetchdf()

        # Group by symbol
        sym_groups = {}
        for _, row in events.iterrows():
            sym = row["symbol"]
            if sym not in sym_groups:
                sym_groups[sym] = []
            sym_groups[sym].append({
                "date": pd.Timestamp(row["date"]),
                "allotted_ps": row["allotted_ps"],
                "rationed_ps": row["rationed_ps"],
                "rationed_px": row["rationed_px"],
                "bonus_ps": row["bonus_ps"],
            })

        print(f"  Correcting bonus_ps for {len(symbols_to_fix)} symbols "
              f"(baostock preclose)...")

        bs.login()
        all_fixes = []

        for symbol, ev_list in tqdm(
            sym_groups.items(),
            desc="  Fixing bonus_ps",
            unit="stock",
            ncols=100,
        ):
            code, suffix = symbol.split(".")
            bs_code = f"sh.{code}" if suffix == "SS" else f"sz.{code}"

            dates = [e["date"] for e in ev_list]
            min_d = (min(dates) - pd.Timedelta(days=5)).strftime("%Y-%m-%d")
            max_d = max(dates).strftime("%Y-%m-%d")

            try:
                rs = bs.query_history_k_data_plus(
                    bs_code, "date,close,preclose",
                    start_date=min_d, end_date=max_d,
                    frequency="d", adjustflag="3",
                )
            except Exception:
                continue

            rows = []
            while rs.next():
                rows.append(rs.get_row_data())

            if not rows:
                continue

            daily = pd.DataFrame(rows, columns=["date", "close", "preclose"])
            daily["date"] = pd.to_datetime(daily["date"])
            daily["close"] = pd.to_numeric(daily["close"], errors="coerce")
            daily["preclose"] = pd.to_numeric(
                daily["preclose"], errors="coerce"
            )
            daily = daily.set_index("date")

            for ev in ev_list:
                ex_date = pd.Timestamp(ev["date"])
                if ex_date not in daily.index:
                    continue

                preclose_ex = daily.loc[ex_date, "preclose"]
                prev_dates = daily.index[daily.index < ex_date]
                if len(prev_dates) == 0 or pd.isna(preclose_ex):
                    continue

                close_prev = daily.loc[prev_dates[-1], "close"]
                if pd.isna(close_prev):
                    continue

                m = 1.0 + ev["allotted_ps"] + ev["rationed_ps"]
                derived_div = round(
                    close_prev - preclose_ex * m
                    + ev["rationed_ps"] * ev["rationed_px"],
                    4,
                )
                if derived_div < 0:
                    derived_div = 0.0

                if abs(derived_div - ev["bonus_ps"]) > 0.001:
                    all_fixes.append(
                        (symbol, ex_date.strftime("%Y-%m-%d"), derived_div)
                    )

        bs.logout()

        # Apply fixes
        for symbol, date_str, derived_div in all_fixes:
            self.writer.conn.execute(
                "UPDATE exrights SET bonus_ps = ?, dividend = ? "
                "WHERE symbol = ? AND date = ?",
                [derived_div, derived_div, symbol, date_str],
            )

        # Record all symbols as corrected (even those with 0 fixes)
        fix_counts = {}
        for symbol, _, _ in all_fixes:
            fix_counts[symbol] = fix_counts.get(symbol, 0) + 1

        for symbol in symbols_to_fix:
            count = fix_counts.get(symbol, 0)
            self.writer.conn.execute(
                "INSERT OR REPLACE INTO _bonus_ps_corrections "
                "(symbol, corrected_at, fix_count) "
                "VALUES (?, CURRENT_TIMESTAMP, ?)",
                [symbol, count],
            )

        self.writer.conn.commit()
        print(f"  Fixed {len(all_fixes)} events across "
              f"{len(fix_counts)} symbols")
        return len(all_fixes)


def download_all_data(
    skip_fundamentals: bool = False,
    start_date: str = None,
    download_dir: str = None,
    refresh_fundamentals: bool = False,
    skip_ohlcv: bool = False,
    skip_bonus_fix: bool = False,
):
    """
    Main mootdx download function.

    Auto-incremental: each symbol starts from MAX(date)+1.
    """
    with ProcessLock(LOCK_FILE):
        print("=" * 70)
        print("SimTradeData Download (mootdx Source)")
        print("=" * 70)
        print("Mode: Auto-incremental (queries MAX(date) per symbol)")
        if skip_ohlcv:
            print("OHLCV: Skipped (expected to be handled by TDX bulk import)")
        if skip_fundamentals:
            print("Fundamentals: Skipped")
        if skip_bonus_fix:
            print("bonus_ps precision fix: Skipped")
        print("=" * 70)

        # Date range
        end_date = (
            datetime.now().date()
            if END_DATE is None
            else datetime.strptime(END_DATE, "%Y-%m-%d").date()
        )

        use_start_date = start_date or START_DATE
        start_date_str = use_start_date
        end_date_str = end_date.strftime("%Y-%m-%d")

        print(f"\nDate range: {start_date_str} ~ {end_date_str}")

        # Initialize downloader
        db_path = Path(DEFAULT_DB_PATH)
        downloader = MootdxDownloader(
            db_path=str(db_path),
            skip_fundamentals=skip_fundamentals,
            download_dir=download_dir,
        )
        downloader.unified_fetcher.login()

        try:
            # Get stock list from mootdx
            print("\nFetching stock list from mootdx...")
            stock_pool = downloader.unified_fetcher.fetch_stock_list()
            print(f"Total stocks: {len(stock_pool)}")

            if not stock_pool and not skip_ohlcv:
                print("Error: No stocks found")
                return

            # Check if stocks data is already up to date
            global_max_date = downloader.writer.get_max_date("stocks")
            skip_stock_download = skip_ohlcv
            extras_stock_pool = None
            if skip_ohlcv:
                missing = downloader.writer.conn.execute("""
                    SELECT DISTINCT s.symbol FROM stocks s
                    LEFT JOIN (SELECT DISTINCT symbol FROM exrights) x
                        ON s.symbol = x.symbol
                    WHERE x.symbol IS NULL
                    ORDER BY s.symbol
                """).fetchall()
                extras_stock_pool = [row[0] for row in missing]
                print(
                    f"\nSkipping mootdx OHLCV; extras need coverage for "
                    f"{len(extras_stock_pool)} symbols"
                )
            elif global_max_date:
                # Check if there's any new trading day since global_max_date
                # by fetching a single stock's data for the date range
                try:
                    next_date = (
                        datetime.strptime(global_max_date, "%Y-%m-%d")
                        + timedelta(days=1)
                    ).strftime("%Y-%m-%d")
                    test_df = downloader.unified_fetcher.fetch_daily_data(
                        "000001.SZ",
                        next_date,
                        end_date_str,
                    )
                except Exception as e:
                    logger.warning(f"Test fetch for new trading days failed: {e}")
                    test_df = pd.DataFrame()

                if test_df.empty:
                    current_rows = downloader.writer.conn.execute(
                        "SELECT DISTINCT symbol FROM stocks WHERE date = ?",
                        [global_max_date],
                    ).fetchall()
                    current_symbols = {row[0] for row in current_rows}
                    latest_count, total_count, latest_ratio = _latest_stock_coverage(
                        current_symbols, stock_pool
                    )

                    if latest_ratio < LATEST_COVERAGE_SKIP_RATIO:
                        print(
                            f"\nOHLCV incomplete at max_date {global_max_date}: "
                            f"{latest_count}/{total_count} symbols current "
                            f"({latest_ratio:.1%}), downloading missing rows..."
                        )
                    else:
                        # OHLCV is current, check exrights coverage.
                        stock_count = downloader.writer.conn.execute(
                            "SELECT COUNT(DISTINCT symbol) FROM stocks"
                        ).fetchone()[0]
                        exr_count = downloader.writer.conn.execute(
                            "SELECT COUNT(DISTINCT symbol) FROM exrights"
                        ).fetchone()[0]

                        if exr_count >= stock_count * 0.9:
                            print(
                                "\nStocks data already up to date "
                                f"(max_date: {global_max_date})"
                            )
                            print(f"Exrights coverage: {exr_count}/{stock_count} stocks")
                            print(
                                "No new trading days since last update, "
                                "skipping stock download."
                            )
                            skip_stock_download = True
                        else:
                            # Only process stocks missing exrights
                            missing = downloader.writer.conn.execute("""
                                SELECT DISTINCT s.symbol FROM stocks s
                                LEFT JOIN (SELECT DISTINCT symbol FROM exrights) x
                                    ON s.symbol = x.symbol
                                WHERE x.symbol IS NULL
                            """).fetchall()
                            extras_stock_pool = [row[0] for row in missing]
                            from simtradedata.utils.code_utils import is_etf_code

                            etf_count = sum(
                                1 for s in extras_stock_pool if is_etf_code(s)
                            )
                            print(f"\nOHLCV up to date (max_date: {global_max_date})")
                            print(
                                f"But exrights incomplete ({exr_count}/{stock_count}), "
                                f"downloading extras..."
                            )
                            print(
                                f"  Missing: {len(extras_stock_pool)} "
                                f"({etf_count} ETFs, "
                                f"{len(extras_stock_pool) - etf_count} stocks)"
                            )
                            skip_stock_download = True

            if not skip_stock_download:
                # Resume: prioritize stocks needing backfill
                if global_max_date:
                    result = downloader.writer.conn.execute(
                        "SELECT DISTINCT symbol FROM stocks WHERE date = ?",
                        [global_max_date],
                    ).fetchall()
                    current_symbols = {r[0] for r in result}
                    needs_work = [s for s in stock_pool if s not in current_symbols]
                    already_current = [s for s in stock_pool if s in current_symbols]
                    if already_current:
                        stock_pool = needs_work + already_current
                        print(
                            f"  Resume: {len(needs_work)} need download, "
                            f"{len(already_current)} already have latest data"
                        )

                # Download in batches
                batches = [
                    stock_pool[i : i + BATCH_SIZE]
                    for i in range(0, len(stock_pool), BATCH_SIZE)
                ]

                print(
                    f"\nProcessing {len(stock_pool)} stocks in {len(batches)} batches..."
                )
                print(f"Batch size: {BATCH_SIZE}")
                print("=" * 60)

                total_success = 0

                with tqdm(
                    total=len(stock_pool),
                    desc="Downloading stocks",
                    unit="stock",
                    ncols=100,
                    bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
                ) as pbar:
                    for batch in batches:
                        try:
                            success = downloader.download_batch(
                                batch, start_date_str, end_date_str, pbar
                            )
                            total_success += success
                        except Exception as e:
                            logger.error(f"Batch failed: {e}")
                            pbar.update(len(batch))

                print("=" * 60)
                print(
                    f"Download complete: {total_success} updated, "
                    f"{len(stock_pool) - total_success} skipped/failed"
                )

            # Download extras (adjust factors / exrights) for stocks missing them
            if extras_stock_pool:
                batches = [
                    extras_stock_pool[i : i + BATCH_SIZE]
                    for i in range(0, len(extras_stock_pool), BATCH_SIZE)
                ]
                print(f"\nDownloading extras for {len(extras_stock_pool)} stocks in {len(batches)} batches...")
                print("=" * 60)
                total_success = 0

                with tqdm(
                    total=len(extras_stock_pool),
                    desc="Downloading extras",
                    unit="stock",
                    ncols=100,
                    bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
                ) as pbar:
                    for batch in batches:
                        try:
                            success = downloader.download_batch(
                                batch,
                                start_date_str,
                                end_date_str,
                                pbar,
                                include_ohlcv=False,
                            )
                            total_success += success
                        except Exception as e:
                            logger.error(f"Extras batch failed: {e}")
                            pbar.update(len(batch))

                print("=" * 60)
                print(f"Extras complete: {total_success} updated, "
                      f"{len(extras_stock_pool) - total_success} skipped/failed")

            # Fix bonus_ps precision using exchange reference prices (baostock)
            if not skip_bonus_fix:
                print("\nFixing bonus_ps precision (baostock preclose)...")
                try:
                    downloader.fix_exrights_precision()
                except Exception as e:
                    logger.error(f"Failed to fix exrights: {e}")

            # Download batch fundamentals
            if not skip_fundamentals:
                print("\nDownloading batch financial data (mootdx Affair)...")
                try:
                    downloader.download_fundamentals_batch(
                        start_date_str, end_date_str,
                        refresh=refresh_fundamentals,
                    )
                except Exception as e:
                    logger.error(f"Failed to download fundamentals: {e}")

            # Download global data
            print("\nDownloading global data...")

            # Trading calendar
            print("  Trading calendar...")
            try:
                trade_cal = downloader.unified_fetcher.fetch_trade_calendar(
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

            # Benchmark index
            benchmark = BENCHMARK_CONFIG["default_index"]
            print(f"  Benchmark index ({benchmark})...")
            try:
                benchmark_df = downloader.unified_fetcher.fetch_index_data(
                    benchmark, start_date_str, end_date_str
                )
                if not benchmark_df.empty:
                    downloader.writer.write_benchmark(benchmark_df)
                    print(f"    {len(benchmark_df)} days")
            except Exception as e:
                logger.error(f"Failed to download benchmark: {e}")

        finally:
            try:
                downloader.writer.close()
            except Exception as e:
                logger.warning("Failed to close writer: %s", e)
            try:
                downloader.unified_fetcher.logout()
            except Exception as e:
                logger.warning("Failed to logout unified_fetcher: %s", e)

        # Summary
        print("\n" + "=" * 70)
        print("Download Complete!")
        print("=" * 70)

        db_file = Path(DEFAULT_DB_PATH)
        if db_file.exists():
            db_size = db_file.stat().st_size / (1024 * 1024)
            print(f"\nDatabase: {db_file}")
            print(f"Size: {db_size:.1f} MB")

        if downloader.failed_stocks:
            print(f"\nFailed stocks: {len(downloader.failed_stocks)}")
            logger.info(f"Failed stocks: {downloader.failed_stocks}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Download mootdx data to DuckDB (auto-incremental)"
    )
    parser.add_argument(
        "--skip-fundamentals",
        action="store_true",
        help="Skip batch financial data download",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="Override default start date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--download-dir",
        type=str,
        default=None,
        help="Directory for downloading financial data ZIP files",
    )
    parser.add_argument(
        "--refresh-fundamentals",
        action="store_true",
        help="Force re-download all financial data quarters",
    )
    parser.add_argument(
        "--skip-ohlcv",
        action="store_true",
        help="Skip mootdx per-symbol OHLCV download",
    )
    parser.add_argument(
        "--skip-bonus-fix",
        action="store_true",
        help="Skip slow baostock preclose bonus_ps precision correction",
    )

    args = parser.parse_args()

    download_all_data(
        skip_fundamentals=args.skip_fundamentals,
        start_date=args.start_date,
        download_dir=args.download_dir,
        refresh_fundamentals=args.refresh_fundamentals,
        skip_ohlcv=args.skip_ohlcv,
        skip_bonus_fix=args.skip_bonus_fix,
    )
