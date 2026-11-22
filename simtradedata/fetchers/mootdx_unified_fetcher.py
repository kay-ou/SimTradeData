"""
Mootdx unified data fetcher

Combines MootdxFetcher (quotes/k-line) and MootdxAffairFetcher (financials)
into a unified interface compatible with DataSplitter and DuckDBWriter.
"""

import logging

import pandas as pd

from simtradedata.cache import DEFAULT_TTL, cached
from simtradedata.config.field_mappings import MARKET_FIELD_MAP
from simtradedata.fetchers.mootdx_affair_fetcher import MootdxAffairFetcher
from simtradedata.fetchers.mootdx_fetcher import MootdxFetcher
from simtradedata.utils.code_utils import convert_to_ptrade_code, is_etf_code

logger = logging.getLogger(__name__)


class MootdxUnifiedFetcher:
    """
    Unified mootdx data fetcher combining market data and financial data.

    This class provides a high-level interface that:
    - Uses MootdxFetcher for k-line, index, and real-time data
    - Uses MootdxAffairFetcher for batch financial data
    - Outputs DataFrames compatible with DataSplitter and DuckDBWriter
    """

    def __init__(self, download_dir: str = None):
        """
        Initialize the unified fetcher.

        Args:
            download_dir: Directory for downloading financial data ZIP files
        """
        self._quotes_fetcher = MootdxFetcher()
        self._affair_fetcher = MootdxAffairFetcher(download_dir=download_dir)

    def login(self):
        """Quote methods open their session lazily; Affair needs no quote login."""

    def logout(self):
        """Logout from mootdx quotes server"""
        self._quotes_fetcher.logout()

    def __enter__(self):
        self.login()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.logout()
        return False

    def fetch_daily_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """
        Fetch daily OHLCV data for a stock.

        Output columns match the format expected by DataSplitter:
        date, open, high, low, close, volume, amount

        Args:
            symbol: Stock code in PTrade format
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)

        Returns:
            DataFrame with standardized market data columns
        """
        df = self._quotes_fetcher.fetch_daily_bars(
            symbol, start_date, end_date
        )

        if df.empty:
            return pd.DataFrame()

        fields = ["open", "high", "low", "close", "volume", "amount"]
        available = [field for field in fields if field in df.columns]
        return df[["date"] + available].copy()

    def fetch_index_data(
        self,
        index_code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """
        Fetch index OHLCV data (for benchmark).

        Args:
            index_code: Index code in PTrade format (e.g., '000300.SS')
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)

        Returns:
            DataFrame with date index and columns: open, high, low, close, volume, money
        """
        df = self._quotes_fetcher.fetch_index_bars(
            index_code,
            start_date=start_date,
            end_date=end_date,
        )

        if df.empty:
            return pd.DataFrame()

        # Set date as index
        if "date" in df.columns:
            df = df.set_index("date")

        # Rename to PTrade format
        rename_map = {k: v for k, v in MARKET_FIELD_MAP.items() if k in df.columns}
        if rename_map:
            df = df.rename(columns=rename_map)

        return df

    @cached(ttl=DEFAULT_TTL["stock_list"], key_prefix="mootdx_stock_list")
    def fetch_stock_list(self) -> list:
        """
        Fetch all stock codes in PTrade format.

        Returns:
            Sorted list of PTrade stock codes (e.g., ['000001.SZ', '600000.SS'])
        """
        df = self._quotes_fetcher.fetch_stock_list()

        if df.empty:
            return []

        # Filter to stock and ETF codes (exclude indices, bonds, warrants)
        sz_stock_prefixes = ("000", "001", "002", "003", "300", "301", "302")
        sh_stock_prefixes = ("600", "601", "603", "605", "688", "689")

        codes = []
        for _, row in df.iterrows():
            code = str(row.get("code", "")).strip()
            if not code or len(code) != 6:
                continue

            first_three = code[:3]

            if (
                first_three in sz_stock_prefixes
                or first_three in sh_stock_prefixes
                or is_etf_code(code)
            ):
                codes.append(convert_to_ptrade_code(code, "qstock"))

        return sorted(codes)

    def fetch_adjust_factor(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """
        Calculate backward adjust factors.

        Args:
            symbol: Stock code in PTrade format
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)

        Returns:
            DataFrame with columns: date, backAdjustFactor
        """
        return self._quotes_fetcher.fetch_adjust_factor(
            symbol, start_date, end_date
        )

    def fetch_xdxr(self, symbol: str) -> pd.DataFrame:
        """
        Fetch XDXR (ex-dividend/ex-rights) data.

        Args:
            symbol: Stock code in PTrade format

        Returns:
            DataFrame with XDXR records
        """
        return self._quotes_fetcher.fetch_xdxr(symbol)

    def fetch_fundamentals_for_quarter(
        self,
        year: int,
        quarter: int,
    ) -> pd.DataFrame:
        """
        Fetch all stocks' financial data for a quarter (batch download).

        This uses the Affair API which downloads one ZIP containing
        all stocks' data for the quarter.

        Args:
            year: Year
            quarter: Quarter (1-4)

        Returns:
            DataFrame with PTrade-compatible financial fields
        """
        return self._affair_fetcher.fetch_fundamentals_for_quarter(year, quarter)

    @cached(ttl=DEFAULT_TTL["trade_calendar"], key_prefix="mootdx_trade_cal")
    def fetch_trade_calendar(
        self,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """
        Derive trading calendar from index data.

        Args:
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)

        Returns:
            DataFrame with columns: calendar_date, is_trading_day
        """
        return self._quotes_fetcher.fetch_trade_calendar(start_date, end_date)

    def fetch_realtime_quotes(self, symbols: list) -> pd.DataFrame:
        """
        Fetch real-time quotes for multiple stocks.

        Args:
            symbols: List of stock codes in PTrade format

        Returns:
            DataFrame with real-time data
        """
        return self._quotes_fetcher.fetch_realtime_quotes(symbols)

    def fetch_blocks(self, filename="block.dat") -> pd.DataFrame:
        return self._quotes_fetcher.fetch_blocks(filename)

    def fetch_f10_catalog(self, symbol) -> pd.DataFrame:
        return self._quotes_fetcher.fetch_f10_catalog(symbol)

    def fetch_f10_detail(self, symbol, name):
        return self._quotes_fetcher.fetch_f10_detail(symbol, name)

    def fetch_history_transactions(self, symbol, date, start=0, offset=800) -> pd.DataFrame:
        return self._quotes_fetcher.fetch_history_transactions(symbol, date, start, offset)

    def fetch_history_minutes(self, symbol, date) -> pd.DataFrame:
        return self._quotes_fetcher.fetch_history_minutes(symbol, date)

    def fetch_minute_bars(
        self,
        symbol: str,
        frequency: int = 0,
        offset: int = 800,
    ) -> pd.DataFrame:
        """
        Fetch minute-level K-line data.

        Forwards to the inner MootdxFetcher.

        Args:
            symbol: Stock code in PTrade format
            frequency: Bar frequency (0=5m, 1=15m, 2=30m, 3=1h, 7=1m)
            offset: Number of bars to fetch (max 800)

        Returns:
            DataFrame with minute bars
        """
        return self._quotes_fetcher.fetch_minute_bars(
            symbol, frequency=frequency, offset=offset,
        )
