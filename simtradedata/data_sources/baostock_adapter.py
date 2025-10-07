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

    def connect(self) -> bool:
        """
        连接BaoStock

        注意：BaoStock的login()是匿名接口，不需要真实用户名密码，
        只是建立会话连接的形式调用。
        """
        try:
            import baostock as bs

            self._baostock = bs

            # 建立会话连接（匿名，不需要真实账号）
            lg = bs.login()
            if lg.error_code != "0":
                raise DataSourceConnectionError(f"BaoStock连接失败: {lg.error_msg}")

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

                # 如果是会话过期错误，尝试重新连接
                if (
                    "login" in rs.error_msg.lower()
                    or "not login" in rs.error_msg.lower()
                    or "用户未登录" in rs.error_msg
                    or "未登录" in rs.error_msg
                ):
                    logger.info(f"检测到BaoStock会话过期，尝试重新建立连接...")
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

            # 转换DataFrame为符合接口规范的字典格式
            return self._convert_daily_data(df, symbol)

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
        self, symbol: str = None, target_date: str = None
    ) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
        """
        获取股票基础信息

        Args:
            symbol: 股票代码，为None时返回所有股票
            target_date: 目标日期（YYYY-MM-DD），用于查询指定日期的股票列表（避免幸存者偏差）

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

                basic_info = self._convert_stock_info(df.iloc[0], symbol_norm)

                # 获取行业分类信息
                industry_rs = self._baostock.query_stock_industry(code=bs_symbol)
                if industry_rs.error_code == "0":
                    industry_df = industry_rs.get_data()
                    if not industry_df.empty:
                        industry_row = industry_df.iloc[0]
                        basic_info["industry_l1"] = industry_row.get("industry", "")
                        basic_info["industry_l2"] = industry_row.get(
                            "industryClassification", ""
                        )

                # 获取股本信息 - 使用最新财务数据获取股本
                try:
                    from datetime import datetime

                    current_year = datetime.now().year
                    profit_rs = self._baostock.query_profit_data(
                        code=bs_symbol, year=current_year, quarter=4
                    )
                    if profit_rs.error_code == "0":
                        profit_df = profit_rs.get_data()
                        if not profit_df.empty:
                            profit_row = profit_df.iloc[0]
                            # 从财务数据获取股本信息
                            total_share_capital = profit_row.get(
                                "totalShareCapital", ""
                            )
                            if total_share_capital and total_share_capital != "":
                                try:
                                    basic_info["total_shares"] = (
                                        float(total_share_capital) * 10000
                                    )  # 万股转股
                                    # 如果没有流通股本，暂时用总股本代替
                                    if (
                                        "float_shares" not in basic_info
                                        or not basic_info["float_shares"]
                                    ):
                                        basic_info["float_shares"] = basic_info[
                                            "total_shares"
                                        ]
                                except (ValueError, TypeError):
                                    pass
                except Exception as e:
                    logger.debug(f"获取股本信息失败 {symbol}: {e}")

                return basic_info
            else:
                # 获取所有股票列表（支持指定日期，避免幸存者偏差）
                if target_date:
                    # 使用指定日期查询（确保获取该日期的真实股票列表）
                    query_date = target_date
                    logger.debug(f"查询指定日期的股票列表: {query_date}")
                else:
                    # 使用最近的日期确保能获取到数据
                    from datetime import datetime, timedelta

                    query_date = (datetime.now() - timedelta(days=1)).strftime(
                        "%Y-%m-%d"
                    )
                    logger.debug(f"查询最近日期的股票列表: {query_date}")

                rs = self._baostock.query_all_stock(day=query_date)
                df = rs.get_data()
                return self._convert_stock_list(df)

        return self._retry_request(_fetch_data)

    def get_valuation_data(
        self,
        symbol: str,
        start_date: Union[str, date],
        end_date: Union[str, date] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取估值数据

        Args:
            symbol: 股票代码
            start_date: 开始日期
            end_date: 结束日期，默认为开始日期

        Returns:
            List[Dict[str, Any]]: 估值数据列表
        """
        if not self.is_connected():
            self.connect()

        symbol = self._normalize_symbol(symbol)
        start_date = self._normalize_date(start_date)
        end_date = self._normalize_date(end_date) if end_date else start_date

        def _fetch_data():
            bs_symbol = self._convert_to_baostock_symbol(symbol)

            # 获取日K线数据，包含估值指标
            rs = self._baostock.query_history_k_data_plus(
                bs_symbol,
                "date,code,close,peTTM,pbMRQ,psTTM,pcfNcfTTM",
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag="3",  # 不复权
            )

            if rs.error_code != "0":
                logger.warning(f"获取 {symbol} 估值数据失败: {rs.error_msg}")
                return []

            data_list = []
            while (rs.error_code == "0") & rs.next():
                data_list.append(rs.get_row_data())

            if not data_list:
                return []

            df = pd.DataFrame(data_list, columns=rs.fields)

            # 数据类型转换和清理
            numeric_columns = ["close", "peTTM", "pbMRQ", "psTTM", "pcfNcfTTM"]
            for col in numeric_columns:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

            # 过滤掉无效数据
            df = df[
                (df["peTTM"].notna() & (df["peTTM"] > 0))
                | (df["pbMRQ"].notna() & (df["pbMRQ"] > 0))
                | (df["psTTM"].notna() & (df["psTTM"] > 0))
                | (df["pcfNcfTTM"].notna() & (df["pcfNcfTTM"] > 0))
            ]

            valuation_records = []
            for _, row in df.iterrows():
                record = {
                    "symbol": symbol,
                    "date": row["date"],
                    "pe_ratio": (
                        row["peTTM"]
                        if pd.notna(row["peTTM"]) and row["peTTM"] > 0
                        else None
                    ),
                    "pb_ratio": (
                        row["pbMRQ"]
                        if pd.notna(row["pbMRQ"]) and row["pbMRQ"] > 0
                        else None
                    ),
                    "ps_ratio": (
                        row["psTTM"]
                        if pd.notna(row["psTTM"]) and row["psTTM"] > 0
                        else None
                    ),
                    "pcf_ratio": (
                        row["pcfNcfTTM"]
                        if pd.notna(row["pcfNcfTTM"]) and row["pcfNcfTTM"] > 0
                        else None
                    ),
                    "source": "baostock_api",
                }
                valuation_records.append(record)

            return valuation_records

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
            year = report_date[:4]
            quarter = self._convert_report_type(report_type)

            # 获取完整的财务数据 - 需要调用多个API
            financial_data = {}

            # 1. 获取利润表数据
            profit_rs = self._baostock.query_profit_data(
                code=bs_symbol, year=year, quarter=quarter
            )
            if profit_rs.error_code == "0":
                profit_df = profit_rs.get_data()
                if not profit_df.empty:
                    financial_data.update(profit_df.iloc[0].to_dict())

            # 2. 获取资产负债表数据
            balance_rs = self._baostock.query_balance_data(
                code=bs_symbol, year=year, quarter=quarter
            )
            if balance_rs.error_code == "0":
                balance_df = balance_rs.get_data()
                if not balance_df.empty:
                    financial_data.update(balance_df.iloc[0].to_dict())

            # 3. 获取现金流量表数据
            cash_flow_rs = self._baostock.query_cash_flow_data(
                code=bs_symbol, year=year, quarter=quarter
            )
            if cash_flow_rs.error_code == "0":
                cash_flow_df = cash_flow_rs.get_data()
                if not cash_flow_df.empty:
                    financial_data.update(cash_flow_df.iloc[0].to_dict())

            if not financial_data:
                return {}

            return self._convert_fundamentals(
                financial_data, symbol, report_date, report_type
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

    def query_profit_data(self, symbol: str, year: int, quarter: int) -> Dict[str, Any]:
        """
        获取季频盈利能力数据

        Args:
            symbol: 标准化股票代码 (如 "000001.SZ")
            year: 统计年份
            quarter: 统计季度 (1/2/3/4)

        Returns:
            {
                "success": True/False,
                "data": Dict 盈利能力数据,
                "error": str 错误信息 (可选)
            }
        """
        if not self.is_connected():
            self.connect()

        symbol = self._normalize_symbol(symbol)

        def _fetch_data():
            try:
                bs_symbol = self._convert_to_baostock_symbol(symbol)
                logger.info(
                    f"查询盈利能力数据: {symbol} ({bs_symbol}), {year}年第{quarter}季度"
                )

                # 调用BaoStock API
                rs = self._baostock.query_profit_data(
                    code=bs_symbol, year=year, quarter=quarter
                )

                if rs.error_code != "0":
                    logger.error(f"BaoStock 盈利能力查询失败: {rs.error_msg}")
                    raise DataSourceDataError(f"查询盈利能力数据失败: {rs.error_msg}")

                # 使用get_data()获取DataFrame
                df = rs.get_data()

                if df.empty:
                    return {}

                # 清理DataFrame中的空字符串
                df = df.replace("", None)

                # 取第一条记录(通常只有一条)
                record = df.iloc[0]

                return self._convert_profit_data(record, symbol)

            except Exception as e:
                logger.error(f"查询盈利能力数据失败 {symbol}: {e}")
                raise DataSourceDataError(f"查询盈利能力数据失败: {e}")

        return self._retry_request(_fetch_data)

    def query_operation_data(
        self, symbol: str, year: int, quarter: int
    ) -> Dict[str, Any]:
        """
        获取季频营运能力数据 (100%覆盖operating_ability)

        Args:
            symbol: 标准化股票代码 (如 "000001.SZ")
            year: 统计年份
            quarter: 统计季度 (1/2/3/4)

        Returns:
            {
                "success": True/False,
                "data": Dict 营运能力数据,
                "error": str 错误信息 (可选)
            }
        """
        if not self.is_connected():
            self.connect()

        symbol = self._normalize_symbol(symbol)

        def _fetch_data():
            try:
                bs_symbol = self._convert_to_baostock_symbol(symbol)
                logger.info(
                    f"查询营运能力数据: {symbol} ({bs_symbol}), {year}年第{quarter}季度"
                )

                # 调用BaoStock API
                rs = self._baostock.query_operation_data(
                    code=bs_symbol, year=year, quarter=quarter
                )

                if rs.error_code != "0":
                    logger.error(f"BaoStock 营运能力查询失败: {rs.error_msg}")
                    raise DataSourceDataError(f"查询营运能力数据失败: {rs.error_msg}")

                # 使用get_data()获取DataFrame
                df = rs.get_data()

                if df.empty:
                    return {}

                # 清理DataFrame中的空字符串
                df = df.replace("", None)

                # 取第一条记录
                record = df.iloc[0]

                return self._convert_operation_data(record, symbol)

            except Exception as e:
                logger.error(f"查询营运能力数据失败 {symbol}: {e}")
                raise DataSourceDataError(f"查询营运能力数据失败: {e}")

        return self._retry_request(_fetch_data)

    def query_growth_data(self, symbol: str, year: int, quarter: int) -> Dict[str, Any]:
        """
        获取季频成长能力数据

        Args:
            symbol: 标准化股票代码 (如 "000001.SZ")
            year: 统计年份
            quarter: 统计季度 (1/2/3/4)

        Returns:
            {
                "success": True/False,
                "data": Dict 成长能力数据,
                "error": str 错误信息 (可选)
            }
        """
        if not self.is_connected():
            self.connect()

        symbol = self._normalize_symbol(symbol)

        def _fetch_data():
            try:
                bs_symbol = self._convert_to_baostock_symbol(symbol)
                logger.info(
                    f"查询成长能力数据: {symbol} ({bs_symbol}), {year}年第{quarter}季度"
                )

                # 调用BaoStock API
                rs = self._baostock.query_growth_data(
                    code=bs_symbol, year=year, quarter=quarter
                )

                if rs.error_code != "0":
                    logger.error(f"BaoStock 成长能力查询失败: {rs.error_msg}")
                    raise DataSourceDataError(f"查询成长能力数据失败: {rs.error_msg}")

                # 使用get_data()获取DataFrame
                df = rs.get_data()

                if df.empty:
                    return {}

                # 清理DataFrame中的空字符串
                df = df.replace("", None)

                # 取第一条记录
                record = df.iloc[0]

                return self._convert_growth_data(record, symbol)

            except Exception as e:
                logger.error(f"查询成长能力数据失败 {symbol}: {e}")
                raise DataSourceDataError(f"查询成长能力数据失败: {e}")

        return self._retry_request(_fetch_data)

    def query_balance_data(
        self, symbol: str, year: int, quarter: int
    ) -> Dict[str, Any]:
        """
        获取季频偿债能力数据

        Args:
            symbol: 标准化股票代码 (如 "000001.SZ")
            year: 统计年份
            quarter: 统计季度 (1/2/3/4)

        Returns:
            {
                "success": True/False,
                "data": Dict 偿债能力数据,
                "error": str 错误信息 (可选)
            }
        """
        if not self.is_connected():
            self.connect()

        symbol = self._normalize_symbol(symbol)

        def _fetch_data():
            try:
                bs_symbol = self._convert_to_baostock_symbol(symbol)
                logger.info(
                    f"查询偿债能力数据: {symbol} ({bs_symbol}), {year}年第{quarter}季度"
                )

                # 调用BaoStock API
                rs = self._baostock.query_balance_data(
                    code=bs_symbol, year=year, quarter=quarter
                )

                if rs.error_code != "0":
                    logger.error(f"BaoStock 偿债能力查询失败: {rs.error_msg}")
                    raise DataSourceDataError(f"查询偿债能力数据失败: {rs.error_msg}")

                # 使用get_data()获取DataFrame
                df = rs.get_data()

                if df.empty:
                    return {}

                # 清理DataFrame中的空字符串
                df = df.replace("", None)

                # 取第一条记录
                record = df.iloc[0]

                return self._convert_balance_data(record, symbol)

            except Exception as e:
                logger.error(f"查询偿债能力数据失败 {symbol}: {e}")
                raise DataSourceDataError(f"查询偿债能力数据失败: {e}")

        return self._retry_request(_fetch_data)

    def query_cash_flow_data(
        self, symbol: str, year: int, quarter: int
    ) -> Dict[str, Any]:
        """
        获取季频现金流量数据

        Args:
            symbol: 标准化股票代码 (如 "000001.SZ")
            year: 统计年份
            quarter: 统计季度 (1/2/3/4)

        Returns:
            {
                "success": True/False,
                "data": Dict 现金流量数据,
                "error": str 错误信息 (可选)
            }
        """
        if not self.is_connected():
            self.connect()

        symbol = self._normalize_symbol(symbol)

        def _fetch_data():
            try:
                bs_symbol = self._convert_to_baostock_symbol(symbol)
                logger.info(
                    f"查询现金流量数据: {symbol} ({bs_symbol}), {year}年第{quarter}季度"
                )

                # 调用BaoStock API
                rs = self._baostock.query_cash_flow_data(
                    code=bs_symbol, year=year, quarter=quarter
                )

                if rs.error_code != "0":
                    logger.error(f"BaoStock 现金流量查询失败: {rs.error_msg}")
                    raise DataSourceDataError(f"查询现金流量数据失败: {rs.error_msg}")

                # 使用get_data()获取DataFrame
                df = rs.get_data()

                if df.empty:
                    return {}

                # 清理DataFrame中的空字符串
                df = df.replace("", None)

                # 取第一条记录
                record = df.iloc[0]

                return self._convert_cash_flow_data(record, symbol)

            except Exception as e:
                logger.error(f"查询现金流量数据失败 {symbol}: {e}")
                raise DataSourceDataError(f"查询现金流量数据失败: {e}")

        return self._retry_request(_fetch_data)

    def query_dupont_data(self, symbol: str, year: int, quarter: int) -> Dict[str, Any]:
        """
        获取季频杜邦指数数据

        Args:
            symbol: 标准化股票代码 (如 "000001.SZ")
            year: 统计年份
            quarter: 统计季度 (1/2/3/4)

        Returns:
            {
                "success": True/False,
                "data": Dict 杜邦指数数据,
                "error": str 错误信息 (可选)
            }
        """
        if not self.is_connected():
            self.connect()

        symbol = self._normalize_symbol(symbol)

        def _fetch_data():
            try:
                bs_symbol = self._convert_to_baostock_symbol(symbol)
                logger.info(
                    f"查询杜邦指数数据: {symbol} ({bs_symbol}), {year}年第{quarter}季度"
                )

                # 调用BaoStock API
                rs = self._baostock.query_dupont_data(
                    code=bs_symbol, year=year, quarter=quarter
                )

                if rs.error_code != "0":
                    logger.error(f"BaoStock 杜邦指数查询失败: {rs.error_msg}")
                    raise DataSourceDataError(f"查询杜邦指数数据失败: {rs.error_msg}")

                # 使用get_data()获取DataFrame
                df = rs.get_data()

                if df.empty:
                    return {}

                # 清理DataFrame中的空字符串
                df = df.replace("", None)

                # 取第一条记录
                record = df.iloc[0]

                return self._convert_dupont_data(record, symbol)

            except Exception as e:
                logger.error(f"查询杜邦指数数据失败 {symbol}: {e}")
                raise DataSourceDataError(f"查询杜邦指数数据失败: {e}")

        return self._retry_request(_fetch_data)

    def _get_share_data(self, bs_symbol: str, trade_date: str) -> Dict[str, float]:
        """获取股本数据用于市值计算"""
        try:
            # 从trade_date提取年份和季度
            from datetime import datetime

            trade_dt = datetime.strptime(trade_date, "%Y-%m-%d")
            year = str(trade_dt.year)

            # 根据月份确定季度
            month = trade_dt.month
            if month <= 3:
                quarter = "1"
            elif month <= 6:
                quarter = "2"
            elif month <= 9:
                quarter = "3"
            else:
                quarter = "4"

            # 获取利润表数据（包含股本信息）
            rs = self._baostock.query_profit_data(
                code=bs_symbol, year=year, quarter=quarter
            )

            if rs.error_code == "0":
                df = rs.get_data()
                if not df.empty:
                    row = df.iloc[0]
                    total_share = row.get("totalShare", 0)
                    liqa_share = row.get("liqaShare", 0)

                    # 转换为浮点数，BaoStock返回的是字符串
                    try:
                        total_share = (
                            float(total_share)
                            if total_share and str(total_share) != ""
                            else 0
                        )
                        liqa_share = (
                            float(liqa_share)
                            if liqa_share and str(liqa_share) != ""
                            else 0
                        )
                    except (ValueError, TypeError):
                        total_share = liqa_share = 0

                    return {"total_share": total_share, "liqa_share": liqa_share}

            # 如果获取不到当前季度数据，尝试上一季度
            if quarter == "1":
                year = str(trade_dt.year - 1)
                quarter = "4"
            else:
                quarter = str(int(quarter) - 1)

            rs = self._baostock.query_profit_data(
                code=bs_symbol, year=year, quarter=quarter
            )

            if rs.error_code == "0":
                df = rs.get_data()
                if not df.empty:
                    row = df.iloc[0]
                    total_share = row.get("totalShare", 0)
                    liqa_share = row.get("liqaShare", 0)

                    try:
                        total_share = (
                            float(total_share)
                            if total_share and str(total_share) != ""
                            else 0
                        )
                        liqa_share = (
                            float(liqa_share)
                            if liqa_share and str(liqa_share) != ""
                            else 0
                        )
                    except (ValueError, TypeError):
                        total_share = liqa_share = 0

                    return {"total_share": total_share, "liqa_share": liqa_share}

        except Exception as e:
            logger.warning(f"获取股本数据失败 {bs_symbol}: {e}")

        return {"total_share": 0, "liqa_share": 0}

    def _convert_valuation_data(
        self, data, symbol: str, trade_date: str, share_data: Dict[str, float] = None
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

        # 获取收盘价用于计算市值
        close_price = safe_float(data.get("close", 0))

        # 计算市值（如果有股本数据和收盘价）
        market_cap = 0
        circulating_cap = 0

        if share_data and close_price > 0:
            total_share = share_data.get("total_share", 0)
            liqa_share = share_data.get("liqa_share", 0)

            if total_share > 0:
                market_cap = total_share * close_price  # 总市值 = 总股本 × 收盘价
            if liqa_share > 0:
                circulating_cap = (
                    liqa_share * close_price
                )  # 流通市值 = 流通股本 × 收盘价

        return {
            "symbol": symbol,
            "date": str(trade_date),
            "pe_ratio": safe_float(data.get("peTTM", 0)),
            "pb_ratio": safe_float(data.get("pbMRQ", 0)),
            "ps_ratio": safe_float(data.get("psTTM", 0)),
            "pcf_ratio": safe_float(data.get("pcfNcfTTM", 0)),
            # 注意：total_value和market_cap在返回结果中提供，但不会存储到数据库
            "total_value": market_cap,  # 为兼容性保留，提供给策略使用
            "market_cap": market_cap,  # 计算得出的总市值（不存储）
            "circulating_cap": circulating_cap,  # 计算得出的流通市值（不存储）
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
        self, data: Dict[str, Any], symbol: str, report_date: str, report_type: str
    ) -> Dict[str, Any]:
        """转换财务数据格式"""

        def safe_float(value, default=0.0):
            """安全的浮点数转换"""
            if value is None or value == "" or str(value).strip() == "":
                return default
            try:
                return float(value)
            except (ValueError, TypeError):
                return default

        # BaoStock字段映射
        revenue = safe_float(data.get("MBRevenue", 0))  # 营业收入
        net_profit = safe_float(data.get("netProfit", 0))  # 净利润
        eps = safe_float(data.get("epsTTM", 0))  # 每股收益
        roe = safe_float(data.get("roeAvg", 0))  # ROE

        # 股本信息
        total_shares = safe_float(data.get("totalShare", 0))  # 总股本
        liquid_shares = safe_float(data.get("liqaShare", 0))  # 流通股本

        # 资产负债信息（BaoStock主要提供比率，需要根据净利润等推算）
        asset_to_equity = safe_float(data.get("assetToEquity", 0))  # 资产权益比
        liability_to_asset = safe_float(data.get("liabilityToAsset", 0))  # 负债资产比

        # 尝试根据净利润和ROE推算总资产和股东权益
        total_assets = 0.0
        shareholders_equity = 0.0

        if roe > 0 and net_profit > 0:
            # 根据 ROE = 净利润 / 股东权益 推算股东权益
            shareholders_equity = net_profit / roe

            # 根据资产权益比推算总资产
            if asset_to_equity > 0:
                total_assets = shareholders_equity * asset_to_equity

        return {
            "symbol": symbol,
            "report_date": report_date,
            "report_type": report_type,
            "revenue": revenue,
            "net_profit": net_profit,
            "total_assets": total_assets,
            "shareholders_equity": shareholders_equity,
            "eps": eps,
            "roe": roe,
            "total_shares": total_shares,
            "liquid_shares": liquid_shares,
            "asset_to_equity": asset_to_equity,
            "liability_to_asset": liability_to_asset,
            "source": "baostock",
        }

    def _convert_profit_data(self, record: pd.Series, symbol: str) -> Dict[str, Any]:
        """转换盈利能力数据格式"""

        def safe_float(value, default=0.0):
            if pd.isna(value) or value == "" or value is None:
                return default
            try:
                return float(value)
            except (ValueError, TypeError):
                return default

        return {
            "symbol": symbol,
            "pub_date": str(record.get("pubDate", "")),
            "stat_date": str(record.get("statDate", "")),
            "roe_avg": safe_float(record.get("roeAvg")),  # 净资产收益率(平均)
            "np_margin": safe_float(record.get("npMargin")),  # 销售净利率
            "gp_margin": safe_float(record.get("gpMargin")),  # 销售毛利率
            "net_profit": safe_float(record.get("netProfit")),  # 净利润
            "eps_ttm": safe_float(record.get("epsTTM")),  # 每股收益
            "mb_revenue": safe_float(record.get("MBRevenue")),  # 主营营业收入
            "total_share": safe_float(record.get("totalShare")),  # 总股本
            "liqa_share": safe_float(record.get("liqaShare")),  # 流通股本
            "source": "baostock",
        }

    def _convert_operation_data(self, record: pd.Series, symbol: str) -> Dict[str, Any]:
        """转换营运能力数据格式 (100%覆盖operating_ability)"""

        def safe_float(value, default=0.0):
            if pd.isna(value) or value == "" or value is None:
                return default
            try:
                return float(value)
            except (ValueError, TypeError):
                return default

        return {
            "symbol": symbol,
            "pub_date": str(record.get("pubDate", "")),
            "stat_date": str(record.get("statDate", "")),
            "nr_turn_ratio": safe_float(
                record.get("NRTurnRatio")
            ),  # 应收账款周转率(次)
            "nr_turn_days": safe_float(
                record.get("NRTurnDays")
            ),  # 应收账款周转天数(天)
            "inv_turn_ratio": safe_float(record.get("INVTurnRatio")),  # 存货周转率(次)
            "inv_turn_days": safe_float(record.get("INVTurnDays")),  # 存货周转天数(天)
            "ca_turn_ratio": safe_float(
                record.get("CATurnRatio")
            ),  # 流动资产周转率(次)
            "asset_turn_ratio": safe_float(
                record.get("AssetTurnRatio")
            ),  # 总资产周转率
            "source": "baostock",
        }

    def _convert_growth_data(self, record: pd.Series, symbol: str) -> Dict[str, Any]:
        """转换成长能力数据格式"""

        def safe_float(value, default=0.0):
            if pd.isna(value) or value == "" or value is None:
                return default
            try:
                return float(value)
            except (ValueError, TypeError):
                return default

        return {
            "symbol": symbol,
            "pub_date": str(record.get("pubDate", "")),
            "stat_date": str(record.get("statDate", "")),
            "yoy_equity": safe_float(record.get("YOYEquity")),  # 净资产同比增长率
            "yoy_asset": safe_float(record.get("YOYAsset")),  # 总资产同比增长率
            "yoy_ni": safe_float(record.get("YOYNI")),  # 净利润同比增长率
            "yoy_eps_basic": safe_float(
                record.get("YOYEPSBasic")
            ),  # 基本每股收益同比增长率
            "yoy_pni": safe_float(
                record.get("YOYPNI")
            ),  # 归属母公司股东净利润同比增长率
            "source": "baostock",
        }

    def _convert_balance_data(self, record: pd.Series, symbol: str) -> Dict[str, Any]:
        """转换偿债能力数据格式"""

        def safe_float(value, default=0.0):
            if pd.isna(value) or value == "" or value is None:
                return default
            try:
                return float(value)
            except (ValueError, TypeError):
                return default

        return {
            "symbol": symbol,
            "pub_date": str(record.get("pubDate", "")),
            "stat_date": str(record.get("statDate", "")),
            "current_ratio": safe_float(record.get("currentRatio")),  # 流动比率
            "quick_ratio": safe_float(record.get("quickRatio")),  # 速动比率
            "cash_ratio": safe_float(record.get("cashRatio")),  # 现金比率
            "yoy_liability": safe_float(record.get("YOYLiability")),  # 总负债同比增长率
            "liability_to_asset": safe_float(
                record.get("liabilityToAsset")
            ),  # 资产负债率
            "asset_to_equity": safe_float(record.get("assetToEquity")),  # 权益乘数
            "source": "baostock",
        }

    def _convert_cash_flow_data(self, record: pd.Series, symbol: str) -> Dict[str, Any]:
        """转换现金流量数据格式"""

        def safe_float(value, default=0.0):
            if pd.isna(value) or value == "" or value is None:
                return default
            try:
                return float(value)
            except (ValueError, TypeError):
                return default

        return {
            "symbol": symbol,
            "pub_date": str(record.get("pubDate", "")),
            "stat_date": str(record.get("statDate", "")),
            "ca_to_asset": safe_float(record.get("CAToAsset")),  # 流动资产除以总资产
            "nca_to_asset": safe_float(
                record.get("NCAToAsset")
            ),  # 非流动资产除以总资产
            "tangible_asset_to_asset": safe_float(
                record.get("tangibleAssetToAsset")
            ),  # 有形资产除以总资产
            "ebit_to_interest": safe_float(
                record.get("ebitToInterest")
            ),  # 已获利息倍数
            "cfo_to_or": safe_float(
                record.get("CFOToOR")
            ),  # 经营活动产生的现金流量净额除以营业收入
            "cfo_to_np": safe_float(
                record.get("CFOToNP")
            ),  # 经营性现金净流量除以净利润
            "cfo_to_gr": safe_float(
                record.get("CFOToGr")
            ),  # 经营性现金净流量除以营业总收入
            "source": "baostock",
        }

    def _convert_dupont_data(self, record: pd.Series, symbol: str) -> Dict[str, Any]:
        """转换杜邦指数数据格式"""

        def safe_float(value, default=0.0):
            if pd.isna(value) or value == "" or value is None:
                return default
            try:
                return float(value)
            except (ValueError, TypeError):
                return default

        return {
            "symbol": symbol,
            "pub_date": str(record.get("pubDate", "")),
            "stat_date": str(record.get("statDate", "")),
            "dupont_roe": safe_float(record.get("dupontROE")),  # 净资产收益率
            "dupont_asset_sto_equity": safe_float(
                record.get("dupontAssetStoEquity")
            ),  # 权益乘数
            "dupont_asset_turn": safe_float(
                record.get("dupontAssetTurn")
            ),  # 总资产周转率
            "dupont_pnitoni": safe_float(
                record.get("dupontPnitoni")
            ),  # 归属母公司股东的净利润/净利润
            "dupont_nitogr": safe_float(
                record.get("dupontNitogr")
            ),  # 净利润/营业总收入
            "dupont_tax_burden": safe_float(
                record.get("dupontTaxBurden")
            ),  # 净利润/利润总额
            "dupont_int_burden": safe_float(
                record.get("dupontIntburden")
            ),  # 利润总额/息税前利润
            "dupont_ebit_to_gr": safe_float(
                record.get("dupontEbittogr")
            ),  # 息税前利润/营业总收入
            "source": "baostock",
        }

    def _convert_trade_calendar(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """转换交易日历格式"""
        calendar_list = []

        for _, row in df.iterrows():
            # BaoStock交易日历对所有A股市场都通用，需要为每个市场生成记录
            for market in ["SZ", "SS"]:  # 深交所和上交所
                calendar_list.append(
                    {
                        "trade_date": row["calendar_date"],
                        "is_trading": int(row["is_trading_day"]),
                        "market": market,
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

    def _convert_daily_data(self, df: pd.DataFrame, symbol: str) -> Dict[str, Any]:
        """
        将BaoStock返回的DataFrame转换为符合接口规范的字典格式

        基于实际测试：BaoStock的rs.get_data()返回pandas DataFrame，
        需要转换为与其他适配器一致的格式：{"success": bool, "data": list, "count": int}
        """
        if df is None or df.empty:
            return {"success": False, "data": None, "error": "数据为空"}

        try:
            records = []
            for _, row in df.iterrows():
                # 安全的数值转换函数
                def safe_float(value, default=0.0):
                    if pd.isna(value) or value == "" or value is None:
                        return default
                    try:
                        return float(value)
                    except (ValueError, TypeError):
                        return default

                # 转换为标准记录格式，保留BaoStock的所有字段
                record = {
                    "symbol": symbol,
                    "date": str(row.get("date", "")),
                    "open": safe_float(row.get("open")),
                    "high": safe_float(row.get("high")),
                    "low": safe_float(row.get("low")),
                    "close": safe_float(row.get("close")),
                    "preclose": safe_float(row.get("preclose")),
                    "volume": safe_float(row.get("volume")),
                    "amount": safe_float(row.get("amount")),
                    "adjustflag": str(row.get("adjustflag", "")),
                    "turn": safe_float(row.get("turn")),
                    "tradestatus": str(row.get("tradestatus", "")),
                    "pctChg": safe_float(row.get("pctChg")),
                    "peTTM": safe_float(row.get("peTTM")),
                    "pbMRQ": safe_float(row.get("pbMRQ")),
                    "psTTM": safe_float(row.get("psTTM")),
                    "pcfNcfTTM": safe_float(row.get("pcfNcfTTM")),
                    "isST": str(row.get("isST", "")),
                }

                # 基本数据验证
                if (
                    record["open"] > 0
                    and record["high"] > 0
                    and record["low"] > 0
                    and record["close"] > 0
                ):
                    records.append(record)

            return {"success": True, "data": records, "count": len(records)}

        except Exception as e:
            logger.error(f"转换BaoStock DataFrame失败: {e}")
            return {"success": False, "data": None, "error": str(e)}
