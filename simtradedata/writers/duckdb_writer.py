# -*- coding: utf-8 -*-
"""
DuckDB writer for SimTradeData

This module provides incremental data storage using DuckDB,
with automatic upsert (INSERT OR REPLACE) for deduplication.
"""

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import duckdb
import pandas as pd

from simtradedata.config.field_mappings import (
    BENCHMARK_CONFIG,
    BENCHMARK_HISTORY_FLOOR,
    benchmark_history_ok,
)
from simtradedata.utils.paths import DUCKDB_PATH, safe_rmtree
from simtradedata.validators.data_validator import validate_before_write

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = str(DUCKDB_PATH)

_FUNDAMENTAL_EXTRA_COLUMNS = [
    "basic_eps",
    "undivided_profit",
    "naps",
    "capital_surplus_fund_ps",
    "net_operate_cash_flow_ps",
    "cash_equivalents",
    "account_receivable",
    "inventories",
    "total_current_assets",
    "fixed_assets",
    "intangible_assets",
    "total_non_current_assets",
    "total_assets",
    "shortterm_loan",
    "accounts_payable",
    "total_current_liability",
    "longterm_loan",
    "total_non_current_liability",
    "total_liability",
    "paidin_capital",
    "retained_profit",
    "total_shareholder_equity",
    "operating_revenue",
    "operating_cost",
    "financial_expense",
    "operating_profit",
    "total_profit",
    "net_profit",
    "np_parent_company_owners",
    "net_operate_cash_flow",
    "net_invest_cash_flow",
    "net_finance_cash_flow",
    "net_asset_grow_rate",
    "roe_weighted",
]

_FUNDAMENTAL_WRITE_COLUMNS = [
    "symbol",
    "date",
    "publ_date",
    "operating_revenue_grow_rate",
    "net_profit_grow_rate",
    "basic_eps_yoy",
    "np_parent_company_yoy",
    "net_profit_ratio",
    "net_profit_ratio_ttm",
    "gross_income_ratio",
    "gross_income_ratio_ttm",
    "roa",
    "roa_ttm",
    "roe",
    "roe_ttm",
    "total_asset_grow_rate",
    "total_asset_turnover_rate",
    "current_assets_turnover_rate",
    "inventory_turnover_rate",
    "accounts_receivables_turnover_rate",
    "current_ratio",
    "quick_ratio",
    "debt_equity_ratio",
    "interest_cover",
    "roic",
    "roa_ebit_ttm",
    "total_shares",
    "a_floats",
    *_FUNDAMENTAL_EXTRA_COLUMNS,
]

_FUNDAMENTAL_EXPORT_EXTRA_SQL = ",\n                ".join(_FUNDAMENTAL_EXTRA_COLUMNS)


