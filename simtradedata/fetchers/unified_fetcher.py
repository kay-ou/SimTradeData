"""
Unified data fetcher for efficient BaoStock data download

This module provides a unified interface to fetch multiple data types
in a single API call, reducing redundant queries.
"""

import logging
import platform
import time

import baostock as bs
import pandas as pd

from simtradedata.fetchers.baostock_fetcher import BaoStockFetcher, read_baostock_result
from simtradedata.utils.code_utils import convert_from_ptrade_code
from simtradedata.config.field_mappings import MARKET_FIELD_MAP

logger = logging.getLogger(__name__)

# Retryable BaoStock server errors (Chinese messages from baostock server)
_RETRYABLE_ERRORS = ["接收数据异常", "网络异常", "超时"]

# Max retries for transient errors
MAX_API_RETRIES = 3
RETRY_BASE_DELAY = 5  # seconds, exponential backoff: 5, 10, 20

# Check if signal.alarm is available (Unix-like systems only)
IS_POSIX = platform.system() != 'Windows'

if IS_POSIX:
    import signal
else:
    # For Windows, we'll use threading-based timeout
    import threading


# All fields that can be fetched from query_history_k_data_plus in one call
UNIFIED_DAILY_FIELDS = [
    # === Market data ===
    "date", "open", "high", "low", "close", "preclose", "volume", "amount",

    # === Valuation data ===
    "peTTM",      # PE ratio TTM
    "pbMRQ",      # PB ratio
    "psTTM",      # PS ratio TTM
    "pcfNcfTTM",  # PCF ratio TTM
    "turn",       # Turnover rate

    # === Status data (for building stock_status_history) ===
    "isST",       # ST status (1=ST, 0=normal)
    "tradestatus" # Trading status (1=normal, 0=halted)
]


def _run_with_timeout(func, timeout_seconds, error_message):
    """
    Run a function with timeout protection (cross-platform)

    Args:
        func: Function to execute
        timeout_seconds: Timeout in seconds
        error_message: Error message for TimeoutError

    Returns:
        Result of func()

    Raises:
        TimeoutError: If execution exceeds timeout
    """
    if IS_POSIX:
        # Unix-like systems: use signal.alarm
        def timeout_handler(signum, frame):
            raise TimeoutError(error_message)

        old_handler = signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(timeout_seconds)

        try:
            result = func()
            signal.alarm(0)  # Cancel timeout
            return result
        except TimeoutError:
            signal.alarm(0)
            raise
        finally:
            signal.signal(signal.SIGALRM, old_handler)
    else:
        # Windows: use threading
        result = [None]
        exception = [None]

        def wrapper():
            try:
                result[0] = func()
            except Exception as e:
                exception[0] = e

        thread = threading.Thread(target=wrapper)
        thread.daemon = True
        thread.start()
        thread.join(timeout=timeout_seconds)

        if thread.is_alive():
            # Thread is still running, timeout occurred
            logger.warning(f"Timeout: {error_message}")
            raise TimeoutError(error_message)

        if exception[0]:
            raise exception[0]

        return result[0]


