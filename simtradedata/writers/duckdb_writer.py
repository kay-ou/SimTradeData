# -*- coding: utf-8 -*-
"""
DuckDB writer for SimTradeData

This module provides incremental data storage using DuckDB,
with automatic upsert (INSERT OR REPLACE) for deduplication.
"""

import hashlib
import json
import logging
import math
import os
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
from simtradedata.config.mootdx_finvalue_map import FULL_FUNDAMENTAL_FIELDS
from simtradedata.utils.paths import DUCKDB_PATH, safe_rmtree
from simtradedata.validators.data_validator import DataQualityError, validate_before_write
from simtradedata.web.daily_categories import ETF_SYMBOL_PATTERN, NON_TRADING_ETF_CODES, is_exchange_etf

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

# Keep parser, schema migration, writes and exports aligned for newly mapped fields.
_ADDITIONAL_MAPPED_COLUMNS = [
    field for field in FULL_FUNDAMENTAL_FIELDS
    if field not in _FUNDAMENTAL_WRITE_COLUMNS
]
_FUNDAMENTAL_EXTRA_COLUMNS.extend(_ADDITIONAL_MAPPED_COLUMNS)
_FUNDAMENTAL_WRITE_COLUMNS.extend(_ADDITIONAL_MAPPED_COLUMNS)

_FUNDAMENTAL_EXPORT_EXTRA_SQL = ",\n                ".join(_FUNDAMENTAL_EXTRA_COLUMNS)

# Valuation export column list. Daily values (v.*) win over quarterly
# fundamentals enrichment (f.*) when present; fall back to the computed
# value otherwise.
#
# a_floats：客户侧 2026-10 起直接提供平台流通股本（redis a_floats），优先用它；
# 旧部署没有该列数据时退回「float_value ÷ 当日收盘」派生（引擎用 close × a_floats
# 现算流通市值，join 的 s.close 与引擎读的市场数据同源，除法与乘法逐行相消）。
# a_shares：客户侧已停供，导出置 NULL 对齐平台行为（get_fundamentals('a_shares')
# 返回 NaN），旧口径冻结值会误导策略。
#
# SIMTRADE_CN_VALUATION_REDIS_ONLY=1（客户云部署开关）：
# redis 平台直供的估值字段只信 redis——缺口置 NULL，不再用自采 f 数据
# 现算/兜底。自采值与平台口径不一致（如流通市值），混入会让回测选股
# 偏离 ptrade 平台。默认关闭，开源部署保持原有 fallback 行为。
_TOTAL_SHARES_SQL = "COALESCE(v.total_shares, f.total_shares)"


def _valuation_enrich_select() -> str:
    """估值导出列清单。

    redis_only 开关开启时（客户云部署，与 SIMTRADE_REDIS_AUTHORITATIVE
    同类但独立控制导出口径），平台直供字段只信 redis，缺口置 NULL。
    默认关闭：开源部署保持自采 f 数据兜底。
    """
    redis_only = os.environ.get("SIMTRADE_CN_VALUATION_REDIS_ONLY", "0") == "1"

    def pick(value: str, fallback: str) -> str:
        return value if redis_only else f"COALESCE({value}, {fallback})"

    return f"""
    v.pe_ttm, v.pb, v.ps_ttm, v.pcf,
    {pick("v.roe", "f.roe")} AS roe, f.roe_ttm,
    {pick("v.roa", "f.roa")} AS roa, f.roa_ttm,
    {pick("v.naps", "CASE WHEN v.pb > 0 THEN ROUND(s.close / v.pb, 4) ELSE NULL END")} AS naps,
    {_TOTAL_SHARES_SQL} AS total_shares,
    {pick("v.a_floats", "v.float_value / s.close")} AS a_floats,
    NULL::DOUBLE AS a_shares,
    CASE WHEN {_TOTAL_SHARES_SQL} > 0 AND s.close IS NOT NULL
         THEN ROUND({_TOTAL_SHARES_SQL} * s.close, 2) END AS total_value,
    {pick("v.float_value", "CASE WHEN f.a_floats > 0 AND s.close IS NOT NULL "
                            "THEN ROUND(f.a_floats * s.close, 2) END")} AS float_value,
    v.turnover_rate
"""


def _fill_exer_factors(df) -> None:
    """Fill exer_forward_a/b columns on an exrights frame.

    官方因子（客户 redis 同步落库）优先，全行非空即透传；缺列或任一行为
    NULL 时按 ps 字段自算兜底。因子是累积序列，不能按行混源，所以
    整只股票要么全官方、要么全自算。
    """
    if (
        "exer_forward_a" in df.columns
        and "exer_forward_b" in df.columns
        and df["exer_forward_a"].notna().all()
        and df["exer_forward_b"].notna().all()
    ):
        return
    import numpy as np

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


