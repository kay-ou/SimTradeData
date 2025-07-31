"""
数据处理引擎

统一的数据处理引擎，负责股票数据的获取、验证、清洗和存储。
"""

# 标准库导入
import logging
from datetime import date
from typing import Any, Dict, List, Optional

# 项目内导入
from ..config import Config
from ..core.base_manager import BaseManager
from ..core.error_handling import ValidationError, unified_error_handler
from ..data_sources.manager import DataSourceManager
from ..database.manager import DatabaseManager

logger = logging.getLogger(__name__)


class DataProcessingEngine(BaseManager):
    """数据处理引擎"""

    def __init__(
        self,
        db_manager: DatabaseManager,
        data_source_manager: DataSourceManager,
        config: Optional[Config] = None,
        **kwargs,
    ):
        """
        初始化数据处理引擎

        Args:
            db_manager: 数据库管理器
            data_source_manager: 数据源管理器
            config: 配置对象
        """
        super().__init__(
            config=config,
            db_manager=db_manager,
            data_source_manager=data_source_manager,
            **kwargs,
        )

    def _init_specific_config(self):
        """初始化数据处理引擎特定配置"""
        self.enable_technical_indicators = self._get_config(
            "enable_technical_indicators", True
        )
        self.enable_valuations = self._get_config("enable_valuations", True)
        self.data_quality_threshold = self._get_config("data_quality_threshold", 0.8)
        self.max_price_change_pct = self._get_config("max_price_change_pct", 20.0)

    def _init_components(self):
        """初始化处理引擎组件"""
        # 验证依赖项存在
        if not hasattr(self, "db_manager") or self.db_manager is None:
            raise ValueError("数据库管理器不能为空")
        if not hasattr(self, "data_source_manager") or self.data_source_manager is None:
            raise ValueError("数据源管理器不能为空")

    def _get_required_attributes(self) -> List[str]:
        """必需属性列表"""
        return ["db_manager", "data_source_manager"]

    @unified_error_handler(return_dict=True)
    def process_symbol_data(
        self,
        symbol: str,
        start_date: date,
        end_date: Optional[date] = None,
        frequency: str = "1d",
        force_update: bool = False,
    ) -> Dict[str, Any]:
        """处理股票数据"""
        if not symbol:
            raise ValidationError("股票代码不能为空")

        if not start_date:
            raise ValidationError("开始日期不能为空")

        if end_date is None:
            end_date = start_date

        self._log_method_start(
            "process_symbol_data",
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
        )

        result = {
            "symbol": symbol,
            "processed_dates": [],
            "skipped_dates": [],
            "failed_dates": [],
            "total_records": 0,
        }

        try:
            # 获取原始数据
            self.logger.debug(f"开始获取原始数据: {symbol}")
            raw_data = self.data_source_manager.get_daily_data(
                symbol, start_date, end_date
            )
            self.logger.debug(f"原始数据获取完成，类型: {type(raw_data)}")

            # 检查数据格式
            if raw_data is None:
                self._log_warning("process_symbol_data", f"未获取到数据: {symbol}")
                return result

            # 详细记录原始数据情况，特别是空字典的情况
            try:
                if isinstance(raw_data, dict):
                    if len(raw_data) == 0:
                        self._log_warning(
                            "process_symbol_data",
                            f"数据源返回空字典: {symbol}, 日期范围: {start_date} 到 {end_date}",
                        )
                        return result
                elif hasattr(raw_data, "empty") and raw_data.empty:
                    self._log_warning(
                        "process_symbol_data",
                        f"数据源返回空DataFrame: {symbol}, 日期范围: {start_date} 到 {end_date}",
                    )
                    return result
            except Exception as e:
                self.logger.error(f"检查数据格式时出错: {e}")
                raise

            # 处理嵌套的装饰器返回格式
            if isinstance(raw_data, dict) and "data" in raw_data:
                raw_data = raw_data["data"]
                if isinstance(raw_data, dict) and "data" in raw_data:
                    raw_data = raw_data["data"]

            self.logger.debug(f"处理后数据类型: {type(raw_data)}")
            try:
                if hasattr(raw_data, "shape"):
                    self.logger.debug(f"DataFrame形状: {raw_data.shape}")
                    self.logger.debug(f"DataFrame列数: {len(raw_data.columns)}")
                    self.logger.debug(f"DataFrame列名: {list(raw_data.columns)}")
            except Exception as e:
                self.logger.error(f"检查DataFrame属性失败: {e}")
                raise

            # 如果数据是DataFrame，进行批量处理和计算
            if hasattr(raw_data, "iterrows") and hasattr(raw_data, "sort_values"):
                self.logger.debug("开始DataFrame数据处理")
                processed_count = self._process_dataframe_data(raw_data, symbol, result)
                result["total_records"] = processed_count
            elif isinstance(raw_data, list):
                self.logger.debug("开始列表数据处理")
                # 如果是列表格式，转换为逐行处理
                for item in raw_data:
                    try:
                        validated_data = self._validate_data(item)
                        trade_date = item.get("date", str(start_date))
                        self._store_market_data(validated_data, symbol, trade_date)
                        result["processed_dates"].append(trade_date)
                    except Exception as e:
                        self._log_error(
                            "process_symbol_data", e, symbol=symbol, item=item
                        )
                        result["failed_dates"].append(str(item.get("date", start_date)))

                result["total_records"] = len(result["processed_dates"])
            elif isinstance(raw_data, dict):
                # 处理字典类型数据（包括空字典）
                if len(raw_data) == 0:
                    self._log_warning(
                        "process_symbol_data",
                        f"数据源返回空字典: {symbol}, 日期范围: {start_date} 到 {end_date}",
                    )
                else:
                    # 非空字典，可能是其他格式的数据，尝试处理
                    self._log_warning(
                        "process_symbol_data",
                        f"收到字典格式数据但未能处理: {symbol}, 键: {list(raw_data.keys())}",
                    )
            else:
                # 详细记录不支持的数据格式，包括数据内容用于调试
                self._log_warning(
                    "process_symbol_data",
                    f"不支持的数据格式: {type(raw_data)}, 数据内容: {raw_data}, 符号: {symbol}",
                )

        except Exception as e:
            self._log_error("process_symbol_data", e, symbol=symbol)
            result["failed_dates"].append(f"{start_date}~{end_date}")

        self._log_method_end(
            "process_symbol_data", records=result["total_records"], symbol=symbol
        )

        return result

    def _process_dataframe_data(self, df, symbol: str, result: Dict[str, Any]) -> int:
        """处理DataFrame格式的数据，计算衍生字段"""

        if df.empty:
            return 0

        try:
            # 确保DataFrame有必要的列
            required_columns = ["date", "open", "high", "low", "close", "volume"]
            missing_cols = [col for col in required_columns if col not in df.columns]
            if missing_cols:
                self._log_warning(
                    "_process_dataframe_data", f"缺少必要列: {missing_cols}"
                )
                return 0

            # 按日期排序
            df = df.sort_values("date").copy()

            # 计算衍生字段
            df = self._calculate_derived_fields(df, symbol)

            # 批量存储到数据库
            records_stored = 0
            for row_idx, (_, row) in enumerate(df.iterrows()):
                try:
                    self.logger.debug(f"处理第 {row_idx+1} 行数据，索引: {row.name}")

                    # 转换为字典并验证
                    try:
                        row_dict = row.to_dict()
                        self.logger.debug(
                            f"row.to_dict() 成功，字典大小: {len(row_dict)}"
                        )
                    except Exception as e:
                        self.logger.error(f"row.to_dict() 失败: {e}")
                        raise

                    try:
                        validated_data = self._validate_and_prepare_data(row_dict)
                        self.logger.debug(f"数据验证成功")
                    except Exception as e:
                        self.logger.error(f"数据验证失败: {e}")
                        raise

                    # 存储数据
                    try:
                        self._store_enhanced_market_data(
                            validated_data, symbol, row_dict.get("date")
                        )
                        self.logger.debug(f"数据存储成功")
                    except Exception as e:
                        self.logger.error(f"数据存储失败: {e}")
                        raise

                    result["processed_dates"].append(str(row_dict.get("date")))
                    records_stored += 1

                except Exception as e:
                    self._log_error(
                        "_process_dataframe_data",
                        e,
                        symbol=symbol,
                        date=row.get("date"),
                        row_index=row_idx,
                    )
                    result["failed_dates"].append(str(row.get("date", "unknown")))

            return records_stored

        except Exception as e:
            self._log_error("_process_dataframe_data", e, symbol=symbol)
            return 0

    def _calculate_derived_fields(self, df, symbol: str):
        """计算衍生字段"""
        import numpy as np
        import pandas as pd

        # 调试信息
        self.logger.debug(f"计算衍生字段开始: {symbol}, DataFrame形状: {df.shape}")
        self.logger.debug(f"DataFrame列: {list(df.columns)}")
        self.logger.debug(f"DataFrame数据类型: {df.dtypes}")

        # 确保数据按日期排序
        df = df.sort_values("date")

        # 确保数值列为float类型
        numeric_columns = ["open", "high", "low", "close", "volume", "amount"]
        for col in numeric_columns:
            if col in df.columns:
                try:
                    old_type = df[col].dtype
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                    self.logger.debug(
                        f"列 {col} 类型转换: {old_type} -> {df[col].dtype}"
                    )
                except Exception as e:
                    self.logger.warning(f"列 {col} 类型转换失败: {e}")

        # 计算前一日收盘价（向前填充）
        try:
            df["prev_close"] = df["close"].shift(1)
        except Exception as e:
            self.logger.error(f"计算prev_close失败: {e}")
            raise

        # 计算涨跌额
        try:
            df["change_amount"] = df["close"] - df["prev_close"]
        except Exception as e:
            self.logger.error(f"计算change_amount失败: {e}")
            raise

        # 计算涨跌幅（百分比）
        try:
            df["change_percent"] = np.where(
                df["prev_close"] > 0,
                (df["change_amount"] / df["prev_close"] * 100).round(4),
                0.0,
            )
        except Exception as e:
            self.logger.error(f"计算change_percent失败: {e}")
            raise

        # 计算振幅
        try:
            df["amplitude"] = np.where(
                df["prev_close"] > 0,
                ((df["high"] - df["low"]) / df["prev_close"] * 100).round(4),
                0.0,
            )
        except Exception as e:
            self.logger.error(f"计算amplitude失败: {e}")
            raise

        # 计算换手率（如果有流通股本数据）
        # 这里暂时设为None，后续可以从股票信息表获取流通股本
        df["turnover_rate"] = None

        # 计算涨跌停价格（简化版本，假设10%涨跌停）
        try:
            df["high_limit"] = np.where(
                df["prev_close"] > 0, (df["prev_close"] * 1.1).round(2), None
            )
            df["low_limit"] = np.where(
                df["prev_close"] > 0, (df["prev_close"] * 0.9).round(2), None
            )
        except Exception as e:
            self.logger.error(f"计算涨跌停价格失败: {e}")
            raise

        # 判断是否涨停/跌停（处理None值）
        try:
            df["is_limit_up"] = False
            df["is_limit_down"] = False

            # 只对有涨跌停价格的行进行判断
            valid_high_limit = df["high_limit"].notna()
            valid_low_limit = df["low_limit"].notna()

            if valid_high_limit.any():
                df.loc[valid_high_limit, "is_limit_up"] = (
                    df.loc[valid_high_limit, "close"]
                    >= df.loc[valid_high_limit, "high_limit"]
                )
            if valid_low_limit.any():
                df.loc[valid_low_limit, "is_limit_down"] = (
                    df.loc[valid_low_limit, "close"]
                    <= df.loc[valid_low_limit, "low_limit"]
                )
        except Exception as e:
            self.logger.error(f"计算涨跌停判断失败: {e}")
            raise

        # 第一行数据没有前一日数据，设为默认值
        try:
            if len(df) > 0:
                first_idx = df.index[0]
                # 逐列设置，避免长度不匹配的问题
                df.loc[first_idx, "prev_close"] = None
                df.loc[first_idx, "change_amount"] = 0.0
                df.loc[first_idx, "change_percent"] = 0.0
                df.loc[first_idx, "amplitude"] = 0.0
                df.loc[first_idx, "high_limit"] = None
                df.loc[first_idx, "low_limit"] = None
                df.loc[first_idx, "is_limit_up"] = False
                df.loc[first_idx, "is_limit_down"] = False
        except Exception as e:
            self.logger.error(f"设置第一行默认值失败: {e}")
            raise

        self.logger.debug(f"为股票 {symbol} 计算了 {len(df)} 条记录的衍生字段")

        return df

    @unified_error_handler(return_dict=True)
    def process_stock_data(
        self,
        symbol: str,
        start_date: date,
        end_date: Optional[date] = None,
        frequency: str = "1d",
        force_update: bool = False,
    ) -> Dict[str, Any]:
        """处理股票数据 - 别名方法"""
        return self.process_symbol_data(
            symbol, start_date, end_date, frequency, force_update
        )

    def _validate_and_prepare_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """验证和准备增强数据（包含衍生字段）"""

        def safe_float(value, default=0.0):
            """安全的浮点数转换"""
            if value is None or value == "" or str(value).strip() == "":
                return default
            # 处理pandas NaN值
            import pandas as pd

            if pd.isna(value):
                return default
            try:
                return float(value)
            except (ValueError, TypeError):
                return default

        def safe_bool(value, default=False):
            """安全的布尔值转换"""
            if value is None:
                return default
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return value != 0
            return default

        # 基础字段验证 - 确保关键字段不为None
        validated_data = {
            "open": safe_float(data.get("open"), 0.0),
            "high": safe_float(data.get("high"), 0.0),
            "low": safe_float(data.get("low"), 0.0),
            "close": safe_float(data.get("close"), 0.0),
            "volume": safe_float(data.get("volume"), 0.0),  # 确保volume不为NULL
            "amount": safe_float(data.get("amount"), 0.0),
        }

        # 衍生字段验证
        validated_data.update(
            {
                "prev_close": safe_float(data.get("prev_close")),
                "change_amount": safe_float(data.get("change_amount")),
                "change_percent": safe_float(data.get("change_percent")),
                "amplitude": safe_float(data.get("amplitude")),
                "turnover_rate": (
                    safe_float(data.get("turnover_rate"))
                    if data.get("turnover_rate") is not None
                    else None
                ),
                "high_limit": (
                    safe_float(data.get("high_limit"))
                    if data.get("high_limit") is not None
                    else None
                ),
                "low_limit": (
                    safe_float(data.get("low_limit"))
                    if data.get("low_limit") is not None
                    else None
                ),
                "is_limit_up": safe_bool(data.get("is_limit_up")),
                "is_limit_down": safe_bool(data.get("is_limit_down")),
            }
        )

        # 数据质量检查
        if validated_data["high"] < validated_data["low"]:
            self._log_warning(
                "_validate_and_prepare_data", "最高价低于最低价，数据异常"
            )

        if validated_data["close"] <= 0:
            self._log_warning(
                "_validate_and_prepare_data", "收盘价为零或负数，数据异常"
            )

        return validated_data

    def _store_enhanced_market_data(
        self, data: Dict[str, Any], symbol: str, trade_date
    ):
        """存储增强的市场数据（包含衍生字段）"""
        sql = """
        INSERT OR REPLACE INTO market_data (
            symbol, date, frequency, open, high, low, close, volume, amount, 
            prev_close, change_amount, change_percent, amplitude, turnover_rate,
            high_limit, low_limit, is_limit_up, is_limit_down, source, quality_score
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        params = (
            symbol,
            str(trade_date),
            "1d",
            data.get("open") or 0,
            data.get("high") or 0,
            data.get("low") or 0,
            data.get("close") or 0,
            data.get("volume") or 0,  # 确保None时也使用0
            data.get("amount") or 0,
            data.get("prev_close"),
            data.get("change_amount") or 0,
            data.get("change_percent") or 0,
            data.get("amplitude") or 0,
            data.get("turnover_rate"),
            data.get("high_limit"),
            data.get("low_limit"),
            data.get("is_limit_up") or False,
            data.get("is_limit_down") or False,
            "processed_enhanced",
            100,  # 默认质量分数
        )

        self.db_manager.execute(sql, params)

    def _validate_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """数据验证和清洗"""
        # 字段映射
        field_mapping = {
            "open": ["open", "开盘"],
            "high": ["high", "最高"],
            "low": ["low", "最低"],
            "close": ["close", "收盘"],
            "volume": ["volume", "成交量"],
        }

        def safe_float(value, default=0.0):
            """安全的浮点数转换"""
            if value is None or value == "" or str(value).strip() == "":
                return default
            try:
                return float(value)
            except (ValueError, TypeError):
                return default

        validated_data = {}
        for std_field, possible_fields in field_mapping.items():
            for field in possible_fields:
                if field in data:
                    validated_data[std_field] = safe_float(data[field])
                    break
            else:
                validated_data[std_field] = 0.0

        # 数据质量检查
        if validated_data["high"] < validated_data["low"]:
            self._log_warning("_validate_data", "最高价低于最低价，数据异常")

        if validated_data["close"] <= 0:
            self._log_warning("_validate_data", "收盘价为零或负数，数据异常")

        return validated_data

    def _store_market_data(self, data: Dict[str, Any], symbol: str, trade_date):
        """存储市场数据"""
        sql = """
        INSERT OR REPLACE INTO market_data (
            symbol, date, frequency, open, high, low, close, volume, amount, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        params = (
            symbol,
            str(trade_date),
            "1d",
            data.get("open", 0),
            data.get("high", 0),
            data.get("low", 0),
            data.get("close", 0),
            data.get("volume", 0),
            data.get("amount", 0),
            "processed",
        )

        self.db_manager.execute(sql, params)

    @unified_error_handler(return_dict=True)
    def process_symbols_batch_pipeline(
        self,
        symbols: List[str],
        start_date: date,
        end_date: date,
        batch_size: int = 10,
        max_workers: int = 4,
        progress_bar=None,
    ) -> Dict[str, Any]:
        """批量处理股票数据"""
        if not symbols:
            raise ValidationError("股票代码列表不能为空")

        if not start_date:
            raise ValidationError("开始日期不能为空")

        if not end_date:
            raise ValidationError("结束日期不能为空")

        self._log_method_start(
            "process_symbols_batch_pipeline",
            symbols_count=len(symbols),
            start_date=start_date,
            end_date=end_date,
            batch_size=batch_size,
        )

        result = {
            "success_count": 0,
            "error_count": 0,
            "total_records": 0,
            "processed_symbols": [],
            "failed_symbols": [],
        }

        # 批量处理
        for symbol in symbols:
            try:
                symbol_result = self.process_symbol_data(symbol, start_date, end_date)

                # 检查是否成功（统一错误处理返回字典格式）
                if isinstance(symbol_result, dict) and symbol_result.get(
                    "success", True
                ):
                    data = symbol_result.get("data", symbol_result)
                    if data["total_records"] > 0:
                        result["success_count"] += 1
                        result["processed_symbols"].append(symbol)
                        result["total_records"] += data["total_records"]
                    else:
                        result["error_count"] += 1
                        result["failed_symbols"].append(symbol)
                else:
                    result["error_count"] += 1
                    result["failed_symbols"].append(symbol)

            except Exception as e:
                self._log_error("process_symbols_batch_pipeline", e, symbol=symbol)
                result["error_count"] += 1
                result["failed_symbols"].append(symbol)

            if progress_bar:
                progress_bar.update(1)

        self._log_method_end(
            "process_symbols_batch_pipeline",
            success_count=result["success_count"],
            error_count=result["error_count"],
            total_records=result["total_records"],
        )

        return result

    def _get_market_from_symbol(self, symbol: str) -> str:
        """从股票代码获取市场"""
        if symbol.endswith(".SZ"):
            return "SZ"
        elif symbol.endswith(".SS"):
            return "SS"
        elif symbol.endswith(".HK"):
            return "HK"
        elif symbol.endswith(".US"):
            return "US"
        else:
            return "CN"

    def _calculate_quality_score(self, result: Dict[str, Any]) -> float:
        """计算质量分数"""
        total = len(result["processed_dates"]) + len(result["failed_dates"])
        if total == 0:
            return 0.0
        return len(result["processed_dates"]) / total * 100