class UnifiedDataFetcher(BaoStockFetcher):
    """
    Fetch all daily data types in a single API call

    This fetcher optimizes BaoStock API usage by fetching market data,
    valuation data, and status data in one query_history_k_data_plus call.

    Inherits from BaoStockFetcher to share the global BaoStock session.
    """

    def fetch_unified_daily_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        frequency: str = "d",
        adjustflag: str = "3"
    ) -> pd.DataFrame:
        """
        Fetch all daily data types in a single API call

        This method fetches market data, valuation data, and status data
        in one query, significantly reducing API calls.

        Args:
            symbol: Stock code in PTrade format (e.g., '600000.SH')
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            frequency: d=daily, w=weekly, m=monthly
            adjustflag: "1"=forward, "2"=backward, "3"=none

        Returns:
            DataFrame with all fields: market + valuation + status
            Columns: date, open, high, low, close, volume, amount,
                    peTTM, pbMRQ, psTTM, pcfNcfTTM, turn, isST, tradestatus
        """
        # Convert to BaoStock format
        bs_code = convert_from_ptrade_code(symbol, "baostock")

        # Build fields string (all fields in one call)
        fields_str = ",".join(UNIFIED_DAILY_FIELDS)

        logger.debug(f"Fetching unified data for {symbol}...")

        with self._resilient_operation() as ok:
            if not ok:
                logger.warning(
                    "Skipping %s: source '%s' is unavailable (cooldown or circuit open)",
                    symbol, self.source_name,
                )
                return pd.DataFrame()

            # Define API call function
            def api_call():
                return bs.query_history_k_data_plus(
                    bs_code,
                    fields_str,
                    start_date=start_date,
                    end_date=end_date,
                    frequency=frequency,
                    adjustflag=adjustflag,
                )

            # Retry loop for transient errors
            last_error = None
            for attempt in range(MAX_API_RETRIES):
                try:
                    rs = _run_with_timeout(
                        api_call, 60,
                        f"BaoStock API timeout for {symbol}"
                    )
                except TimeoutError:
                    last_error = TimeoutError(f"Timeout fetching {symbol}")
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        f"Timeout for {symbol} (attempt {attempt + 1}/{MAX_API_RETRIES}), "
                        f"retrying in {delay}s..."
                    )
                    time.sleep(delay)
                    continue

                # Check for login expiration
                if rs.error_code != "0":
                    if "未登录" in rs.error_msg or "登录" in rs.error_msg:
                        logger.warning("BaoStock session expired, re-login...")
                        BaoStockFetcher._bs_logged_in = False
                        BaoStockFetcher._ensure_login()
                        continue

                    if any(err in rs.error_msg for err in _RETRYABLE_ERRORS):
                        delay = RETRY_BASE_DELAY * (2 ** attempt)
                        logger.warning(
                            f"Server error for {symbol}: {rs.error_msg} "
                            f"(attempt {attempt + 1}/{MAX_API_RETRIES}), "
                            f"retrying in {delay}s..."
                        )
                        last_error = RuntimeError(
                            f"Server error for {symbol}: {rs.error_msg}"
                        )
                        time.sleep(delay)
                        continue

                    raise RuntimeError(
                        f"Failed to query unified data for {symbol}: {rs.error_msg}"
                    )

                # Parse data
                try:
                    df_result = read_baostock_result(rs)
                except UnicodeDecodeError as e:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        f"Encoding error for {symbol}: {e} "
                        f"(attempt {attempt + 1}/{MAX_API_RETRIES}), "
                        f"retrying in {delay}s..."
                    )
                    last_error = e
                    time.sleep(delay)
                    continue

                # Success — context manager records the result
                df = df_result
                break
            else:
                if last_error:
                    logger.error(
                        f"All {MAX_API_RETRIES} attempts failed for {symbol}: {last_error}"
                    )
                    raise last_error
                raise RuntimeError(f"All retries failed for {symbol}")

        # Post-context-manager: process data
        if df.empty:
            logger.info(f"No unified data for {symbol} (may be delisted or no trading)")
            return pd.DataFrame()

        # Convert data types
        df["date"] = pd.to_datetime(df["date"])

        # Convert all numeric columns
        numeric_cols = [c for c in df.columns if c != "date"]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        logger.info(
            f"Fetched unified data for {symbol}: {len(df)} rows, "
            f"{len(df.columns)} fields"
        )

        return df

    def fetch_index_data(
        self,
        index_code: str,
        start_date: str,
        end_date: str,
        frequency: str = "d"
    ) -> pd.DataFrame:
        """
        Fetch index OHLCV data (for benchmark)

        Args:
            index_code: Index code in PTrade format (e.g., '000300.SS' for CSI300)
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            frequency: d=daily, w=weekly, m=monthly

        Returns:
            DataFrame with columns [date, open, high, low, close, volume, amount]
            with date as DatetimeIndex
        """
        bs_code = convert_from_ptrade_code(index_code, "baostock")

        # Fetch basic OHLCV data for index
        fields = "date,open,high,low,close,volume,amount"

        logger.debug(f"Fetching index data for {index_code}...")

        with self._resilient_operation() as ok:
            if not ok:
                logger.warning(
                    "Skipping index %s: source '%s' is unavailable",
                    index_code, self.source_name,
                )
                return pd.DataFrame()

            def api_call():
                return bs.query_history_k_data_plus(
                    bs_code, fields,
                    start_date=start_date, end_date=end_date,
                    frequency=frequency, adjustflag="3",
                )

            rs = _run_with_timeout(
                api_call, 60,
                f"BaoStock API timeout for index {index_code}"
            )

            if rs.error_code != "0":
                raise RuntimeError(
                    f"Failed to query index data for {index_code}: {rs.error_msg}"
                )

            df = read_baostock_result(rs)

        if df.empty:
            logger.info(f"No index data for {index_code} (may be unavailable for date range)")
            return pd.DataFrame()

        # Convert data types
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")

        # Convert numeric columns
        numeric_cols = ["open", "high", "low", "close", "volume", "amount"]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # Rename fields to match PTrade format
        rename_map = {k: v for k, v in MARKET_FIELD_MAP.items() if k in df.columns}
        if rename_map:
            df = df.rename(columns=rename_map)

        logger.info(
            f"Fetched index data for {index_code}: {len(df)} rows"
        )

        return df
