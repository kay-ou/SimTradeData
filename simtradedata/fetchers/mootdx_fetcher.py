"""
Mootdx data fetcher implementation

This module provides data fetching from mootdx (TDX-based) API.
Mootdx offers real-time quotes, minute bars, and XDXR data that BaoStock lacks.
"""

import logging
import time
from datetime import datetime
from typing import List, Optional

import pandas as pd

from simtradedata.fetchers.base_fetcher import BaseFetcher
from simtradedata.resilience.retry import RetryConfig, retry
from simtradedata.utils.code_utils import (
    convert_from_ptrade_code,
    get_mootdx_market,
    get_price_divisor,
)

logger = logging.getLogger(__name__)

_MOOTDX_RETRY = RetryConfig(max_retries=3, base_delay=2.0)

# Frequency constants for mootdx bars API
FREQ_5M = 0
FREQ_15M = 1
FREQ_30M = 2
FREQ_1H = 3
FREQ_DAILY_X100 = 4  # Daily K-line with prices * 100
FREQ_WEEKLY = 5
FREQ_MONTHLY = 6
FREQ_1M = 7
FREQ_DAILY = 9  # Standard daily K-line
FREQ_QUARTERLY = 10
FREQ_YEARLY = 11


class MootdxFetcher(BaseFetcher):
    """
    Fetch data from mootdx (TDX) API

    Mootdx provides A-share market data including:
    - Real-time quotes (multi-stock)
    - K-line data (daily, weekly, monthly, minute-level)
    - XDXR (ex-dividend/ex-rights) data
    - Company financial data
    - Stock list
    """

    source_name = "mootdx"

    def __init__(self, multithread: bool = True, timeout: int = 15):
        """
        Initialize MootdxFetcher.

        Args:
            multithread: Enable multithreading for better performance
            timeout: Connection timeout in seconds
        """
        super().__init__()
        self._multithread = multithread
        self._timeout = timeout
        self._client = None

    def _do_login(self):
        """Initialize mootdx Quotes client"""
        from mootdx.quotes import Quotes
        from mootdx.server import connect2, hosts

        client = Quotes.factory(
            market="std",
            multithread=self._multithread,
            timeout=min(self._timeout, 1.0),
            auto_retry=False,
            bestip=False,
            quiet=True,
        )
        try:
            if not client.client.get_security_count(0):
                client.close()
                deadline = time.monotonic() + self._timeout
                for host in hosts["HQ"]:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    # Reuse vendor candidates and its short protocol-level probe.
                    if not connect2(dict(host)).get("time"):
                        continue
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    if client.client.connect(
                        host["addr"], int(host["port"]),
                        time_out=min(remaining, 1.0),
                    ):
                        if client.client.get_security_count(0):
                            client.server = (host["addr"], int(host["port"]))
                            break
                    client.close()
                else:
                    raise ConnectionError("No available Mootdx quote server")
                if time.monotonic() >= deadline:
                    raise ConnectionError("No available Mootdx quote server within timeout")
        except Exception:
            client.close()
            raise
        client.client.client.settimeout(self._timeout)
        client.client.auto_retry = True
        self._client = client
        logger.info("Mootdx client initialized: %s", client.server)

    def _do_logout(self):
        """Release mootdx client resources"""
        if self._client is not None:
            self._client.close()
        self._client = None

    def _ensure_client(self):
        """Ensure client is available"""
        if self._client is None:
            self.login()

    @retry(config=_MOOTDX_RETRY)
    def fetch_stock_list(self, market: int = None) -> pd.DataFrame:
        """
        Fetch stock list from mootdx.

        Args:
            market: 0 for Shenzhen, 1 for Shanghai, None for both

        Returns:
            DataFrame with columns: code, name, market
        """
        if not self._ensure_source_available():
            return pd.DataFrame()
        self._ensure_client()

        if market is not None:
            df = self._client.stocks(market=market)
            if df is not None and not df.empty:
                df["market"] = market
            return df if df is not None else pd.DataFrame()

        # Fetch both markets
        dfs = []
        for m in [0, 1]:
            try:
                df = self._client.stocks(market=m)
                if df is not None and not df.empty:
                    df["market"] = m
                    dfs.append(df)
            except Exception as e:
                logger.warning(f"Failed to fetch stocks for market {m}: {e}")

        if not dfs:
            return pd.DataFrame()

        return pd.concat(dfs, ignore_index=True)

    @retry(config=_MOOTDX_RETRY)
    def fetch_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = None,
    ) -> pd.DataFrame:
        """
        Fetch daily K-line data.

        Args:
            symbol: Stock code in PTrade format (e.g., '600000.SS')
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            adjust: Adjustment type - None (raw), 'qfq' (forward), 'hfq' (backward)

        Returns:
            DataFrame with columns: date, open, high, low, close, volume, amount
        """
        if not self._ensure_source_available():
            return pd.DataFrame()
        self._ensure_client()

        code = convert_from_ptrade_code(symbol, "mootdx")

        try:
            # Use k() method which supports date range
            try:
                df = self._client.k(
                    symbol=code, begin=start_date, end=end_date,
                )
            except KeyError as e:
                # mootdx k() indexes datetime even when its response is empty.
                if e.args != ("datetime",):
                    raise
                df = pd.DataFrame()

            if df is None or df.empty:
                # For current-year ranges, retry an empty k() response via
                # bars(), which returns the latest 800 daily bars.
                current_year = str(datetime.now().year)
                if start_date[:4] >= current_year:
                    logger.debug(
                        f"k() returned empty for {symbol}, falling back to bars()"
                    )
                    df = self._client.bars(
                        symbol=code,
                        frequency=FREQ_DAILY,
                        start=0,
                        offset=800,
                    )
                if df is None or df.empty:
                    logger.debug(f"No daily data for {symbol}")
                    return pd.DataFrame()
                if start_date[:4] >= current_year:
                    if "datetime" in df.columns and "date" not in df.columns:
                        df = df.rename(columns={"datetime": "date"})
                    elif "datetime" in df.columns:
                        df = df.drop(columns=["datetime"])
                    elif "date" not in df.columns:
                        df = df.rename_axis("date").reset_index()

                    if "date" in df.columns:
                        df["date"] = pd.to_datetime(df["date"])
                        df = df[
                            (df["date"] >= start_date) & (df["date"] <= end_date)
                        ]

            # mootdx k() returns 'date' as both index and column; drop index
            df = df.reset_index(drop=True)

            # Apply adjustment if requested
            if adjust:
                df_adj = self._client.bars(
                    symbol=code,
                    frequency=FREQ_DAILY,
                    adjust=adjust,
                )
                if df_adj is not None and not df_adj.empty:
                    df = df_adj

            # Standardize column names
            if "volume" in df.columns and "vol" in df.columns:
                df = df.drop(columns=["vol"])
            df = df.rename(
                columns={
                    "datetime": "date",
                    "vol": "volume",
                }
            )

            # Apply price divisor for ETF/LOF (TDX returns 10x prices)
            divisor = get_price_divisor(symbol)
            if divisor != 1.0:
                for col in ("open", "high", "low", "close"):
                    if col in df.columns:
                        df[col] = df[col] / divisor

            # Convert date column
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
                df = df[(df["date"] >= start_date) & (df["date"] <= end_date)]

            logger.info(f"Fetched {len(df)} daily bars for {symbol}")
            return df

        except Exception as e:
            logger.error(f"Failed to fetch daily bars for {symbol}: {e}")
            raise

    @retry(config=_MOOTDX_RETRY)
    def fetch_minute_bars(
        self,
        symbol: str,
        frequency: int = FREQ_5M,
        offset: int = 800,
    ) -> pd.DataFrame:
        """
        Fetch minute-level K-line data (mootdx unique feature).

        Args:
            symbol: Stock code in PTrade format
            frequency: Bar frequency (FREQ_1M, FREQ_5M, FREQ_15M, FREQ_30M, FREQ_1H)
            offset: Number of bars to fetch (max 800)

        Returns:
            DataFrame with minute bars
        """
        if not self._ensure_source_available():
            return pd.DataFrame()
        self._ensure_client()

        code = convert_from_ptrade_code(symbol, "mootdx")

        try:
            df = self._client.bars(symbol=code, frequency=frequency, offset=offset)

            if df is None or df.empty:
                return pd.DataFrame()

            df = df.rename(columns={"datetime": "date", "vol": "volume"})

            logger.info(f"Fetched {len(df)} minute bars for {symbol}")
            return df

        except Exception as e:
            logger.error(f"Failed to fetch minute bars for {symbol}: {e}")
            raise

    @retry(config=_MOOTDX_RETRY)
    def fetch_realtime_quotes(self, symbols: List[str]) -> pd.DataFrame:
        """
        Fetch real-time quotes for multiple stocks (mootdx unique feature).

        Args:
            symbols: List of stock codes in PTrade format

        Returns:
            DataFrame with real-time quote data
        """
        if not self._ensure_source_available():
            return pd.DataFrame()
        self._ensure_client()

        codes = [convert_from_ptrade_code(s, "mootdx") for s in symbols]

        try:
            df = self._client.quotes(symbol=codes)

            if df is None or df.empty:
                return pd.DataFrame()

            # Add PTrade codes for reference
            if "code" in df.columns:
                df["ptrade_code"] = symbols[: len(df)]

            logger.info(f"Fetched real-time quotes for {len(df)} stocks")
            return df

        except Exception as e:
            logger.error(f"Failed to fetch real-time quotes: {e}")
            raise

    @retry(config=_MOOTDX_RETRY)
    def fetch_xdxr(self, symbol: str) -> pd.DataFrame:
        """
        Fetch XDXR (ex-dividend/ex-rights) data.

        Args:
            symbol: Stock code in PTrade format

        Returns:
            DataFrame with XDXR records
        """
        if not self._ensure_source_available():
            return pd.DataFrame()
        self._ensure_client()

        code = convert_from_ptrade_code(symbol, "mootdx")

        try:
            df = self._client.xdxr(symbol=code)

            if df is None or df.empty:
                logger.debug(f"No XDXR data for {symbol}")
                return pd.DataFrame()

            logger.info(f"Fetched {len(df)} XDXR records for {symbol}")
            return df

        except Exception as e:
            logger.error(f"Failed to fetch XDXR for {symbol}: {e}")
            raise

    @retry(config=_MOOTDX_RETRY)
    def fetch_stock_basic(self, symbol: str) -> pd.DataFrame:
        """
        Fetch basic financial information for a stock.

        Note: This returns the latest snapshot, not historical data.
        For historical fundamentals, use MootdxAffairFetcher.

        Args:
            symbol: Stock code in PTrade format

        Returns:
            DataFrame with financial data (FINVALUE array values)
        """
        if not self._ensure_source_available():
            return pd.DataFrame()
        self._ensure_client()

        code = convert_from_ptrade_code(symbol, "mootdx")

        try:
            df = self._client.finance(symbol=code)

            if df is None or df.empty:
                logger.debug(f"No finance data for {symbol}")
                return pd.DataFrame()

            return df

        except Exception as e:
            logger.error(f"Failed to fetch finance for {symbol}: {e}")
            raise

    @retry(config=_MOOTDX_RETRY)
    def fetch_index_bars(
        self,
        symbol: str,
        start_date: str = None,
        end_date: str = None,
        frequency: int = FREQ_DAILY,
        offset: int = 800,
    ) -> pd.DataFrame:
        """
        Fetch index K-line data.

        Args:
            symbol: Index code in PTrade format (e.g., '000001.SS' for SSE Composite)
            start_date: Start date (YYYY-MM-DD), optional
            end_date: End date (YYYY-MM-DD), optional
            frequency: Bar frequency (default: daily)
            offset: Number of bars to fetch

        Returns:
            DataFrame with index bars
        """
        if not self._ensure_source_available():
            return pd.DataFrame()
        self._ensure_client()

        code = convert_from_ptrade_code(symbol, "mootdx")
        market = get_mootdx_market(symbol)

        try:
            df = self._client.index(
                symbol=code,
                market=market,
                frequency=frequency,
                offset=offset,
            )

            if df is None or df.empty:
                logger.debug(f"No index data for {symbol}")
                return pd.DataFrame()

            # mootdx index() returns 'date' as both index and column; drop index
            df = df.reset_index(drop=True)

            df = df.rename(columns={"datetime": "date", "vol": "volume"})

            # Filter by date range if specified
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
                if start_date:
                    df = df[df["date"] >= start_date]
                if end_date:
                    df = df[df["date"] <= end_date]

            logger.info(f"Fetched {len(df)} index bars for {symbol}")
            return df

        except Exception as e:
            logger.error(f"Failed to fetch index bars for {symbol}: {e}")
            raise

    @retry(config=_MOOTDX_RETRY)
    def fetch_trade_calendar(
        self,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """
        Derive trading calendar from index daily bars.

        Mootdx doesn't have a direct trade calendar API, so we extract
        trading days from the SSE Composite Index.

        Args:
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)

        Returns:
            DataFrame with columns: calendar_date, is_trading_day
        """
        if not self._ensure_source_available():
            return pd.DataFrame()
        # Use SSE Composite Index to derive trading days
        index_df = self.fetch_index_bars(
            "000001.SS",
            start_date=start_date,
            end_date=end_date,
            offset=2000,
        )

        if index_df.empty:
            return pd.DataFrame()

        trading_days = set(index_df["date"].dt.strftime("%Y-%m-%d"))

        # Generate full calendar
        all_dates = pd.date_range(start=start_date, end=end_date, freq="D")
        result = pd.DataFrame({"calendar_date": all_dates.strftime("%Y-%m-%d")})
        result["is_trading_day"] = result["calendar_date"].isin(trading_days).astype(str)
        result["is_trading_day"] = result["is_trading_day"].map(
            {True: "1", False: "0", "True": "1", "False": "0"}
        )

        return result

    @retry(config=_MOOTDX_RETRY)
    def fetch_adjust_factor(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """
        Calculate adjust factors from hfq (backward adjusted) and raw prices.

        Mootdx doesn't provide adjust factors directly, but we can derive them
        from the ratio of hfq prices to raw prices.

        Args:
            symbol: Stock code in PTrade format
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)

        Returns:
            DataFrame with columns: date, backAdjustFactor
        """
        if not self._ensure_source_available():
            return pd.DataFrame()
        self._ensure_client()

        code = convert_from_ptrade_code(symbol, "mootdx")

        try:
            # Fetch raw and hfq data
            raw_df = self._client.k(
                symbol=code,
                begin=start_date.replace("-", ""),
                end=end_date.replace("-", ""),
            )

            if raw_df is None or raw_df.empty:
                return pd.DataFrame()

            hfq_df = self._client.k(
                symbol=code,
                begin=start_date.replace("-", ""),
                end=end_date.replace("-", ""),
                adjust="hfq",
            )

            if hfq_df is None or hfq_df.empty:
                return pd.DataFrame()

            # mootdx k() returns 'date' as both index and column;
            # drop the index to avoid ambiguity in merge()
            raw_df = raw_df.reset_index(drop=True)
            hfq_df = hfq_df.reset_index(drop=True)

            raw_df["date"] = pd.to_datetime(raw_df["date"])
            hfq_df["date"] = pd.to_datetime(hfq_df["date"])

            merged = raw_df[["date", "close"]].merge(
                hfq_df[["date", "close"]],
                on="date",
                suffixes=("_raw", "_hfq"),
            )

            merged["backAdjustFactor"] = merged["close_hfq"] / merged["close_raw"]
            result = merged[["date", "backAdjustFactor"]]

            logger.info(f"Calculated {len(result)} adjust factors for {symbol}")
            return result

        except Exception as e:
            logger.error(f"Failed to calculate adjust factor for {symbol}: {e}")
            raise

    def fetch_f10_catalog(self, symbol: str) -> pd.DataFrame:
        """
        Fetch F10 company information catalog.

        Args:
            symbol: Stock code in PTrade format

        Returns:
            DataFrame with F10 categories
        """
        if not self._ensure_source_available():
            return pd.DataFrame()
        self._ensure_client()

        code = convert_from_ptrade_code(symbol, "mootdx")

        try:
            df = self._client.F10C(symbol=code)
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.error(f"Failed to fetch F10 catalog for {symbol}: {e}")
            raise

    def fetch_f10_detail(self, symbol: str, name: str) -> Optional[str]:
        """
        Fetch F10 company information detail by category name.

        Args:
            symbol: Stock code in PTrade format
            name: Category name from F10 catalog (e.g., '最新提示')

        Returns:
            Text content of the F10 section
        """
        if not self._ensure_source_available():
            return None
        self._ensure_client()

        code = convert_from_ptrade_code(symbol, "mootdx")

        try:
            result = self._client.F10(symbol=code, name=name)
            return result
        except Exception as e:
            logger.error(f"Failed to fetch F10 detail for {symbol}/{name}: {e}")
            raise
