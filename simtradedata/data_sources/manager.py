"""
数据源管理器

统一管理多个数据源，禁止fallback机制，确保数据源的明确性和可靠性。
"""

# 标准库导入
import logging
import time
from datetime import date
from typing import Any, Dict, List, Optional, Union

# 项目内导入
from ..config import Config
from ..core import BaseManager, ValidationError, unified_error_handler
from .akshare_adapter import AkShareAdapter
from .baostock_adapter import BaoStockAdapter
from .base import BaseDataSource
from .qstock_adapter import QStockAdapter

logger = logging.getLogger(__name__)


class DataSourceManager(BaseManager):
    """数据源管理器"""

    def __init__(self, config: Optional[Config] = None, **kwargs):
        """
        初始化数据源管理器

        Args:
            config: 配置对象
        """
        super().__init__(config=config, **kwargs)

    def _get_config_prefix(self) -> str:
        """获取配置前缀"""
        return "data_sources"

    def _init_specific_config(self):
        """初始化数据源管理器特定配置"""
        self.max_retry_attempts = self._get_config("max_retry_attempts", 3)
        self.retry_delay = self._get_config("retry_delay", 1)
        self.health_check_interval = self._get_config("health_check_interval", 300)

    def _init_components(self):
        """初始化组件"""
        self.sources: Dict[str, BaseDataSource] = {}
        self.source_status: Dict[str, Dict] = {}

        # 注册数据源适配器
        self._register_adapters()

        # 初始化数据源
        self._initialize_sources()

    def _get_required_attributes(self) -> List[str]:
        """必需属性列表"""
        return ["sources", "source_status", "adapter_classes"]

    def _register_adapters(self):
        """注册数据源适配器"""
        self.adapter_classes = {
            "akshare": AkShareAdapter,
            "baostock": BaoStockAdapter,
            "qstock": QStockAdapter,
        }

    def _initialize_sources(self):
        """初始化数据源"""
        # 由于设置了config_prefix为"data_sources"，直接获取各个数据源配置
        data_sources_config = {
            "akshare": self._get_config("akshare", {}),
            "baostock": self._get_config("baostock", {}),
            "qstock": self._get_config("qstock", {}),
        }

        for source_name, source_config in data_sources_config.items():
            if source_name in self.adapter_classes and source_config.get(
                "enabled", False
            ):
                try:
                    adapter_class = self.adapter_classes[source_name]
                    source = adapter_class(source_config)

                    self.sources[source_name] = source
                    self.source_status[source_name] = {
                        "enabled": True,
                        "connected": False,
                        "last_check": None,
                        "error_count": 0,
                        "last_error": None,
                    }

                    self.logger.info(f"数据源 {source_name} 注册成功")

                except Exception as e:
                    self._log_error("_initialize_sources", e, source_name=source_name)
                    self.source_status[source_name] = {
                        "enabled": False,
                        "connected": False,
                        "last_check": time.time(),
                        "error_count": 1,
                        "last_error": str(e),
                    }

    def get_source(self, source_name: str) -> Optional[BaseDataSource]:
        """
        获取数据源实例

        Args:
            source_name: 数据源名称

        Returns:
            Optional[BaseDataSource]: 数据源实例
        """
        if not source_name:
            raise ValidationError("数据源名称不能为空")

        return self.sources.get(source_name)

    def get_available_sources(self) -> List[str]:
        """获取可用的数据源列表"""
        available = []
        for name, source in self.sources.items():
            if self.source_status[name]["enabled"]:
                available.append(name)
        return available

    def get_source_priorities(
        self, market: str, frequency: str, data_type: str
    ) -> List[str]:
        """
        获取数据源优先级

        Args:
            market: 市场代码
            frequency: 频率
            data_type: 数据类型

        Returns:
            List[str]: 按优先级排序的数据源列表
        """
        if not all([market, frequency, data_type]):
            raise ValidationError("市场、频率和数据类型参数不能为空")

        # 从配置中获取优先级，或使用默认优先级
        priority_config = self._get_config("source_priorities", {})
        key = f"{market}_{frequency}_{data_type}"

        if key in priority_config:
            return priority_config[key]

        # 默认优先级策略
        if data_type == "ohlcv":
            return ["baostock", "akshare", "qstock"]
        elif data_type == "fundamentals":
            return ["baostock", "akshare"]
        elif data_type == "valuation":
            return ["baostock", "akshare", "qstock"]  # BaoStock有估值数据且稳定
        elif data_type == "adjustment":
            return ["baostock"]  # 只有BaoStock支持除权除息数据
        elif data_type == "calendar":
            return ["baostock", "akshare"]
        else:
            # 返回所有可用的数据源作为默认优先级
            return self.get_available_sources()

    @unified_error_handler(return_dict=True)
    def get_data_with_fallback(
        self, method_name: str, priorities: List[str], *args, **kwargs
    ) -> Any:
        """
        使用指定数据源获取数据（已移除fallback机制）

        Args:
            method_name: 方法名称
            priorities: 数据源优先级列表（只使用第一个）
            *args, **kwargs: 方法参数

        Returns:
            Any: 数据结果
        """
        if not priorities:
            raise ValidationError("没有指定数据源")

        if not method_name:
            raise ValidationError("方法名称不能为空")

        source_name = priorities[0]
        if source_name not in self.sources:
            raise ValidationError(f"数据源 {source_name} 不存在")

        source = self.sources[source_name]

        try:
            # 确保连接
            if not source.is_connected():
                source.connect()

            # 调用方法
            method = getattr(source, method_name)
            result = method(*args, **kwargs)

            # 在结果中添加数据源信息
            if isinstance(result, dict) and result:
                result["source"] = source_name
            elif isinstance(result, list) and result:
                for item in result:
                    if isinstance(item, dict):
                        item["source"] = source_name

            return result

        except Exception as e:
            self._log_error(
                "get_data_with_fallback",
                e,
                source_name=source_name,
                method_name=method_name,
            )
            raise

    @unified_error_handler(return_dict=True)
    def get_daily_data(
        self,
        symbol: str,
        start_date: Union[str, date],
        end_date: Optional[Union[str, date]] = None,
        market: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        获取日线数据

        Args:
            symbol: 股票代码
            start_date: 开始日期
            end_date: 结束日期
            market: 市场代码

        Returns:
            Dict[str, Any]: 日线数据
        """
        if not symbol:
            raise ValidationError("股票代码不能为空")

        if not start_date:
            raise ValidationError("开始日期不能为空")

        if market is None:
            market = self._parse_market_from_symbol(symbol)

        priorities_result = self.get_source_priorities(market, "1d", "ohlcv")
        priorities = (
            priorities_result["data"]
            if isinstance(priorities_result, dict)
            else priorities_result
        )

        fallback_result = self.get_data_with_fallback(
            "get_daily_data", priorities, symbol, start_date, end_date
        )
        return fallback_result

    @unified_error_handler(return_dict=True)
    def get_minute_data(
        self,
        symbol: str,
        trade_date: Union[str, date],
        frequency: str = "5m",
        market: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        获取分钟线数据

        Args:
            symbol: 股票代码
            trade_date: 交易日期
            frequency: 频率
            market: 市场代码

        Returns:
            Dict[str, Any]: 分钟线数据
        """
        if not symbol:
            raise ValidationError("股票代码不能为空")

        if not trade_date:
            raise ValidationError("交易日期不能为空")

        if market is None:
            market = self._parse_market_from_symbol(symbol)

        priorities_result = self.get_source_priorities(market, frequency, "ohlcv")
        priorities = (
            priorities_result["data"]
            if isinstance(priorities_result, dict)
            else priorities_result
        )

        fallback_result = self.get_data_with_fallback(
            "get_minute_data", priorities, symbol, trade_date, frequency
        )
        return fallback_result

    @unified_error_handler(return_dict=True)
    def get_stock_info(
        self, symbol: Optional[str] = None, market: Optional[str] = None
    ) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
        """
        获取股票基础信息

        Args:
            symbol: 股票代码
            market: 市场代码

        Returns:
            Union[Dict, List[Dict]]: 股票信息
        """
        if symbol and market is None:
            market = self._parse_market_from_symbol(symbol)

        priorities = self.get_source_priorities(market or "SZ", "1d", "stock_info")
        return self.get_data_with_fallback("get_stock_info", priorities, symbol)

    @unified_error_handler(return_dict=True)
    def get_fundamentals(
        self,
        symbol: str,
        report_date: Union[str, date],
        report_type: str = "Q4",
        market: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        获取财务数据

        Args:
            symbol: 股票代码
            report_date: 报告期
            report_type: 报告类型
            market: 市场代码

        Returns:
            Dict[str, Any]: 财务数据
        """
        if not symbol:
            raise ValidationError("股票代码不能为空")

        if not report_date:
            raise ValidationError("报告期不能为空")

        if market is None:
            market = self._parse_market_from_symbol(symbol)

        priorities_result = self.get_source_priorities(market, "1d", "fundamentals")
        priorities = (
            priorities_result["data"]
            if isinstance(priorities_result, dict)
            else priorities_result
        )

        fallback_result = self.get_data_with_fallback(
            "get_fundamentals", priorities, symbol, report_date, report_type
        )
        return fallback_result

    @unified_error_handler(return_dict=True)
    def get_trade_calendar(
        self,
        start_date: Union[str, date],
        end_date: Union[str, date] = None,
        market: str = "SZ",
    ) -> List[Dict[str, Any]]:
        """获取交易日历"""
        if not start_date:
            raise ValidationError("开始日期不能为空")

        priorities_result = self.get_source_priorities(market, "1d", "calendar")
        priorities = (
            priorities_result["data"]
            if isinstance(priorities_result, dict)
            else priorities_result
        )

        fallback_result = self.get_data_with_fallback(
            "get_trade_calendar", priorities, start_date, end_date
        )
        return fallback_result

    @unified_error_handler(return_dict=True)
    def get_adjustment_data(
        self,
        symbol: str,
        start_date: Union[str, date],
        end_date: Optional[Union[str, date]] = None,
        market: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """获取除权除息数据"""
        if not symbol:
            raise ValidationError("股票代码不能为空")

        if not start_date:
            raise ValidationError("开始日期不能为空")

        if market is None:
            market = self._parse_market_from_symbol(symbol)

        priorities_result = self.get_source_priorities(market, "1d", "adjustment")
        priorities = (
            priorities_result["data"]
            if isinstance(priorities_result, dict)
            else priorities_result
        )

        fallback_result = self.get_data_with_fallback(
            "get_adjustment_data", priorities, symbol, start_date, end_date
        )
        return fallback_result

    @unified_error_handler(return_dict=True)
    def get_valuation_data(
        self, symbol: str, trade_date: Union[str, date], market: Optional[str] = None
    ) -> Dict[str, Any]:
        """获取估值数据"""
        if not symbol:
            raise ValidationError("股票代码不能为空")

        if not trade_date:
            raise ValidationError("交易日期不能为空")

        if market is None:
            market = self._parse_market_from_symbol(symbol)

        priorities_result = self.get_source_priorities(market, "1d", "valuation")
        priorities = (
            priorities_result["data"]
            if isinstance(priorities_result, dict)
            else priorities_result
        )

        fallback_result = self.get_data_with_fallback(
            "get_valuation_data", priorities, symbol, trade_date
        )
        return fallback_result

    def _parse_market_from_symbol(self, symbol: str) -> str:
        """从股票代码解析市场"""
        if symbol.endswith(".SZ"):
            return "SZ"
        elif symbol.endswith(".SS"):
            return "SS"
        elif symbol.endswith(".SH"):
            return "SS"
        elif symbol.endswith(".HK"):
            return "HK"
        elif symbol.endswith(".US"):
            return "US"
        else:
            # 根据代码前缀推断
            if symbol.startswith(("00", "30")):
                return "SZ"
            elif symbol.startswith(("60", "68")):
                return "SS"
            else:
                return "SZ"

    @unified_error_handler(return_dict=True)
    def health_check(self) -> Dict[str, Any]:
        """健康检查"""
        results = {}

        for source_name, source in self.sources.items():
            try:
                # 检查连接状态
                is_connected = source.is_connected()

                # 尝试连接
                if not is_connected:
                    source.connect()
                    is_connected = source.is_connected()

                results[source_name] = {
                    "status": "healthy" if is_connected else "unhealthy",
                    "connected": is_connected,
                    "capabilities": source.get_capabilities(),
                    "last_check": time.time(),
                }

                # 更新状态
                self.source_status[source_name].update(
                    {
                        "connected": is_connected,
                        "last_check": time.time(),
                    }
                )

            except Exception as e:
                results[source_name] = {
                    "status": "error",
                    "connected": False,
                    "error": str(e),
                    "last_check": time.time(),
                }

                # 更新状态
                self.source_status[source_name].update(
                    {
                        "connected": False,
                        "last_check": time.time(),
                        "last_error": str(e),
                        "error_count": self.source_status[source_name]["error_count"]
                        + 1,
                    }
                )

        return results

    @unified_error_handler(return_dict=True)
    def get_status(self) -> Dict[str, Any]:
        """获取管理器状态"""
        available_sources_result = self.get_available_sources()
        available_sources = (
            available_sources_result["data"]
            if isinstance(available_sources_result, dict)
            else available_sources_result
        )

        return {
            "sources": self.source_status.copy(),
            "available_sources": available_sources,
            "total_sources": len(self.sources),
            "healthy_sources": len(
                [
                    s
                    for s in self.source_status.values()
                    if s["enabled"] and s["connected"]
                ]
            ),
        }

    def disconnect_all(self):
        """断开所有数据源连接"""
        for source_name, source in self.sources.items():
            try:
                source.disconnect()
                self.source_status[source_name]["connected"] = False
                self.logger.info(f"数据源 {source_name} 已断开")
            except Exception as e:
                self._log_error("disconnect_all", e, source_name=source_name)

    def __enter__(self):
        """上下文管理器入口"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        self.disconnect_all()
        return False

    def __del__(self):
        """析构函数"""
        try:
            self.disconnect_all()
        except:
            pass