def _fill_dividend_from_bonus_ps(df) -> None:
    """Backfill cash dividend from bonus_ps when the source omits it.

    部分权威数据源只提供 bonus_ps（税前每股现金分红），不含 dividend；
    SimTradeLab 的分红缓存读 dividend，导出前用 bonus_ps 补齐。
    已有 dividend 的行保持原值（现有数据上两列完全一致）。
    """
    if "dividend" in df.columns and "bonus_ps" in df.columns:
        df["dividend"] = df["dividend"].fillna(df["bonus_ps"])


# Legacy code-pattern fallback for databases created before
# stock_metadata.security_type was populated from BaoStock query_stock_basic().
# New exports should use cn_stock_filter_sql(), which prefers BaoStock's
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


def cn_stock_filter_sql(symbol_expr: str = "symbol") -> str:
    """Prefer each symbol's official classification; fall back only if unknown."""
    fallback = _CN_STOCK_FILTER.replace("symbol", symbol_expr)
    return f"""(
        EXISTS (SELECT 1 FROM stock_metadata sm WHERE sm.symbol={symbol_expr}
                AND sm.security_type='1' AND ({symbol_expr} LIKE '%.SZ' OR {symbol_expr} LIKE '%.SS'))
        OR (NOT EXISTS (SELECT 1 FROM stock_metadata sm WHERE sm.symbol={symbol_expr}
                        AND sm.security_type IS NOT NULL AND sm.security_type!='')
            AND {fallback})
    )"""


