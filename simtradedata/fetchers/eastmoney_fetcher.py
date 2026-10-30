"""EastMoney HTTP data fetcher implementation.

This module provides data fetching from EastMoney's public HTTP APIs,
including daily K-line bars, money flow, Dragon Tiger Board (LHB),
and margin trading data.
"""

import logging
import time
from typing import Optional

import pandas as pd
import requests

from simtradedata.fetchers.base_fetcher import BaseFetcher
from simtradedata.resilience.retry import RetryConfig, retry

logger = logging.getLogger(__name__)

_EASTMONEY_RETRY = RetryConfig(max_retries=3, base_delay=2.0)

# Minimum interval between HTTP requests in seconds.
_MIN_REQUEST_INTERVAL = 0.3

# Market suffix to EastMoney secid prefix mapping.
_MARKET_MAP = {
    "SZ": "0",
    "SS": "1",
    "BJ": "0",
}

# Rotating User-Agent pool to reduce request fingerprinting.
_USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
]


class EastMoneyFetcher(BaseFetcher):
    """Fetch data from EastMoney public HTTP APIs.

    Provides access to:
    - Daily K-line bars (unadjusted)
    - Money flow (main/super/large/medium/small net amounts)
    - Dragon Tiger Board (LHB) records
    - Margin trading data (rzye/rqyl/rzrqye)
    """

    source_name = "eastmoney"

    def __init__(self, timeout: int = 15):
        """Initialize EastMoneyFetcher.

        Args:
            timeout: HTTP request timeout in seconds.
        """
        super().__init__()
        self._timeout = timeout
        self._session: Optional[requests.Session] = None
        self._last_request_time: float = 0.0
        self._ua_index: int = 0

    def _do_login(self):
        """Create a persistent requests.Session with base headers."""
        self._session = requests.Session()
        self._session.headers.update({
            "Referer": "https://quote.eastmoney.com",
        })
        logger.info("EastMoney HTTP session created")

    def _do_logout(self):
        """Close the requests.Session."""
        if self._session is not None:
            self._session.close()
            self._session = None

    def _ensure_session(self):
        """Ensure the HTTP session is available."""
        if self._session is None:
            self.login()

    def _rate_limit(self):
        """Enforce minimum interval between requests."""
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < _MIN_REQUEST_INTERVAL:
            time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
        self._last_request_time = time.monotonic()

    def _next_user_agent(self) -> str:
        """Return the next User-Agent from the rotation pool."""
        ua = _USER_AGENTS[self._ua_index % len(_USER_AGENTS)]
        self._ua_index += 1
        return ua

    def _get(self, url: str, params: dict) -> dict:
        """Perform a rate-limited GET request with rotating User-Agent.

        Args:
            url: Target URL.
            params: Query parameters.

        Returns:
            Parsed JSON response as dict.

        Raises:
            ConnectionError: If the HTTP status code is not 200.
        """
        self._ensure_session()
        self._rate_limit()
        self._session.headers["User-Agent"] = self._next_user_agent()

        resp = self._session.get(url, params=params, timeout=self._timeout)
        if resp.status_code != 200:
            raise ConnectionError(
                f"EastMoney API returned HTTP {resp.status_code}"
            )
        return resp.json()

    # -- secid conversion --

    @staticmethod
    def to_secid(ptrade_code: str) -> str:
        """Convert PTrade code to EastMoney secid format.

        Args:
            ptrade_code: Code in PTrade format, e.g. '000001.SZ', '600000.SS'.

        Returns:
            EastMoney secid, e.g. '0.000001', '1.600000'.

        Raises:
            ValueError: If the market suffix is not recognized.
        """
        code, market = ptrade_code.split(".")
        prefix = _MARKET_MAP.get(market)
        if prefix is None:
            raise ValueError(f"Unknown market suffix: {market}")
        return f"{prefix}.{code}"

    # -- parsers --

    @staticmethod
    def parse_klines(klines: list) -> pd.DataFrame:
        """Parse EastMoney kline CSV strings into a DataFrame.

        Each string has the format:
            date,open,close,high,low,volume,amount,amplitude

        Args:
            klines: List of CSV-formatted kline strings.

        Returns:
            DataFrame with columns: date, open, close, high, low,
            volume, amount, amplitude.
        """
        if not klines:
            return pd.DataFrame()

        columns = [
            "date", "open", "close", "high", "low",
            "volume", "amount", "amplitude",
        ]
        rows = [line.split(",") for line in klines]
        df = pd.DataFrame(rows, columns=columns)

        for col in columns[1:]:
            df[col] = pd.to_numeric(df[col])

        return df

    @staticmethod
    def parse_money_flow(klines: list) -> pd.DataFrame:
        """Parse EastMoney money flow CSV strings into a DataFrame.

        Each string has the format:
            date,net_main,net_super,net_large,net_medium,net_small

        Args:
            klines: List of CSV-formatted money flow strings.

        Returns:
            DataFrame with columns: date, net_main, net_super,
            net_large, net_medium, net_small.
        """
        if not klines:
            return pd.DataFrame()

        columns = [
            "date", "net_main", "net_super",
            "net_large", "net_medium", "net_small",
        ]
        rows = [line.split(",") for line in klines]
        df = pd.DataFrame(rows, columns=columns)

        for col in columns[1:]:
            df[col] = pd.to_numeric(df[col])

        return df

    @staticmethod
    def parse_lhb(records: list) -> pd.DataFrame:
        """Parse EastMoney LHB (Dragon Tiger Board) records.

        Args:
            records: List of dicts from the datacenter-web API response.

        Returns:
            DataFrame with columns: symbol, date, reason,
            net_buy, buy_amount, sell_amount.
        """
        if not records:
            return pd.DataFrame()

        rows = []
        for rec in records:
            trade_date = rec["TRADE_DATE"]
            # Truncate timestamp portion if present (e.g. 2024-01-02T00:00:00.000)
            if "T" in trade_date:
                trade_date = trade_date.split("T")[0]

            rows.append({
                "symbol": rec["SECUCODE"],
                "date": trade_date,
                "reason": rec["EXPLAIN"],
                "net_buy": rec["BILLBOARD_NET_AMT"],
                "buy_amount": rec["BILLBOARD_BUY_AMT"],
                "sell_amount": rec["BILLBOARD_SELL_AMT"],
            })

        return pd.DataFrame(rows)

    @staticmethod
    def parse_dividends(records: list) -> pd.DataFrame:
        """Parse EastMoney share bonus detail records into dividend events.

        Args:
            records: List of dicts from RPT_SHAREBONUS_DET API response.

        Returns:
            DataFrame with columns: date, bonus_ps, allotted_ps, rationed_ps,
            rationed_px, dividend.
            bonus_ps = PRETAX_BONUS_RMB / 10 (yuan per share)
            allotted_ps = (BONUS_RATIO + IT_RATIO) / 10 (shares per share)
            rationed_ps/rationed_px = 0（东财此接口不提供配股）
            dividend = bonus_ps（兼容字段）
        """
        if not records:
            return pd.DataFrame()

        rows = []
        for rec in records:
            ex_date = rec.get("EX_DIVIDEND_DATE")
            if not ex_date:
                continue
            # Truncate timestamp portion: "2024-01-02 00:00:00" or
            # "2024-01-02T00:00:00.000"
            ex_date = ex_date[:10]

            pretax = rec.get("PRETAX_BONUS_RMB") or 0.0
            bonus_ratio = rec.get("BONUS_RATIO") or 0.0
            it_ratio = rec.get("IT_RATIO") or 0.0

            rows.append({
                "date": ex_date,
                "bonus_ps": pretax / 10.0,
                "allotted_ps": (bonus_ratio + it_ratio) / 10.0,
                "rationed_ps": 0.0,
                "rationed_px": 0.0,
                "dividend": pretax / 10.0,
            })

        if not rows:
            return pd.DataFrame()

        return pd.DataFrame(rows)

    @staticmethod
    def parse_margin(records: list) -> pd.DataFrame:
        """Parse EastMoney margin trading records.

        Args:
            records: List of dicts from the datacenter-web API response.

        Returns:
            DataFrame with columns: symbol, date, rzye (financing balance),
            rqyl (securities lending balance), rzrqye (total margin balance).
        """
        if not records:
            return pd.DataFrame()

        rows = []
        for rec in records:
            stats_date = rec.get("STATISTICS_DATE") or rec["DATE"]
            if "T" in stats_date:
                stats_date = stats_date.split("T")[0]
            else:
                stats_date = stats_date[:10]

            rows.append({
                "symbol": rec["SECUCODE"],
                "date": stats_date,
                "rzye": rec.get("FIN_BALANCE", rec.get("RZYE")),
                "rqyl": rec.get("LOAN_BALANCE", rec.get("RQYL")),
                "rzrqye": rec.get("MARGIN_BALANCE", rec.get("RZRQYE")),
            })

        return pd.DataFrame(rows)

    # -- API methods --

    @retry(config=_EASTMONEY_RETRY)
    def fetch_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """Fetch daily K-line bars for a symbol.

        Args:
            symbol: Stock code in PTrade format (e.g. '600000.SS').
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).

        Returns:
            DataFrame with columns: date, open, close, high, low,
            volume, amount, amplitude.
        """
        if not self._ensure_source_available():
            return pd.DataFrame()
        secid = self.to_secid(symbol)
        beg = start_date.replace("-", "")
        end = end_date.replace("-", "")

        url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        params = {
            "secid": secid,
            "klt": "101",
            "fqt": "0",
            "beg": beg,
            "end": end,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        }

        data = self._get(url, params)

        klines_data = (data.get("data") or {}).get("klines")
        if not klines_data:
            logger.debug("No daily bars for %s", symbol)
            return pd.DataFrame()

        df = self.parse_klines(klines_data)
        logger.info("Fetched %d daily bars for %s", len(df), symbol)
        return df

    @retry(config=_EASTMONEY_RETRY)
    def fetch_money_flow(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """Fetch daily money flow data for a symbol.

        Args:
            symbol: Stock code in PTrade format (e.g. '600000.SS').
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).

        Returns:
            DataFrame with columns: date, net_main, net_super,
            net_large, net_medium, net_small.
        """
        if not self._ensure_source_available():
            return pd.DataFrame()
        secid = self.to_secid(symbol)
        beg = start_date.replace("-", "")
        end = end_date.replace("-", "")

        url = (
            "https://push2his.eastmoney.com"
            "/api/qt/stock/fflow/daykline/get"
        )
        params = {
            "secid": secid,
            "lmt": "0",
            "klt": "101",
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
            "beg": beg,
            "end": end,
        }

        data = self._get(url, params)

        klines_data = (data.get("data") or {}).get("klines")
        if not klines_data:
            logger.debug("No money flow data for %s", symbol)
            return pd.DataFrame()

        df = self.parse_money_flow(klines_data)
        logger.info("Fetched %d money flow records for %s", len(df), symbol)
        return df

    @retry(config=_EASTMONEY_RETRY)
    def fetch_lhb(
        self,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """Fetch Dragon Tiger Board (LHB) records for a date range.

        Args:
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).

        Returns:
            DataFrame with columns: symbol, date, reason,
            net_buy, buy_amount, sell_amount.
        """
        if not self._ensure_source_available():
            return pd.DataFrame()
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = {
            "reportName": "RPT_DAILYBILLBOARD_DETAILSNEW",
            "columns": (
                "SECUCODE,TRADE_DATE,EXPLAIN,"
                "BILLBOARD_NET_AMT,BILLBOARD_BUY_AMT,BILLBOARD_SELL_AMT"
            ),
            "filter": (
                f"(TRADE_DATE>='{start_date}')"
                f"(TRADE_DATE<='{end_date}')"
            ),
            "pageNumber": "1",
            "pageSize": "500",
            "sortTypes": "-1",
            "sortColumns": "TRADE_DATE",
            "source": "WEB",
            "client": "WEB",
        }

        data = self._get(url, params)

        records = (data.get("result") or {}).get("data")
        if not records:
            logger.debug(
                "No LHB data for %s to %s", start_date, end_date,
            )
            return pd.DataFrame()

        df = self.parse_lhb(records)
        logger.info(
            "Fetched %d LHB records for %s to %s",
            len(df), start_date, end_date,
        )
        return df

    @retry(config=_EASTMONEY_RETRY)
    def fetch_dividends(self, symbol: str) -> pd.DataFrame:
        """Fetch dividend/bonus share events from EastMoney RPT_SHAREBONUS_DET.

        Returns all implemented (has EX_DIVIDEND_DATE) dividend events for a
        stock, including cash dividends and bonus/transferred shares.

        Args:
            symbol: Stock code in PTrade format (e.g. '000001.SZ').

        Returns:
            DataFrame with columns: date, bonus_ps, allotted_ps.
        """
        if not self._ensure_source_available():
            return pd.DataFrame()
        code, _ = symbol.split(".")

        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = {
            "reportName": "RPT_SHAREBONUS_DET",
            "columns": (
                "SECURITY_CODE,EX_DIVIDEND_DATE,"
                "PRETAX_BONUS_RMB,BONUS_RATIO,IT_RATIO"
            ),
            "filter": f"(SECURITY_CODE=\"{code}\")",
            "pageNumber": "1",
            "pageSize": "500",
            "sortTypes": "1",
            "sortColumns": "EX_DIVIDEND_DATE",
            "source": "WEB",
            "client": "WEB",
        }

        data = self._get(url, params)

        records = (data.get("result") or {}).get("data")
        if not records:
            logger.debug("No dividend data for %s", symbol)
            return pd.DataFrame()

        df = self.parse_dividends(records)
        logger.info("Fetched %d dividend events for %s", len(df), symbol)
        return df

    @retry(config=_EASTMONEY_RETRY)
    def fetch_margin(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """Fetch margin trading data for a symbol.

        EastMoney's datacenter-web uses '.SH' suffix instead of '.SS'
        for Shanghai stocks, so the symbol is converted accordingly.

        Args:
            symbol: Stock code in PTrade format (e.g. '600000.SS').
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).

        Returns:
            DataFrame with columns: symbol, date, rzye (financing balance),
            rqyl (securities lending balance), rzrqye (total margin balance).
        """
        code = symbol.split(".", 1)[0]

        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        page_size = 500
        page = 1
        records = []

        while True:
            params = {
                "reportName": "RPTA_WEB_RZRQ_GGMX",
                "columns": "DATE,SECUCODE,RZYE,RQYL,RZRQYE",
                "filter": f"(SCODE=\"{code}\")",
                "pageNumber": str(page),
                "pageSize": str(page_size),
                "sortTypes": "-1",
                "sortColumns": "DATE",
                "source": "WEB",
                "client": "WEB",
            }
            data = self._get(url, params)

            result = data.get("result") or {}
            page_records = result.get("data") or []
            if not page_records:
                break
            records.extend(page_records)

            dates = [rec["DATE"][:10] for rec in page_records if rec.get("DATE")]
            if dates and min(dates) < start_date:
                break
            if page >= result.get("pages", page):
                break
            page += 1

        if not records:
            logger.debug("No margin data for %s", symbol)
            return pd.DataFrame()

        df = self.parse_margin(records)
        df = df[(df["date"] >= start_date) & (df["date"] <= end_date)]
        logger.info("Fetched %d margin records for %s", len(df), symbol)
        return df
