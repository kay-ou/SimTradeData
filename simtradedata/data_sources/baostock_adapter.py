"""
BaoStock数据源适配器

提供BaoStock数据源的统一接口实现。
"""

import logging
from datetime import date
from typing import Any, Dict, List, Union

import pandas as pd

from .base import BaseDataSource, DataSourceConnectionError, DataSourceDataError

logger = logging.getLogger(__name__)


class BaoStockAdapter(BaseDataSource):
    """BaoStock数据源适配器"""

    def __init__(self, config: Dict[str, Any] = None):
        """
        初始化BaoStock适配器

        Args:
            config: 配置参数
        """
        super().__init__("baostock", config)
        self._baostock = None

        # BaoStock特定配置
        self.user_id = self.config.get("user_id", "anonymous")
        self.password = self.config.get("password", "123456")

    def connect(self) -> bool:
        """连接BaoStock"""
        try:
            import baostock as bs

            self._baostock = bs

            # 登录BaoStock
            lg = bs.login(user_id=self.user_id, password=self.password)
            if lg.error_code != "0":
                raise DataSourceConnectionError(f"BaoStock登录失败: {lg.error_msg}")

            self._connected = True
            logger.info(f"BaoStock连接成功，版本: {bs.__version__}")
            return True

        except ImportError as e:
            logger.error(f"BaoStock导入失败: {e}")
            raise DataSourceConnectionError(f"BaoStock导入失败: {e}")
        except Exception as e:
            logger.error(f"BaoStock连接失败: {e}")
            raise DataSourceConnectionError(f"BaoStock连接失败: {e}")

    def disconnect(self):
        """断开BaoStock连接"""
        if self._baostock and self._connected:
            try:
                self._baostock.logout()
                logger.info("BaoStock连接已断开")
            except:
                pass

        self._baostock = None
        self._connected = False

    def is_connected(self) -> bool:
        """检查连接状态"""
        return self._connected and self._baostock is not None

    def get_daily_data(
        self,
        symbol: str,
        start_date: Union[str, date],
        end_date: Union[str, date] = None,
    ) -> Dict[str, Any]:
        """
        获取日线数据

        Args:
            symbol: 股票代码 (如: 000001.SZ)
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            Dict[str, Any]: 日线数据
        """
        if not self.is_connected():
            self.connect()

        symbol = self._normalize_symbol(symbol)
        start_date = self._normalize_date(start_date)
        end_date = self._normalize_date(end_date) if end_date else start_date

        def _fetch_data():
            # 转换为BaoStock格式
            bs_symbol = self._convert_to_baostock_symbol(symbol)

            # 获取日线数据
            rs = self._baostock.query_history_k_data_plus(
                bs_symbol,
                "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg,peTTM,pbMRQ,psTTM,pcfNcfTTM,isST",
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag="3",  # 不复权
            )

            # 检查查询是否成功
            if rs.error_code != "0":
                logger.warning(f"BaoStock查询失败 {bs_symbol}: {rs.error_msg}")

                # 如果是会话相关错误，尝试重新连接
                if (
                    "login" in rs.error_msg.lower()
                    or "not login" in rs.error_msg.lower()
                ):
                    logger.info(f"检测到BaoStock会话过期，尝试重新连接...")
                    self.disconnect()
                    self.connect()

                    # 重新查询
                    rs = self._baostock.query_history_k_data_plus(
                        bs_symbol,
                        "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg,peTTM,pbMRQ,psTTM,pcfNcfTTM,isST",
                        start_date=start_date,
                        end_date=end_date,
                        frequency="d",
                        adjustflag="3",  # 不复权
                    )

                    if rs.error_code != "0":
                        logger.error(
                            f"重新连接后仍查询失败 {bs_symbol}: {rs.error_msg}"
                        )
                        return {}
                else:
                    return {}

            # 直接使用get_data()获取DataFrame
            df = rs.get_data()

            # 验证返回的数据类型
            if not hasattr(df, "empty") or not hasattr(df, "replace"):
                logger.error(f"BaoStock返回了异常数据类型: {type(df)}, 内容: {df}")
                return {}

            if df.empty:
                logger.debug(
                    f"BaoStock返回空DataFrame: {bs_symbol}, 日期: {start_date}-{end_date}"
                )
                return {}

            # 清理DataFrame中的空字符串
            df = df.replace("", None)

            # 验证DataFrame结构
            if len(df.columns) == 0:
                logger.error(f"BaoStock返回空列DataFrame: {bs_symbol}")
                return {}

            # 直接返回DataFrame
            return df

        # 使用重试机制处理网络错误
        return self._retry_request(_fetch_data)

    def get_minute_data(
        self, symbol: str, trade_date: Union[str, date], frequency: str = "5m"
    ) -> Dict[str, Any]:
        """
        获取分钟线数据 (BaoStock不支持分钟线)

        Args:
            symbol: 股票代码
            trade_date: 交易日期
            frequency: 频率

        Returns:
            Dict[str, Any]: 分钟线数据
        """
        raise NotImplementedError("BaoStock不支持分钟线数据")

    def get_stock_info(
        self, symbol: str = None
    ) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
        """
        获取股票基础信息

        Args:
            symbol: 股票代码，为None时返回所有股票

        Returns:
            Union[Dict, List[Dict]]: 股票信息
        """
        if not self.is_connected():
            self.connect()

        def _fetch_data():
            if symbol:
                # 获取单个股票信息
                symbol_norm = self._normalize_symbol(symbol)
                bs_symbol = self._convert_to_baostock_symbol(symbol_norm)

                # 获取股票基本信息
                rs = self._baostock.query_stock_basic(code=bs_symbol)
                df = rs.get_data()
                if df.empty:
                    return {}
                return self._convert_stock_info(df.iloc[0], symbol_norm)
            else:
                # 获取所有股票列表
                rs = self._baostock.query_all_stock()
                df = rs.get_data()
                return self._convert_stock_list(df)

        return self._retry_request(_fetch_data)

    def get_fundamentals(
        self, symbol: str, report_date: Union[str, date], report_type: str = "Q4"
    ) -> Dict[str, Any]:
        """
        获取财务数据

        Args:
            symbol: 股票代码
            report_date: 报告期
            report_type: 报告类型

        Returns:
            Dict[str, Any]: 财务数据
        """
        if not self.is_connected():
            self.connect()

        symbol = self._normalize_symbol(symbol)
        report_date = self._normalize_date(report_date)

        def _fetch_data():
            bs_symbol = self._convert_to_baostock_symbol(symbol)

            # 获取财务数据
            rs = self._baostock.query_profit_data(
                code=bs_symbol,
                year=report_date[:4],
                quarter=self._convert_report_type(report_type),
            )
            df = rs.get_data()
            if df.empty:
                return {}
            return self._convert_fundamentals(
                df.iloc[0], symbol, report_date, report_type
            )

        return self._retry_request(_fetch_data)

    def get_trade_calendar(
        self, start_date: Union[str, date], end_date: Union[str, date] = None
    ) -> List[Dict[str, Any]]:
        """获取交易日历"""
        if not self.is_connected():
            self.connect()

        start_date = self._normalize_date(start_date)
        end_date = self._normalize_date(end_date) if end_date else start_date

        def _fetch_data():
            rs = self._baostock.query_trade_dates(
                start_date=start_date, end_date=end_date
            )
            df = rs.get_data()
            return self._convert_trade_calendar(df)

        return self._retry_request(_fetch_data)

    def get_adjustment_data(
        self,
        symbol: str,
        start_date: Union[str, date],
        end_date: Union[str, date, None] = None,
    ) -> List[Dict[str, Any]]:
        """获取除权除息数据"""
        if not self.is_connected():
            self.connect()

        symbol = self._normalize_symbol(symbol)
        start_date = self._normalize_date(start_date)
        end_date = self._normalize_date(end_date) if end_date else start_date

        def _fetch_data():
            try:
                bs_symbol = self._convert_to_baostock_symbol(symbol)

                # BaoStock的除权除息API只支持按年查询
                start_year = (
                    start_date.year
                    if hasattr(start_date, "year")
                    else int(str(start_date)[:4])
                )
                end_year = (
                    end_date.year
                    if hasattr(end_date, "year")
                    else int(str(end_date)[:4])
                )

                all_dataframes = []
                for year in range(start_year, end_year + 1):
                    rs = self._baostock.query_dividend_data(
                        code=bs_symbol, year=str(year)
                    )

                    if rs.error_code == "0":
                        # 直接使用get_data()获取DataFrame
                        df = rs.get_data()
                        if not df.empty:
                            all_dataframes.append(df)

                if all_dataframes:
                    # 合并所有年份的数据
                    combined_df = pd.concat(all_dataframes, ignore_index=True)
                    # 清理DataFrame中的空字符串
                    combined_df = combined_df.replace("", None)
                    return self._convert_adjustment_data(combined_df, symbol)
                else:
                    return []

            except Exception as e:
                logger.error(f"BaoStock获取除权除息数据失败 {symbol}: {e}")
                raise DataSourceDataError(f"获取除权除息数据失败: {e}")

        return self._retry_request(_fetch_data)

    def get_valuation_data(
        self,
        symbol: str,
        trade_date: Union[str, date],
    ) -> Dict[str, Any]:
        """获取估值数据（从K线数据中提取）"""
        if not self.is_connected():
            self.connect()

        symbol = self._normalize_symbol(symbol)
        trade_date = self._normalize_date(trade_date)

        def _fetch_data():
            try:
                bs_symbol = self._convert_to_baostock_symbol(symbol)

                # 获取包含估值数据的K线数据
                rs = self._baostock.query_history_k_data_plus(
                    bs_symbol,
                    "date,code,close,peTTM,pbMRQ,psTTM,pcfNcfTTM",
                    start_date=trade_date,
                    end_date=trade_date,
                    frequency="d",
                    adjustflag="3",
                )

                # 直接使用get_data()获取DataFrame
                df = rs.get_data()

                if df.empty:
                    return {}

                # 清理DataFrame中的空字符串
                df = df.replace("", None)

                # 取最新一条记录并转换
                latest = df.iloc[-1]
                return self._convert_valuation_data(latest, symbol, trade_date)

            except Exception as e:
                logger.error(f"BaoStock获取估值数据失败 {symbol}: {e}")
                raise DataSourceDataError(f"获取估值数据失败: {e}")

        return self._retry_request(_fetch_data)

    def _convert_valuation_data(
        self, data, symbol: str, trade_date: str
    ) -> Dict[str, Any]:
        """转换估值数据格式"""

        def safe_float(value, default=0.0):
            """安全的浮点数转换"""
            if pd.isna(value) or value == "" or value is None:
                return default
            try:
                return float(value)
            except (ValueError, TypeError):
                return default

        return {
            "symbol": symbol,
            "date": str(trade_date),
            "pe_ratio": safe_float(data.get("peTTM", 0)),
            "pb_ratio": safe_float(data.get("pbMRQ", 0)),
            "ps_ratio": safe_float(data.get("psTTM", 0)),
            "pcf_ratio": safe_float(data.get("pcfNcfTTM", 0)),
            "market_cap": 0,  # BaoStock K线数据中没有市值，需要单独计算
            "circulating_cap": 0,  # BaoStock K线数据中没有流通市值
            "source": "baostock",
        }

    def _convert_adjustment_data(self, df, symbol: str) -> List[Dict[str, Any]]:
        """转换除权除息数据格式"""
        adjustment_list = []

        if df is None or df.empty:
            return adjustment_list

        # BaoStock除权除息字段包括：dividOperateDate, dividCashPsBeforeTax, dividStocksPs等
        for _, row in df.iterrows():
            try:
                # 安全获取字段值
                ex_date = row.get("dividOperateDate", "")
                dividend = row.get("dividCashPsBeforeTax", "")
                stock_dividend = row.get("dividStocksPs", "")

                # 安全转换数值
                dividend_value = (
                    float(dividend)
                    if dividend and str(dividend) != "" and not pd.isna(dividend)
                    else 0
                )
                stock_dividend_value = (
                    float(stock_dividend)
                    if stock_dividend
                    and str(stock_dividend) != ""
                    and not pd.isna(stock_dividend)
                    else 0
                )

                if ex_date and str(ex_date) != "" and not pd.isna(ex_date):
                    adjustment_list.append(
                        {
                            "symbol": symbol,
                            "ex_date": str(ex_date),
                            "dividend": dividend_value,
                            "split_ratio": 1.0,  # BaoStock没有拆股数据
                            "bonus_ratio": stock_dividend_value,
                            "source": "baostock",
                        }
                    )
            except (ValueError, TypeError, KeyError):
                continue

        return adjustment_list

    def _convert_to_baostock_symbol(self, symbol: str) -> str:
        """转换为BaoStock股票代码格式"""
        # BaoStock格式: sz.000001, sh.600000
        if "." in symbol:
            code, market = symbol.split(".")
            if market.upper() == "SZ":
                return f"sz.{code}"
            elif market.upper() in ["SS", "SH"]:
                return f"sh.{code}"
        return symbol.lower()

    def _convert_report_type(self, report_type: str) -> int:
        """转换报告期类型"""
        type_map = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}
        return type_map.get(report_type, 4)

    def _convert_stock_info(self, data: pd.Series, symbol: str) -> Dict[str, Any]:
        """转换股票信息格式"""
        return {
            "symbol": symbol,
            "name": data.get("code_name", ""),
            "market": symbol.split(".")[-1] if "." in symbol else "SZ",
            "industry": data.get("industry", ""),
            "list_date": data.get("ipoDate", ""),
            "status": "active" if data.get("status") == "1" else "inactive",
        }

    def _convert_stock_list(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """转换股票列表格式"""
        stock_list = []

        for _, row in df.iterrows():
            code = row["code"]
            # 转换为标准格式
            if code.startswith("sz."):
                symbol = code[3:] + ".SZ"
            elif code.startswith("sh."):
                symbol = code[3:] + ".SS"
            else:
                continue

            stock_list.append(
                {
                    "symbol": symbol,
                    "name": row["code_name"],
                    "market": symbol.split(".")[-1],
                    "type": row.get("type", ""),
                    "status": "active" if row.get("status") == "1" else "inactive",
                }
            )

        return stock_list

    def _convert_fundamentals(
        self, data: pd.Series, symbol: str, report_date: str, report_type: str
    ) -> Dict[str, Any]:
        """转换财务数据格式"""
        return {
            "symbol": symbol,
            "report_date": report_date,
            "report_type": report_type,
            "revenue": (
                float(data.get("operatingRevenue", 0))
                if data.get("operatingRevenue")
                else 0
            ),
            "net_profit": (
                float(data.get("netProfit", 0)) if data.get("netProfit") else 0
            ),
            "total_assets": (
                float(data.get("totalAssets", 0)) if data.get("totalAssets") else 0
            ),
            "total_equity": (
                float(data.get("totalEquity", 0)) if data.get("totalEquity") else 0
            ),
            "eps": float(data.get("basicEPS", 0)) if data.get("basicEPS") else 0,
            "roe": float(data.get("roeAvg", 0)) if data.get("roeAvg") else 0,
        }

    def _convert_trade_calendar(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """转换交易日历格式"""
        calendar_list = []

        for _, row in df.iterrows():
            calendar_list.append(
                {
                    "trade_date": row["calendar_date"],
                    "is_trading": int(row["is_trading_day"]),
                    "market": "SZ",  # BaoStock主要是A股
                }
            )

        return calendar_list

    def get_capabilities(self) -> Dict[str, Any]:
        """获取BaoStock能力信息"""
        capabilities = super().get_capabilities()
        capabilities.update(
            {
                "supports_minute": False,
                "supports_trade_calendar": True,
                "supports_adjustment": True,
                "supported_frequencies": ["1d"],
                "supported_markets": ["SZ", "SS"],
                "quality_score": "very_high",
                "update_frequency": "daily",
            }
        )
        return capabilities
