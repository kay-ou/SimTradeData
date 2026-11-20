"""
Mootdx data fetcher implementation

This module provides data fetching from mootdx (TDX-based) API.
Mootdx offers real-time quotes, minute bars, and XDXR data that BaoStock lacks.
"""

import logging
import time
from typing import List, Optional

import pandas as pd

from simtradedata.fetchers.base_fetcher import BaseFetcher
from simtradedata.fetchers.mootdx_session import install_session
from simtradedata.resilience.retry import RetryConfig, retry
from simtradedata.utils.code_utils import (
    convert_from_ptrade_code,
    get_mootdx_market,
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
            install_session(client.client)
            client.close()
            try:
                connected = client.client.connect(
                    *client.server, time_out=min(self._timeout, 1.0),
                )
            except ConnectionError:
                connected = False
            if not connected or not client.client.get_security_count(0):
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
                    try:
                        connected = client.client.connect(
                            host["addr"], int(host["port"]),
                            time_out=min(remaining, 1.0),
                        )
                    except ConnectionError:
                        connected = False
                    if connected:
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
        # After server selection, failed requests must not become empty data.
        client.client.raise_exception = True
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
            begin = pd.Timestamp(start_date).normalize()
            end = pd.Timestamp(end_date).normalize()
            if begin > end:
                raise ValueError("start_date must not be after end_date")

            pages = []
            offset = 0
            previous_oldest = None
            while True:
                page = self._client.bars(
                    symbol=code, frequency=FREQ_DAILY, start=offset, offset=800,
                )
                if page is None or page.empty:
                    break
                page = page.copy()
                if "datetime" in page.columns:
                    dates = page["datetime"]
                elif "date" in page.columns:
                    dates = page["date"]
                elif isinstance(page.index, pd.DatetimeIndex):
                    dates = page.index.to_series()
                else:
                    raise ValueError("Mootdx daily bars have no timestamps")
                page["date"] = pd.to_datetime(dates).dt.normalize()
                if page["date"].isna().any() or page["date"].duplicated().any():
                    raise ValueError("Invalid or duplicate Mootdx daily timestamps")
                oldest = page["date"].min()
                if previous_oldest is not None and oldest >= previous_oldest:
                    raise ValueError("Mootdx historical pagination did not advance")
                pages.append(page)
                if oldest <= begin or len(page) < 800:
                    break
                previous_oldest = oldest
                offset += len(page)

            if not pages:
                return pd.DataFrame()
            df = pd.concat(pages, ignore_index=True)
            df = df.drop_duplicates("date").sort_values("date")

            # Apply vendor adjustment to the assembled history, not one 800-bar page.
            if adjust:
                from mootdx.utils.adjust import to_adjust

                df = df.set_index("date", drop=False)
                df["datetime"] = df.index
                original_dates = df.index
                df = to_adjust(df, symbol=code, adjust=adjust).reindex(original_dates)
                df["date"] = df.index

            # tdxpy already decodes ETF/LOF prices into currency units.
            if "volume" in df.columns and "vol" in df.columns:
                df = df.drop(columns=["vol"])
            df = df.rename(columns={"vol": "volume"})
            # Category 9 uses 100-share/ETF-unit lots; other daily sources use units.
            df["volume"] = pd.to_numeric(df["volume"]) * 100
            df = df.drop(columns=["datetime"], errors="ignore")
            df = df[(df["date"] >= begin) & (df["date"] <= end)]
            df = df.reset_index(drop=True)

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

            if "volume" in df.columns and "vol" in df.columns:
                df = df.drop(columns=["vol"])
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

        if not symbols:
            return pd.DataFrame()
        securities = [
            (get_mootdx_market(s), convert_from_ptrade_code(s, "mootdx"))
            for s in symbols
        ]
        requested = dict(zip(securities, symbols))

        try:
            rows = []
            for start in range(0, len(securities), 80):
                rows.extend(self._client.client.get_security_quotes(securities[start:start + 80]) or [])
            df = pd.DataFrame(rows)

            if df is None or df.empty:
                return pd.DataFrame()

            # Identify returned securities, even when the server skips/reorders rows.
            df["ptrade_code"] = [requested.get((row.market, row.code)) for row in df.itertuples()]
            df = df.dropna(subset=["ptrade_code"])
            if "vol" in df.columns:
                df["volume"] = df["vol"]

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
            DataFrame with TDX financial-summary fields (not the FINVALUE array)
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

    @retry(config=_MOOTDX_RETRY)
    def fetch_blocks(self, filename: str = "block.dat") -> pd.DataFrame:
        """Current TDX memberships; not historical constituents or XBHS codes."""
        from simtradedata.fetchers.tdx_blocks import parse_blocks

        if filename not in {"block.dat", "block_zs.dat", "block_fg.dat"}:
            raise ValueError("Unsupported TDX block file")
        if not self._ensure_source_available():
            return pd.DataFrame()
        self._ensure_client()
        api = self._client.client
        meta = api.get_block_info_meta(filename)
        if not meta:
            return pd.DataFrame()
        size = meta["size"]
        data = bytearray()
        while len(data) < size:
            piece = api.get_block_info(filename, len(data), min(60000, size - len(data)))
            if not piece:
                raise ConnectionError("Incomplete TDX block download")
            data.extend(piece[:size - len(data)])
        return parse_blocks(data)

    @retry(config=_MOOTDX_RETRY)
    def fetch_history_transactions(self, symbol: str, date: str, start=0, offset=800) -> pd.DataFrame:
        """Raw ordinary trades: HH:MM and vendor direction, not Level-2."""
        if start < 0 or not 1 <= offset <= 2000:
            raise ValueError("Invalid transaction page")
        if not self._ensure_source_available():
            return pd.DataFrame()
        self._ensure_client()
        code = convert_from_ptrade_code(symbol, "mootdx")
        date = pd.Timestamp(date).strftime("%Y%m%d")
        result = self._client.client.get_history_transaction_data(
            get_mootdx_market(symbol), code, start, offset, int(date),
        )
        return pd.DataFrame(result)

    @retry(config=_MOOTDX_RETRY)
    def fetch_history_minutes(self, symbol: str, date: str) -> pd.DataFrame:
        """Raw historical intraday price/volume curve, not minute OHLC bars."""
        if not self._ensure_source_available():
            return pd.DataFrame()
        self._ensure_client()
        code = convert_from_ptrade_code(symbol, "mootdx")
        result = self._client.client.get_history_minute_time_data(
            get_mootdx_market(symbol), code, int(pd.Timestamp(date).strftime("%Y%m%d")),
        )
        return pd.DataFrame(result)

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
            return pd.DataFrame(df) if df is not None else pd.DataFrame()
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
