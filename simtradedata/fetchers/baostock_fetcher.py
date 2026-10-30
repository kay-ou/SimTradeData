"""
BaoStock data fetcher implementation
"""

import logging
import threading
from datetime import datetime

import baostock as bs
import pandas as pd

from simtradedata.fetchers.base_fetcher import BaseFetcher
from simtradedata.resilience.retry import RetryConfig, retry
from simtradedata.utils.code_utils import convert_from_ptrade_code

logger = logging.getLogger(__name__)

_BAOSTOCK_RETRY = RetryConfig(max_retries=2, base_delay=1.0)


class BaoStockFetcher(BaseFetcher):
    """
    Fetch data from BaoStock API

    BaoStock provides free A-share market data including:
    - Daily K-line data
    - Financial statements
    - Valuation indicators
    - Adjust factors
    - Dividend data
    """

    source_name = "baostock"

    # Class-level login state tracking (BaoStock uses global session)
    _bs_logged_in = False
    _bs_login_count = 0
    _bs_lock = threading.Lock()

    @retry(config=_BAOSTOCK_RETRY)
    def _do_login(self):
        """BaoStock-specific login implementation"""
        with BaoStockFetcher._bs_lock:
            # BaoStock uses a global session, only login once
            if not BaoStockFetcher._bs_logged_in:
                lg = bs.login()
                if lg.error_code != "0":
                    raise ConnectionError(f"BaoStock login failed: {lg.error_msg}")
                BaoStockFetcher._bs_logged_in = True
                logger.info("BaoStock login successful")
            BaoStockFetcher._bs_login_count += 1

    @classmethod
    @retry(config=_BAOSTOCK_RETRY)
    def _ensure_login(cls):
        """Ensure BaoStock session is valid, re-login if needed"""
        with cls._bs_lock:
            if not cls._bs_logged_in:
                lg = bs.login()
                if lg.error_code != "0":
                    raise ConnectionError(f"BaoStock re-login failed: {lg.error_msg}")
                cls._bs_logged_in = True
                logger.info("BaoStock re-login successful")

    def _do_logout(self):
        """BaoStock-specific logout implementation"""
        with BaoStockFetcher._bs_lock:
            BaoStockFetcher._bs_login_count -= 1
            # Only logout when last fetcher disconnects
            if BaoStockFetcher._bs_login_count <= 0:
                bs.logout()
                BaoStockFetcher._bs_logged_in = False
                BaoStockFetcher._bs_login_count = 0


    @retry(config=_BAOSTOCK_RETRY)
    def fetch_adjust_factor(
        self, symbol: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """
        Fetch adjust factors

        Args:
            symbol: Stock code in PTrade format
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)

        Returns:
            DataFrame with columns: date, foreAdjustFactor, backAdjustFactor
        """

        bs_code = convert_from_ptrade_code(symbol, "baostock")

        rs = bs.query_adjust_factor(
            code=bs_code, start_date=start_date, end_date=end_date
        )

        if rs.error_code != "0":
            raise RuntimeError(
                f"Failed to query adjust factor for {symbol}: {rs.error_msg}"
            )

        df = rs.get_data()

        if df.empty:
            # Check if it's an index (indices don't have adjust factors)
            if bs_code.startswith("sh.") and bs_code[3:].startswith("00"):
                logger.debug(f"No adjust factor data for index {symbol} (expected)")
            elif bs_code.startswith("sz.399"):  # Shenzhen indices
                logger.debug(f"No adjust factor data for index {symbol} (expected)")
            else:
                logger.warning(f"No adjust factor data for {symbol}")
            return pd.DataFrame()

        # Note: BaoStock returns 'dividOperateDate', not 'date'
        df = df.rename(columns={"dividOperateDate": "date"})
        df["date"] = pd.to_datetime(df["date"])

        # Convert adjust factors to numeric
        df["foreAdjustFactor"] = pd.to_numeric(df["foreAdjustFactor"], errors="coerce")
        df["backAdjustFactor"] = pd.to_numeric(df["backAdjustFactor"], errors="coerce")

        # Log warning if NaN values were introduced
        nan_count = df["backAdjustFactor"].isna().sum()
        if nan_count > 0:
            logger.warning(
                f"{symbol}: {nan_count}/{len(df)} adjust factors are invalid/NaN"
            )

        logger.info(f"Fetched {len(df)} adjust factor rows for {symbol}")

        return df


    @retry(config=_BAOSTOCK_RETRY)
    def fetch_stock_basic(self, symbol: str) -> pd.DataFrame:
        """
        Fetch stock basic information

        Args:
            symbol: Stock code in PTrade format

        Returns:
            DataFrame with basic stock information
        """

        bs_code = convert_from_ptrade_code(symbol, "baostock")
        rs = bs.query_stock_basic(code=bs_code)

        if rs.error_code != "0":
            raise RuntimeError(
                f"Failed to query stock basic info for {symbol}: {rs.error_msg}"
            )

        df = rs.get_data()

        if df.empty:
            return pd.DataFrame()

        return df


    @retry(config=_BAOSTOCK_RETRY)
    def fetch_stock_industry(self, symbol: str, date: str = None) -> pd.DataFrame:
        """
        Fetch stock industry classification

        Args:
            symbol: Stock code in PTrade format
            date: Date string (YYYY-MM-DD), if None use today

        Returns:
            DataFrame with industry classification
        """
        bs_code = convert_from_ptrade_code(symbol, "baostock")
        date_str = date or datetime.now().strftime("%Y-%m-%d")

        rs = bs.query_stock_industry(code=bs_code, date=date_str)

        if rs.error_code != "0":
            raise RuntimeError(f"Failed to query industry for {symbol}: {rs.error_msg}")

        df = rs.get_data()

        if df.empty:
            logger.warning(f"No industry data for {symbol}")
            return pd.DataFrame()

        return df

    @retry(config=_BAOSTOCK_RETRY)
    def fetch_trade_calendar(self, start_date: str, end_date: str) -> pd.DataFrame:
        """
        Fetch trading calendar

        Args:
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)

        Returns:
            DataFrame with trading days
        """

        rs = bs.query_trade_dates(start_date=start_date, end_date=end_date)

        if rs.error_code != "0":
            raise RuntimeError(f"Failed to query trade calendar: {rs.error_msg}")

        df = rs.get_data()

        if df.empty:
            return pd.DataFrame()

        return df

    @retry(config=_BAOSTOCK_RETRY)
    def fetch_index_stocks(self, index_code: str, date: str = None) -> pd.DataFrame:
        """
        Fetch index constituent stocks

        Args:
            index_code: Index code in PTrade format (e.g., '000016.SS', '000300.SS', '000905.SS')
            date: Date string (YYYY-MM-DD), if None use latest

        Returns:
            DataFrame with stock codes

        Note:
            BaoStock only supports specific indices:
            - 000016.SS (上证50): query_sz50_stocks
            - 000300.SS (沪深300): query_hs300_stocks
            - 000905.SS (中证500): query_zz500_stocks
        """
        query_date = date or datetime.now().strftime("%Y-%m-%d")

        # Map PTrade index codes to BaoStock query functions
        index_query_map = {
            "000016.SS": bs.query_sz50_stocks,
            "000300.SS": bs.query_hs300_stocks,
            "000905.SS": bs.query_zz500_stocks,
        }

        query_func = index_query_map.get(index_code)
        if query_func is None:
            logger.warning(f"Index {index_code} not supported by BaoStock")
            return pd.DataFrame()

        rs = query_func(date=query_date)

        if rs.error_code != "0":
            raise RuntimeError(
                f"Failed to query index stocks for {index_code}: {rs.error_msg}"
            )

        df = rs.get_data()

        if df.empty:
            logger.warning(f"No constituent stocks found for {index_code}")
            return pd.DataFrame()

        return df

    @retry(config=_BAOSTOCK_RETRY)
    def fetch_quarterly_fundamentals(
        self, symbol: str, year: int, quarter: int
    ) -> pd.DataFrame:
        """
        Fetch all quarterly fundamentals for a stock

        Combines data from 5 BaoStock APIs:
        - query_profit_data (盈利能力)
        - query_growth_data (成长能力)
        - query_balance_data (偿债能力)
        - query_operation_data (营运能力)
        - query_cash_flow_data (现金流量)

        Args:
            symbol: Stock code in PTrade format
            year: Year (e.g., 2024)
            quarter: Quarter (1-4)

        Returns:
            DataFrame with PTrade format fields including publ_date and end_date
        """
        bs_code = convert_from_ptrade_code(symbol, "baostock")

        # Define all API calls
        api_calls = [
            bs.query_profit_data,
            bs.query_growth_data,
            bs.query_balance_data,
            bs.query_operation_data,
            bs.query_cash_flow_data,
        ]

        # Fetch from all APIs
        dfs = []
        for api_func in api_calls:
            rs = api_func(code=bs_code, year=year, quarter=quarter)
            if rs.error_code == "0":
                df = rs.get_data()
                if not df.empty:
                    dfs.append(df)

        if not dfs:
            logger.debug(f"No fundamentals data for {symbol} {year}Q{quarter}")
            return pd.DataFrame()

        # Merge all dataframes on common keys
        result = dfs[0]
        merge_keys = ['code', 'pubDate', 'statDate']

        for df in dfs[1:]:
            result = result.merge(df, on=merge_keys, how='outer', suffixes=('', '_dup'))
            # Remove duplicate columns
            result = result.loc[:, ~result.columns.str.endswith('_dup')]
        
        # Map to PTrade format
        field_mapping = {
            # Date fields (CRITICAL!)
            'pubDate': 'publ_date',  # 公告日期 - 最重要！
            'statDate': 'end_date',  # 统计日期（季度结束日）
            
            # Profitability
            'roeAvg': 'roe',
            'roa': 'roa',
            'npMargin': 'net_profit_ratio',
            'gpMargin': 'gross_income_ratio',
            
            # Growth
            'YOYORev': 'operating_revenue_grow_rate',
            'YOYNI': 'net_profit_grow_rate',
            'YOYAsset': 'total_asset_grow_rate',
            'YOYEPSBasic': 'basic_eps_yoy',
            'YOYPNI': 'np_parent_company_yoy',
            
            # Solvency
            'currentRatio': 'current_ratio',
            'quickRatio': 'quick_ratio',
            'liabilityToAsset': 'debt_equity_ratio',
            
            # Operating
            'NRTurnRatio': 'accounts_receivables_turnover_rate',
            'INVTurnRatio': 'inventory_turnover_rate',
            'CATurnRatio': 'current_assets_turnover_rate',
            'AssetTurnRatio': 'total_asset_turnover_rate',
            
            # Cash flow
            'ebitToInterest': 'interest_cover',

            # Share data (from profit data, needed for valuation)
            'totalShare': 'total_shares',
            'liqaShare': 'a_floats',
        }
        
        # Rename columns
        result = result.rename(columns=field_mapping)
        
        # Convert date fields with error handling
        if "publ_date" in result.columns:
            result["publ_date"] = pd.to_datetime(result["publ_date"], errors="coerce")

        if "end_date" in result.columns:
            result["end_date"] = pd.to_datetime(result["end_date"], errors="coerce")
            # Drop rows with invalid end_date (required for index)
            result = result.dropna(subset=["end_date"])
        # Convert numeric fields
        numeric_fields = [
            'roe', 'roa', 'net_profit_ratio', 'gross_income_ratio',
            'operating_revenue_grow_rate', 'net_profit_grow_rate',
            'total_asset_grow_rate', 'basic_eps_yoy', 'np_parent_company_yoy',
            'current_ratio', 'quick_ratio', 'debt_equity_ratio',
            'accounts_receivables_turnover_rate', 'inventory_turnover_rate',
            'current_assets_turnover_rate', 'total_asset_turnover_rate',
            'interest_cover', 'total_shares', 'a_floats'
        ]
        
        for field in numeric_fields:
            if field in result.columns:
                result[field] = pd.to_numeric(result[field], errors='coerce')
        
        logger.info(f"Fetched fundamentals for {symbol} {year}Q{quarter}: {len(result)} rows")
        return result

    @retry(config=_BAOSTOCK_RETRY)
    def fetch_dividend_data(
        self, symbol: str, year: int, year_type: str = "operate"
    ) -> pd.DataFrame:
        """
        Fetch dividend (ex-rights and ex-dividend) data for a stock

        Args:
            symbol: Stock code in PTrade format (e.g., '600000.SH')
            year: Year to query (e.g., 2024)
            year_type: "report" for announcement year, "operate" for ex-date year

        Returns:
            DataFrame with columns mapped to PTrade format:
            - date: ex-dividend date (dividOperateDate)
            - allotted_ps: allotted shares per share (not provided by BaoStock, set to 0)
            - rationed_ps: rationed shares per share (dividReserveToStockPs)
            - rationed_px: rationed price (not provided by BaoStock, set to 0)
            - bonus_ps: bonus shares per share (dividStocksPs)
            - dividend: cash dividend per share before tax (dividCashPsBeforeTax)
        """
        bs_code = convert_from_ptrade_code(symbol, "baostock")

        rs = bs.query_dividend_data(
            code=bs_code, year=str(year), yearType=year_type
        )

        if rs.error_code != "0":
            raise RuntimeError(
                f"Failed to query dividend data for {symbol} year {year}: {rs.error_msg}"
            )

        # Use get_data() instead of iterating with next()
        df = rs.get_data()

        if df.empty:
            logger.debug(f"No dividend data for {symbol} year {year}")
            return pd.DataFrame()

        # Filter only records with valid ex-dividend date
        df = df[df["dividOperateDate"].notna() & (df["dividOperateDate"] != "")]

        if df.empty:
            logger.debug(f"No valid dividend records for {symbol} year {year}")
            return pd.DataFrame()

        # Map to PTrade format
        result = pd.DataFrame()
        result["date"] = pd.to_datetime(df["dividOperateDate"])

        # allotted_ps: 送股 + 资本公积转增（PTrade 口径统一归入送股）
        result["allotted_ps"] = (
            pd.to_numeric(df["dividStocksPs"], errors="coerce").fillna(0.0)
            + pd.to_numeric(df["dividReserveToStockPs"], errors="coerce").fillna(0.0)
        )

        # rationed_ps/rationed_px: BaoStock 不提供配股，置 0
        result["rationed_ps"] = 0.0
        result["rationed_px"] = 0.0

        # bonus_ps: cash dividend per share (used in adj factor formula)
        result["bonus_ps"] = pd.to_numeric(
            df["dividCashPsBeforeTax"], errors="coerce"
        ).fillna(0.0)

        # dividend: same as bonus_ps, kept for record
        result["dividend"] = result["bonus_ps"]

        logger.info(f"Fetched {len(result)} dividend records for {symbol} year {year}")
        return result

    def fetch_dividend_data_range(
        self, symbol: str, start_year: int, end_year: int
    ) -> pd.DataFrame:
        """
        Fetch dividend data for a range of years

        Args:
            symbol: Stock code in PTrade format
            start_year: Start year (inclusive)
            end_year: End year (inclusive)

        Returns:
            DataFrame with all dividend records in the year range
        """
        dfs = []
        for year in range(start_year, end_year + 1):
            try:
                df = self.fetch_dividend_data(symbol, year, year_type="operate")
                if not df.empty:
                    dfs.append(df)
            except Exception as e:
                logger.warning(f"Failed to fetch dividend for {symbol} year {year}: {e}")

        if not dfs:
            return pd.DataFrame()

        result = pd.concat(dfs, ignore_index=True)
        result = result.drop_duplicates(subset=["date"]).sort_values("date")

        logger.info(
            f"Fetched {len(result)} total dividend records for {symbol} "
            f"({start_year}-{end_year})"
        )
        return result