class DuckDBWriter:
    """
    Writer for DuckDB incremental storage

    Features:
    - Automatic upsert via INSERT OR REPLACE (uses PRIMARY KEY)
    - Incremental updates: query MAX(date) to determine start_date
    - Export to PTrade Parquet format
    """

    # Legacy code-pattern fallback for databases created before
    # stock_metadata.security_type was populated from BaoStock query_stock_basic().
    # New exports should use _cn_stock_filter_sql(), which prefers BaoStock's
    # official security type classification.
    _CN_STOCK_FILTER = (
        "(symbol LIKE '000___.SZ' OR symbol LIKE '001___.SZ' "
        "OR symbol LIKE '002___.SZ' OR symbol LIKE '003___.SZ' "
        "OR symbol LIKE '300___.SZ' OR symbol LIKE '301___.SZ' "
        "OR symbol LIKE '302___.SZ' "
        "OR symbol LIKE '600___.SS' OR symbol LIKE '601___.SS' "
        "OR symbol LIKE '603___.SS' OR symbol LIKE '605___.SS' "
        "OR symbol LIKE '688___.SS' OR symbol LIKE '689___.SS')"
    )

    _ALLOWED_TABLES = frozenset({
        "stocks", "valuation", "fundamentals", "exrights",
        "stock_metadata", "stock_pool", "stock_status",
        "benchmark", "trade_days", "index_constituents",
        "money_flow", "lhb", "margin_trading",
        "data_change_log", "version_info", "fundamentals_progress",
        "sampled_dates",
    })

    @staticmethod
    def _check_table(table: str) -> None:
        """Validate table name to prevent SQL injection via f-string interpolation."""
        if table not in DuckDBWriter._ALLOWED_TABLES:
            raise ValueError(
                f"Unknown table '{table}'. Allowed: "
                f"{sorted(DuckDBWriter._ALLOWED_TABLES)}"
            )

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.conn = duckdb.connect(str(self.db_path))
        self._init_schema()

        logger.info(f"DuckDBWriter initialized: {self.db_path}")

    def _init_schema(self) -> None:
        """Initialize database schema"""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS stocks (
                symbol VARCHAR NOT NULL,
                date DATE NOT NULL,
                open DOUBLE,
                close DOUBLE,
                high DOUBLE,
                low DOUBLE,
                high_limit DOUBLE,
                low_limit DOUBLE,
                preclose DOUBLE,
                volume BIGINT,
                money DOUBLE,
                PRIMARY KEY (symbol, date)
            )
        """)

        # Create index for faster MAX(date) queries
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_stocks_symbol_date
            ON stocks (symbol, date DESC)
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS exrights (
                symbol VARCHAR NOT NULL,
                date DATE NOT NULL,
                allotted_ps DOUBLE DEFAULT 0,
                rationed_ps DOUBLE DEFAULT 0,
                rationed_px DOUBLE DEFAULT 0,
                bonus_ps DOUBLE DEFAULT 0,
                dividend DOUBLE,
                PRIMARY KEY (symbol, date)
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS valuation (
                symbol VARCHAR NOT NULL,
                date DATE NOT NULL,
                pe_ttm DOUBLE,
                pb DOUBLE,
                ps_ttm DOUBLE,
                pcf DOUBLE,
                roe DOUBLE,
                roe_ttm DOUBLE,
                roa DOUBLE,
                roa_ttm DOUBLE,
                naps DOUBLE,
                total_shares DOUBLE,
                a_floats DOUBLE,
                turnover_rate DOUBLE,
                PRIMARY KEY (symbol, date)
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS fundamentals (
                symbol VARCHAR NOT NULL,
                date DATE NOT NULL,
                publ_date VARCHAR,
                operating_revenue_grow_rate DOUBLE,
                net_profit_grow_rate DOUBLE,
                basic_eps_yoy DOUBLE,
                np_parent_company_yoy DOUBLE,
                net_profit_ratio DOUBLE,
                net_profit_ratio_ttm DOUBLE,
                gross_income_ratio DOUBLE,
                gross_income_ratio_ttm DOUBLE,
                roa DOUBLE,
                roa_ttm DOUBLE,
                roe DOUBLE,
                roe_ttm DOUBLE,
                total_asset_grow_rate DOUBLE,
                total_asset_turnover_rate DOUBLE,
                current_assets_turnover_rate DOUBLE,
                inventory_turnover_rate DOUBLE,
                accounts_receivables_turnover_rate DOUBLE,
                current_ratio DOUBLE,
                quick_ratio DOUBLE,
                debt_equity_ratio DOUBLE,
                interest_cover DOUBLE,
                roic DOUBLE,
                roa_ebit_ttm DOUBLE,
                total_shares DOUBLE,
                a_floats DOUBLE,
                PRIMARY KEY (symbol, date)
            )
        """)
        self._migrate_fundamentals_columns()


        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS stock_metadata (
                symbol VARCHAR PRIMARY KEY,
                stock_name VARCHAR,
                listed_date VARCHAR,
                de_listed_date VARCHAR,
                security_type VARCHAR,
                listing_status VARCHAR,
                blocks VARCHAR
            )
        """)
        self._migrate_stock_metadata()

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS benchmark (
                date DATE PRIMARY KEY,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume DOUBLE,
                money DOUBLE
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_days (
                date DATE PRIMARY KEY
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS index_constituents (
                date VARCHAR NOT NULL,
                index_code VARCHAR NOT NULL,
                symbols VARCHAR NOT NULL,
                PRIMARY KEY (date, index_code)
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS stock_status (
                date VARCHAR NOT NULL,
                status_type VARCHAR NOT NULL,
                symbols VARCHAR NOT NULL,
                PRIMARY KEY (date, status_type)
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS stock_pool (
                symbol VARCHAR PRIMARY KEY,
                first_seen_date DATE NOT NULL,
                last_seen_date DATE NOT NULL
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS sampling_progress (
                sample_date DATE PRIMARY KEY
            )
        """)

        # Fundamentals download progress tracking (by quarter)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS fundamentals_progress (
                year INTEGER NOT NULL,
                quarter INTEGER NOT NULL,
                completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                stock_count INTEGER DEFAULT 0,
                filename VARCHAR,
                file_hash VARCHAR,
                PRIMARY KEY (year, quarter)
            )
        """)

        # Migrate existing table: add filename and file_hash columns if missing
        self._migrate_fundamentals_progress()

        # Money flow data (from EastMoney)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS money_flow (
                symbol VARCHAR NOT NULL,
                date DATE NOT NULL,
                net_main DOUBLE,
                net_super DOUBLE,
                net_large DOUBLE,
                net_medium DOUBLE,
                net_small DOUBLE,
                PRIMARY KEY (symbol, date)
            )
        """)

        # LHB (Dragon Tiger Board) data
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS lhb (
                symbol VARCHAR NOT NULL,
                date DATE NOT NULL,
                reason VARCHAR DEFAULT '',
                net_buy DOUBLE,
                buy_amount DOUBLE,
                sell_amount DOUBLE,
                PRIMARY KEY (symbol, date, reason)
            )
        """)

        # Margin trading data
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS margin_trading (
                symbol VARCHAR NOT NULL,
                date DATE NOT NULL,
                rzye DOUBLE,
                rqyl DOUBLE,
                rzrqye DOUBLE,
                PRIMARY KEY (symbol, date)
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS data_change_log (
                table_name VARCHAR NOT NULL,
                symbol VARCHAR,
                date DATE,
                changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (table_name, symbol, date)
            )
        """)

        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_data_change_log_table_date
            ON data_change_log (table_name, changed_at, date)
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS version_info (
                key VARCHAR PRIMARY KEY,
                value VARCHAR
            )
        """)

        # Initialize format info
        self.conn.execute("""
            INSERT OR IGNORE INTO version_info VALUES ('format', 'duckdb')
        """)

    def _migrate_fundamentals_progress(self) -> None:
        """Migrate fundamentals_progress table to add filename and file_hash columns."""
        columns = self.conn.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'fundamentals_progress'
        """).fetchall()
        column_names = {row[0] for row in columns}

        if "filename" not in column_names:
            self.conn.execute("""
                ALTER TABLE fundamentals_progress ADD COLUMN filename VARCHAR
            """)
            logger.info("Added filename column to fundamentals_progress")

        if "file_hash" not in column_names:
            self.conn.execute("""
                ALTER TABLE fundamentals_progress ADD COLUMN file_hash VARCHAR
            """)
            logger.info("Added file_hash column to fundamentals_progress")

    def _migrate_fundamentals_columns(self) -> None:
        """Add expanded PTrade financial statement columns when upgrading DBs."""
        columns = self.conn.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'fundamentals'
        """).fetchall()
        column_names = {row[0] for row in columns}

        for column in _FUNDAMENTAL_EXTRA_COLUMNS:
            if column not in column_names:
                self.conn.execute(f"""
                    ALTER TABLE fundamentals ADD COLUMN {column} DOUBLE
                """)
                logger.info("Added fundamentals column %s", column)

    def _migrate_stock_metadata(self) -> None:
        """Migrate stock_metadata to keep official BaoStock type/status fields."""
        columns = self.conn.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'stock_metadata'
        """).fetchall()
        column_names = {row[0] for row in columns}

        if "security_type" not in column_names:
            self.conn.execute("""
                ALTER TABLE stock_metadata ADD COLUMN security_type VARCHAR
            """)
            logger.info("Added security_type column to stock_metadata")

        if "listing_status" not in column_names:
            self.conn.execute("""
                ALTER TABLE stock_metadata ADD COLUMN listing_status VARCHAR
            """)
            logger.info("Added listing_status column to stock_metadata")

    def get_sampled_dates(self) -> list:
        """Get list of dates that have already been sampled"""
        result = self.conn.execute(
            "SELECT sample_date FROM sampling_progress ORDER BY sample_date"
        ).fetchall()
        return [row[0] for row in result]

    def add_sampled_date(self, sample_date) -> None:
        """Mark a date as sampled"""
        self.conn.execute(
            "INSERT OR IGNORE INTO sampling_progress VALUES (?)", [sample_date]
        )

    def get_stock_pool(self) -> list:
        """Get all symbols in stock pool"""
        result = self.conn.execute(
            "SELECT symbol FROM stock_pool ORDER BY symbol"
        ).fetchall()
        return [row[0] for row in result]

    def update_stock_pool(self, symbols: list, sample_date) -> None:
        """Update stock pool with new symbols from a sample date"""
        for symbol in symbols:
            self.conn.execute(
                """
                INSERT INTO stock_pool (symbol, first_seen_date, last_seen_date)
                VALUES (?, ?, ?)
                ON CONFLICT (symbol) DO UPDATE SET
                    last_seen_date = CASE
                        WHEN excluded.last_seen_date > stock_pool.last_seen_date
                        THEN excluded.last_seen_date
                        ELSE stock_pool.last_seen_date
                    END,
                    first_seen_date = CASE
                        WHEN excluded.first_seen_date < stock_pool.first_seen_date
                        THEN excluded.first_seen_date
                        ELSE stock_pool.first_seen_date
                    END
            """,
                [symbol, sample_date, sample_date],
            )

    # ========================================
    # Fundamentals progress tracking
    # ========================================

    def get_existing_fundamental_dates(self, symbol: str) -> set:
        """Get set of existing quarter end dates for a symbol in fundamentals table.

        Returns:
            Set of date strings like {'2024-03-31', '2024-06-30', ...}
        """
        result = self.conn.execute(
            """
            SELECT DISTINCT date FROM fundamentals WHERE symbol = ?
        """,
            [symbol],
        ).fetchall()
        return {str(row[0]) for row in result}

    def has_fundamental(self, symbol: str, date_str: str) -> bool:
        """Check if a specific symbol+date exists in fundamentals table."""
        result = self.conn.execute(
            """
            SELECT 1 FROM fundamentals WHERE symbol = ? AND date = ?
        """,
            [symbol, date_str],
        ).fetchone()
        return result is not None

    def get_completed_fundamental_quarters(self) -> set:
        """Get set of (year, quarter) tuples that are fully downloaded."""
        result = self.conn.execute(
            "SELECT year, quarter FROM fundamentals_progress ORDER BY year, quarter"
        ).fetchall()
        return {(row[0], row[1]) for row in result}

    def get_fundamental_quarter_hash(self, year: int, quarter: int) -> Optional[str]:
        """Get stored hash value for a quarter's financial data.

        Args:
            year: Year (e.g., 2024)
            quarter: Quarter (1-4)

        Returns:
            Hash string if exists, None otherwise
        """
        result = self.conn.execute(
            """
            SELECT file_hash FROM fundamentals_progress
            WHERE year = ? AND quarter = ?
        """,
            [year, quarter],
        ).fetchone()
        return result[0] if result else None

    def delete_fundamental_quarter_data(self, year: int, quarter: int) -> int:
        """Delete all fundamentals data for a specific quarter.

        Args:
            year: Year (e.g., 2024)
            quarter: Quarter (1-4)

        Returns:
            Number of rows deleted
        """
        quarter_end = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}
        date_str = f"{year}-{quarter_end[quarter]}"

        # Get count before delete (DuckDB doesn't have changes() function)
        count_result = self.conn.execute(
            """
            SELECT COUNT(*) FROM fundamentals WHERE date = ?
        """,
            [date_str],
        ).fetchone()
        count = count_result[0] if count_result else 0

        self.conn.execute(
            """
            DELETE FROM fundamentals WHERE date = ?
        """,
            [date_str],
        )

        # Also delete the progress record
        self.conn.execute(
            """
            DELETE FROM fundamentals_progress WHERE year = ? AND quarter = ?
        """,
            [year, quarter],
        )

        logger.info(f"Deleted {count} fundamentals rows for {year}Q{quarter}")
        return count

    def mark_fundamental_quarter_completed(
        self,
        year: int,
        quarter: int,
        stock_count: int,
        filename: str | None = None,
        file_hash: str | None = None,
    ) -> None:
        """Mark a quarter's fundamentals as fully downloaded.

        Args:
            year: Year (e.g., 2024)
            quarter: Quarter (1-4)
            stock_count: Number of stocks with data
            filename: Source filename (e.g., 'gpcw20231231.zip')
            file_hash: Hash value from TDX server for change detection
        """
        self.conn.execute(
            """
            INSERT OR REPLACE INTO fundamentals_progress
                (year, quarter, stock_count, filename, file_hash)
            VALUES (?, ?, ?, ?, ?)
        """,
            [year, quarter, stock_count, filename, file_hash],
        )

    def close(self) -> None:
        """Close database connection"""
        if self.conn:
            self.conn.close()
            self.conn = None

    def begin(self) -> None:
        """Begin a transaction for batch writes"""
        self.conn.execute("BEGIN TRANSACTION")

    def commit(self) -> None:
        """Commit current transaction"""
        self.conn.execute("COMMIT")

    def rollback(self) -> None:
        """Rollback current transaction"""
        self.conn.execute("ROLLBACK")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    @staticmethod
    def _validate_or_warn(df: pd.DataFrame, data_type: str, symbol: str) -> None:
        """Validate data before write and log a warning on failure.

        Constructs a DatetimeIndex from the already-parsed date column
        to avoid re-parsing, then delegates to validate_before_write.
        """
        date_idx = pd.DatetimeIndex(df["date"])
        if not validate_before_write(df.set_index(date_idx), data_type, symbol,
                                     strict=False):
            logger.warning(
                "%s data validation failed for %s, writing anyway",
                data_type.capitalize(), symbol,
            )

    def _record_symbol_changes(self, table_name: str, df: pd.DataFrame) -> None:
        """Record symbol/date rows touched by an upsert for delta export."""
        if df.empty or "symbol" not in df.columns or "date" not in df.columns:
            return
        changes = df[["symbol", "date"]].dropna().drop_duplicates().copy()
        if changes.empty:
            return
        changes["table_name"] = table_name
        changes["date"] = pd.to_datetime(changes["date"]).dt.date
        self.conn.register("_data_change_rows", changes)
        try:
            self.conn.execute("""
                INSERT OR REPLACE INTO data_change_log (table_name, symbol, date, changed_at)
                SELECT table_name, symbol, date, CURRENT_TIMESTAMP FROM _data_change_rows
            """)
        finally:
            self.conn.unregister("_data_change_rows")

    def _record_stock_metadata_changes(self, symbols: list[str]) -> None:
        """Record metadata-only symbol changes for delta export."""
        if not symbols:
            return
        changes = pd.DataFrame({"table_name": "stock_metadata", "symbol": sorted(set(symbols))})
        changes["date"] = pd.to_datetime("1900-01-01").date()
        self.conn.register("_metadata_change_rows", changes)
        try:
            self.conn.execute("""
                INSERT OR REPLACE INTO data_change_log (table_name, symbol, date, changed_at)
                SELECT table_name, symbol, date, CURRENT_TIMESTAMP FROM _metadata_change_rows
            """)
        finally:
            self.conn.unregister("_metadata_change_rows")

    # ========================================
    # Core write methods (with upsert)
    # ========================================

    def write_market_data(self, symbol: str, df: pd.DataFrame) -> int:
        """Write market data with automatic upsert"""
        if df.empty:
            return 0

        df = df.copy()
        df["symbol"] = symbol

        if isinstance(df.index, pd.DatetimeIndex):
            df = df.reset_index()
            if "index" in df.columns:
                df = df.rename(columns={"index": "date"})

        df["date"] = pd.to_datetime(df["date"]).dt.date
        self._validate_or_warn(df, "market", symbol)

        columns = [
            "symbol",
            "date",
            "open",
            "close",
            "high",
            "low",
            "high_limit",
            "low_limit",
            "preclose",
            "volume",
            "money",
        ]
        available = [c for c in columns if c in df.columns]
        df = df[available]

        cols_str = ", ".join(available)
        self.conn.execute(f"""
            INSERT OR REPLACE INTO stocks ({cols_str})
            SELECT {cols_str} FROM df
        """)
        self._record_symbol_changes("stocks", df)

        logger.debug(f"Wrote {len(df)} market rows for {symbol}")
        return len(df)

    def write_valuation(self, symbol: str, df: pd.DataFrame) -> int:
        """Write valuation data with upsert"""
        if df.empty:
            return 0

        df = df.copy()
        df["symbol"] = symbol

        if isinstance(df.index, pd.DatetimeIndex):
            df = df.reset_index()
            if "index" in df.columns:
                df = df.rename(columns={"index": "date"})

        df["date"] = pd.to_datetime(df["date"]).dt.date
        self._validate_or_warn(df, "valuation", symbol)

        columns = [
            "symbol",
            "date",
            "pe_ttm",
            "pb",
            "ps_ttm",
            "pcf",
            "roe",
            "roe_ttm",
            "roa",
            "roa_ttm",
            "naps",
            "total_shares",
            "a_floats",
            "turnover_rate",
        ]
        available = [c for c in columns if c in df.columns]
        df = df[available]

        cols_str = ", ".join(available)
        self.conn.execute(f"""
            INSERT OR REPLACE INTO valuation ({cols_str})
            SELECT {cols_str} FROM df
        """)
        self._record_symbol_changes("valuation", df)

        logger.debug(f"Wrote {len(df)} valuation rows for {symbol}")
        return len(df)

    def write_fundamentals(self, symbol: str, df: pd.DataFrame) -> int:
        """Write quarterly fundamentals with upsert"""
        if df.empty:
            return 0

        df = df.copy()
        df["symbol"] = symbol

        if isinstance(df.index, pd.DatetimeIndex):
            df = df.reset_index()
            if "index" in df.columns:
                df = df.rename(columns={"index": "date"})

        if "end_date" in df.columns and "date" not in df.columns:
            df = df.rename(columns={"end_date": "date"})

        df["date"] = pd.to_datetime(df["date"]).dt.date
        self._validate_or_warn(df, "fundamental", symbol)

        if "publ_date" in df.columns:
            publ_dt = pd.to_datetime(df["publ_date"], errors="coerce")
            df["publ_date"] = publ_dt.dt.strftime("%Y%m%d").where(
                publ_dt.notna(), None
            )

        columns = _FUNDAMENTAL_WRITE_COLUMNS
        available = [c for c in columns if c in df.columns]
        df = df[available]

        cols_str = ", ".join(available)
        # Use ON CONFLICT to only update columns present in the DataFrame,
        # preserving existing values for columns not in this write batch.
        update_cols = [c for c in available if c not in ("symbol", "date")]
        if update_cols:
            set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
            conflict_clause = f"ON CONFLICT (symbol, date) DO UPDATE SET {set_clause}"
        else:
            conflict_clause = "ON CONFLICT (symbol, date) DO NOTHING"
        self.conn.execute(f"""
            INSERT INTO fundamentals ({cols_str})
            SELECT {cols_str} FROM df
            {conflict_clause}
        """)
        self._record_symbol_changes("fundamentals", df)

        logger.debug(f"Wrote {len(df)} fundamental rows for {symbol}")
        return len(df)

    def write_exrights(self, symbol: str, df: pd.DataFrame) -> int:
        """Write exrights data with upsert"""
        if df.empty:
            return 0

        df = df.copy()
        df["symbol"] = symbol

        if isinstance(df.index, pd.DatetimeIndex):
            df = df.reset_index()
            if "index" in df.columns:
                df = df.rename(columns={"index": "date"})

        df["date"] = pd.to_datetime(df["date"]).dt.date

        columns = [
            "symbol",
            "date",
            "allotted_ps",
            "rationed_ps",
            "rationed_px",
            "bonus_ps",
            "dividend",
        ]
        available = [c for c in columns if c in df.columns]
        df = df[available]

        cols_str = ", ".join(available)
        self.conn.execute(f"""
            INSERT OR REPLACE INTO exrights ({cols_str})
            SELECT {cols_str} FROM df
        """)
        self._record_symbol_changes("exrights", df)

        logger.debug(f"Wrote {len(df)} exrights rows for {symbol}")
        return len(df)

    def write_adjust_factor(self, symbol: str, data) -> int:
        """Deprecated: adjust factors removed. SimTradeLab computes from exrights."""
        return 0

    def write_benchmark(self, df: pd.DataFrame) -> int:
        """Write benchmark index data"""
        if df.empty:
            return 0

        df = df.copy()

        if isinstance(df.index, pd.DatetimeIndex):
            df = df.reset_index()
            if "index" in df.columns:
                df = df.rename(columns={"index": "date"})

        df["date"] = pd.to_datetime(df["date"]).dt.date

        columns = ["date", "open", "high", "low", "close", "volume", "money"]
        available = [c for c in columns if c in df.columns]
        df = df[available]

        cols_str = ", ".join(available)
        self.conn.execute(f"""
            INSERT OR REPLACE INTO benchmark ({cols_str})
            SELECT {cols_str} FROM df
        """)

        logger.info(f"Wrote {len(df)} benchmark rows")
        return len(df)

    def write_trade_days(self, df: pd.DataFrame) -> int:
        """Write trading calendar"""
        if df.empty:
            return 0

        df = df.copy()

        if isinstance(df.index, pd.DatetimeIndex):
            df = df.reset_index()
            if "index" in df.columns:
                df = df.rename(columns={"index": "date"})

        if "trade_date" in df.columns:
            df = df.rename(columns={"trade_date": "date"})

        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df[["date"]]

        self.conn.execute("""
            INSERT OR IGNORE INTO trade_days
            SELECT * FROM df
        """)

        logger.info(f"Wrote {len(df)} trade days")
        return len(df)

    def write_stock_metadata(self, df: pd.DataFrame) -> int:
        """Write stock metadata"""
        if df.empty:
            return 0

        df = df.copy()

        if df.index.name == "stock_code" or "stock_code" in df.columns:
            df = df.reset_index()
            if "stock_code" in df.columns:
                df = df.rename(columns={"stock_code": "symbol"})

        if "index" in df.columns and "symbol" not in df.columns:
            df = df.rename(columns={"index": "symbol"})

        if "type" in df.columns and "security_type" not in df.columns:
            df = df.rename(columns={"type": "security_type"})
        if "status" in df.columns and "listing_status" not in df.columns:
            df = df.rename(columns={"status": "listing_status"})

        columns = [
            "symbol",
            "stock_name",
            "listed_date",
            "de_listed_date",
            "security_type",
            "listing_status",
            "blocks",
        ]
        available = [c for c in columns if c in df.columns]
        df = df[available]

        cols_str = ", ".join(available)
        self.conn.execute(f"""
            INSERT OR REPLACE INTO stock_metadata ({cols_str})
            SELECT {cols_str} FROM df
        """)
        if "symbol" in df.columns:
            self._record_stock_metadata_changes(df["symbol"].dropna().astype(str).tolist())

        logger.info(f"Wrote {len(df)} stock metadata records")
        return len(df)

    def write_index_constituents(
        self, date: str, index_code: str, symbols: List[str]
    ) -> None:
        """Write index constituents for a specific date"""
        symbols_json = json.dumps(symbols, ensure_ascii=False)

        self.conn.execute(
            """
            INSERT OR REPLACE INTO index_constituents (date, index_code, symbols)
            VALUES (?, ?, ?)
        """,
            [date, index_code, symbols_json],
        )

    def write_stock_status(
        self, date: str, status_type: str, symbols: List[str]
    ) -> None:
        """Write stock status for a specific date"""
        symbols_json = json.dumps(symbols, ensure_ascii=False)

        self.conn.execute(
            """
            INSERT OR REPLACE INTO stock_status (date, status_type, symbols)
            VALUES (?, ?, ?)
        """,
            [date, status_type, symbols_json],
        )

    def write_money_flow(self, symbol: str, df: pd.DataFrame) -> int:
        """Write money flow data with upsert."""
        if df.empty:
            return 0
        df = df.copy()
        df["symbol"] = symbol
        df["date"] = pd.to_datetime(df["date"]).dt.date
        columns = [
            "symbol",
            "date",
            "net_main",
            "net_super",
            "net_large",
            "net_medium",
            "net_small",
        ]
        available = [c for c in columns if c in df.columns]
        df = df[available]
        cols_str = ", ".join(available)
        self.conn.execute(
            f"INSERT OR REPLACE INTO money_flow ({cols_str}) SELECT {cols_str} FROM df"
        )
        return len(df)

    def write_lhb(self, df: pd.DataFrame) -> int:
        """Write LHB data with upsert. DataFrame must include symbol column."""
        if df.empty:
            return 0
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        if "reason" in df.columns:
            df["reason"] = df["reason"].fillna("")
        columns = ["symbol", "date", "reason", "net_buy", "buy_amount", "sell_amount"]
        available = [c for c in columns if c in df.columns]
        df = df[available]
        cols_str = ", ".join(available)
        self.conn.execute(
            f"INSERT OR REPLACE INTO lhb ({cols_str}) SELECT {cols_str} FROM df"
        )
        return len(df)

    def write_margin_trading(self, symbol: str, df: pd.DataFrame) -> int:
        """Write margin trading data with upsert."""
        if df.empty:
            return 0
        df = df.copy()
        df["symbol"] = symbol
        df["date"] = pd.to_datetime(df["date"]).dt.date
        columns = ["symbol", "date", "rzye", "rqyl", "rzrqye"]
        available = [c for c in columns if c in df.columns]
        df = df[available]
        cols_str = ", ".join(available)
        self.conn.execute(
            f"INSERT OR REPLACE INTO margin_trading ({cols_str}) SELECT {cols_str} FROM df"
        )
        return len(df)

    def write_global_metadata(self, meta: pd.Series) -> None:
        """Write global metadata to version_info table"""
        for key, value in meta.items():
            self.conn.execute(
                """
                INSERT OR REPLACE INTO version_info (key, value)
                VALUES (?, ?)
            """,
                [str(key), str(value)],
            )

    # ========================================
    # Incremental update helpers
    # ========================================

    def get_max_date(self, table: str, symbol: str = None) -> Optional[str]:
        """Get maximum date for incremental update"""
        self._check_table(table)
        if symbol:
            result = self.conn.execute(
                f"""
                SELECT MAX(date) FROM {table} WHERE symbol = ?
            """,
                [symbol],
            ).fetchone()
        else:
            result = self.conn.execute(f"""
                SELECT MAX(date) FROM {table}
            """).fetchone()

        if result and result[0]:
            return str(result[0])
        return None

    def get_min_date(self, table: str, symbol: str = None) -> Optional[str]:
        """Get minimum date for backfill detection"""
        self._check_table(table)
        if symbol:
            result = self.conn.execute(
                f"""
                SELECT MIN(date) FROM {table} WHERE symbol = ?
            """,
                [symbol],
            ).fetchone()
        else:
            result = self.conn.execute(f"""
                SELECT MIN(date) FROM {table}
            """).fetchone()

        if result and result[0]:
            return str(result[0])
        return None

    def get_existing_stocks(self, table: str = "stocks") -> List[str]:
        """Get list of symbols in database"""
        self._check_table(table)
        result = self.conn.execute(f"""
            SELECT DISTINCT symbol FROM {table}
        """).fetchall()
        return [r[0] for r in result]

    def get_stock_count(self) -> int:
        """Get total number of unique stocks"""
        result = self.conn.execute("""
            SELECT COUNT(DISTINCT symbol) FROM stocks
        """).fetchone()
        return result[0] if result else 0

    def get_data_status(self) -> dict:
        """Get a summary of data completeness across all tables.

        Returns:
            Dict with table names as keys and summary dicts as values.
        """
        status = {}
        for table in [
            "stocks",
            "valuation",
            "fundamentals",
            "exrights",
        ]:
            status[table] = self._get_table_summary(table)

        # Add fundamentals quarter progress
        status["fundamentals_quarters"] = len(self.get_completed_fundamental_quarters())

        # Add metadata counts
        for table in ["benchmark", "trade_days", "index_constituents", "stock_status"]:
            status[table] = self._get_table_summary_simple(table)

        return status

    def _get_table_summary(self, table: str) -> dict:
        self._check_table(table)
        """Get row count, stock count, and date range for a symbol-based table."""
        try:
            result = self.conn.execute(f"""
                SELECT
                    COUNT(*) as row_count,
                    COUNT(DISTINCT symbol) as stock_count,
                    MIN(date) as min_date,
                    MAX(date) as max_date
                FROM {table}
            """).fetchone()
            return {
                "rows": result[0],
                "stocks": result[1],
                "min_date": str(result[2]) if result[2] else None,
                "max_date": str(result[3]) if result[3] else None,
            }
        except Exception:
            return {"rows": 0, "stocks": 0, "min_date": None, "max_date": None}

    def _get_table_summary_simple(self, table: str) -> dict:
        self._check_table(table)
        """Get row count for a non-symbol table."""
        try:
            result = self.conn.execute(f"""
                SELECT COUNT(*) FROM {table}
            """).fetchone()
            return {"rows": result[0]}
        except Exception:
            return {"rows": 0}

    # ========================================
    # Derived fields
    # ========================================

    def compute_derived_fundamentals(self, symbols: list[str] | None = None) -> None:
        """
        Fill roa, roe_ttm, roa_ttm from existing fundamentals data.

        - roa = roe / (1 + debt_equity_ratio)
        - roe_ttm = rolling 4-quarter average of roe
        - roa_ttm = rolling 4-quarter average of roa

        When symbols is provided, only compute for those symbols (used by
        delta exports to avoid full-table window function recomputation).
        """
        symbol_filter = ""
        sub_where = "WHERE roe IS NOT NULL OR roa IS NOT NULL"
        if symbols:
            symbols_clause = self._symbols_in_clause(symbols)
            symbol_filter = f"AND symbol IN ({symbols_clause})"
            sub_where = f"WHERE fundamentals.symbol IN ({symbols_clause}) AND (roe IS NOT NULL OR roa IS NOT NULL)"

        # roa = roe / (1 + debt_equity_ratio)
        self.conn.execute(f"""
            UPDATE fundamentals
            SET roa = roe / (1 + debt_equity_ratio)
            WHERE roe IS NOT NULL
              AND debt_equity_ratio IS NOT NULL
              AND debt_equity_ratio != -1
              AND roa IS NULL
              {symbol_filter}
        """)

        # roe_ttm / roa_ttm = rolling 4-quarter average.
        # The outer UPDATE aliases fundamentals as "f", so the symbol
        # filter must reference "f.symbol" rather than "symbol".
        outer_symbol_filter = ""
        if symbols:
            outer_symbol_filter = f"AND f.symbol IN ({symbols_clause})"

        self.conn.execute(f"""
            UPDATE fundamentals f
            SET
                roe_ttm = sub.roe_ttm,
                roa_ttm = sub.roa_ttm
            FROM (
                SELECT
                    symbol, date,
                    AVG(roe) OVER w AS roe_ttm,
                    AVG(roa) OVER w AS roa_ttm
                FROM fundamentals
                {sub_where}
                WINDOW w AS (
                    PARTITION BY symbol
                    ORDER BY date
                    ROWS BETWEEN 3 PRECEDING AND CURRENT ROW
                )
            ) sub
            WHERE f.symbol = sub.symbol
              AND f.date = sub.date
              AND f.roe_ttm IS NULL
              {outer_symbol_filter}
        """)

        count_filter = f"WHERE symbol IN ({symbols_clause})" if symbols else ""
        updated = self.conn.execute(f"""
            SELECT
                SUM(CASE WHEN roa IS NOT NULL THEN 1 ELSE 0 END),
                SUM(CASE WHEN roe_ttm IS NOT NULL THEN 1 ELSE 0 END),
                SUM(CASE WHEN roa_ttm IS NOT NULL THEN 1 ELSE 0 END)
            FROM fundamentals
            {count_filter}
        """).fetchone()
        updated = tuple(value or 0 for value in updated)
        logger.info(
            f"Derived fundamentals: roa={updated[0]:,} roe_ttm={updated[1]:,} roa_ttm={updated[2]:,}"
        )

    # ========================================
    # Export to Parquet
    # ========================================

    def export_to_parquet(self, output_dir: str, market: str = "cn") -> None:
        """Export all data to PTrade Parquet format"""
        output_path = Path(output_dir)

        # Compute derived fields before export
        self.compute_derived_fundamentals()

        # Clean output directory to avoid mixing data from different markets
        if output_path.exists():
            safe_rmtree(output_path)

        for subdir in ["stocks", "exrights", "fundamentals", "valuation", "metadata"]:
            (output_path / subdir).mkdir(parents=True, exist_ok=True)

        logger.info("Exporting stocks...")
        self._export_stocks_batch(output_path / "stocks", market=market)

        logger.info("Exporting exrights...")
        self._export_exrights_batch(output_path / "exrights", market=market)

        logger.info("Exporting fundamentals...")
        self._export_fundamentals_batch(output_path / "fundamentals", market=market)

        logger.info("Exporting valuation...")
        self._export_valuation_batch(output_path / "valuation", market=market)

        logger.info("Exporting metadata...")
        self._export_metadata(output_path / "metadata", market=market)

        self._write_manifest(output_path, market=market)

        logger.info(f"Export complete: {output_path}")

    def export_delta(
        self,
        output_dir: str,
        base_version: str,
        target_version: str | None = None,
        market: str = "cn",
    ) -> None:
        """Export changed rows for client-side delta merge."""
        output_path = Path(output_dir)
        if output_path.exists():
            safe_rmtree(output_path)

        output_path.mkdir(parents=True, exist_ok=True)
        target_version = target_version or self.get_max_date("stocks")
        if not target_version:
            raise ValueError("target_version is required when stocks table is empty")
        if target_version <= base_version:
            raise ValueError(
                f"target_version ({target_version}) must be newer than "
                f"base_version ({base_version})"
            )

        # Only recompute derived fundamentals for symbols that changed,
        # avoiding a full-table window function scan for every delta export.
        fund_symbols = [r[0] for r in self.conn.execute("""
            SELECT DISTINCT symbol FROM (
                SELECT symbol
                FROM data_change_log
                WHERE table_name = 'fundamentals'
                  AND symbol IS NOT NULL AND date IS NOT NULL
                  AND date <= ?::DATE
                  AND (date > ?::DATE OR changed_at::DATE > ?::DATE)
                UNION
                SELECT DISTINCT symbol
                FROM fundamentals
                WHERE date > ?::DATE AND date <= ?::DATE
            ) changed
            ORDER BY symbol
        """, [target_version, base_version, base_version, base_version, target_version]).fetchall()]
        self.compute_derived_fundamentals(symbols=fund_symbols or None)

        tables = ["stocks", "valuation", "fundamentals", "exrights"]
        changed_tables = []
        changed_symbols = set()
        files = []

        for table in tables:
            table_dir = output_path / table
            table_dir.mkdir(parents=True, exist_ok=True)
            rows, symbols = self._export_delta_table(
                table, table_dir, base_version, target_version, market=market,
                changed_symbols=fund_symbols if table == "fundamentals" else None,
            )
            if rows == 0:
                table_dir.rmdir()
                continue
            changed_tables.append({"table": table, "rows": rows, "symbols": len(symbols)})
            changed_symbols.update(symbols)

        metadata_dir = output_path / "metadata"
        metadata_dir.mkdir(parents=True, exist_ok=True)
        changed_symbols.update(self._changed_stock_metadata_symbols(base_version))
        changed_symbols = set(self._filter_symbols_for_market(changed_symbols, market))
        metadata_tables = self._export_delta_metadata(
            metadata_dir,
            base_version=base_version,
            target_version=target_version,
            market=market,
            changed_symbols=sorted(changed_symbols),
        )
        changed_tables.extend(metadata_tables)

        if not changed_tables:
            # Clean up empty output directory before raising
            safe_rmtree(output_path)
            raise ValueError("no changed rows for delta export")

        self._write_delta_version_file(metadata_dir, target_version, market)
        for path in sorted(output_path.rglob("*.parquet")):
            files.append({
                "path": path.relative_to(output_path).as_posix(),
                "sha256": self._sha256_file(path),
                "size": path.stat().st_size,
            })

        self._write_delta_manifest(
            output_path,
            base_version=base_version,
            target_version=target_version,
            market=market,
            changed_tables=changed_tables,
            changed_symbols=sorted(changed_symbols),
            files=files,
        )

    def _changed_stock_metadata_symbols(self, base_version: str) -> list[str]:
        return [r[0] for r in self.conn.execute("""
            SELECT DISTINCT symbol
            FROM data_change_log
            WHERE table_name = 'stock_metadata'
              AND symbol IS NOT NULL
              AND changed_at::DATE > ?::DATE
            ORDER BY symbol
        """, [base_version]).fetchall()]

    def _cn_stock_filter_sql(self, symbol_expr: str = "symbol") -> str:
        """Official-first CN stock filter using BaoStock security_type when available."""
        fallback = self._CN_STOCK_FILTER.replace("symbol", symbol_expr)
        has_security_type_metadata = """
            EXISTS (
                SELECT 1 FROM stock_metadata
                WHERE security_type IS NOT NULL AND security_type != ''
            )
        """
        return f"""(
            (
                {has_security_type_metadata}
                AND EXISTS (
                    SELECT 1 FROM stock_metadata sm
                    WHERE sm.symbol = {symbol_expr}
                      AND sm.security_type = '1'
                      AND ({symbol_expr} LIKE '%.SZ' OR {symbol_expr} LIKE '%.SS')
                )
            )
            OR (
                NOT {has_security_type_metadata}
                AND {fallback}
            )
        )"""

    def _filter_symbols_for_market(self, symbols, market: str) -> list[str]:
        if market != "cn":
            return sorted(symbols)
        symbols = sorted(symbol for symbol in symbols if isinstance(symbol, str))
        if not symbols:
            return []
        candidates = pd.DataFrame({"symbol": symbols})
        self.conn.register("_symbol_filter_candidates", candidates)
        try:
            return [r[0] for r in self.conn.execute(
                f"""
                SELECT c.symbol
                FROM _symbol_filter_candidates c
                LEFT JOIN stock_metadata sm ON sm.symbol = c.symbol
                WHERE {self._cn_stock_filter_sql('c.symbol')}
                ORDER BY c.symbol
                """
            ).fetchall()]
        finally:
            self.conn.unregister("_symbol_filter_candidates")

    @staticmethod
    def _symbols_in_clause(symbols: list[str]) -> str:
        """Return a SQL-safe comma-separated IN clause from a symbol list."""
        escaped = [s.replace("'", "''") for s in symbols]
        return ", ".join(f"'{s}'" for s in escaped)

    @staticmethod
    def _delta_changed_cte_inner(
        table_name: str, base_version: str, target_version: str
    ) -> tuple[str, list]:
        """Return (inner_sql, params) selecting symbols changed between versions.

        The returned SQL is a ``SELECT DISTINCT symbol FROM (...)`` subquery
        suitable for embedding inside a CTE definition.  Callers append the
        returned params to their own parameter list when executing.

        Uses ``?::DATE`` positional placeholders — params are
        ``[target_version, base_version, base_version, base_version, target_version]``.
        """
        sql = f"""
            SELECT DISTINCT symbol FROM (
                SELECT symbol
                FROM data_change_log
                WHERE table_name = '{table_name}'
                  AND symbol IS NOT NULL AND date IS NOT NULL
                  AND date <= ?::DATE
                  AND (date > ?::DATE OR changed_at::DATE > ?::DATE)
                UNION
                SELECT DISTINCT symbol
                FROM {table_name}
                WHERE date > ?::DATE AND date <= ?::DATE
            ) changed"""
        params = [target_version, base_version, base_version, base_version, target_version]
        return sql, params

    def _export_delta_table(
        self,
        table: str,
        output_dir: Path,
        base_version: str,
        target_version: str,
        market: str = "cn",
        changed_symbols: list[str] | None = None,
    ) -> tuple[int, list[str]]:
        """Export one symbol-keyed table as per-symbol delta parquet files."""
        if table == "stocks":
            return self._export_delta_stocks_table(
                output_dir, base_version, target_version, market=market
            )
        if table == "fundamentals":
            return self._export_delta_fundamentals_table(
                output_dir, base_version, target_version, market=market,
                changed_symbols=changed_symbols,
            )
        if table == "valuation":
            return self._export_delta_valuation_table(
                output_dir, base_version, target_version, market=market
            )
        if table == "exrights":
            return self._export_delta_exrights_table(
                output_dir, base_version, target_version, market=market
            )
        raise ValueError(f"unsupported delta table: {table}")

    def _copy_delta_symbol_temp_table(
        self,
        table_name: str,
        temp_table: str,
        output_dir: Path,
        base_version: str,
        target_version: str,
    ) -> tuple[int, list[str]]:
        ranges_table = f"_delta_{table_name}_ranges"
        self.conn.execute(f"""
            CREATE OR REPLACE TEMP TABLE {ranges_table} AS
            SELECT symbol, MIN(date) AS min_date
            FROM (
                SELECT symbol, date
                FROM data_change_log
                WHERE table_name = ?
                  AND symbol IS NOT NULL
                  AND date IS NOT NULL
                  AND date <= ?::DATE
                  AND (date > ?::DATE OR changed_at::DATE > ?::DATE)
                UNION ALL
                SELECT DISTINCT symbol, date
                FROM {temp_table}
                WHERE date > ?::DATE AND date <= ?::DATE
            ) changed
            GROUP BY symbol
        """, [
            table_name,
            target_version,
            base_version,
            base_version,
            base_version,
            target_version,
        ])
        symbols = [r[0] for r in self.conn.execute(f"""
            SELECT DISTINCT t.symbol
            FROM {temp_table} t
            JOIN {ranges_table} r ON r.symbol = t.symbol
            WHERE t.date >= r.min_date AND t.date <= ?::DATE
            ORDER BY t.symbol
        """, [target_version]).fetchall()]

        if not symbols:
            return 0, []

        for symbol in symbols:
            symbol_escaped = symbol.replace("'", "''")
            output_file = output_dir / f"{symbol}.parquet"
            self.conn.execute(f"""
                COPY (
                    SELECT t.* EXCLUDE (symbol)
                    FROM {temp_table} t
                    JOIN {ranges_table} r ON r.symbol = t.symbol
                    WHERE t.symbol = '{symbol_escaped}'
                      AND t.date >= r.min_date AND t.date <= ?::DATE
                    ORDER BY t.date
                ) TO '{output_file}' (FORMAT PARQUET, CODEC 'ZSTD')
            """, [target_version])

        total_rows = self.conn.execute(f"""
            SELECT COUNT(*)
            FROM {temp_table} t
            JOIN {ranges_table} r ON r.symbol = t.symbol
            WHERE t.date >= r.min_date AND t.date <= ?::DATE
        """, [target_version]).fetchone()[0]

        return total_rows, symbols

    def _export_delta_stocks_table(
        self,
        output_dir: Path,
        base_version: str,
        target_version: str,
        market: str = "cn",
    ) -> tuple[int, list[str]]:
        # Determine changed stock symbols so we only gap-fill those,
        # not all ~5000 stocks. Two sources: change_log (modified) and
        # direct stocks query (new symbols without log entries).
        cn_cond = self._cn_stock_filter_sql('stocks.symbol') if market == "cn" else ""
        changed_cte, changed_params = self._delta_changed_cte_inner(
            "stocks", base_version, target_version
        )

        # Build lifespans WHERE clause: changed symbols + optional CN filter
        lifespan_filters = ["symbol IN (SELECT symbol FROM changed_symbols)"]
        if cn_cond:
            lifespan_filters.append(cn_cond)
        lifespan_where = "WHERE " + " AND ".join(lifespan_filters)

        if market == "us":
            high_limit_sql = "CAST(NULL AS DOUBLE) AS high_limit"
            low_limit_sql = "CAST(NULL AS DOUBLE) AS low_limit"
        else:
            high_limit_sql = """
                CASE
                    WHEN LEFT(symbol, 3) IN ('300', '301', '688', '689')
                         AND date >= '2020-08-24' THEN ROUND(preclose * 1.20, 2)
                    ELSE ROUND(preclose * 1.10, 2)
                END AS high_limit
            """
            low_limit_sql = """
                CASE
                    WHEN LEFT(symbol, 3) IN ('300', '301', '688', '689')
                         AND date >= '2020-08-24' THEN ROUND(preclose * 0.80, 2)
                    ELSE ROUND(preclose * 0.90, 2)
                END AS low_limit
            """

        self.conn.execute(f"""
            CREATE OR REPLACE TEMP TABLE _delta_stocks_export AS
            WITH trade_cal AS (
                SELECT DISTINCT date FROM stocks
                UNION
                SELECT date FROM trade_days
            ),
            changed_symbols AS (
                {changed_cte}
            ),
            lifespans AS (
                SELECT symbol, MIN(date) AS first_date, MAX(date) AS last_date
                FROM stocks
                {lifespan_where}
                GROUP BY symbol
            ),
            joined AS (
                SELECT
                    ls.symbol,
                    tc.date,
                    CASE WHEN s.volume > 0 THEN s.open END AS open,
                    CASE WHEN s.volume > 0 THEN s.close END AS close,
                    CASE WHEN s.volume > 0 THEN s.high END AS high,
                    CASE WHEN s.volume > 0 THEN s.low END AS low,
                    CASE WHEN s.volume > 0 THEN s.preclose END AS preclose,
                    COALESCE(s.volume, 0) AS volume,
                    COALESCE(s.money, 0.0) AS money
                FROM lifespans ls
                CROSS JOIN trade_cal tc
                LEFT JOIN stocks s ON s.symbol = ls.symbol AND s.date = tc.date
                WHERE tc.date >= ls.first_date AND tc.date <= ls.last_date
            ),
            gap_filled AS (
                SELECT
                    symbol, date,
                    COALESCE(open, last_value(close IGNORE NULLS) OVER w) AS open,
                    COALESCE(close, last_value(close IGNORE NULLS) OVER w) AS close,
                    COALESCE(high, last_value(close IGNORE NULLS) OVER w) AS high,
                    COALESCE(low, last_value(close IGNORE NULLS) OVER w) AS low,
                    preclose, volume, money
                FROM joined
                WINDOW w AS (
                    PARTITION BY symbol ORDER BY date
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                )
            ),
            with_lag AS (
                SELECT
                    symbol, date,
                    open, close, high, low,
                    LAG(close) OVER (PARTITION BY symbol ORDER BY date) AS lag_close,
                    last_value(CASE WHEN volume > 0 THEN date END IGNORE NULLS) OVER (
                        PARTITION BY symbol ORDER BY date
                        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                    ) AS last_active_date,
                    preclose AS stored_preclose,
                    volume, money
                FROM gap_filled
            ),
            adj AS (
                SELECT
                    wl.symbol, wl.date,
                    SUM(COALESCE(ex.bonus_ps, 0)
                        - COALESCE(ex.rationed_px, 0) * COALESCE(ex.rationed_ps, 0)
                    ) AS total_deduction,
                    EXP(SUM(LN(
                        1 + COALESCE(ex.allotted_ps, 0) + COALESCE(ex.rationed_ps, 0)
                    ))) AS total_divisor,
                    COUNT(ex.date) AS event_count
                FROM with_lag wl
                INNER JOIN exrights ex ON ex.symbol = wl.symbol
                    AND ex.date > wl.last_active_date AND ex.date <= wl.date
                GROUP BY wl.symbol, wl.date
            ),
            filled AS (
                SELECT
                    wl.symbol,
                    wl.date::TIMESTAMP_NS AS date,
                    wl.open, wl.close, wl.high, wl.low,
                    CASE
                        WHEN adj.event_count > 0 AND wl.lag_close IS NOT NULL
                             AND wl.volume > 0 THEN
                            ROUND(
                                (wl.lag_close - adj.total_deduction)
                                / adj.total_divisor,
                                2)
                        ELSE COALESCE(wl.lag_close, wl.stored_preclose)
                    END AS preclose,
                    wl.volume, wl.money
                FROM with_lag wl
                LEFT JOIN adj ON adj.symbol = wl.symbol AND adj.date = wl.date
            )
            SELECT
                symbol, date, open, close, high, low,
                {high_limit_sql},
                {low_limit_sql},
                preclose, volume, money
            FROM filled
        """, changed_params)
        try:
            return self._copy_delta_symbol_temp_table(
                "stocks", "_delta_stocks_export", output_dir, base_version, target_version
            )
        finally:
            self.conn.execute("DROP TABLE IF EXISTS _delta_stocks_export")

    def _export_delta_fundamentals_table(
        self,
        output_dir: Path,
        base_version: str,
        target_version: str,
        market: str = "cn",
        changed_symbols: list[str] | None = None,
    ) -> tuple[int, list[str]]:
        # When changed_symbols is provided (pre-computed by export_delta),
        # skip the data_change_log query and use the list directly.
        if changed_symbols:
            cte_sql = "SELECT unnest(?::VARCHAR[]) AS symbol"
            cte_params = [changed_symbols]
        else:
            cte_sql, cte_params = self._delta_changed_cte_inner(
                "fundamentals", base_version, target_version
            )
        cn_filter = (
            f"AND {self._cn_stock_filter_sql('fundamentals.symbol')}"
            if market == "cn" else ""
        )
        self.conn.execute(f"""
            CREATE OR REPLACE TEMP TABLE _delta_fundamentals_export AS
            WITH changed_symbols AS (
                {cte_sql}
            )
            SELECT
                symbol,
                date::TIMESTAMP_NS AS date, publ_date,
                operating_revenue_grow_rate, net_profit_grow_rate,
                basic_eps_yoy, np_parent_company_yoy,
                net_profit_ratio,
                AVG(net_profit_ratio) OVER (
                    PARTITION BY symbol ORDER BY date ROWS BETWEEN 3 PRECEDING AND CURRENT ROW
                ) AS net_profit_ratio_ttm,
                gross_income_ratio,
                AVG(gross_income_ratio) OVER (
                    PARTITION BY symbol ORDER BY date ROWS BETWEEN 3 PRECEDING AND CURRENT ROW
                ) AS gross_income_ratio_ttm,
                roa, roa_ttm,
                roe, roe_ttm,
                total_asset_grow_rate, total_asset_turnover_rate,
                current_assets_turnover_rate, inventory_turnover_rate,
                accounts_receivables_turnover_rate,
                current_ratio, quick_ratio, debt_equity_ratio,
                interest_cover, roic, roa_ebit_ttm,
                total_shares, a_floats,
                {_FUNDAMENTAL_EXPORT_EXTRA_SQL}
            FROM fundamentals
            WHERE symbol IN (SELECT symbol FROM changed_symbols)
            {cn_filter}
        """, cte_params)
        try:
            return self._copy_delta_symbol_temp_table(
                "fundamentals", "_delta_fundamentals_export", output_dir, base_version, target_version
            )
        finally:
            self.conn.execute("DROP TABLE IF EXISTS _delta_fundamentals_export")

    def _export_delta_valuation_table(
        self,
        output_dir: Path,
        base_version: str,
        target_version: str,
        market: str = "cn",
    ) -> tuple[int, list[str]]:
        # Only process valuation rows for symbols that actually changed.
        changed_cte, changed_params = self._delta_changed_cte_inner(
            "valuation", base_version, target_version
        )
        cn_filter = (
            f"AND {self._cn_stock_filter_sql('v.symbol')}"
            if market == "cn" else ""
        )
        self.conn.execute(f"""
            CREATE OR REPLACE TEMP TABLE _delta_valuation_export AS
            WITH changed_val_symbols AS (
                {changed_cte}
            )
            SELECT
                v.symbol,
                v.date::TIMESTAMP_NS AS date,
                v.pe_ttm, v.pb, v.ps_ttm, v.pcf,
                f.roe, f.roe_ttm, f.roa, f.roa_ttm,
                CASE WHEN v.pb > 0 THEN ROUND(s.close / v.pb, 4) ELSE NULL END AS naps,
                f.total_shares, f.a_floats,
                CASE WHEN f.total_shares > 0 AND s.close IS NOT NULL
                     THEN ROUND(f.total_shares * s.close, 2) END AS total_value,
                CASE WHEN f.a_floats > 0 AND s.close IS NOT NULL
                     THEN ROUND(f.a_floats * s.close, 2) END AS float_value,
                v.turnover_rate
            FROM valuation v
            ASOF JOIN (SELECT symbol, date, close FROM stocks) s
                ON v.symbol = s.symbol AND v.date >= s.date
            LEFT JOIN LATERAL (
                SELECT total_shares, a_floats, roe, roe_ttm, roa, roa_ttm
                FROM fundamentals f2
                WHERE f2.symbol = v.symbol AND f2.date <= v.date
                ORDER BY f2.date DESC LIMIT 1
            ) f ON TRUE
            WHERE v.symbol IN (SELECT symbol FROM changed_val_symbols)
            {cn_filter}
        """, changed_params)
        try:
            return self._copy_delta_symbol_temp_table(
                "valuation", "_delta_valuation_export", output_dir, base_version, target_version
            )
        finally:
            self.conn.execute("DROP TABLE IF EXISTS _delta_valuation_export")

    def _export_delta_exrights_table(
        self,
        output_dir: Path,
        base_version: str,
        target_version: str,
        market: str = "cn",
    ) -> tuple[int, list[str]]:
        import numpy as np

        cn_filter = f"AND {self._cn_stock_filter_sql('exrights.symbol')}" if market == "cn" else ""
        symbols = [r[0] for r in self.conn.execute(f"""
            SELECT DISTINCT symbol FROM (
                SELECT symbol
                FROM data_change_log
                WHERE table_name = 'exrights'
                  AND symbol IS NOT NULL
                  AND date IS NOT NULL
                  AND date <= ?::DATE
                  AND (date > ?::DATE OR changed_at::DATE > ?::DATE)
                UNION
                SELECT symbol
                FROM exrights
                WHERE date > ?::DATE AND date <= ?::DATE {cn_filter}
            ) changed
            ORDER BY symbol
        """, [
            target_version,
            base_version,
            base_version,
            base_version,
            target_version,
        ]).fetchall()]
        if not symbols:
            return 0, []

        symbols_sql = ", ".join("'" + symbol.replace("'", "''") + "'" for symbol in symbols)
        df_all = self.conn.execute(f"""
            SELECT symbol, date::TIMESTAMP_NS AS date,
                   allotted_ps, rationed_ps, rationed_px, bonus_ps, dividend
            FROM exrights
            WHERE symbol IN ({symbols_sql}) {cn_filter}
            ORDER BY symbol, date
        """).fetchdf()

        base_ts = pd.Timestamp(base_version)
        target_ts = pd.Timestamp(target_version)
        rows = 0
        written_symbols = []
        for symbol, group in df_all.groupby("symbol", sort=True):
            group = group.reset_index(drop=True)
            n = len(group)
            allotted = group["allotted_ps"].values
            bonus = group["bonus_ps"].values
            rationed = group["rationed_ps"].values
            rationed_px = group["rationed_px"].values

            fa = np.ones(n + 1, dtype="float64")
            fb = np.zeros(n + 1, dtype="float64")
            for i in range(n - 1, -1, -1):
                m = 1.0 + allotted[i] + rationed[i]
                fa[i] = fa[i + 1] / m
                fb[i] = fa[i + 1] * (-bonus[i] + rationed[i] * rationed_px[i]) / m + fb[i + 1]

            group["exer_forward_a"] = fa[:n]
            group["exer_forward_b"] = fb[:n]
            has_delta = (
                (group["date"] > base_ts) & (group["date"] <= target_ts)
            ).any()
            if not has_delta:
                continue
            group.drop(columns=["symbol"]).to_parquet(
                str(output_dir / f"{symbol}.parquet"),
                index=False,
                compression="zstd",
            )
            rows += len(group)
            written_symbols.append(symbol)

        return rows, written_symbols

    def _export_delta_metadata(
        self,
        output_dir: Path,
        base_version: str,
        target_version: str,
        market: str,
        changed_symbols: list[str],
    ) -> list[dict]:
        """Export metadata rows needed by a delta package."""
        changed_tables = []
        if changed_symbols:
            trade_days_rows = self._copy_delta_trade_days(output_dir, base_version, target_version)
            if trade_days_rows:
                changed_tables.append({"table": "trade_days", "rows": trade_days_rows})

            benchmark_rows = self._copy_delta_benchmark(output_dir, base_version, target_version)
            if benchmark_rows:
                changed_tables.append({"table": "benchmark", "rows": benchmark_rows})

            index_rows = self._copy_delta_json_date_table(
                "index_constituents",
                output_dir / "index_constituents.parquet",
                base_version,
                target_version,
                "date, index_code, symbols::JSON::VARCHAR[] AS symbols",
            )
            if index_rows:
                changed_tables.append({"table": "index_constituents", "rows": index_rows})

            self._enrich_halt_status_from_volume()
            status_rows = self._copy_delta_json_date_table(
                "stock_status",
                output_dir / "stock_status.parquet",
                base_version,
                target_version,
                "date, status_type, symbols::JSON::VARCHAR[] AS symbols",
            )
            if status_rows:
                changed_tables.append({"table": "stock_status", "rows": status_rows})

        metadata_rows = self._copy_delta_stock_metadata(
            output_dir, changed_symbols, base_version, market
        )
        if metadata_rows:
            changed_tables.append({"table": "stock_metadata", "rows": metadata_rows})

        return changed_tables

    def _copy_delta_trade_days(
        self, output_dir: Path, base_version: str, target_version: str
    ) -> int:
        rows = self.conn.execute("""
            SELECT COUNT(*) FROM (
                SELECT DISTINCT date FROM (
                    SELECT date FROM trade_days
                    UNION
                    SELECT DISTINCT date FROM stocks
                )
                WHERE date > ?::DATE AND date <= ?::DATE
            )
        """, [base_version, target_version]).fetchone()[0]
        if rows:
            self.conn.execute(f"""
                COPY (
                    SELECT DISTINCT date FROM (
                        SELECT date FROM trade_days
                        UNION
                        SELECT DISTINCT date FROM stocks
                    )
                    WHERE date > ?::DATE AND date <= ?::DATE
                    ORDER BY date
                ) TO '{output_dir / "trade_days.parquet"}' (FORMAT PARQUET, CODEC 'ZSTD')
            """, [base_version, target_version])
        return rows

    def _copy_delta_benchmark(
        self, output_dir: Path, base_version: str, target_version: str
    ) -> int:
        benchmark_symbol = BENCHMARK_CONFIG["default_index"]
        rows = self.conn.execute("""
            SELECT COUNT(*) FROM stocks
            WHERE symbol = ? AND date > ?::DATE AND date <= ?::DATE
        """, [benchmark_symbol, base_version, target_version]).fetchone()[0]
        if rows:
            self.conn.execute(f"""
                COPY (
                    SELECT date, open, high, low, close, volume,
                           COALESCE(money, 0.0) AS money
                    FROM stocks
                    WHERE symbol = ? AND date > ?::DATE AND date <= ?::DATE
                    ORDER BY date
                ) TO '{output_dir / "benchmark.parquet"}' (FORMAT PARQUET, CODEC 'ZSTD')
            """, [benchmark_symbol, base_version, target_version])
            return rows

        rows = self.conn.execute("""
            SELECT COUNT(*) FROM benchmark
            WHERE date > ?::DATE AND date <= ?::DATE
        """, [base_version, target_version]).fetchone()[0]
        if rows:
            self.conn.execute(f"""
                COPY (
                    SELECT * FROM benchmark
                    WHERE date > ?::DATE AND date <= ?::DATE
                    ORDER BY date
                ) TO '{output_dir / "benchmark.parquet"}' (FORMAT PARQUET, CODEC 'ZSTD')
            """, [base_version, target_version])
        return rows

    def _copy_delta_json_date_table(
        self,
        table: str,
        output_file: Path,
        base_version: str,
        target_version: str,
        select_columns: str,
    ) -> int:
        date_expr = "STRPTIME(date, '%Y%m%d')::DATE"
        rows = self.conn.execute(f"""
            SELECT COUNT(*) FROM {table}
            WHERE {date_expr} > ?::DATE AND {date_expr} <= ?::DATE
        """, [base_version, target_version]).fetchone()[0]
        if rows:
            self.conn.execute(f"""
                COPY (
                    SELECT {select_columns}
                    FROM {table}
                    WHERE {date_expr} > ?::DATE AND {date_expr} <= ?::DATE
                    ORDER BY date
                ) TO '{output_file}' (FORMAT PARQUET, CODEC 'ZSTD')
            """, [base_version, target_version])
        return rows

    def _copy_delta_stock_metadata(
        self,
        output_dir: Path,
        changed_symbols: list[str],
        base_version: str,
        market: str,
    ) -> int:
        metadata_symbols = self._changed_stock_metadata_symbols(base_version)
        symbols = self._filter_symbols_for_market(set(changed_symbols) | set(metadata_symbols), market)
        if not symbols:
            return 0
        symbols_sql = ", ".join("'" + symbol.replace("'", "''") + "'" for symbol in symbols)
        cn_filter = f"AND {self._cn_stock_filter_sql('stock_metadata.symbol')}" if market == "cn" else ""
        rows = self.conn.execute(f"""
            SELECT COUNT(*) FROM stock_metadata
            WHERE symbol IN ({symbols_sql}) {cn_filter}
        """).fetchone()[0]
        if rows:
            self.conn.execute(f"""
                COPY (
                    SELECT * FROM stock_metadata
                    WHERE symbol IN ({symbols_sql}) {cn_filter}
                    ORDER BY symbol
                ) TO '{output_dir / "stock_metadata.parquet"}'
                (FORMAT PARQUET, CODEC 'ZSTD')
            """)
        return rows

    def _write_delta_version_file(self, output_dir: Path, target_version: str, market: str) -> None:
        cn_filter = f"WHERE {self._cn_stock_filter_sql('stocks.symbol')}" if market == "cn" else ""
        result = self.conn.execute(f"""
            SELECT
                (SELECT COUNT(DISTINCT symbol) FROM stocks {cn_filter}) as num_stocks,
                (SELECT MIN(date)::VARCHAR FROM stocks) as start_date
        """).fetchone()
        version_data = pd.DataFrame([
            {
                "version": target_version,
                "num_stocks": result[0] or 0,
                "export_date": datetime.now().strftime("%Y-%m-%d"),
                "start_date": result[1] or "",
            }
        ])
        version_data.to_parquet(output_dir / "version.parquet", index=False)

    def _export_per_symbol_table(
        self, table: str, output_dir: Path, market: str = "cn"
    ) -> None:
        """Export table to per-symbol Parquet files using DuckDB COPY"""
        symbols = self.get_existing_stocks(table)

        if not symbols:
            logger.info(f"No data in {table} to export")
            return

        for symbol in symbols:
            output_file = output_dir / f"{symbol}.parquet"
            # Escape single quotes in symbol for SQL safety
            symbol_escaped = symbol.replace("'", "''")

            if table == "stocks":
                # Calculate high_limit and low_limit during export
                self._export_stocks_with_limits(
                    symbol_escaped, output_file, market=market
                )
            elif table == "fundamentals":
                # Calculate TTM indicators during export
                self._export_fundamentals_with_ttm(symbol_escaped, output_file)
            elif table == "valuation":
                # Enrich with total_shares/a_floats from fundamentals
                self._export_valuation_enriched(symbol_escaped, output_file)
            elif table == "exrights":
                self._export_exrights_with_factors(symbol_escaped, output_file)
            else:
                self.conn.execute(f"""
                    COPY (
                        SELECT * EXCLUDE (symbol) REPLACE (date::TIMESTAMP_NS AS date) FROM {table}
                        WHERE symbol = '{symbol_escaped}'
                        ORDER BY date
                    ) TO '{output_file}' (FORMAT PARQUET, CODEC 'ZSTD')
                """)

        logger.info(f"Exported {len(symbols)} {table} files")

    def _export_exrights_with_factors(self, symbol_escaped: str, output_file: Path) -> None:
        """Export exrights with computed exer_forward_a/b factors"""
        import numpy as np

        df = self.conn.execute(f"""
            SELECT * EXCLUDE (symbol) REPLACE (date::TIMESTAMP_NS AS date)
            FROM exrights WHERE symbol = '{symbol_escaped}' ORDER BY date
        """).fetchdf()

        if df.empty:
            df.to_parquet(str(output_file), index=False)
            return

        n = len(df)
        allotted = df["allotted_ps"].values
        bonus = df["bonus_ps"].values
        rationed = df["rationed_ps"].values
        rationed_px = df["rationed_px"].values

        # Backward accumulation of forward-adjustment factors.
        # Continuity at each ex-date requires:
        #   fa[i] * P_raw + fb[i] = fa[i+1] * P_ex + fb[i+1]
        # where P_ex = (P_raw - bonus + rat*rat_px) / m, giving:
        #   fa[i] = fa[i+1] / m
        #   fb[i] = fa[i+1] * (-bonus + rat*rat_px) / m + fb[i+1]
        fa = np.ones(n + 1, dtype="float64")
        fb = np.zeros(n + 1, dtype="float64")
        for i in range(n - 1, -1, -1):
            m = 1.0 + allotted[i] + rationed[i]
            fa[i] = fa[i + 1] / m
            fb[i] = fa[i + 1] * (-bonus[i] + rationed[i] * rationed_px[i]) / m + fb[i + 1]

        df["exer_forward_a"] = fa[:n]
        df["exer_forward_b"] = fb[:n]

        df.to_parquet(str(output_file), index=False, compression="zstd")

    def _export_exrights_batch(self, output_dir: Path, market: str = "cn") -> None:
        """Export all exrights with pre-computed forward adj factors (batch)."""
        import time
        import numpy as np
        t0 = time.time()

        market_filter = (
            f"WHERE {self._cn_stock_filter_sql('e.symbol')}"
            if market == "cn"
            else ""
        )
        df_all = self.conn.execute(f"""
            SELECT e.symbol, e.date::TIMESTAMP_NS AS date,
                   allotted_ps, rationed_ps, rationed_px, bonus_ps, dividend
            FROM exrights e
            {market_filter}
            ORDER BY e.symbol, e.date
        """).fetchdf()

        if df_all.empty:
            logger.info("No exrights data to export")
            return

        count = 0
        for symbol, group in df_all.groupby("symbol"):
            df = group.drop(columns=["symbol"]).reset_index(drop=True)
            n = len(df)
            allotted = df["allotted_ps"].values
            bonus = df["bonus_ps"].values
            rationed = df["rationed_ps"].values
            rationed_px = df["rationed_px"].values

            fa = np.ones(n + 1, dtype="float64")
            fb = np.zeros(n + 1, dtype="float64")
            for i in range(n - 1, -1, -1):
                m = 1.0 + allotted[i] + rationed[i]
                fa[i] = fa[i + 1] / m
                fb[i] = fa[i + 1] * (-bonus[i] + rationed[i] * rationed_px[i]) / m + fb[i + 1]

            df["exer_forward_a"] = fa[:n]
            df["exer_forward_b"] = fb[:n]
            df.to_parquet(str(output_dir / f"{symbol}.parquet"), index=False, compression="zstd")
            count += 1

        logger.info(f"Exported {count} exrights files in {time.time() - t0:.1f}s")

    def _export_fundamentals_batch(self, output_dir: Path, market: str = "cn") -> None:
        """Export all fundamentals with TTM ratios (batch via temp table)."""
        import time
        t0 = time.time()

        market_filter = (
            f"WHERE {self._cn_stock_filter_sql('f.symbol')}"
            if market == "cn"
            else ""
        )
        self.conn.execute(f"""
            CREATE OR REPLACE TEMP TABLE _fundamentals_export AS
            SELECT
                symbol,
                date::TIMESTAMP_NS AS date, publ_date,
                operating_revenue_grow_rate, net_profit_grow_rate,
                basic_eps_yoy, np_parent_company_yoy,
                net_profit_ratio,
                AVG(net_profit_ratio) OVER (
                    PARTITION BY symbol ORDER BY date ROWS BETWEEN 3 PRECEDING AND CURRENT ROW
                ) AS net_profit_ratio_ttm,
                gross_income_ratio,
                AVG(gross_income_ratio) OVER (
                    PARTITION BY symbol ORDER BY date ROWS BETWEEN 3 PRECEDING AND CURRENT ROW
                ) AS gross_income_ratio_ttm,
                roa, roa_ttm,
                roe, roe_ttm,
                total_asset_grow_rate, total_asset_turnover_rate,
                current_assets_turnover_rate, inventory_turnover_rate,
                accounts_receivables_turnover_rate,
                current_ratio, quick_ratio, debt_equity_ratio,
                interest_cover, roic, roa_ebit_ttm,
                total_shares, a_floats,
                {_FUNDAMENTAL_EXPORT_EXTRA_SQL}
            FROM fundamentals f
            {market_filter}
        """)

        symbols = [r[0] for r in self.conn.execute(
            "SELECT DISTINCT symbol FROM _fundamentals_export ORDER BY symbol"
        ).fetchall()]

        for symbol in symbols:
            se = symbol.replace("'", "''")
            self.conn.execute(f"""
                COPY (
                    SELECT * EXCLUDE (symbol) FROM _fundamentals_export
                    WHERE symbol = '{se}' ORDER BY date
                ) TO '{output_dir / f"{symbol}.parquet"}' (FORMAT PARQUET, CODEC 'ZSTD')
            """)

        self.conn.execute("DROP TABLE IF EXISTS _fundamentals_export")
        logger.info(f"Exported {len(symbols)} fundamentals files in {time.time() - t0:.1f}s")

    def _export_valuation_batch(self, output_dir: Path, market: str = "cn") -> None:
        """Export valuation data enriched with fundamentals."""
        import time
        t0 = time.time()

        market_filter = (
            f"WHERE {self._cn_stock_filter_sql('v.symbol')}"
            if market == "cn"
            else ""
        )

        symbols = [r[0] for r in self.conn.execute(
            f"SELECT DISTINCT v.symbol FROM valuation v {market_filter} ORDER BY v.symbol"
        ).fetchall()]

        chunk_size = 200
        exported_count = 0
        for offset in range(0, len(symbols), chunk_size):
            chunk = symbols[offset:offset + chunk_size]
            escaped_symbols = [symbol.replace("'", "''") for symbol in chunk]
            symbol_list = ", ".join(f"'{symbol}'" for symbol in escaped_symbols)
            self.conn.execute(f"""
                CREATE OR REPLACE TEMP TABLE _valuation_export_chunk AS
                SELECT
                    v.symbol,
                    v.date::TIMESTAMP_NS AS date,
                    v.pe_ttm, v.pb, v.ps_ttm, v.pcf,
                    f.roe, f.roe_ttm, f.roa, f.roa_ttm,
                    CASE WHEN v.pb > 0 THEN ROUND(s.close / v.pb, 4) ELSE NULL END AS naps,
                    f.total_shares, f.a_floats,
                    CASE WHEN f.total_shares > 0 AND s.close IS NOT NULL
                         THEN ROUND(f.total_shares * s.close, 2) END AS total_value,
                    CASE WHEN f.a_floats > 0 AND s.close IS NOT NULL
                         THEN ROUND(f.a_floats * s.close, 2) END AS float_value,
                    v.turnover_rate
                FROM valuation v
                ASOF JOIN (SELECT symbol, date, close FROM stocks WHERE symbol IN ({symbol_list})) s
                    ON v.symbol = s.symbol AND v.date >= s.date
                LEFT JOIN LATERAL (
                    SELECT total_shares, a_floats, roe, roe_ttm, roa, roa_ttm
                    FROM fundamentals f2
                    WHERE f2.symbol = v.symbol AND f2.date <= v.date
                    ORDER BY f2.date DESC LIMIT 1
                ) f ON TRUE
                WHERE v.symbol IN ({symbol_list})
            """)

            export_symbols = [r[0] for r in self.conn.execute(
                "SELECT DISTINCT symbol FROM _valuation_export_chunk ORDER BY symbol"
            ).fetchall()]
            exported_count += len(export_symbols)

            for symbol in export_symbols:
                se = symbol.replace("'", "''")
                self.conn.execute(f"""
                    COPY (
                        SELECT * EXCLUDE (symbol) FROM _valuation_export_chunk
                        WHERE symbol = '{se}' ORDER BY date
                    ) TO '{output_dir / f"{symbol}.parquet"}' (FORMAT PARQUET, CODEC 'ZSTD')
                """)

        self.conn.execute("DROP TABLE IF EXISTS _valuation_export_chunk")
        logger.info(f"Exported {exported_count} valuation files in {time.time() - t0:.1f}s")

    def _export_stocks_batch(self, output_dir: Path, market: str = "cn") -> None:
        """Export all stocks with gap-filling and price limits (batch optimized).

        Pre-computes gap-filled data for ALL stocks in one query,
        then writes per-symbol parquet files with a simple filter.
        """
        import time
        t0 = time.time()

        # Step 1: Build gap-filled table for all stocks at once
        cn_filter = (
            f"WHERE {self._cn_stock_filter_sql('s0.symbol')}"
            if market == "cn" else ""
        )
        logger.info("  Pre-computing gap-filled data for all stocks...")
        self.conn.execute(f"""
            CREATE OR REPLACE TEMP TABLE _stocks_filled AS
            WITH trade_cal AS (
                SELECT DISTINCT date FROM stocks
                UNION
                SELECT date FROM trade_days
            ),
            lifespans AS (
                SELECT s0.symbol, MIN(s0.date) AS first_date, MAX(s0.date) AS last_date
                FROM stocks s0 {cn_filter} GROUP BY s0.symbol
            ),
            joined AS (
                SELECT
                    ls.symbol,
                    tc.date,
                    -- NULL out OHLC when volume=0 (suspended).
                    -- This prevents mootdx's ex-rights adjusted prices
                    -- from replacing the last traded price during suspension.
                    CASE WHEN s.volume > 0 THEN s.open END AS open,
                    CASE WHEN s.volume > 0 THEN s.close END AS close,
                    CASE WHEN s.volume > 0 THEN s.high END AS high,
                    CASE WHEN s.volume > 0 THEN s.low END AS low,
                    -- Keep preclose only on trading days; during suspension
                    -- it will be recomputed as LAG(close) in the final step.
                    CASE WHEN s.volume > 0 THEN s.preclose END AS preclose,
                    COALESCE(s.volume, 0) AS volume,
                    COALESCE(s.money, 0.0) AS money
                FROM lifespans ls
                CROSS JOIN trade_cal tc
                LEFT JOIN stocks s ON s.symbol = ls.symbol AND s.date = tc.date
                WHERE tc.date >= ls.first_date AND tc.date <= ls.last_date
            ),
            gap_filled AS (
                SELECT
                    symbol, date,
                    COALESCE(open, last_value(close IGNORE NULLS) OVER w) AS open,
                    COALESCE(close, last_value(close IGNORE NULLS) OVER w) AS close,
                    COALESCE(high, last_value(close IGNORE NULLS) OVER w) AS high,
                    COALESCE(low, last_value(close IGNORE NULLS) OVER w) AS low,
                    preclose, volume, money
                FROM joined
                WINDOW w AS (PARTITION BY symbol ORDER BY date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
            ),
            with_lag AS (
                SELECT
                    symbol, date,
                    open, close, high, low,
                    LAG(close) OVER (PARTITION BY symbol ORDER BY date) AS lag_close,
                    -- Use last ACTIVE trading date (not gap-filled date) for exrights range check.
                    -- During suspension, lag_date would be yesterday (gap-filled),
                    -- but we need the last real trading day to catch exrights events
                    -- that occurred during the suspension period.
                    last_value(CASE WHEN volume > 0 THEN date END IGNORE NULLS) OVER (
                        PARTITION BY symbol ORDER BY date
                        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                    ) AS last_active_date,
                    preclose AS stored_preclose,
                    volume, money
                FROM gap_filled
            ),
            adj AS (
                SELECT
                    wl.symbol, wl.date,
                    SUM(COALESCE(ex.bonus_ps, 0)
                        - COALESCE(ex.rationed_px, 0) * COALESCE(ex.rationed_ps, 0)
                    ) AS total_deduction,
                    EXP(SUM(LN(
                        1 + COALESCE(ex.allotted_ps, 0) + COALESCE(ex.rationed_ps, 0)
                    ))) AS total_divisor,
                    COUNT(ex.date) AS event_count
                FROM with_lag wl
                INNER JOIN exrights ex ON ex.symbol = wl.symbol
                    AND ex.date > wl.last_active_date AND ex.date <= wl.date
                GROUP BY wl.symbol, wl.date
            )
            SELECT
                wl.symbol,
                wl.date::TIMESTAMP_NS AS date,
                wl.open, wl.close, wl.high, wl.low,
                CASE
                    WHEN adj.event_count > 0 AND wl.lag_close IS NOT NULL
                         AND wl.volume > 0 THEN
                        ROUND(
                            (wl.lag_close - adj.total_deduction)
                            / adj.total_divisor,
                            2)
                    ELSE COALESCE(wl.lag_close, wl.stored_preclose)
                END AS preclose,
                wl.volume, wl.money
            FROM with_lag wl
            LEFT JOIN adj ON adj.symbol = wl.symbol AND adj.date = wl.date
        """)
        t1 = time.time()
        row_count = self.conn.execute("SELECT COUNT(*) FROM _stocks_filled").fetchone()[0]
        logger.info(f"  Gap-fill complete: {row_count} rows in {t1 - t0:.1f}s")

        # Step 2: Write per-symbol parquet files with price limits
        symbols = [r[0] for r in self.conn.execute(
            "SELECT DISTINCT symbol FROM _stocks_filled ORDER BY symbol"
        ).fetchall()]

        if market == "us":
            for symbol in symbols:
                se = symbol.replace("'", "''")
                self.conn.execute(f"""
                    COPY (
                        SELECT date, open, close, high, low,
                            NULL AS high_limit, NULL AS low_limit,
                            preclose, volume, money
                        FROM _stocks_filled WHERE symbol = '{se}' ORDER BY date
                    ) TO '{output_dir / f"{symbol}.parquet"}' (FORMAT PARQUET, CODEC 'ZSTD')
                """)
        else:
            for symbol in symbols:
                se = symbol.replace("'", "''")
                output_file = output_dir / f"{symbol}.parquet"
                code_prefix = symbol[:3]
                is_chinext_star = code_prefix in ("300", "301", "688", "689")

                if is_chinext_star:
                    limit_sql = """
                        CASE WHEN date >= '2020-08-24' THEN ROUND(preclose * 1.20, 2)
                             ELSE ROUND(preclose * 1.10, 2) END AS high_limit,
                        CASE WHEN date >= '2020-08-24' THEN ROUND(preclose * 0.80, 2)
                             ELSE ROUND(preclose * 0.90, 2) END AS low_limit,
                    """
                else:
                    limit_sql = """
                        ROUND(preclose * 1.10, 2) AS high_limit,
                        ROUND(preclose * 0.90, 2) AS low_limit,
                    """

                self.conn.execute(f"""
                    COPY (
                        SELECT date, open, close, high, low,
                            {limit_sql}
                            preclose, volume, money
                        FROM _stocks_filled WHERE symbol = '{se}' ORDER BY date
                    ) TO '{output_file}' (FORMAT PARQUET, CODEC 'ZSTD')
                """)

        self.conn.execute("DROP TABLE IF EXISTS _stocks_filled")
        logger.info(f"Exported {len(symbols)} stocks files in {time.time() - t0:.1f}s")

    def _export_stocks_with_limits(
        self, symbol_escaped: str, output_file: Path, market: str = "cn"
    ) -> None:
        """
        Export stocks data with calculated price limits

        Price limit rules:
        - US stocks: no price limits (NULL)
        - Normal stocks: ±10%
        - ST stocks: ±5%
        - ChiNext (300xxx, 301xxx) / STAR (688xxx, 689xxx): ±20% after 2020-08-24
        """
        # CTE fills suspension days (volume=0, OHLC = last close)
        # and missing preclose with previous day's close.
        # trade_cal: all trading days derived from stocks table
        # raw: actual data rows for this symbol
        # joined: left join ensures every trading day within stock's lifespan has a row
        # gap_filled: forward-fill close into suspension gaps
        # filled: compute preclose from gap_filled close
        base_cte = f"""
            WITH raw AS (
                SELECT date, open, close, high, low, preclose, volume, money
                FROM stocks WHERE symbol = '{symbol_escaped}'
            ),
            lifespan AS (
                SELECT MIN(date) AS first_date, MAX(date) AS last_date FROM raw
            ),
            joined AS (
                SELECT
                    tc.date,
                    -- NULL out OHLC when volume=0 (suspended).
                    -- Prevents ex-rights adjusted prices from replacing
                    -- the last traded price during suspension.
                    CASE WHEN r.volume > 0 THEN r.open END AS open,
                    CASE WHEN r.volume > 0 THEN r.close END AS close,
                    CASE WHEN r.volume > 0 THEN r.high END AS high,
                    CASE WHEN r.volume > 0 THEN r.low END AS low,
                    CASE WHEN r.volume > 0 THEN r.preclose END AS preclose,
                    COALESCE(r.volume, 0) AS volume,
                    COALESCE(r.money, 0.0) AS money
                FROM _trade_cal tc
                CROSS JOIN lifespan ls
                LEFT JOIN raw r ON tc.date = r.date
                WHERE tc.date >= ls.first_date AND tc.date <= ls.last_date
            ),
            gap_filled AS (
                SELECT
                    date,
                    COALESCE(open, last_value(close IGNORE NULLS) OVER w) AS open,
                    COALESCE(close, last_value(close IGNORE NULLS) OVER w) AS close,
                    COALESCE(high, last_value(close IGNORE NULLS) OVER w) AS high,
                    COALESCE(low, last_value(close IGNORE NULLS) OVER w) AS low,
                    preclose,
                    volume,
                    money
                FROM joined
                WINDOW w AS (ORDER BY date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
            ),
            with_lag AS (
                SELECT
                    date, open, close, high, low,
                    LAG(close) OVER (ORDER BY date) AS lag_close,
                    last_value(CASE WHEN volume > 0 THEN date END IGNORE NULLS) OVER (
                        ORDER BY date
                        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                    ) AS last_active_date,
                    preclose AS stored_preclose,
                    volume, money
                FROM gap_filled
            ),
            adj AS (
                SELECT
                    wl.date,
                    SUM(COALESCE(ex.bonus_ps, 0)
                        - COALESCE(ex.rationed_px, 0) * COALESCE(ex.rationed_ps, 0)
                    ) AS total_deduction,
                    EXP(SUM(LN(
                        1 + COALESCE(ex.allotted_ps, 0) + COALESCE(ex.rationed_ps, 0)
                    ))) AS total_divisor,
                    COUNT(ex.date) AS event_count
                FROM with_lag wl
                INNER JOIN exrights ex ON ex.symbol = '{symbol_escaped}'
                    AND ex.date > wl.last_active_date AND ex.date <= wl.date
                GROUP BY wl.date
            ),
            filled AS (
                SELECT
                    wl.date::TIMESTAMP_NS AS date, wl.open, wl.close, wl.high, wl.low,
                    CASE
                        WHEN adj.event_count > 0 AND wl.lag_close IS NOT NULL
                             AND wl.volume > 0 THEN
                            ROUND(
                                (wl.lag_close - adj.total_deduction)
                                / adj.total_divisor,
                                2)
                        ELSE COALESCE(wl.lag_close, wl.stored_preclose)
                    END AS preclose,
                    wl.volume, wl.money
                FROM with_lag wl
                LEFT JOIN adj ON adj.date = wl.date
            )
        """

        if market == "us":
            self.conn.execute(f"""
                COPY (
                    {base_cte}
                    SELECT date, open, close, high, low,
                        NULL AS high_limit, NULL AS low_limit,
                        preclose, volume, money
                    FROM filled ORDER BY date
                ) TO '{output_file}' (FORMAT PARQUET, CODEC 'ZSTD')
            """)
            return
        # Extract numeric code prefix to determine board type
        code_prefix = symbol_escaped[:3]

        # Check if ChiNext or STAR market
        is_chinext_star = code_prefix in ("300", "301", "688", "689")

        if is_chinext_star:
            # ChiNext/STAR: 20% after 2020-08-24, 10% before
            self.conn.execute(f"""
                COPY (
                    {base_cte}
                    SELECT date, open, close, high, low,
                        CASE
                            WHEN date >= '2020-08-24' THEN ROUND(preclose * 1.20, 2)
                            ELSE ROUND(preclose * 1.10, 2)
                        END AS high_limit,
                        CASE
                            WHEN date >= '2020-08-24' THEN ROUND(preclose * 0.80, 2)
                            ELSE ROUND(preclose * 0.90, 2)
                        END AS low_limit,
                        preclose, volume, money
                    FROM filled ORDER BY date
                ) TO '{output_file}' (FORMAT PARQUET, CODEC 'ZSTD')
            """)
        else:
            # Normal stocks: 10% limit (ST handling needs isST from status)
            # For now, use 10% as default; ST detection could be added later
            self.conn.execute(f"""
                COPY (
                    {base_cte}
                    SELECT date, open, close, high, low,
                        ROUND(preclose * 1.10, 2) AS high_limit,
                        ROUND(preclose * 0.90, 2) AS low_limit,
                        preclose, volume, money
                    FROM filled ORDER BY date
                ) TO '{output_file}' (FORMAT PARQUET, CODEC 'ZSTD')
            """)

    def _export_fundamentals_with_ttm(
        self, symbol_escaped: str, output_file: Path
    ) -> None:
        """
        Export fundamentals data with TTM indicators.

        TTM fields (roe_ttm, roa_ttm, net_profit_ratio_ttm, gross_income_ratio_ttm)
        are pre-computed in DB by compute_derived_fundamentals() or download.
        Only net_profit_ratio_ttm and gross_income_ratio_ttm are calculated here
        since they are not stored in the DB.
        """
        self.conn.execute(f"""
            COPY (
                SELECT
                    date::TIMESTAMP_NS AS date, publ_date,
                    operating_revenue_grow_rate, net_profit_grow_rate,
                    basic_eps_yoy, np_parent_company_yoy,
                    net_profit_ratio,
                    AVG(net_profit_ratio) OVER (
                        ORDER BY date ROWS BETWEEN 3 PRECEDING AND CURRENT ROW
                    ) AS net_profit_ratio_ttm,
                    gross_income_ratio,
                    AVG(gross_income_ratio) OVER (
                        ORDER BY date ROWS BETWEEN 3 PRECEDING AND CURRENT ROW
                    ) AS gross_income_ratio_ttm,
                    roa, roa_ttm,
                    roe, roe_ttm,
                    total_asset_grow_rate, total_asset_turnover_rate,
                    current_assets_turnover_rate, inventory_turnover_rate,
                    accounts_receivables_turnover_rate,
                    current_ratio, quick_ratio, debt_equity_ratio,
                    interest_cover, roic, roa_ebit_ttm,
                    total_shares, a_floats,
                    {_FUNDAMENTAL_EXPORT_EXTRA_SQL}
                FROM fundamentals
                WHERE symbol = '{symbol_escaped}'
                ORDER BY date
            ) TO '{output_file}' (FORMAT PARQUET, CODEC 'ZSTD')
        """)

    def _export_valuation_enriched(
        self, symbol_escaped: str, output_file: Path
    ) -> None:
        """
        Export valuation data with enriched fields:
        - total_shares, a_floats: forward filled from fundamentals
        - total_value, float_value: market cap computed as shares * close
        - roe, roa, roe_ttm, roa_ttm: forward filled from fundamentals
        - naps: calculated as close / pb (derived from pbMRQ definition)

        Uses LAST_VALUE with IGNORE NULLS for forward fill.
        """
        self.conn.execute(f"""
            COPY (
                SELECT
                    v.date::TIMESTAMP_NS AS date,
                    v.pe_ttm, v.pb, v.ps_ttm, v.pcf,
                    f.roe, f.roe_ttm, f.roa, f.roa_ttm,
                    CASE WHEN v.pb > 0 THEN ROUND(s.close / v.pb, 4)
                         ELSE NULL END AS naps,
                    f.total_shares, f.a_floats,
                    CASE WHEN f.total_shares > 0 AND s.close IS NOT NULL
                         THEN ROUND(f.total_shares * s.close, 2) END AS total_value,
                    CASE WHEN f.a_floats > 0 AND s.close IS NOT NULL
                         THEN ROUND(f.a_floats * s.close, 2) END AS float_value,
                    v.turnover_rate
                FROM valuation v
                ASOF JOIN stocks s
                    ON v.symbol = s.symbol AND v.date >= s.date
                LEFT JOIN LATERAL (
                    SELECT total_shares, a_floats, roe, roe_ttm, roa, roa_ttm
                    FROM fundamentals f2
                    WHERE f2.symbol = v.symbol AND f2.date <= v.date
                    ORDER BY f2.date DESC LIMIT 1
                ) f ON TRUE
                WHERE v.symbol = '{symbol_escaped}'
                ORDER BY v.date
            ) TO '{output_file}' (FORMAT PARQUET, CODEC 'ZSTD')
        """)

    def _ensure_stock_metadata_from_pool(self) -> None:
        """Populate stock_metadata with accurate listed/de_listed dates.

        Uses MIN(date) from stocks table as listed_date (99.8% match with
        actual IPO dates). Sets de_listed_date to '2900-01-01' for active
        stocks, or MAX(date) for stocks no longer in the mootdx stock list.
        """
        # Check current stock_metadata quality
        current_count = self.conn.execute(
            "SELECT COUNT(*) FROM stock_metadata"
        ).fetchone()[0]
        valid_delisted_count = self.conn.execute(
            "SELECT COUNT(*) FROM stock_metadata "
            "WHERE de_listed_date IS NOT NULL AND de_listed_date != ''"
        ).fetchone()[0]
        pool_count = self.conn.execute(
            "SELECT COUNT(*) FROM stock_pool"
        ).fetchone()[0]

        needs_population = (
            current_count < pool_count or valid_delisted_count < 100
        )

        if not needs_population:
            return

        logger.info(
            f"Populating stock_metadata: {current_count} records, "
            f"{valid_delisted_count} with de_listed_date, pool has {pool_count}"
        )

        # Step 1: Get stock names from mootdx (current active stocks)
        active_stock_names = {}
        try:
            from mootdx.quotes import Quotes

            client = Quotes.factory(market="std", quiet=True)
            sz_prefixes = ("000", "001", "002", "003", "300", "301", "302")
            sh_prefixes = ("600", "601", "603", "605", "688", "689")

            for market in [0, 1]:  # 0=SZ, 1=SH
                try:
                    df = client.stocks(market=market)
                    if df is None or df.empty:
                        continue
                    for _, row in df.iterrows():
                        code = str(row.get("code", "")).strip()
                        name = str(row.get("name", "")).strip()
                        if not code or len(code) != 6:
                            continue
                        if market == 1 and code.startswith(("000", "399", "999")):
                            continue
                        if market == 0 and (
                            code.startswith(
                                ("15", "16", "50", "51", "52", "56", "58", "59")
                            )
                            or code.startswith("39")
                        ):
                            continue
                        if market == 0 and code.startswith(sz_prefixes):
                            active_stock_names[f"{code}.SZ"] = name
                        elif market == 1 and code.startswith(sh_prefixes):
                            active_stock_names[f"{code}.SS"] = name
                except Exception as e:
                    logger.warning(
                        f"Failed to fetch stocks for market {market}: {e}"
                    )
            logger.info(f"Fetched {len(active_stock_names)} stock names from mootdx")
        except Exception as e:
            logger.warning(f"Failed to fetch stock names: {e}")

        # Step 2: Get listed_date from MIN(date) in stocks table
        # This matches actual IPO dates with 99.8% accuracy
        # Filter to A-share codes only (exclude indices, B-shares, etc.)
        stock_dates = {}
        try:
            dates_df = self.conn.execute(
                "SELECT symbol, MIN(date) as listed_date, MAX(date) as last_date "
                "FROM stocks "
                "WHERE (symbol LIKE '000___.SZ' OR symbol LIKE '001___.SZ' "
                "    OR symbol LIKE '002___.SZ' OR symbol LIKE '003___.SZ' "
                "    OR symbol LIKE '300___.SZ' OR symbol LIKE '301___.SZ' "
                "    OR symbol LIKE '302___.SZ' "
                "    OR symbol LIKE '600___.SS' OR symbol LIKE '601___.SS' "
                "    OR symbol LIKE '603___.SS' OR symbol LIKE '605___.SS' "
                "    OR symbol LIKE '688___.SS' OR symbol LIKE '689___.SS') "
                "GROUP BY symbol"
            ).fetchdf()
            for _, row in dates_df.iterrows():
                stock_dates[row["symbol"]] = {
                    "listed_date": str(row["listed_date"]),
                    "last_date": str(row["last_date"]),
                }
            logger.info(
                f"Got listed dates from stocks table for {len(stock_dates)} symbols"
            )
        except Exception as e:
            logger.warning(f"Failed to get dates from stocks table: {e}")

        # Step 3: Determine de_listed_date and build batch
        # Active in mootdx → '2900-01-01'
        # Not active and last_date < latest → last_date (likely delisted)
        active_set = set(active_stock_names.keys())
        max_date_val = self.conn.execute(
            "SELECT MAX(date) FROM stocks"
        ).fetchone()[0]
        latest_date_str = str(max_date_val) if max_date_val is not None else ""

        # Pre-load existing blocks to preserve them
        existing_blocks = {}
        try:
            blocks_df = self.conn.execute(
                "SELECT symbol, blocks FROM stock_metadata WHERE blocks IS NOT NULL"
            ).fetchdf()
            for _, row in blocks_df.iterrows():
                existing_blocks[row["symbol"]] = row["blocks"]
        except Exception:
            pass

        # Build batch rows
        all_symbols = set(stock_dates.keys()) | active_set
        rows = []
        for symbol in all_symbols:
            dates = stock_dates.get(symbol, {})
            listed_date = dates.get("listed_date")
            last_date = dates.get("last_date")

            if symbol in active_set:
                de_listed_date = "2900-01-01"
            elif last_date and last_date < latest_date_str:
                de_listed_date = last_date
            else:
                de_listed_date = "2900-01-01"

            rows.append((
                symbol,
                active_stock_names.get(symbol),
                listed_date,
                de_listed_date,
                existing_blocks.get(symbol),
            ))

        # Batch insert via temp table
        batch_df = pd.DataFrame(
            rows,
            columns=["symbol", "stock_name", "listed_date", "de_listed_date", "blocks"],
        )
        self.conn.register("_stock_metadata_batch", batch_df)
        try:
            self.conn.execute(
                "INSERT OR REPLACE INTO stock_metadata "
                "(symbol, stock_name, listed_date, de_listed_date, blocks) "
                "SELECT symbol, stock_name, listed_date, de_listed_date, blocks "
                "FROM _stock_metadata_batch"
            )
        finally:
            self.conn.unregister("_stock_metadata_batch")

        logger.info(
            f"stock_metadata population complete: {len(rows)} records, "
            f"{len(active_set)} active stocks"
        )

    def _enrich_halt_status_from_volume(self) -> None:
        """Enrich stock_status HALT entries using volume=0 from stocks table.

        For each trading date, marks A-share stocks with volume=0 (within
        their lifespan) as HALT. Merges with any existing BaoStock-sourced
        HALT data already in the stock_status table.
        """
        import time
        t0 = time.time()

        # Use a temp table to avoid complex INSERT OR REPLACE with aggregation
        self.conn.execute(f"""
            CREATE OR REPLACE TEMP TABLE _halt_enriched AS
            WITH lifespans AS (
                SELECT s0.symbol, MIN(s0.date) AS first_date, MAX(s0.date) AS last_date
                FROM stocks s0
                WHERE {self._cn_stock_filter_sql("s0.symbol")}
                GROUP BY s0.symbol
            ),
            trade_dates AS (
                SELECT DISTINCT date FROM stocks
            ),
            vol_halted AS (
                SELECT td.date, ls.symbol
                FROM trade_dates td
                CROSS JOIN lifespans ls
                LEFT JOIN stocks s ON s.symbol = ls.symbol AND s.date = td.date
                WHERE td.date >= ls.first_date AND td.date <= ls.last_date
                  AND (s.volume IS NULL OR s.volume = 0)
            ),
            existing_halt AS (
                SELECT STRPTIME(date, '%Y%m%d')::DATE AS date,
                       unnest(symbols::JSON::VARCHAR[]) AS symbol
                FROM stock_status
                WHERE status_type = 'HALT'
            ),
            combined AS (
                SELECT date, symbol FROM vol_halted
                UNION
                SELECT date, symbol FROM existing_halt
            )
            SELECT
                STRFTIME(date, '%Y%m%d') AS date,
                'HALT' AS status_type,
                to_json(list(symbol ORDER BY symbol)) AS symbols
            FROM combined
            GROUP BY date
        """)

        # Replace existing HALT entries atomically
        self.begin()
        try:
            self.conn.execute(
                "DELETE FROM stock_status WHERE status_type = 'HALT'"
            )
            self.conn.execute("""
                INSERT INTO stock_status (date, status_type, symbols)
                SELECT date, status_type, symbols FROM _halt_enriched
            """)
            self.commit()
        except Exception:
            self.rollback()
            raise
        self.conn.execute("DROP TABLE IF EXISTS _halt_enriched")

        halt_count = self.conn.execute(
            "SELECT COUNT(*) FROM stock_status WHERE status_type = 'HALT'"
        ).fetchone()[0]
        logger.info(
            f"Enriched HALT status from volume data: "
            f"{halt_count} date entries in {time.time() - t0:.1f}s"
        )

    def _export_metadata(self, output_dir: Path, market: str = "cn") -> None:
        """Export metadata tables using DuckDB COPY"""

        # Before exporting stock_metadata, ensure it's populated from stock_pool
        # This ensures all A-shares are included, not just those with downloaded data
        self._ensure_stock_metadata_from_pool()

        # stock_metadata.parquet
        count = self.conn.execute("SELECT COUNT(*) FROM stock_metadata").fetchone()[0]
        if count > 0:
            self.conn.execute(f"""
                COPY stock_metadata TO '{output_dir / "stock_metadata.parquet"}'
                (FORMAT PARQUET, CODEC 'ZSTD')
            """)

        # benchmark.parquet — prefer stocks table for full history,
        # fall back to benchmark table for recent data only
        benchmark_symbol = BENCHMARK_CONFIG["default_index"]
        has_stocks_benchmark = self.conn.execute(
            f"SELECT COUNT(*) FROM stocks WHERE symbol = '{benchmark_symbol}'"
        ).fetchone()[0]

        if has_stocks_benchmark > 0:
            self.conn.execute(f"""
                COPY (
                    SELECT date, open, high, low, close, volume,
                           COALESCE(money, 0.0) AS money
                    FROM stocks
                    WHERE symbol = '{benchmark_symbol}'
                    ORDER BY date
                ) TO '{output_dir / "benchmark.parquet"}' (FORMAT PARQUET, CODEC 'ZSTD')
            """)
        else:
            count = self.conn.execute(
                "SELECT COUNT(*) FROM benchmark"
            ).fetchone()[0]
            if count > 0:
                self.conn.execute(f"""
                    COPY (SELECT * FROM benchmark ORDER BY date)
                    TO '{output_dir / "benchmark.parquet"}'
                    (FORMAT PARQUET, CODEC 'ZSTD')
                """)

        # Fail closed when the CN benchmark export is missing or truncated.
        # TDX index bars are depth-limited, so a short history here means the
        # released package would silently drop early benchmark dates.
        if market == "cn":
            benchmark_path = output_dir / "benchmark.parquet"
            if not benchmark_path.exists():
                raise ValueError("benchmark export missing for CN market")
            # Query the export source rather than re-reading the written file
            if has_stocks_benchmark > 0:
                min_date = self.conn.execute(
                    f"SELECT MIN(date)::VARCHAR FROM stocks "
                    f"WHERE symbol = '{benchmark_symbol}'"
                ).fetchone()[0]
            else:
                min_date = self.conn.execute(
                    "SELECT MIN(date)::VARCHAR FROM benchmark"
                ).fetchone()[0]
            if not benchmark_history_ok(min_date):
                raise ValueError(
                    f"benchmark export truncated: earliest date {min_date}, "
                    f"required <= {BENCHMARK_HISTORY_FLOOR}"
                )

        # trade_days.parquet — merge DB trade_days with dates from stocks table
        # The trade_days table may only have recent dates (from mootdx),
        # but stocks table has full history back to 1991.
        self.conn.execute(f"""
            COPY (
                SELECT DISTINCT date FROM (
                    SELECT date FROM trade_days
                    UNION
                    SELECT DISTINCT date FROM stocks
                ) ORDER BY date
            ) TO '{output_dir / "trade_days.parquet"}' (FORMAT PARQUET, CODEC 'ZSTD')
        """)

        # index_constituents.parquet
        count = self.conn.execute("SELECT COUNT(*) FROM index_constituents").fetchone()[
            0
        ]
        if count > 0:
            self.conn.execute(f"""
                COPY (
                    SELECT date, index_code, symbols::JSON::VARCHAR[] AS symbols
                    FROM index_constituents
                ) TO '{output_dir / "index_constituents.parquet"}'
                (FORMAT PARQUET, CODEC 'ZSTD')
            """)

        # stock_status.parquet — enrich HALT data from volume before export
        self._enrich_halt_status_from_volume()
        count = self.conn.execute("SELECT COUNT(*) FROM stock_status").fetchone()[0]
        if count > 0:
            self.conn.execute(f"""
                COPY (
                    SELECT date, status_type, symbols::JSON::VARCHAR[] AS symbols
                    FROM stock_status
                ) TO '{output_dir / "stock_status.parquet"}'
                (FORMAT PARQUET, CODEC 'ZSTD')
            """)

        # version.parquet
        cn_filter = (
            f"WHERE {self._cn_stock_filter_sql('s0.symbol')}"
            if market == "cn" else ""
        )
        result = self.conn.execute(f"""
            SELECT
                (SELECT MAX(date)::VARCHAR FROM stocks) as version,
                (SELECT COUNT(DISTINCT s0.symbol) FROM stocks s0 {cn_filter}) as num_stocks,
                CURRENT_DATE as export_date,
                (SELECT MIN(date)::VARCHAR FROM stocks) as start_date
        """).fetchone()

        version_data = pd.DataFrame(
            [
                {
                    "version": result[0] or "",
                    "num_stocks": result[1] or 0,
                    "export_date": str(result[2]),
                    "start_date": result[3] or "",
                }
            ]
        )
        version_data.to_parquet(output_dir / "version.parquet", index=False)


    def _write_manifest(self, output_dir: Path, market: str = "cn") -> None:
        """Write manifest.json"""
        cn_filter = (
            f"WHERE {self._cn_stock_filter_sql('s0.symbol')}"
            if market == "cn" else ""
        )
        result = self.conn.execute(f"""
            SELECT MIN(s0.date), MAX(s0.date), COUNT(DISTINCT s0.symbol)
            FROM stocks s0 {cn_filter}
        """).fetchone()

        start_date = str(result[0]) if result[0] else ""
        end_date = str(result[1]) if result[1] else ""
        stock_count = result[2] or 0

        manifest = {
            "package_format": "simtrade-data-market-v1",
            "mode": "full",
            "version": end_date,
            "market": market.upper(),
            "date_range": {
                "start": start_date,
                "end": end_date,
            },
            "description": f"SimTradeData export ({stock_count} stocks)",
            "export_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        with open(output_dir / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _write_delta_manifest(
        self,
        output_dir: Path,
        base_version: str,
        target_version: str,
        market: str,
        changed_tables: list[dict],
        changed_symbols: list[str],
        files: list[dict],
    ) -> None:
        manifest = {
            "package_format": "simtradedata_delta_v1",
            "schema_version": 1,
            "market": market.upper(),
            "mode": "delta",
            "base_version": base_version,
            "target_version": target_version,
            "version": target_version,
            "date_range": {"start_exclusive": base_version, "end": target_version},
            "changed_tables": changed_tables,
            "changed_symbols": changed_symbols,
            "files": files,
            "export_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(output_dir / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
