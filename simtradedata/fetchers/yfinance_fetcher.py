# -*- coding: utf-8 -*-
"""
YFinance data fetcher for US stocks

Uses yfinance library to fetch US stock data from Yahoo Finance.
No API key required.
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import yfinance as yf

from simtradedata.config.us_field_mappings import (
    NASDAQ_TRADED_URL,
    YFINANCE_MARKET_FIELD_MAP,
)
from simtradedata.fetchers.base_fetcher import BaseFetcher
from simtradedata.utils.code_utils import (
    convert_from_ptrade_code,
    convert_to_ptrade_code,
)

logger = logging.getLogger(__name__)


class YFinanceFetcher(BaseFetcher):
    """
    Fetcher for US stock data via yfinance.

    Provides:
    - Stock list from NASDAQ trader file
    - Batch OHLCV via yf.download()
    - Per-stock fundamentals, metadata, ex-rights via yf.Ticker()
    - Benchmark index data (S&P 500)
    - Index constituents (S&P 500, NASDAQ-100) from Wikipedia
    """

    source_name = "yfinance"

    def __init__(self, rate_limit: float = 0.5):
        super().__init__()
        self._rate_limit = rate_limit

    def _do_login(self):
        # yfinance is stateless HTTP - no login needed
        pass

    def _do_logout(self):
        # yfinance is stateless HTTP - no logout needed
        pass

    def _throttle(self):
        """Rate limit between per-stock API calls."""
        if self._rate_limit > 0:
            time.sleep(self._rate_limit)

    # ========================================
    # Stock list
    # ========================================

    def fetch_stock_list(self) -> list[str]:
        """
        Fetch US common stock list from NASDAQ trader file.

        Returns:
            List of PTrade-format symbols (e.g., ['AAPL.US', 'MSFT.US', ...])
        """
        try:
            df = pd.read_csv(NASDAQ_TRADED_URL, sep="|")
        except Exception as e:
            logger.error(f"Failed to download NASDAQ traded file: {e}")
            return []

        if "Symbol" not in df.columns:
            logger.error("NASDAQ traded file missing 'Symbol' column")
            return []

        # Last row is a footer line (File Creation Time), drop it
        df = df[df["Symbol"].notna()]

        # Filter for common stocks only:
        # - ETF column: 'Y' = ETF, 'N' = not ETF
        # - Test Issue: 'Y' = test, 'N' = real
        # - Financial Status: normal stocks have 'N' (Normal)
        if "ETF" in df.columns:
            df = df[df["ETF"] == "N"]
        if "Test Issue" in df.columns:
            df = df[df["Test Issue"] == "N"]

        # Filter by security name to exclude warrants, units, rights, etc.
        if "Security Name" in df.columns:
            exclude_patterns = [
                "Warrant",
                "Rights",
                "Units",
                "Acquisition Corp",
            ]
            for pattern in exclude_patterns:
                df = df[~df["Security Name"].str.contains(pattern, case=False, na=False)]

        symbols = df["Symbol"].str.strip().tolist()

        # Remove symbols with special characters (preferred shares, etc.)
        symbols = [
            s for s in symbols
            if s.isalpha() and len(s) <= 5
        ]

        # Convert to PTrade format
        ptrade_symbols = [convert_to_ptrade_code(s, "yfinance") for s in symbols]
        logger.info(f"Fetched {len(ptrade_symbols)} US common stocks")
        return ptrade_symbols

    # ========================================
    # Batch OHLCV download
    # ========================================

    def fetch_batch_ohlcv(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
        """
        Batch download OHLCV data and adjust factors from a single yf.download().

        Args:
            symbols: List of PTrade-format symbols (e.g., ['AAPL.US', 'MSFT.US'])
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)

        Returns:
            Tuple of (ohlcv_dict, adjust_dict):
            - ohlcv_dict: PTrade symbol -> DataFrame (date index, OHLCV columns)
            - adjust_dict: PTrade symbol -> DataFrame (date, adj_a, adj_b)
        """
        yf_tickers = [convert_from_ptrade_code(s, "yfinance") for s in symbols]

        try:
            raw = yf.download(
                tickers=yf_tickers,
                start=start_date,
                end=end_date,
                auto_adjust=False,
                group_by="ticker",
                threads=True,
                progress=False,
            )
        except Exception as e:
            logger.error(f"yf.download failed: {e}")
            return {}, {}

        if raw.empty:
            return {}, {}

        ohlcv_result = {}
        adjust_result = {}
        is_single = len(yf_tickers) == 1

        for ptrade_sym, yf_ticker in zip(symbols, yf_tickers):
            try:
                if is_single:
                    df = self._flatten_columns(raw.copy())
                else:
                    df = self._extract_ticker(raw, yf_ticker, is_single)
                    if df is None:
                        continue

                # Drop rows where all price columns are NaN
                price_cols = ["Open", "High", "Low", "Close"]
                available = [c for c in price_cols if c in df.columns]
                if not available:
                    continue
                df = df.dropna(subset=available, how="all")
                if df.empty:
                    continue

                # Extract adjust factors before dropping Adj Close
                if "Close" in df.columns and "Adj Close" in df.columns:
                    adj_rows = df.dropna(subset=["Close", "Adj Close"])
                    if not adj_rows.empty:
                        adj_df = pd.DataFrame({
                            "date": adj_rows.index,
                            "adj_a": adj_rows["Adj Close"].values / adj_rows["Close"].values,
                            "adj_b": 0.0,
                        })
                        adj_df = adj_df.dropna(subset=["adj_a"])
                        if not adj_df.empty:
                            adjust_result[ptrade_sym] = adj_df

                # Rename columns
                df = df.rename(columns=YFINANCE_MARKET_FIELD_MAP)

                # Calculate preclose (previous day's close)
                df["preclose"] = df["close"].shift(1)

                # Yahoo supplies no actual traded amount; keep it unknown.
                df["money"] = float('nan')

                # high_limit and low_limit are NULL for US stocks
                df["high_limit"] = None
                df["low_limit"] = None

                # Drop Adj Close if present
                if "Adj Close" in df.columns:
                    df = df.drop(columns=["Adj Close"])

                df.index.name = "date"
                ohlcv_result[ptrade_sym] = df

            except Exception as e:
                logger.warning(f"Failed to process OHLCV for {ptrade_sym}: {e}")
                continue

        return ohlcv_result, adjust_result

    # ========================================
    # Per-stock data (fundamentals, valuation, metadata, exrights)
    # ========================================

    def fetch_stock_detail(
        self, symbol: str, ohlcv_df: pd.DataFrame
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        Fetch fundamentals and valuation using a single Ticker object.

        Returns:
            Tuple of (fundamentals_df, valuation_df)
        """
        yf_ticker = convert_from_ptrade_code(symbol, "yfinance")
        ticker = yf.Ticker(yf_ticker)

        try:
            income = ticker.quarterly_income_stmt
            balance = ticker.quarterly_balance_sheet
        except Exception as e:
            logger.warning(f"Failed to fetch financials for {symbol}: {e}")
            return pd.DataFrame(), pd.DataFrame()

        fund_df = self._compute_fundamentals(income, balance)

        val_df = pd.DataFrame()
        if not ohlcv_df.empty:
            info = self._safe_get_info(ticker)
            val_df = self._compute_valuation(ohlcv_df, income, balance, info)

        return fund_df, val_df

    def fetch_fundamentals(self, symbol: str) -> pd.DataFrame:
        """
        Fetch quarterly fundamentals. Public wrapper for smart_router.
        For batch downloads, prefer fetch_stock_detail().
        """
        yf_ticker = convert_from_ptrade_code(symbol, "yfinance")
        ticker = yf.Ticker(yf_ticker)

        try:
            income = ticker.quarterly_income_stmt
            balance = ticker.quarterly_balance_sheet
        except Exception as e:
            logger.warning(f"Failed to fetch financials for {symbol}: {e}")
            return pd.DataFrame()

        return self._compute_fundamentals(income, balance)

    def fetch_valuation_data(
        self, symbol: str, ohlcv_df: pd.DataFrame
    ) -> pd.DataFrame:
        """
        Compute daily valuation metrics. Public wrapper for smart_router.
        For batch downloads, prefer fetch_stock_detail().
        """
        if ohlcv_df.empty:
            return pd.DataFrame()

        yf_ticker = convert_from_ptrade_code(symbol, "yfinance")
        ticker = yf.Ticker(yf_ticker)
        info = self._safe_get_info(ticker)

        try:
            income = ticker.quarterly_income_stmt
            balance = ticker.quarterly_balance_sheet
        except Exception:
            income = None
            balance = None

        return self._compute_valuation(ohlcv_df, income, balance, info)

    def _compute_fundamentals(
        self,
        income: Optional[pd.DataFrame],
        balance: Optional[pd.DataFrame],
    ) -> pd.DataFrame:
        """Compute fundamentals from pre-fetched financial statements."""
        if income is None or income.empty:
            return pd.DataFrame()

        quarters = sorted(income.columns)

        rows = []
        for q_date in quarters:
            row = {"date": q_date}

            total_revenue = _safe_get_from_stmt(income, "Total Revenue", q_date)
            net_income = _safe_get_from_stmt(income, "Net Income", q_date)
            gross_profit = _safe_get_from_stmt(income, "Gross Profit", q_date)
            ebit = _safe_get_from_stmt(income, "EBIT", q_date)

            total_assets = _safe_get_from_stmt(balance, "Total Assets", q_date)
            current_assets = _safe_get_from_stmt(balance, "Current Assets", q_date)
            current_liabilities = _safe_get_from_stmt(
                balance, "Current Liabilities", q_date
            )
            total_liabilities = _safe_get_from_stmt(
                balance, "Total Liabilities Net Minority Interest", q_date
            )
            equity = _safe_get_from_stmt(balance, "Stockholders Equity", q_date)
            inventory = _safe_get_from_stmt(balance, "Inventory", q_date)
            accounts_receivable = _safe_get_from_stmt(
                balance, "Accounts Receivable", q_date
            )

            interest_expense = _safe_get_from_stmt(income, "Interest Expense", q_date)

            # Net profit ratio
            if total_revenue and total_revenue != 0 and net_income is not None:
                row["net_profit_ratio"] = net_income / total_revenue * 100
            # Gross income ratio
            if total_revenue and total_revenue != 0 and gross_profit is not None:
                row["gross_income_ratio"] = gross_profit / total_revenue * 100
            # ROE
            if equity and equity != 0 and net_income is not None:
                row["roe"] = net_income / equity * 100
            # ROA
            if total_assets and total_assets != 0 and net_income is not None:
                row["roa"] = net_income / total_assets * 100
            # Current ratio
            if current_liabilities and current_liabilities != 0 and current_assets is not None:
                row["current_ratio"] = current_assets / current_liabilities
            # Quick ratio
            if current_liabilities and current_liabilities != 0 and current_assets is not None:
                inv = inventory or 0
                row["quick_ratio"] = (current_assets - inv) / current_liabilities
            # Debt/equity ratio
            if equity and equity != 0 and total_liabilities is not None:
                row["debt_equity_ratio"] = total_liabilities / equity * 100
            # Interest coverage
            if interest_expense and interest_expense != 0 and ebit is not None:
                row["interest_cover"] = ebit / abs(interest_expense)
            # Total asset turnover (annualized from quarterly)
            if total_assets and total_assets != 0 and total_revenue is not None:
                row["total_asset_turnover_rate"] = (total_revenue * 4) / total_assets
            # AR turnover (annualized)
            if accounts_receivable and accounts_receivable != 0 and total_revenue is not None:
                row["accounts_receivables_turnover_rate"] = (
                    (total_revenue * 4) / accounts_receivable
                )
            # Inventory turnover (annualized, using revenue as proxy for COGS)
            if inventory and inventory != 0 and total_revenue is not None:
                row["inventory_turnover_rate"] = (total_revenue * 4) / inventory

            rows.append(row)

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").set_index("date")

        # Calculate YoY growth rates (compare with same quarter last year)
        # Need at least 5 quarters of data
        if len(df) >= 5:
            for q_date in quarters:
                if q_date not in df.index:
                    continue
                prev_year = q_date - pd.DateOffset(years=1)
                # Find closest quarter date
                closest = df.index[df.index <= prev_year]
                if len(closest) == 0:
                    continue
                prev_q = closest[-1]
                # Only compare if within 400 days (roughly 1 year + margin)
                if (q_date - prev_q).days > 400:
                    continue

                # Revenue growth
                rev_cur = _safe_get_from_stmt(income, "Total Revenue", q_date)
                rev_prev = _safe_get_from_stmt(income, "Total Revenue", prev_q)
                if rev_prev and rev_prev != 0 and rev_cur is not None:
                    df.loc[q_date, "operating_revenue_grow_rate"] = (
                        (rev_cur - rev_prev) / abs(rev_prev) * 100
                    )

                # Net profit growth
                ni_cur = _safe_get_from_stmt(income, "Net Income", q_date)
                ni_prev = _safe_get_from_stmt(income, "Net Income", prev_q)
                if ni_prev and ni_prev != 0 and ni_cur is not None:
                    df.loc[q_date, "net_profit_grow_rate"] = (
                        (ni_cur - ni_prev) / abs(ni_prev) * 100
                    )

                # Total asset growth
                ta_cur = _safe_get_from_stmt(balance, "Total Assets", q_date)
                ta_prev = _safe_get_from_stmt(balance, "Total Assets", prev_q)
                if ta_prev and ta_prev != 0 and ta_cur is not None:
                    df.loc[q_date, "total_asset_grow_rate"] = (
                        (ta_cur - ta_prev) / abs(ta_prev) * 100
                    )

        return df

    def _compute_valuation(
        self,
        ohlcv_df: pd.DataFrame,
        income: Optional[pd.DataFrame],
        balance: Optional[pd.DataFrame],
        info: dict,
    ) -> pd.DataFrame:
        """Compute daily valuation from pre-fetched data."""
        shares_outstanding = info.get("sharesOutstanding")
        float_shares = info.get("floatShares")

        if not shares_outstanding:
            return pd.DataFrame()

        # Build quarterly metrics
        quarterly_metrics = {}
        if income is not None and not income.empty:
            for q_date in income.columns:
                metrics = {}
                ni = _safe_get_from_stmt(income, "Net Income", q_date)
                rev = _safe_get_from_stmt(income, "Total Revenue", q_date)
                bvps = None
                if balance is not None and not balance.empty and q_date in balance.columns:
                    eq = _safe_get_from_stmt(balance, "Stockholders Equity", q_date)
                    if eq is not None and shares_outstanding:
                        bvps = eq / shares_outstanding
                metrics["net_income"] = ni
                metrics["revenue"] = rev
                metrics["bvps"] = bvps
                quarterly_metrics[q_date] = metrics

        # Calculate TTM values (trailing 4 quarters)
        sorted_dates = sorted(quarterly_metrics.keys())
        ttm_data = {}
        for i, q_date in enumerate(sorted_dates):
            start_idx = max(0, i - 3)
            window = sorted_dates[start_idx:i + 1]
            ttm_ni = sum(
                quarterly_metrics[d].get("net_income") or 0 for d in window
            )
            ttm_rev = sum(
                quarterly_metrics[d].get("revenue") or 0 for d in window
            )
            bvps = quarterly_metrics[q_date].get("bvps")
            ttm_data[q_date] = {
                "eps_ttm": ttm_ni / shares_outstanding if shares_outstanding else None,
                "revenue_ttm": ttm_rev,
                "bvps": bvps,
            }

        # Build daily valuation
        df = ohlcv_df[["close", "volume"]].copy()
        df["total_shares"] = float(shares_outstanding)
        df["a_floats"] = float(float_shares) if float_shares else None

        # Forward-fill TTM data onto daily dates via reindex
        if ttm_data:
            ttm_records = [{"date": q, **v} for q, v in sorted(ttm_data.items())]
            ttm_df = pd.DataFrame(ttm_records).set_index("date")
            ttm_df.index = pd.to_datetime(ttm_df.index)
            ttm_daily = ttm_df.reindex(df.index, method="ffill")
            df["eps_ttm"] = ttm_daily["eps_ttm"]
            df["bvps"] = ttm_daily["bvps"]
            df["revenue_ttm"] = ttm_daily["revenue_ttm"]
        else:
            df["eps_ttm"] = None
            df["bvps"] = None
            df["revenue_ttm"] = None

        # Calculate ratios
        df["pe_ttm"] = None
        mask = df["eps_ttm"].notna() & (df["eps_ttm"] != 0)
        df.loc[mask, "pe_ttm"] = df.loc[mask, "close"] / df.loc[mask, "eps_ttm"]

        df["pb"] = None
        mask = df["bvps"].notna() & (df["bvps"] != 0)
        df.loc[mask, "pb"] = df.loc[mask, "close"] / df.loc[mask, "bvps"]

        df["ps_ttm"] = None
        mask = df["revenue_ttm"].notna() & (df["revenue_ttm"] != 0)
        df.loc[mask, "ps_ttm"] = (
            df.loc[mask, "close"] * shares_outstanding / df.loc[mask, "revenue_ttm"]
        )

        # Turnover rate
        df["turnover_rate"] = df["volume"] / shares_outstanding * 100

        # Select output columns
        result = df[
            ["pe_ttm", "pb", "ps_ttm", "total_shares", "a_floats", "turnover_rate"]
        ].copy()
        result.index.name = "date"

        return result

    def fetch_metadata(self, symbol: str) -> Optional[dict]:
        """
        Fetch stock metadata from ticker.info.

        Returns:
            Dict with keys: stock_name, listed_date, blocks (JSON string)
        """
        yf_ticker = convert_from_ptrade_code(symbol, "yfinance")
        ticker = yf.Ticker(yf_ticker)
        info = self._safe_get_info(ticker)

        if not info:
            return None

        stock_name = info.get("shortName") or info.get("longName")
        if not stock_name:
            return None

        # Listed date from firstTradeDateEpochUtc
        listed_date = None
        epoch = info.get("firstTradeDateEpochUtc")
        if epoch:
            listed_date = datetime.fromtimestamp(epoch, tz=timezone.utc).strftime(
                "%Y-%m-%d"
            )

        # Blocks: sector and industry as JSON
        blocks = {}
        if info.get("sector"):
            blocks["sector"] = info["sector"]
        if info.get("industry"):
            blocks["industry"] = info["industry"]

        return {
            "symbol": symbol,
            "stock_name": stock_name,
            "listed_date": listed_date,
            "blocks": json.dumps(blocks, ensure_ascii=False) if blocks else None,
        }

    def fetch_exrights(self, symbol: str) -> pd.DataFrame:
        """
        Fetch ex-rights data from ticker.actions (dividends + splits).

        Field mapping aligned with A-share adj_cache formula:
        - allotted_ps: share multiplier from stock splits (4:1 → 3.0)
        - bonus_ps: cash dividend per share (same as dividend column)
        - dividend: cash dividend per share (for DividendLazyLoader)

        Returns:
            DataFrame with columns: date, dividend, bonus_ps, allotted_ps,
            rationed_ps, rationed_px
        """
        yf_ticker = convert_from_ptrade_code(symbol, "yfinance")
        ticker = yf.Ticker(yf_ticker)

        try:
            actions = ticker.actions
        except Exception as e:
            logger.warning(f"Failed to fetch actions for {symbol}: {e}")
            return pd.DataFrame()

        if actions is None or actions.empty:
            return pd.DataFrame()

        result = pd.DataFrame(index=actions.index)
        result.index.name = "date"

        # Cash dividends → both dividend and bonus_ps (formula reads bonus_ps)
        if "Dividends" in actions.columns:
            result["dividend"] = actions["Dividends"]
        else:
            result["dividend"] = 0.0
        result["bonus_ps"] = result["dividend"]

        # Stock splits → allotted_ps (formula uses it in share multiplier m)
        # yfinance reports ratio (e.g., 4.0 for 4:1 split)
        # allotted_ps = split_ratio - 1 (4:1 means 3 bonus shares per share)
        if "Stock Splits" in actions.columns:
            splits = actions["Stock Splits"]
            result["allotted_ps"] = splits.apply(
                lambda x: x - 1.0 if x >= 1.0 else 0.0
            )
        else:
            result["allotted_ps"] = 0.0

        # Not applicable to US stocks
        result["rationed_ps"] = 0.0
        result["rationed_px"] = 0.0

        # Filter out rows where nothing happened
        result = result[
            (result["dividend"] > 0) | (result["allotted_ps"] > 0)
        ]

        return result

    # ========================================
    # Benchmark and index data
    # ========================================

    def fetch_benchmark(
        self, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """
        Fetch S&P 500 benchmark data.

        Returns:
            DataFrame with date index and columns: open, high, low, close, volume.
            money is NULL for index data.
        """
        try:
            raw = yf.download(
                "^GSPC",
                start=start_date,
                end=end_date,
                auto_adjust=True,
                progress=False,
            )
        except Exception as e:
            logger.error(f"Failed to fetch benchmark: {e}")
            return pd.DataFrame()

        if raw.empty:
            return pd.DataFrame()

        df = self._flatten_columns(raw)
        df = df.rename(columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        })

        df.index.name = "date"
        df["money"] = None

        return df[["open", "high", "low", "close", "volume", "money"]]

    def fetch_index_constituents_sp500(self) -> list[str]:
        """
        Fetch S&P 500 constituents from Wikipedia.

        Returns:
            List of PTrade-format symbols (e.g., ['AAPL.US', 'MSFT.US', ...])
        """
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        try:
            tables = pd.read_html(
                url, storage_options={"User-Agent": "SimTradeData/1.0"}
            )
            if not tables:
                return []
            df = tables[0]
            symbols = df["Symbol"].str.strip().str.replace(".", "-", regex=False).tolist()
            return [convert_to_ptrade_code(s, "yfinance") for s in symbols]
        except Exception as e:
            logger.error(f"Failed to fetch S&P 500 constituents: {e}")
            return []

    def fetch_index_constituents_ndx100(self) -> list[str]:
        """
        Fetch NASDAQ-100 constituents from Wikipedia.

        Returns:
            List of PTrade-format symbols (e.g., ['AAPL.US', 'MSFT.US', ...])
        """
        url = "https://en.wikipedia.org/wiki/Nasdaq-100"
        try:
            tables = pd.read_html(
                url, storage_options={"User-Agent": "SimTradeData/1.0"}
            )
            if not tables:
                return []
            # Find the table with Ticker column
            for table in tables:
                if "Ticker" in table.columns:
                    symbols = table["Ticker"].str.strip().tolist()
                    return [convert_to_ptrade_code(s, "yfinance") for s in symbols]
                if "Symbol" in table.columns:
                    symbols = table["Symbol"].str.strip().tolist()
                    return [convert_to_ptrade_code(s, "yfinance") for s in symbols]
            return []
        except Exception as e:
            logger.error(f"Failed to fetch NASDAQ-100 constituents: {e}")
            return []

    # ========================================
    # Helpers
    # ========================================

    def _safe_get_info(self, ticker: yf.Ticker) -> dict:
        """Safely get ticker.info with error handling."""
        try:
            return ticker.info or {}
        except Exception as e:
            logger.warning(f"Failed to get ticker info: {e}")
            return {}

    @staticmethod
    def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
        """Flatten MultiIndex columns from yf.download().

        yfinance >= 0.2.x returns MultiIndex columns as (Ticker, Price).
        This method drops the Ticker level, keeping only the Price level.
        """
        if isinstance(df.columns, pd.MultiIndex):
            df = df.droplevel("Ticker", axis=1)
        return df

    @staticmethod
    def _extract_ticker(
        raw: pd.DataFrame, yf_ticker: str, is_single: bool
    ) -> pd.DataFrame | None:
        """Extract a single ticker's data from yf.download result.

        With group_by='ticker', MultiIndex is (Ticker, Price).
        For single ticker: just flatten. For multi: select by ticker name.
        """
        if not isinstance(raw.columns, pd.MultiIndex):
            return raw.copy()

        if is_single:
            return raw.droplevel("Ticker", axis=1).copy()

        # Multi-ticker: top level is Ticker name
        top_level = raw.columns.get_level_values(0).unique()
        if yf_ticker in top_level:
            return raw[yf_ticker].copy()
        return None


def _safe_get_from_stmt(
    stmt: Optional[pd.DataFrame], field: str, date
) -> Optional[float]:
    """Safely extract a value from a financial statement DataFrame."""
    if stmt is None or stmt.empty:
        return None
    if date not in stmt.columns:
        return None
    if field not in stmt.index:
        return None
    val = stmt.loc[field, date]
    if pd.notna(val):
        return float(val)
    return None
