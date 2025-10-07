"""
mootdx数据源适配器

基于通达信数据源的统一接口实现，提供离线和在线两种数据获取方式。
"""

import logging
import os
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import pandas as pd

from .base import BaseDataSource, DataSourceDataError

logger = logging.getLogger(__name__)


class MootdxAdapter(BaseDataSource):
    """mootdx数据源适配器 - 基于通达信"""

    def __init__(self, config: Dict[str, Any] = None):
        """
        初始化mootdx适配器

        Args:
            config: 配置参数
                - tdx_dir: 通达信安装目录路径
                - use_online: 是否使用在线接口（默认True）
                - market: 市场类型（std=标准市场, ext=扩展市场）
                - financial_cache_dir: 财务数据缓存目录
        """
        super().__init__("mootdx", config)
        self._reader = None
        self._quotes = None
        self._financial_cache = {}  # 财务数据内存缓存

        # mootdx特定配置
        self.tdx_dir = self.config.get("tdx_dir", "/mnt/c/new_tdx")
        self.use_online = self.config.get("use_online", True)
        self.market = self.config.get("market", "std")
        self.financial_cache_dir = self.config.get(
            "financial_cache_dir", "./data/mootdx_financial"
        )

        # 创建财务数据缓存目录
        Path(self.financial_cache_dir).mkdir(parents=True, exist_ok=True)

    def connect(self) -> bool:
        """连接mootdx"""
        try:
            from mootdx.quotes import Quotes
            from mootdx.reader import Reader

            # 初始化Reader（离线数据读取）
            self._reader = Reader.factory(market=self.market, tdxdir=self.tdx_dir)

            # 初始化Quotes（在线数据获取）
            if self.use_online:
                self._quotes = Quotes.factory(
                    market=self.market,
                    multithread=True,
                    heartbeat=True,
                    timeout=self.config.get("timeout", 15),
                )

            self._connected = True
            logger.info(
                f"mootdx连接成功 (tdx_dir={self.tdx_dir}, use_online={self.use_online})"
            )
            return True

        except Exception as e:
            logger.error(f"mootdx连接失败: {e}")
            return False

    def disconnect(self):
        """断开mootdx连接"""
        self._reader = None
        self._quotes = None
        self._connected = False
        logger.info("mootdx连接已断开")

    def is_connected(self) -> bool:
        """检查连接状态"""
        return self._connected and self._reader is not None

    def get_daily_data(
        self,
        symbol: str,
        start_date: Union[str, date],
        end_date: Union[str, date] = None,
    ) -> Dict[str, Any]:
        """
        获取日线数据

        Args:
            symbol: 股票代码 (如: 000001.SZ 或 000001)
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
            try:
                # 转换为mootdx格式（6位代码）
                tdx_symbol = self._convert_to_tdx_symbol(symbol)

                # 1. 优先尝试本地读取
                try:
                    df = self._reader.daily(symbol=tdx_symbol)

                    if df is not None and not df.empty:
                        # 应用日期过滤
                        df = self._filter_by_date(df, start_date, end_date)
                        return self._convert_daily_data(df, symbol)

                except Exception as e:
                    logger.warning(f"本地读取日线数据失败 {symbol}: {e}")

                # 2. 降级到在线接口
                if self.use_online and self._quotes:
                    return self._fetch_online_daily(
                        tdx_symbol, symbol, start_date, end_date
                    )

                raise DataSourceDataError(f"无法获取日线数据: {symbol}")

            except Exception as e:
                logger.error(f"获取日线数据失败 {symbol}: {e}")
                raise DataSourceDataError(f"获取日线数据失败: {e}")

        return self._retry_request(_fetch_data)

    def _fetch_online_daily(
        self, tdx_symbol: str, symbol: str, start_date: str, end_date: str
    ) -> Dict[str, Any]:
        """在线获取日线数据"""
        try:
            # frequency=4 表示日线
            df = self._quotes.bars(
                symbol=tdx_symbol, frequency=4, start=0, offset=10000
            )

            if df is not None and not df.empty:
                # 标准化列名
                df = self._standardize_columns(df)
                # 应用日期过滤
                df = self._filter_by_date(df, start_date, end_date)
                return self._convert_daily_data(df, symbol)

            raise DataSourceDataError(f"在线获取日线数据为空: {symbol}")

        except Exception as e:
            logger.error(f"在线获取日线数据失败 {symbol}: {e}")
            raise DataSourceDataError(f"在线获取日线数据失败: {e}")

    def get_minute_data(
        self, symbol: str, trade_date: Union[str, date], frequency: str = "5m"
    ) -> Dict[str, Any]:
        """
        获取分钟线数据

        Args:
            symbol: 股票代码
            trade_date: 交易日期
            frequency: 频率 (1m/5m/15m/30m/60m)

        Returns:
            Dict[str, Any]: 分钟线数据
        """
        if not self.is_connected():
            self.connect()

        symbol = self._normalize_symbol(symbol)
        trade_date = self._normalize_date(trade_date)
        frequency = self._validate_frequency(frequency)

        def _fetch_data():
            try:
                tdx_symbol = self._convert_to_tdx_symbol(symbol)

                # 频率映射 (mootdx frequency编号)
                freq_map = {
                    "1m": 7,  # 1分钟
                    "5m": 0,  # 5分钟
                    "15m": 1,  # 15分钟
                    "30m": 2,  # 30分钟
                    "60m": 3,  # 60分钟
                }

                if frequency not in freq_map:
                    raise DataSourceDataError(f"mootdx不支持频率: {frequency}")

                freq_code = freq_map[frequency]

                if self.use_online and self._quotes:
                    df = self._quotes.bars(
                        symbol=tdx_symbol, frequency=freq_code, start=0, offset=1000
                    )
                else:
                    # 本地分钟数据读取（如果支持）
                    df = self._reader.minute(symbol=tdx_symbol)

                if df is None or df.empty:
                    raise DataSourceDataError(
                        f"未获取到分钟线数据: {symbol} {trade_date}"
                    )

                # 标准化并过滤指定日期
                df = self._standardize_columns(df)
                df = self._filter_by_date(df, trade_date, trade_date)

                return self._convert_minute_data(df, symbol, trade_date, frequency)

            except Exception as e:
                logger.error(f"获取分钟线数据失败 {symbol} {trade_date}: {e}")
                raise DataSourceDataError(f"获取分钟线数据失败: {e}")

        return self._retry_request(_fetch_data)

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
            try:
                if symbol:
                    # 获取单个股票信息
                    symbol_norm = self._normalize_symbol(symbol)
                    tdx_symbol = self._convert_to_tdx_symbol(symbol_norm)

                    # 从股票列表中查找
                    market_code = self._get_market_code(symbol_norm)
                    df = self._quotes.stocks(market=market_code)

                    if not df.empty:
                        stock_row = df[df["code"] == tdx_symbol]
                        if not stock_row.empty:
                            return self._convert_stock_info_single(
                                stock_row.iloc[0], symbol_norm
                            )

                    return {"success": False, "data": None, "error": "股票不存在"}

                else:
                    # 获取所有股票列表
                    # 注意：mootdx 在线获取股票列表非常慢（>60秒）
                    # 建议使用 BaoStock 或从数据库读取已缓存的股票列表
                    logger.warning(
                        "mootdx 在线获取股票列表很慢，建议使用 BaoStock 或从数据库读取"
                    )

                    # 如果确实需要使用 mootdx，则执行以下操作（但会很慢）
                    df_sz = self._quotes.stocks(market=0)  # 深圳
                    df_ss = self._quotes.stocks(market=1)  # 上海

                    df = pd.concat([df_sz, df_ss], ignore_index=True)
                    return self._convert_stock_list_data(df)

            except Exception as e:
                logger.error(f"获取股票信息失败: {e}")
                return {"success": False, "data": None, "error": str(e)}

        return self._retry_request(_fetch_data)

    def get_fundamentals(
        self, symbol: str, report_date: Union[str, date], report_type: str = "Q4"
    ) -> Dict[str, Any]:
        """
        获取财务数据（使用 Affair 模块下载真实财务数据）

        Args:
            symbol: 股票代码
            report_date: 报告期（如 "2023-12-31"）
            report_type: 报告类型（Q1/Q2/Q3/Q4）

        Returns:
            Dict[str, Any]: 财务数据
        """
        if not self.is_connected():
            self.connect()

        symbol = self._normalize_symbol(symbol)
        report_date = self._normalize_date(report_date)

        def _fetch_data():
            try:
                # 转换报告期为文件名格式
                filename = self._get_financial_filename(report_date, report_type)

                # 下载并解析财务数据
                df = self._get_financial_data_for_period(filename)

                if df is None or df.empty:
                    raise DataSourceDataError(
                        f"无法获取财务数据: {symbol} {report_date}"
                    )

                # 提取该股票的财务数据
                tdx_symbol = self._convert_to_tdx_symbol(symbol)

                # DataFrame的索引是股票代码，而不是'code'列
                if tdx_symbol in df.index:
                    stock_data = df.loc[tdx_symbol]
                else:
                    raise DataSourceDataError(
                        f"财务数据中未找到股票: {symbol} (TDX代码: {tdx_symbol})"
                    )

                # 转换为标准格式
                return self._convert_financial_data(
                    stock_data.to_dict(), symbol, report_date
                )

            except Exception as e:
                logger.error(f"获取财务数据失败 {symbol}: {e}")
                raise DataSourceDataError(f"获取财务数据失败: {e}")

        return self._retry_request(_fetch_data)

    def batch_import_financial_data(
        self, report_date: Union[str, date], report_type: str = "Q4"
    ) -> Dict[str, Any]:
        """
        批量导入指定报告期的所有股票财务数据

        这个方法专为批量操作设计，一次性下载并解析包含所有股票的财务数据文件，
        避免了单股查询时重复下载和解析的性能问题。

        注意：为了提高性能，此方法返回简化的原始数据，不进行复杂的字段映射和转换。

        Args:
            report_date: 报告期（如 "2019-12-31"）
            report_type: 报告类型（Q1/Q2/Q3/Q4）

        Returns:
            Dict[str, Any]: {
                "success": True/False,
                "data": [财务数据记录列表],
                "count": 记录数量,
                "report_date": 报告期,
                "report_type": 报告类型,
                "error": 错误信息（如果失败）
            }
        """
        if not self.is_connected():
            self.connect()

        report_date = self._normalize_date(report_date)

        def _fetch_data():
            try:
                # 生成文件名
                filename = self._get_financial_filename(report_date, report_type)
                logger.info(f"批量导入财务数据: {filename}")

                # 下载并解析整个文件（包含所有股票）
                df = self._get_financial_data_for_period(filename)

                if df is None or df.empty:
                    raise DataSourceDataError(f"无法获取财务数据文件: {filename}")

                # 转换所有股票的财务数据（简化版本，不做复杂转换）
                records = []
                success_count = 0
                error_count = 0

                for tdx_symbol in df.index:
                    try:
                        # 转换为标准代码格式
                        symbol = self._convert_from_tdx_symbol(tdx_symbol)

                        # 获取该股票的财务数据
                        stock_data = df.loc[tdx_symbol]

                        # 转换为字典，过滤掉字典类型的值（可能是其他股票的数据）
                        raw_dict = stock_data.to_dict()

                        # 清理数据：移除字典类型的值（这些是DataFrame宽格式导致的多余列）
                        cleaned_dict = {}
                        for key, value in raw_dict.items():
                            # 只保留基本类型的值
                            if not isinstance(value, (dict, list, tuple)):
                                cleaned_dict[key] = value
                            else:
                                logger.debug(
                                    f"批量导入: 跳过非标量字段 {key} = {value}"
                                )

                        # 转换为字典（简化版本）
                        record = {
                            "symbol": symbol,
                            "report_date": report_date,
                            "report_type": report_type,
                            "data": cleaned_dict,  # 清理后的数据
                        }

                        records.append(record)
                        success_count += 1

                    except Exception as e:
                        error_count += 1
                        logger.debug(f"处理股票 {tdx_symbol} 失败: {e}")
                        continue

                logger.info(
                    f"批量导入完成: 成功 {success_count} 条，失败 {error_count} 条"
                )

                return {
                    "success": True,
                    "data": records,
                    "count": len(records),
                    "report_date": report_date,
                    "report_type": report_type,
                    "success_count": success_count,
                    "error_count": error_count,
                }

            except Exception as e:
                logger.error(f"批量导入财务数据失败: {e}")
                return {
                    "success": False,
                    "data": None,
                    "error": str(e),
                    "report_date": report_date,
                    "report_type": report_type,
                }

        return self._retry_request(_fetch_data)

    def _get_financial_filename(self, report_date: str, report_type: str = "Q4") -> str:
        """
        根据报告期和报告类型生成财务数据文件名

        Args:
            report_date: 报告期 (YYYY-MM-DD)
            report_type: 报告类型

        Returns:
            文件名，如 "gpcw20231231.zip"
        """
        # 解析报告期
        year, month, day = report_date.split("-")

        # 根据报告类型确定月份和日期
        quarter_map = {
            "Q1": "0331",
            "Q2": "0630",
            "Q3": "0930",
            "Q4": "1231",
        }

        period = quarter_map.get(report_type, "1231")
        return f"gpcw{year}{period}.zip"

    def _get_financial_data_for_period(self, filename: str) -> Optional[pd.DataFrame]:
        """
        获取指定报告期的所有股票财务数据

        Args:
            filename: 财务数据文件名

        Returns:
            DataFrame 包含所有股票的财务数据
        """
        try:
            from mootdx.affair import Affair

            # 检查内存缓存
            if filename in self._financial_cache:
                logger.debug(f"从内存缓存读取: {filename}")
                return self._financial_cache[filename]

            # 检查本地文件缓存
            local_file = os.path.join(self.financial_cache_dir, filename)
            dat_file = local_file.replace(".zip", ".dat")

            if os.path.exists(dat_file):
                # 解析本地 DAT 文件
                logger.debug(f"从本地文件读取: {dat_file}")
                df = Affair.parse(downdir=self.financial_cache_dir, filename=filename)
            elif os.path.exists(local_file):
                # 解析本地 ZIP 文件
                logger.debug(f"解析本地ZIP: {local_file}")
                df = Affair.parse(downdir=self.financial_cache_dir, filename=filename)
            else:
                # 下载并解析
                logger.info(f"下载财务数据: {filename}")
                Affair.fetch(downdir=self.financial_cache_dir, filename=filename)
                df = Affair.parse(downdir=self.financial_cache_dir, filename=filename)

            # 处理重复列名（mootdx财务数据有重复列名）
            if df is not None and not df.empty:
                df = self._deduplicate_columns(df)

                # 可选：推断未命名列的名称（默认关闭，可通过配置开启）
                # df = self._infer_column_names(df, confidence_level='high')

            # 缓存到内存（限制缓存大小）
            if len(self._financial_cache) < 10:  # 最多缓存10个报告期
                self._financial_cache[filename] = df

            return df

        except Exception as e:
            logger.error(f"获取财务数据文件失败 {filename}: {e}")
            return None

    def get_valuation_data(
        self, symbol: str, trade_date: Union[str, date]
    ) -> Dict[str, Any]:
        """
        获取估值数据

        注意：mootdx 本身不提供完整的估值数据（PE/PB）
        这里使用 finance() 获取基础财务数据，计算简单估值指标

        Args:
            symbol: 股票代码
            trade_date: 交易日期

        Returns:
            Dict[str, Any]: 估值数据
        """
        if not self.is_connected():
            self.connect()

        symbol = self._normalize_symbol(symbol)
        trade_date = self._normalize_date(trade_date)

        def _fetch_data():
            try:
                tdx_symbol = self._convert_to_tdx_symbol(symbol)

                # 使用 finance() 获取基础财务信息
                if self.use_online and self._quotes:
                    df = self._quotes.finance(symbol=tdx_symbol)

                    if df is not None and not df.empty:
                        row = df.iloc[0]

                        # 提取财务数据
                        total_shares = self._safe_float(
                            row.get("zongguben", 0)
                        )  # 总股本
                        bps = self._safe_float(
                            row.get("meigujingzichan", 0)
                        )  # 每股净资产

                        # 从行情数据获取股价（如果可用）
                        price = None
                        try:
                            quote_df = self._quotes.quotes(symbols=[tdx_symbol])
                            if not quote_df.empty:
                                price = self._safe_float(
                                    quote_df.iloc[0].get("price", 0)
                                )
                        except Exception:
                            pass

                        # 计算简单估值指标
                        pb_ratio = None
                        if bps and bps > 0 and price:
                            pb_ratio = price / bps

                        return {
                            "success": True,
                            "data": {
                                "symbol": symbol,
                                "date": trade_date,
                                "pb_ratio": pb_ratio,
                                "pe_ratio": None,  # mootdx 不直接提供
                                "ps_ratio": None,
                                "pcf_ratio": None,
                                "bps": bps,
                                "total_shares": total_shares,
                                "source": "mootdx",
                            },
                        }

                # 如果无法获取，返回空数据（让 BaoStock 作为主数据源）
                return {
                    "success": False,
                    "data": None,
                    "error": "mootdx不提供完整估值数据，建议使用BaoStock",
                }

            except Exception as e:
                logger.error(f"获取估值数据失败 {symbol}: {e}")
                return {"success": False, "data": None, "error": str(e)}

        return self._retry_request(_fetch_data)

    def _convert_to_tdx_symbol(self, symbol: str) -> str:
        """转换为通达信股票代码格式（6位纯数字）"""
        if "." in symbol:
            return symbol.split(".")[0]
        return symbol

    def _convert_from_tdx_symbol(self, tdx_symbol: str) -> str:
        """
        将通达信代码转换为标准格式（添加市场后缀）

        Args:
            tdx_symbol: 通达信代码（6位纯数字，如 "000001"）

        Returns:
            str: 标准格式代码（如 "000001.SZ"）
        """
        code = str(tdx_symbol).strip()

        # 确保是6位代码
        if len(code) == 6:
            # 深圳市场：0开头（主板）、3开头（创业板）
            if code.startswith("0") or code.startswith("3"):
                return f"{code}.SZ"
            # 上海市场：6开头（主板）、9开头（科创板）
            elif code.startswith("6") or code.startswith("9"):
                return f"{code}.SS"
            else:
                # 默认深圳
                return f"{code}.SZ"
        else:
            # 如果已经有后缀或格式不对，直接返回
            return code

    def _get_market_code(self, symbol: str) -> int:
        """
        获取市场代码

        Args:
            symbol: 股票代码 (如: 000001.SZ)

        Returns:
            int: 0=深圳, 1=上海
        """
        code = self._convert_to_tdx_symbol(symbol)

        if code.startswith("0") or code.startswith("3"):
            return 0  # 深圳
        elif code.startswith("6") or code.startswith("9"):
            return 1  # 上海
        else:
            return 0  # 默认深圳

    def _standardize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        标准化DataFrame列名

        mootdx可能返回中文列名或英文列名，统一转换为英文小写
        """
        column_mapping = {
            # 中文列名映射
            "日期": "date",
            "代码": "code",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",
            "成交额": "amount",
            # 英文列名统一小写
            "Date": "date",
            "Code": "code",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
            "Amount": "amount",
        }

        # 重命名存在的列
        df = df.rename(
            columns={k: v for k, v in column_mapping.items() if k in df.columns}
        )

        return df

    def _filter_by_date(
        self, df: pd.DataFrame, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """按日期范围过滤DataFrame"""
        if df.empty:
            return df

        # 确保有日期列或日期索引
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            df = df[
                (df["date"] >= pd.to_datetime(start_date))
                & (df["date"] <= pd.to_datetime(end_date))
            ]
        elif isinstance(df.index, pd.DatetimeIndex):
            df = df[
                (df.index >= pd.to_datetime(start_date))
                & (df.index <= pd.to_datetime(end_date))
            ]

        return df

    def _convert_daily_data(self, df: pd.DataFrame, symbol: str) -> Dict[str, Any]:
        """转换日线数据格式"""
        if df is None or df.empty:
            return {"success": False, "data": None, "error": "数据为空"}

        try:
            records = []

            for idx, row in df.iterrows():
                # 处理日期
                if "date" in row:
                    date_val = row["date"]
                elif isinstance(idx, pd.Timestamp):
                    date_val = idx
                else:
                    continue

                record = {
                    "symbol": symbol,
                    "date": (
                        str(date_val)[:10]
                        if hasattr(date_val, "strftime")
                        else str(date_val)[:10]
                    ),
                    "open": float(row.get("open", 0) or 0),
                    "high": float(row.get("high", 0) or 0),
                    "low": float(row.get("low", 0) or 0),
                    "close": float(row.get("close", 0) or 0),
                    "volume": float(row.get("volume", 0) or 0),
                    "amount": float(row.get("amount", 0) or 0),
                }

                # 验证数据有效性
                if (
                    record["open"] > 0
                    and record["high"] > 0
                    and record["low"] > 0
                    and record["close"] > 0
                ):
                    records.append(record)

            return {"success": True, "data": records, "count": len(records)}

        except Exception as e:
            logger.error(f"转换日线数据失败: {e}")
            return {"success": False, "data": None, "error": str(e)}

    def _convert_minute_data(
        self, df: pd.DataFrame, symbol: str, trade_date: str, frequency: str
    ) -> Dict[str, Any]:
        """转换分钟数据格式"""
        if df is None or df.empty:
            return {"success": False, "data": None, "error": "分钟数据为空"}

        try:
            records = []

            for idx, row in df.iterrows():
                record = {
                    "symbol": symbol,
                    "datetime": str(row.get("datetime", idx)),
                    "open": float(row.get("open", 0) or 0),
                    "high": float(row.get("high", 0) or 0),
                    "low": float(row.get("low", 0) or 0),
                    "close": float(row.get("close", 0) or 0),
                    "volume": float(row.get("volume", 0) or 0),
                    "amount": float(row.get("amount", 0) or 0),
                    "frequency": frequency,
                }

                if record["close"] > 0:
                    records.append(record)

            return {
                "success": True,
                "data": records,
                "count": len(records),
                "frequency": frequency,
            }

        except Exception as e:
            logger.error(f"转换分钟数据失败: {e}")
            return {"success": False, "data": None, "error": str(e)}

    def _convert_stock_list_data(self, df: pd.DataFrame) -> Dict[str, Any]:
        """转换股票列表数据格式"""
        if df is None or df.empty:
            return {"success": False, "data": None, "error": "股票列表为空"}

        try:
            stocks = []

            for _, row in df.iterrows():
                code = str(row.get("code", ""))
                name = str(row.get("name", ""))

                if code and name and len(code) == 6:
                    # 添加市场后缀
                    if code.startswith("0") or code.startswith("3"):
                        symbol = f"{code}.SZ"
                    elif code.startswith("6") or code.startswith("9"):
                        symbol = f"{code}.SS"
                    else:
                        symbol = f"{code}.SZ"

                    stock_data = {
                        "symbol": symbol,
                        "name": name.strip(),
                        "market": symbol.split(".")[1],
                    }
                    stocks.append(stock_data)

            return {"success": True, "data": stocks, "count": len(stocks)}

        except Exception as e:
            logger.error(f"转换股票列表数据失败: {e}")
            return {"success": False, "data": None, "error": str(e)}

    def _convert_stock_info_single(self, row: pd.Series, symbol: str) -> Dict[str, Any]:
        """转换单个股票信息"""
        try:
            stock_info = {
                "symbol": symbol,
                "name": str(row.get("name", "")),
                "market": symbol.split(".")[1] if "." in symbol else "SZ",
            }

            return {"success": True, "data": stock_info}

        except Exception as e:
            logger.error(f"转换股票信息失败 {symbol}: {e}")
            return {"success": False, "data": None, "error": str(e)}

    def _convert_financial_data(
        self, data_dict: dict, symbol: str, report_date: str
    ) -> Dict[str, Any]:
        """
        转换财务数据格式（使用FINVALUE字段映射）

        Args:
            data_dict: mootdx Affair 返回的单行数据（字典）
            symbol: 股票代码
            report_date: 报告期

        Returns:
            标准格式的财务数据
        """
        try:
            from .mootdx_finvalue_fields import (
                get_required_fields_for_db,
                map_financial_data,
            )

            # 使用字段映射转换数据
            mapped_data = map_financial_data(data_dict)

            # 获取数据库所需字段模板
            financial_data = get_required_fields_for_db()

            # 填充实际数据
            financial_data["symbol"] = symbol
            financial_data["report_date"] = report_date

            # 更新已映射的字段
            for key, value in mapped_data.items():
                if key in financial_data:
                    financial_data[key] = self._safe_float(value, 0.0)

            # 计算派生指标
            if financial_data["revenue"] and financial_data["revenue"] > 0:
                # 毛利率 = (营业收入 - 营业成本) / 营业收入
                operating_cost = mapped_data.get("operating_cost", 0)
                if operating_cost:
                    financial_data["gross_margin"] = (
                        (financial_data["revenue"] - operating_cost)
                        / financial_data["revenue"]
                        * 100
                    )

                # 净利率 = 净利润 / 营业收入
                if financial_data["net_profit"]:
                    financial_data["net_margin"] = (
                        financial_data["net_profit"] / financial_data["revenue"] * 100
                    )

            # 资产负债率 = 总负债 / 总资产
            if financial_data["total_assets"] and financial_data["total_assets"] > 0:
                financial_data["debt_ratio"] = (
                    financial_data["total_liabilities"]
                    / financial_data["total_assets"]
                    * 100
                )

                # ROA = 净利润 / 总资产
                if financial_data["net_profit"]:
                    financial_data["roa"] = (
                        financial_data["net_profit"]
                        / financial_data["total_assets"]
                        * 100
                    )

            return {"success": True, "data": financial_data}

        except Exception as e:
            logger.error(f"转换财务数据失败: {e}")
            logger.debug(f"原始数据: {data_dict}")
            return {"success": False, "data": None, "error": str(e)}

    def _convert_valuation_data(
        self, data: pd.Series, symbol: str, trade_date: str
    ) -> Dict[str, Any]:
        """转换估值数据格式"""
        try:
            return {
                "symbol": symbol,
                "date": trade_date,
                "pe_ratio": self._safe_float(data.get("pe", 0)),
                "pb_ratio": self._safe_float(data.get("pb", 0)),
                "ps_ratio": self._safe_float(data.get("ps", 0)),
                "market_cap": self._safe_float(data.get("market_cap", 0)),
                "circulating_cap": self._safe_float(data.get("circulating_cap", 0)),
                "source": "mootdx",
            }

        except Exception as e:
            logger.error(f"转换估值数据失败: {e}")
            return {}

    def _deduplicate_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        处理DataFrame的重复列名

        mootdx财务数据中有部分重复列名（如"净资产收益率"出现2次）。
        此方法通过为重复列添加后缀来去重，例如：
        - 第1个"净资产收益率" -> "净资产收益率"
        - 第2个"净资产收益率" -> "净资产收益率_2"

        Args:
            df: 原始DataFrame

        Returns:
            DataFrame: 去重后的DataFrame
        """
        # 检查是否有重复列名
        if df.columns.duplicated().any():
            # 记录原始列数
            original_col_count = len(df.columns)

            # 创建新列名列表
            new_columns = []
            col_counts = {}

            for col in df.columns:
                # 统计每个列名出现次数
                if col in col_counts:
                    col_counts[col] += 1
                    # 添加后缀
                    new_col = f"{col}_{col_counts[col]}"
                    new_columns.append(new_col)
                    logger.debug(f"重复列名重命名: {col} -> {new_col}")
                else:
                    col_counts[col] = 1
                    new_columns.append(col)

            # 更新DataFrame列名
            df.columns = new_columns

            # 记录处理结果
            duplicate_count = original_col_count - len(col_counts)
            logger.info(
                f"处理重复列名: {duplicate_count} 个重复列, 原始 {original_col_count} 列 -> 去重后 {len(df.columns)} 列"
            )

        return df

    def _infer_column_names(
        self, df: pd.DataFrame, confidence_level: str = "high"
    ) -> pd.DataFrame:
        """
        推断并重命名未命名列（col开头的列）

        mootdx财务数据中有238个未命名列（col323-col560），这是因为官方也不知道这些列的确切含义。
        此方法基于数据特征分析，为部分列提供推断的名称。

        Args:
            df: 原始DataFrame
            confidence_level: 推断确定性等级
                - 'high': 只重命名高确定性列（如col323-col326）
                - 'medium': 重命名高+中确定性列
                - 'low' or 'all': 重命名所有可推断的列
                - 'none': 不进行推断重命名

        Returns:
            DataFrame: 重命名后的DataFrame
        """
        if confidence_level == "none":
            return df

        try:
            from simtradedata.data_sources.mootdx_column_mappings import (
                rename_columns_with_inference,
            )

            # 生成重命名映射
            rename_map = rename_columns_with_inference(df, confidence_level)

            if rename_map:
                # 应用重命名
                df = df.rename(columns=rename_map)
                logger.info(
                    f"推断列名: 重命名了 {len(rename_map)} 个未命名列 (确定性等级: {confidence_level})"
                )
                logger.debug(f"重命名映射: {rename_map}")

        except ImportError:
            logger.warning("未找到列名映射模块，跳过列名推断")
        except Exception as e:
            logger.warning(f"列名推断失败: {e}")

        return df

    def _safe_float(self, value, default=0.0):
        """安全的浮点数转换"""
        try:
            if pd.isna(value) or value == "" or value is None:
                return default
            return float(value)
        except (ValueError, TypeError):
            return default