class DuckDBWriter:
    """
    Writer for DuckDB incremental storage

    Features:
    - Automatic upsert via INSERT OR REPLACE (uses PRIMARY KEY)
    - Incremental updates: query MAX(date) to determine start_date
    - Export to PTrade Parquet format
    """

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
                exer_forward_a DOUBLE,
                exer_forward_b DOUBLE,
                exer_backward_a DOUBLE,
                exer_backward_b DOUBLE,
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
                a_shares DOUBLE,
                float_value DOUBLE,
                turnover_rate DOUBLE,
                PRIMARY KEY (symbol, date)
            )
        """)
        self._migrate_valuation_columns()
        self._migrate_exrights_columns()

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

        change_index = self.conn.execute("SELECT expressions FROM duckdb_indexes() WHERE index_name='idx_data_change_log_table_date'").fetchone()
        if change_index and "changed_at" in change_index[0]:
            self.conn.execute("DROP INDEX idx_data_change_log_table_date")
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_data_change_log_table_date
            ON data_change_log (table_name, date)
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

    def _migrate_valuation_columns(self) -> None:
        """Add daily valuation columns (a_shares, float_value) when upgrading DBs."""
        columns = self.conn.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'valuation'
        """).fetchall()
        column_names = {row[0] for row in columns}

        for column in ("a_shares", "float_value"):
            if column not in column_names:
                self.conn.execute(f"""
                    ALTER TABLE valuation ADD COLUMN {column} DOUBLE
                """)
                logger.info("Added %s column to valuation", column)

    def _migrate_exrights_columns(self) -> None:
        """Add official ex-rights factor columns when upgrading DBs.

        Filled by the customer index-sync (redis 权威源); NULL elsewhere and
        the export falls back to computing factors from the ps columns.
        """
        columns = self.conn.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'exrights'
        """).fetchall()
        column_names = {row[0] for row in columns}

        for column in ("exer_forward_a", "exer_forward_b",
                       "exer_backward_a", "exer_backward_b"):
            if column not in column_names:
                self.conn.execute(f"""
                    ALTER TABLE exrights ADD COLUMN {column} DOUBLE
                """)
                logger.info("Added %s column to exrights", column)

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

        for column in ("roa_derived", "roa_ttm_derived"):
            if column not in column_names:
                self.conn.execute(f"ALTER TABLE fundamentals ADD COLUMN {column} BOOLEAN")

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
                INSERT INTO data_change_log (table_name, symbol, date, changed_at)
                SELECT table_name, symbol, date, CURRENT_TIMESTAMP FROM _data_change_rows
                ON CONFLICT (table_name, symbol, date) DO UPDATE SET changed_at = EXCLUDED.changed_at
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
                INSERT INTO data_change_log (table_name, symbol, date, changed_at)
                SELECT table_name, symbol, date, CURRENT_TIMESTAMP FROM _metadata_change_rows
                ON CONFLICT (table_name, symbol, date) DO UPDATE SET changed_at = EXCLUDED.changed_at
            """)
        finally:
            self.conn.unregister("_metadata_change_rows")

    # ========================================
    # Core write methods (with upsert)
    # ========================================

    def _upsert_symbol_rows(self, table: str, df: pd.DataFrame) -> None:
        """Update only supplied columns, preserving fields owned by other sources."""
        self._check_table(table)
        cols_str = ", ".join(df.columns)
        update_cols = [c for c in df.columns if c not in ("symbol", "date")]
        if update_cols:
            set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
            conflict_clause = f"ON CONFLICT (symbol, date) DO UPDATE SET {set_clause}"
        else:
            conflict_clause = "ON CONFLICT (symbol, date) DO NOTHING"
        self.conn.execute(f"""
            INSERT INTO {table} ({cols_str})
            SELECT {cols_str} FROM df
            {conflict_clause}
        """)
        if table == "fundamentals" and "roa" in df.columns and "roa_ttm" not in df.columns:
            self.conn.execute("""UPDATE fundamentals SET roa_ttm=NULL, roa_ttm_derived=TRUE
                WHERE symbol IN (SELECT DISTINCT symbol FROM df)
                  AND roa_ttm_derived IS DISTINCT FROM FALSE""")
        self._record_symbol_changes(table, df)

    def write_market_data(
        self, symbol: str, df: pd.DataFrame, insert_only: bool = False
    ) -> int:
        """Write market data with automatic upsert.

        ``insert_only`` skips rows whose (symbol, date) already exists and
        returns the number of rows actually inserted. TDX bulk imports use
        this so a nightly full-history scan neither rewrites every existing
        row nor floods ``data_change_log`` with unchanged symbol/date pairs.
        """
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
        if insert_only:
            inserted = self.conn.execute(f"""
                INSERT INTO stocks ({cols_str})
                SELECT {cols_str} FROM df
                ON CONFLICT DO NOTHING
                RETURNING symbol, date
            """).fetchdf()
            if not inserted.empty:
                self._record_symbol_changes("stocks", inserted)
            written = len(inserted)
        else:
            self.conn.execute(f"""
                INSERT OR REPLACE INTO stocks ({cols_str})
                SELECT {cols_str} FROM df
            """)
            self._record_symbol_changes("stocks", df)
            written = len(df)

        logger.debug(f"Wrote {written} market rows for {symbol}")
        return written

    def insert_market_batch(self, frame: pd.DataFrame) -> int:
        """Insert validated per-session rows once, preserving existing records."""
        if frame.empty:
            return 0
        columns = ['symbol','date','open','close','high','low','preclose','volume','money']
        frame = frame[columns].copy()
        frame['date'] = pd.to_datetime(frame['date']).dt.date
        if frame.duplicated(['symbol','date']).any():
            raise ValueError('Duplicate daily package rows')
        self.conn.register('_daily_batch', frame)
        try:
            fields = ','.join(columns)
            inserted = self.conn.execute(f'INSERT INTO stocks ({fields}) SELECT {fields} FROM _daily_batch ON CONFLICT DO NOTHING RETURNING symbol,date').fetchdf()
            self._record_symbol_changes('stocks', inserted)
            return len(inserted)
        finally:
            self.conn.unregister('_daily_batch')

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
            "a_shares",
            "float_value",
            "turnover_rate",
        ]
        available = [c for c in columns if c in df.columns]
        df = df[available]

        # Only overwrite the columns this source provides; daily columns
        # (roe, roa, naps, total_shares, float_value) must survive rows
        # written by sources that do not carry them.
        self._upsert_symbol_rows("valuation", df)

        logger.debug(f"Wrote {len(df)} valuation rows for {symbol}")
        return len(df)

    def write_fundamentals(self, symbol: str, df: pd.DataFrame) -> int:
        """Write quarterly fundamentals with upsert"""
        if df.empty:
            return 0
        df = self._prepare_fundamentals(symbol, df)
        self._upsert_symbol_rows("fundamentals", df)
        logger.debug(f"Wrote {len(df)} fundamental rows for {symbol}")
        return len(df)

    def write_fundamentals_batch(self, frames: dict) -> int:
        """Validate each company, then upsert together by supplied column set."""
        batches = {}
        for symbol, frame in frames.items():
            if frame.empty:
                continue
            prepared = self._prepare_fundamentals(symbol, frame)
            batches.setdefault(tuple(prepared.columns), []).append(prepared)
        written = 0
        for frames in batches.values():
            batch = pd.concat(frames, ignore_index=True)
            self._upsert_symbol_rows("fundamentals", batch)
            written += len(batch)
        return written

    def _prepare_fundamentals(self, symbol: str, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["symbol"] = symbol

        if isinstance(df.index, pd.DatetimeIndex):
            df = df.reset_index()
            if "index" in df.columns:
                df = df.rename(columns={"index": "date"})

        if "end_date" in df.columns and "date" not in df.columns:
            df = df.rename(columns={"end_date": "date"})

        df["date"] = pd.to_datetime(df["date"]).dt.date

        if "publ_date" in df.columns:
            publ_dt = pd.to_datetime(df["publ_date"], errors="coerce")
            df["publ_date"] = publ_dt.dt.strftime("%Y%m%d").where(
                publ_dt.notna(), None
            )

        columns = _FUNDAMENTAL_WRITE_COLUMNS
        available = [c for c in columns if c in df.columns]
        df = df[available]
        for field in ("roa", "roa_ttm"):
            if field in df.columns:
                df[field + "_derived"] = False

        if len(df) > 1:
            df = df.drop_duplicates()
        if len(df) > 1 and df["date"].duplicated().any():
            duplicate_rows = df[df["date"].duplicated(keep=False)]
            if "publ_date" not in df.columns or duplicate_rows["publ_date"].isna().any():
                raise DataQualityError(f"{symbol}: Conflicting quarters without announcement dates")
            if duplicate_rows.duplicated(subset=["date", "publ_date"]).any():
                raise DataQualityError(f"{symbol}: Conflicting quarters with the same announcement date")
            df = df.sort_values("publ_date").drop_duplicates(subset=["date"], keep="last")
        self._validate_or_warn(df, "fundamental", symbol)
        return df

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

        # PTrade exrights 契约：事件局部字段必须齐全且为每股口径。
        # 缺失列补 0；dividend 是 bonus_ps 的兼容别名，缺省时回填。
        for col in ("allotted_ps", "rationed_ps", "rationed_px", "bonus_ps"):
            if col not in df.columns:
                df[col] = 0.0
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        if "dividend" not in df.columns:
            df["dividend"] = df["bonus_ps"]
        df["dividend"] = pd.to_numeric(df["dividend"], errors="coerce").fillna(
            df["bonus_ps"]
        )

        columns = [
            "symbol",
            "date",
            "allotted_ps",
            "rationed_ps",
            "rationed_px",
            "bonus_ps",
            "dividend",
        ]
        df = df[columns]

        # 官方复权列由权威数据源维护：同日期只更新事件局部列，绝不能整行替换，
        # 否则会把 exer_forward/backward 官方列冲成 NULL。
        self._upsert_symbol_rows("exrights", df)

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

    def _restore_legacy_roa(self, symbol_filter: str) -> None:
        """Resolve suspicious untracked old-formula values from local source files."""
        suspects = self.conn.execute(f"""
            SELECT symbol, date FROM fundamentals
            WHERE roa_derived IS NULL AND roa IS NOT NULL
              AND roe IS NOT NULL AND debt_equity_ratio IS NOT NULL
              AND debt_equity_ratio NOT IN (-100, -1, 0)
              AND isfinite(roa) AND isfinite(roe) AND isfinite(debt_equity_ratio)
              AND abs(roa - roe / (1 + debt_equity_ratio)) <= 1e-10 * greatest(1, abs(roa))
              AND abs(roa - roe / (1 + debt_equity_ratio / 100)) > 1e-10
              {symbol_filter}
        """).fetchall()
        if not suspects:
            return
        from simtradedata.fetchers.mootdx_affair_fetcher import MootdxAffairFetcher
        directory = self.db_path.parent / "mootdx_financial"
        reports, restored = {}, []
        for symbol, day in suspects:
            filename = f"gpcw{day.strftime('%Y%m%d')}.zip"
            if filename not in reports:
                if not (directory / filename).is_file():
                    raise ValueError(f"旧 ROA 来源无法确认，缺少 {filename}；请重新导入季度财务数据后再导出")
                fetcher = MootdxAffairFetcher(download_dir=str(directory))
                report = fetcher._convert_to_ptrade_format(fetcher.parse_local(filename), fields=["roa"])
                if not {"code", "end_date", "roa"}.issubset(report.columns):
                    raise ValueError(f"{filename} 缺少源 ROA；请重新导入季度财务数据后再导出")
                report = report[["code", "end_date", "roa"]].copy()
                report["code"] = report["code"].astype(str).str.zfill(6)
                report["end_date"] = pd.to_datetime(report["end_date"]).dt.date
                reports[filename] = report.set_index(["code", "end_date"])["roa"]
            value = reports[filename].get((symbol[:6], day))
            if isinstance(value, pd.Series) or pd.isna(value) or not math.isfinite(float(value)):
                raise ValueError(f"{symbol} {day} 缺少唯一有效的源 ROA；请重新导入季度财务数据后再导出")
            restored.append({"symbol": symbol, "date": day, "roa": float(value)})
        # Validate all source evidence before changing any stored values.
        frame = pd.DataFrame(restored)
        self.conn.register("_restored_roa", frame)
        try:
            self.conn.execute("""UPDATE fundamentals f SET roa=r.roa, roa_derived=FALSE
                FROM _restored_roa r WHERE f.symbol=r.symbol AND f.date=r.date""")
            self.conn.execute("""UPDATE fundamentals SET roa_ttm=NULL, roa_ttm_derived=TRUE
                WHERE symbol IN (SELECT symbol FROM _restored_roa)
                  AND roa_ttm_derived IS DISTINCT FROM FALSE""")
            self._record_symbol_changes("fundamentals", frame)
        finally:
            self.conn.unregister("_restored_roa")

    def compute_derived_fundamentals(self, symbols: list[str] | None = None) -> None:
        """
        Fill roa, roe_ttm, roa_ttm from existing fundamentals data.

        - roa = roe / (1 + debt_equity_ratio / 100), with debt/equity in percent
        - roe_ttm = rolling 4-quarter average of roe
        - roa_ttm = rolling 4-quarter average of roa

        When symbols is provided, only compute for those symbols (used by
        delta exports to avoid full-table window function recomputation).
        """
        symbol_filter = ""
        sub_where = "WHERE roe IS NOT NULL OR roa IS NOT NULL OR roa_ttm_derived=TRUE"
        if symbols:
            symbols_clause = self._symbols_in_clause(symbols)
            symbol_filter = f"AND symbol IN ({symbols_clause})"
            sub_where = f"WHERE fundamentals.symbol IN ({symbols_clause}) AND (roe IS NOT NULL OR roa IS NOT NULL OR roa_ttm_derived=TRUE)"

        self._restore_legacy_roa(symbol_filter)

        # Convert percent debt/equity into a ratio before deriving missing ROA.
        self.conn.execute(f"""
            UPDATE fundamentals
            SET roa = CASE WHEN roe IS NOT NULL AND debt_equity_ratio IS NOT NULL
                                AND isfinite(roe) AND isfinite(debt_equity_ratio)
                                AND debt_equity_ratio != -100
                           THEN roe / (1 + debt_equity_ratio / 100) END,
                roa_derived = TRUE
            WHERE ((roa IS NULL AND roe IS NOT NULL AND debt_equity_ratio IS NOT NULL
                    AND debt_equity_ratio != -100) OR roa_derived = TRUE)
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
                roe_ttm = sub.roe_ttm
            FROM (
                SELECT
                    symbol, date,
                    AVG(roe) OVER w AS roe_ttm
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

        self.conn.execute(f"""
            UPDATE fundamentals f SET roa_ttm=sub.roa_ttm, roa_ttm_derived=TRUE
            FROM (
                SELECT symbol, date, AVG(roa) OVER (
                    PARTITION BY symbol ORDER BY date ROWS BETWEEN 3 PRECEDING AND CURRENT ROW
                ) AS roa_ttm FROM fundamentals {sub_where}
            ) sub
            WHERE f.symbol=sub.symbol AND f.date=sub.date
              AND (f.roa_ttm IS NULL OR f.roa_ttm_derived=TRUE)
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

    def export_to_parquet(self, output_dir: str, market: str = "cn", progress_callback=None) -> None:
        """Export all data to PTrade Parquet format"""
        output_path = Path(output_dir)

        # Guardrail: cap DuckDB memory so oversized queries spill to disk
        # instead of exhausting the host (the 7GB customer box crashed twice
        # on the old all-at-once gap-fill materialization).
        memory_limit = os.environ.get("SIMTRADE_DUCKDB_MEMORY_LIMIT", "2.5GB")
        self.conn.execute(f"SET memory_limit='{memory_limit}'")
        temp_dir = self.db_path.parent / "tmp_duckdb"
        temp_dir.mkdir(parents=True, exist_ok=True)
        self.conn.execute(f"SET temp_directory='{temp_dir}'")

        try:
            # Compute derived fields before export
            self.compute_derived_fundamentals()

            # Clean output directory to avoid mixing data from different markets
            if output_path.exists():
                safe_rmtree(output_path)

            for subdir in ["stocks", "exrights", "fundamentals", "valuation", "metadata"]:
                (output_path / subdir).mkdir(parents=True, exist_ok=True)

            logger.info("Exporting stocks...")
            if progress_callback:
                self._export_stocks_batch(output_path / "stocks", market=market,
                    progress_callback=lambda done, total: progress_callback('stocks', done / max(total, 1)))
            else:
                self._export_stocks_batch(output_path / "stocks", market=market)
            if progress_callback:
                progress_callback('stocks', 1)

            logger.info("Exporting exrights...")
            self._export_exrights_batch(output_path / "exrights", market=market)
            if progress_callback:
                progress_callback('exrights', 2)

            logger.info("Exporting fundamentals...")
            self._export_fundamentals_batch(output_path / "fundamentals", market=market)
            if progress_callback:
                progress_callback('fundamentals', 3)

            logger.info("Exporting valuation...")
            self._export_valuation_batch(output_path / "valuation", market=market)
            if progress_callback:
                progress_callback('valuation', 4)

            logger.info("Exporting metadata...")
            self._export_metadata(output_path / "metadata", market=market)
            if progress_callback:
                progress_callback('metadata', 5)

            self._write_manifest(output_path, market=market)

            logger.info(f"Export complete: {output_path}")
        finally:
            self.conn.execute("RESET memory_limit")
            # temp_directory cannot be reset once spill files exist
            # (DuckDB raises NotImplementedException); it dies with the
            # connection anyway, so leave it.

    def export_delta(
        self,
        output_dir: str,
        base_version: str,
        target_version: str | None = None,
        market: str = "cn",
    ) -> None:
        """Export changed rows for client-side delta merge."""
        output_path = Path(output_dir)
        target_version = target_version or self.get_max_date("stocks")
        if not target_version:
            raise ValueError("target_version is required when stocks table is empty")
        if target_version <= base_version:
            raise ValueError(
                f"target_version ({target_version}) must be newer than "
                f"base_version ({base_version})"
            )

        self._restore_legacy_roa("")

        if output_path.exists():
            safe_rmtree(output_path)

        output_path.mkdir(parents=True, exist_ok=True)

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
        """Export stocks and exchange ETFs through the shared security layout."""
        excluded = ','.join(f"'{symbol}'" for symbol in NON_TRADING_ETF_CODES)
        return f"({cn_stock_filter_sql(symbol_expr)} OR (regexp_full_match({symbol_expr}, '{ETF_SYMBOL_PATTERN}') AND {symbol_expr} NOT IN ({excluded})))"

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

        if market == 'cn':
            high_value = high_limit_sql.strip().removesuffix('AS high_limit')
            low_value = low_limit_sql.strip().removesuffix('AS low_limit')
            high_limit_sql = f"CASE WHEN regexp_full_match(symbol, '{ETF_SYMBOL_PATTERN}') THEN (SELECT s.high_limit FROM stocks s WHERE s.symbol=filled.symbol AND s.date=filled.date) ELSE COALESCE((SELECT s.high_limit FROM stocks s WHERE s.symbol=filled.symbol AND s.date=filled.date), {high_value}) END AS high_limit"
            low_limit_sql = f"CASE WHEN regexp_full_match(symbol, '{ETF_SYMBOL_PATTERN}') THEN (SELECT s.low_limit FROM stocks s WHERE s.symbol=filled.symbol AND s.date=filled.date) ELSE COALESCE((SELECT s.low_limit FROM stocks s WHERE s.symbol=filled.symbol AND s.date=filled.date), {low_value}) END AS low_limit"

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
                    CASE WHEN s.symbol IS NULL THEN 0.0 ELSE s.money END AS money
                FROM lifespans ls
                CROSS JOIN trade_cal tc
                LEFT JOIN stocks s ON s.symbol = ls.symbol AND s.date = tc.date
                WHERE tc.date >= ls.first_date AND tc.date <= ls.last_date
                  AND (s.symbol IS NOT NULL OR EXISTS (
                      SELECT 1 FROM stock_status h WHERE h.status_type='HALT'
                      AND coalesce(TRY_CAST(h.date AS DATE),try_strptime(h.date,'%Y%m%d')::DATE)=tc.date
                      AND json_contains(TRY_CAST(h.symbols AS JSON),to_json(ls.symbol))))
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
                        WHEN wl.stored_preclose > 0 AND isfinite(wl.stored_preclose) THEN wl.stored_preclose
                        WHEN adj.event_count > 0 AND wl.lag_close IS NOT NULL
                             AND wl.volume > 0 THEN
                            ROUND(
                                (wl.lag_close - adj.total_deduction)
                                / adj.total_divisor,
                                CASE WHEN regexp_full_match(wl.symbol, '{ETF_SYMBOL_PATTERN}') THEN 3 ELSE 2 END)
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
                {_valuation_enrich_select()}
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
                   allotted_ps, rationed_ps, rationed_px, bonus_ps, dividend,
                   exer_forward_a, exer_forward_b, exer_backward_a, exer_backward_b
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
            _fill_exer_factors(group)
            _fill_dividend_from_bonus_ps(group)
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

            status_rows = self._copy_delta_json_date_table(
                "stock_status",
                output_dir / "stock_status.parquet",
                base_version,
                target_version,
                "strftime(coalesce(TRY_CAST(date AS DATE),try_strptime(date,'%Y%m%d')::DATE),'%Y%m%d') AS date, "
                "status_type, symbols::JSON::VARCHAR[] AS symbols",
                full=True,
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

    def _benchmark_export_query(self) -> str:
        symbol = BENCHMARK_CONFIG["default_index"]
        return f"""
            SELECT date, open, high, low, close, volume, money
            FROM stocks WHERE symbol = '{symbol}'
            UNION ALL
            SELECT date, open, high, low, close, volume, money
            FROM benchmark b
            WHERE NOT EXISTS (
                SELECT 1 FROM stocks s WHERE s.symbol = '{symbol}' AND s.date = b.date
            )
        """

    def _copy_delta_benchmark(
        self, output_dir: Path, base_version: str, target_version: str
    ) -> int:
        query = f"SELECT * FROM ({self._benchmark_export_query()}) WHERE date > ?::DATE AND date <= ?::DATE"
        rows = self.conn.execute(
            f"SELECT COUNT(*) FROM ({query})", [base_version, target_version]
        ).fetchone()[0]
        if rows:
            self.conn.execute(f"""
                COPY ({query} ORDER BY date)
                TO '{output_dir / "benchmark.parquet"}' (FORMAT PARQUET, CODEC 'ZSTD')
            """, [base_version, target_version])
        return rows

    def _copy_delta_json_date_table(
        self,
        table: str,
        output_file: Path,
        base_version: str,
        target_version: str,
        select_columns: str,
        full: bool = False,
    ) -> int:
        # full=True：全量导出（stock_status 用）。客户 redis 权威部署会重写
        # 历史状态（ST/HALT/DELISTING），窗口增量到不了客户端，全量 1.7MB
        # 随 delta 重发可接受。
        if full:
            rows = self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if rows:
                self.conn.execute(f"""
                    COPY (
                        SELECT {select_columns}
                        FROM {table}
                        ORDER BY date
                    ) TO '{output_file}' (FORMAT PARQUET, CODEC 'ZSTD')
                """)
            return rows
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

    def _export_exrights_batch(self, output_dir: Path, market: str = "cn") -> None:
        """Export all exrights with pre-computed forward adj factors (batch)."""
        import time
        t0 = time.time()

        market_filter = (
            f"WHERE {self._cn_stock_filter_sql('e.symbol')}"
            if market == "cn"
            else ""
        )
        df_all = self.conn.execute(f"""
            SELECT e.symbol, e.date::TIMESTAMP_NS AS date,
                   allotted_ps, rationed_ps, rationed_px, bonus_ps, dividend,
                   exer_forward_a, exer_forward_b, exer_backward_a, exer_backward_b
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
            _fill_exer_factors(df)
            _fill_dividend_from_bonus_ps(df)
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
                    {_valuation_enrich_select()}
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

    def _export_stocks_batch(self, output_dir: Path, market: str = "cn", progress_callback=None) -> None:
        """Export all stocks with gap-filling and price limits (batch optimized).

        Gap-fill is materialized in symbol batches to bound peak memory;
        materializing all ~7600 x ~5200 rows at once exhausted the 7GB
        customer box (two full-host crashes).
        """
        import time
        t0 = time.time()

        market_filter = (
            f"WHERE {self._cn_stock_filter_sql('s0.symbol')}"
            if market == "cn" else ""
        )
        symbols = [r[0] for r in self.conn.execute(
            f"SELECT DISTINCT s0.symbol FROM stocks s0 "
            f"{market_filter} ORDER BY s0.symbol"
        ).fetchall()]

        # Shared trade calendar, materialized once (~5200 rows)
        self.conn.execute("""
            CREATE OR REPLACE TEMP TABLE _trade_cal AS
            SELECT date FROM stocks
            UNION
            SELECT date FROM trade_days
        """)

        batch_size = 250
        total_batches = (len(symbols) + batch_size - 1) // batch_size
        for offset in range(0, len(symbols), batch_size):
            batch = symbols[offset:offset + batch_size]
            symbol_list = self._symbols_in_clause(batch)

            logger.info(
                f"  Gap-filling batch {offset // batch_size + 1}/{total_batches} "
                f"({len(batch)} symbols)..."
            )
            self.conn.execute(f"""
                CREATE OR REPLACE TEMP TABLE _stocks_filled AS
                WITH lifespans AS (
                    SELECT s0.symbol, MIN(s0.date) AS first_date, MAX(s0.date) AS last_date
                    FROM stocks s0
                    WHERE s0.symbol IN ({symbol_list})
                    GROUP BY s0.symbol
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
                        CASE WHEN s.symbol IS NULL THEN 0.0 ELSE s.money END AS money
                    FROM lifespans ls
                    CROSS JOIN _trade_cal tc
                    LEFT JOIN stocks s ON s.symbol = ls.symbol AND s.date = tc.date
                    WHERE tc.date >= ls.first_date AND tc.date <= ls.last_date
                      AND (s.symbol IS NOT NULL OR EXISTS (
                          SELECT 1 FROM stock_status h WHERE h.status_type='HALT'
                          AND coalesce(TRY_CAST(h.date AS DATE),try_strptime(h.date,'%Y%m%d')::DATE)=tc.date
                          AND json_contains(TRY_CAST(h.symbols AS JSON),to_json(ls.symbol))))
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
                        WHEN wl.stored_preclose > 0 AND isfinite(wl.stored_preclose) THEN wl.stored_preclose
                        WHEN adj.event_count > 0 AND wl.lag_close IS NOT NULL
                             AND wl.volume > 0 THEN
                            ROUND(
                                (wl.lag_close - adj.total_deduction)
                                / adj.total_divisor,
                                CASE WHEN regexp_full_match(wl.symbol, '{ETF_SYMBOL_PATTERN}') THEN 3 ELSE 2 END)
                        ELSE COALESCE(wl.lag_close, wl.stored_preclose)
                    END AS preclose,
                    wl.volume, wl.money
                FROM with_lag wl
                LEFT JOIN adj ON adj.symbol = wl.symbol AND adj.date = wl.date
            """)
            # Write per-symbol parquet files with price limits
            if market == "us":
                for symbol in batch:
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
                for symbol in batch:
                    se = symbol.replace("'", "''")
                    output_file = output_dir / f"{symbol}.parquet"
                    code_prefix = symbol[:3]
                    is_chinext_star = code_prefix in ("300", "301", "688", "689")

                    if is_exchange_etf(symbol):
                        limit_sql = "raw.high_limit, raw.low_limit,"
                    elif is_chinext_star:
                        limit_sql = """
                            COALESCE(raw.high_limit, CASE WHEN f.date >= '2020-08-24' THEN ROUND(f.preclose * 1.20, 2)
                                 ELSE ROUND(f.preclose * 1.10, 2) END) AS high_limit,
                            COALESCE(raw.low_limit, CASE WHEN f.date >= '2020-08-24' THEN ROUND(f.preclose * 0.80, 2)
                                 ELSE ROUND(f.preclose * 0.90, 2) END) AS low_limit,
                        """
                    else:
                        limit_sql = """
                            COALESCE(raw.high_limit, ROUND(f.preclose * 1.10, 2)) AS high_limit,
                            COALESCE(raw.low_limit, ROUND(f.preclose * 0.90, 2)) AS low_limit,
                        """

                    self.conn.execute(f"""
                        COPY (
                            SELECT f.date, f.open, f.close, f.high, f.low,
                                {limit_sql}
                                f.preclose, f.volume, f.money
                            FROM _stocks_filled f LEFT JOIN stocks raw USING(symbol,date)
                            WHERE f.symbol = '{se}' ORDER BY f.date
                        ) TO '{output_file}' (FORMAT PARQUET, CODEC 'ZSTD')
                    """)

            self.conn.execute("DROP TABLE IF EXISTS _stocks_filled")
            if progress_callback:
                progress_callback(offset + len(batch), len(symbols))

        self.conn.execute("DROP TABLE IF EXISTS _trade_cal")
        logger.info(f"Exported {len(symbols)} stocks files in {time.time() - t0:.1f}s")

    def _ensure_stock_metadata_from_pool(self) -> None:
        """Ensure local symbol rows exist without inventing lifecycle facts."""
        self.conn.execute("""
            INSERT INTO stock_metadata(symbol)
            SELECT symbol FROM stock_pool
            UNION SELECT DISTINCT symbol FROM stocks
            ON CONFLICT(symbol) DO NOTHING
        """)

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

        # Merge local histories, preferring stock-table bars for overlapping dates.
        benchmark_query = self._benchmark_export_query()
        count, min_date = self.conn.execute(
            f"SELECT COUNT(*), MIN(date)::VARCHAR FROM ({benchmark_query})"
        ).fetchone()
        if count:
            self.conn.execute(f"""
                COPY ({benchmark_query} ORDER BY date)
                TO '{output_dir / "benchmark.parquet"}' (FORMAT PARQUET, CODEC 'ZSTD')
            """)
        if market == "cn":
            if not count:
                raise ValueError("benchmark export missing for CN market")
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

        # Export only stored status facts; absent rows and zero volume do not prove HALT.
        count = self.conn.execute("SELECT COUNT(*) FROM stock_status").fetchone()[0]
        if count > 0:
            self.conn.execute(f"""
                COPY (
                    SELECT strftime(coalesce(TRY_CAST(date AS DATE),try_strptime(date,'%Y%m%d')::DATE),'%Y%m%d') AS date,
                           status_type, symbols::JSON::VARCHAR[] AS symbols
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
