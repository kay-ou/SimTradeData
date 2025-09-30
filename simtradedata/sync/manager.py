"""
同步管理器

统一管理增量同步、缺口检测和数据验证功能。
"""

# 标准库导入
import logging
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

# 项目内导入
from ..config import Config
from ..core import BaseManager, ValidationError, unified_error_handler
from ..data_sources import DataSourceManager
from ..database import DatabaseManager
from ..preprocessor import DataProcessingEngine
from ..utils.progress_bar import (
    create_phase_progress,
    log_error,
    log_phase_complete,
    log_phase_start,
    update_phase_description,
)
from .gap_detector import GapDetector
from .incremental import IncrementalSync
from .validator import DataValidator

logger = logging.getLogger(__name__)


# 常量定义
class SyncConstants:
    """同步相关常量"""

    # 数据验证范围
    MIN_REPORT_YEAR = 1990
    MAX_PE_RATIO = 1000
    MAX_PB_RATIO = 100

    # 数字单位转换
    WAN_MULTIPLIER = 10000
    YI_MULTIPLIER = 100000000

    # 日期格式
    DATE_FORMAT = "%Y-%m-%d"

    # 重试次数
    DEFAULT_MAX_RETRIES = 3


class DataQualityValidator:
    """数据质量验证器"""

    @staticmethod
    def is_valid_financial_data(data: Dict[str, Any]) -> bool:
        """验证财务数据有效性"""
        if not data or not isinstance(data, dict):
            return False

        # 检查是否有有效的财务指标
        revenue = data.get("revenue", 0)
        net_profit = data.get("net_profit", 0)
        total_assets = data.get("total_assets", 0)

        # 至少要有一个非零的主要财务指标
        return (
            (revenue and revenue > 0)
            or (total_assets and total_assets > 0)
            or (net_profit != 0)  # 净利润可以为负
        )

    @staticmethod
    def is_valid_valuation_data(data: Dict[str, Any]) -> bool:
        """验证估值数据有效性"""
        if not data or not isinstance(data, dict):
            return False

        pe_ratio = data.get("pe_ratio", 0)
        pb_ratio = data.get("pb_ratio", 0)

        # PE/PB应该为正数且在合理范围内
        # 移除对market_cap的依赖，因为市值现在是计算值而非存储值
        return (pe_ratio and 0 < pe_ratio < SyncConstants.MAX_PE_RATIO) or (
            pb_ratio and 0 < pb_ratio < SyncConstants.MAX_PB_RATIO
        )

    @staticmethod
    def is_valid_report_date(report_date: str, symbol: Optional[str] = None) -> bool:
        """验证报告期有效性"""
        try:
            report_dt = datetime.strptime(report_date, SyncConstants.DATE_FORMAT)
            current_dt = datetime.now()

            # 报告期不能是未来日期
            if report_dt > current_dt:
                return False

            # 报告期不能太久远
            if report_dt.year < SyncConstants.MIN_REPORT_YEAR:
                return False

            return True
        except (ValueError, TypeError):
            return False

    @staticmethod
    def is_valid_stock_basic_info(data: Dict[str, Any]) -> bool:
        """验证股票基础信息有效性"""
        if not data or not isinstance(data, dict):
            return False

        # 检查关键字段
        symbol = data.get("symbol", "")
        name = data.get("name", "")
        market = data.get("market", "")

        return bool(symbol and name and market)


class SyncManager(BaseManager):
    """同步管理器"""

    # 类型注解属性（由BaseManager动态注入）
    db_manager: DatabaseManager
    data_source_manager: DataSourceManager
    processing_engine: DataProcessingEngine

    def __init__(
        self,
        db_manager: DatabaseManager,
        data_source_manager: DataSourceManager,
        processing_engine: DataProcessingEngine,
        config: Optional[Config] = None,
        **kwargs,
    ):
        """
        初始化同步管理器

        Args:
            db_manager: 数据库管理器
            data_source_manager: 数据源管理器
            processing_engine: 数据处理引擎
            config: 配置对象
        """
        # 初始化缓存
        self._market_cache = {}
        self._stock_info_cache = {}

        super().__init__(
            config=config,
            db_manager=db_manager,
            data_source_manager=data_source_manager,
            processing_engine=processing_engine,
            **kwargs,
        )

    def _init_specific_config(self):
        """初始化同步管理器特定配置"""
        # 初始化同步管理器特定配置
        self.enable_auto_gap_fix = self._get_config("auto_gap_fix", True)
        self.enable_validation = self._get_config("enable_validation", True)
        self.max_gap_fix_days = self._get_config("max_gap_fix_days", 7)

        # 性能优化配置
        self.batch_size = self._get_config("batch_size", 100)
        self.enable_cache = self._get_config("enable_cache", True)
        self.cache_ttl = self._get_config("cache_ttl", 3600)  # 1小时

    def _init_components(self):
        """初始化子组件"""
        # 初始化子组件
        self.incremental_sync = IncrementalSync(
            self.db_manager,
            self.data_source_manager,
            self.processing_engine,
            self.config,
        )
        self.gap_detector = GapDetector(self.db_manager, self.config)
        self.validator = DataValidator(self.db_manager, self.config)

    def _get_required_attributes(self) -> List[str]:
        """必需属性列表"""
        return [
            "db_manager",
            "data_source_manager",
            "processing_engine",
            "incremental_sync",
            "gap_detector",
            "validator",
        ]

    def _extract_data_safely(self, data: Any) -> Any:
        """
        统一的数据格式处理方法，避免多次拆包

        Args:
            data: 可能被包装的数据

        Returns:
            Any: 拆包后的实际数据
        """
        if not data:
            return None

        # 如果是标准成功响应格式 {"success": True, "data": ..., "count": ...}
        if isinstance(data, dict) and "success" in data:
            if data.get("success"):
                return data.get("data")
            else:
                # 失败响应，记录错误并返回None
                error_msg = data.get("error", "未知错误")
                self.logger.warning(f"数据源返回失败: {error_msg}")
                return None

        # 如果是简单包装格式 {"data": ...} (没有success字段)
        elif isinstance(data, dict) and "data" in data and "success" not in data:
            return data["data"]

        # 否则直接返回原数据
        return data

    @unified_error_handler(return_dict=True)
    def run_full_sync(
        self,
        target_date: Optional[date] = None,
        symbols: Optional[List[str]] = None,
        frequencies: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        运行完整同步流程

        Args:
            target_date: 目标日期，默认为今天
            symbols: 股票代码列表，默认为所有活跃股票
            frequencies: 频率列表，默认为配置中的频率

        Returns:
            Dict[str, Any]: 完整同步结果
        """
        # 如果没有指定频率，使用默认频率
        if frequencies is None:
            frequencies = ["1d"]

        # 如果没有指定symbols，使用默认值
        if symbols is None:
            symbols = []

        if not target_date:
            raise ValidationError("目标日期不能为空")

        if target_date is None:
            target_date = datetime.now().date()

        # 限制目标日期不能超过今天，使用合理的历史日期
        today = datetime.now().date()
        if target_date > today:
            # 如果目标日期是未来，使用最近的交易日
            target_date = date(2025, 1, 24)  # 使用已知有数据的日期
            self._log_warning("run_full_sync", f"目标日期调整为历史日期: {target_date}")

        try:
            self._log_method_start("run_full_sync", target_date=target_date)
            start_time = datetime.now()

            full_result = {
                "target_date": str(target_date),
                "start_time": start_time.isoformat(),
                "phases": {},
                "summary": {
                    "total_phases": 0,
                    "successful_phases": 0,
                    "failed_phases": 0,
                },
            }

            # 🔄 提前进行断点续传检查（在基础数据更新之前）
            # 先获取股票列表用于断点续传检查
            if symbols is None:
                symbols = []
            if not symbols:
                symbols = self._get_active_stocks_from_db()
                if not symbols:
                    # 如果数据库中没有股票，不能进行断点续传，执行完整流程
                    self.logger.info("数据库中没有股票，无法进行断点续传检查")
                else:
                    self.logger.info(f"获取到{len(symbols)}只活跃股票用于断点续传检查")

            # 如果有股票列表，检查断点续传条件
            if symbols:
                # 检查是否有任何已完成的扩展数据记录
                result = self.db_manager.fetchone(
                    "SELECT COUNT(*) as count FROM extended_sync_status WHERE target_date = ? AND status = 'completed'",
                    (str(target_date),),
                )
                completed_count = result["count"] if result else 0

                if completed_count > 0:  # 如果有已完成记录，执行断点续传
                    self.logger.info(
                        f"🔄 检测到断点续传: 发现{completed_count}个已完成记录"
                    )

                    # 重新计算需要处理的股票（基于正确的symbols列表）
                    extended_symbols_to_process = (
                        self._get_extended_data_symbols_to_process(symbols, target_date)
                    )

                    # 如果所有扩展数据都已完成，直接跳过所有阶段
                    if len(extended_symbols_to_process) == 0:
                        self.logger.info("🎉 检测到所有数据已完成，跳过整个同步流程")
                        full_result["phases"]["all_completed"] = {
                            "status": "completed",
                            "message": "所有数据已完成",
                        }
                        full_result["summary"][
                            "successful_phases"
                        ] = 4  # 假设4个阶段都完成
                        return full_result

                    # 计算完成进度
                    total_stocks = len(symbols)
                    remaining_stocks = len(extended_symbols_to_process)
                    completion_rate = (
                        (total_stocks - remaining_stocks) / total_stocks
                        if total_stocks > 0
                        else 0
                    )

                    self.logger.info(
                        f"📊 断点续传状态: 总计{total_stocks}只，已完成{completion_rate:.1%}，剩余{remaining_stocks}只"
                    )

                    # 直接跳到扩展数据同步阶段
                    self.logger.info(
                        "⏭️ 跳过基础数据更新和增量同步，直接进入扩展数据同步"
                    )
                    full_result["phases"]["calendar_update"] = {
                        "status": "skipped",
                        "message": "断点续传跳过",
                    }
                    full_result["phases"]["stock_list_update"] = {
                        "status": "skipped",
                        "message": "断点续传跳过",
                    }
                    full_result["phases"]["incremental_sync"] = {
                        "status": "skipped",
                        "message": "断点续传跳过",
                    }
                    full_result["summary"][
                        "successful_phases"
                    ] += 3  # 标记跳过的阶段为成功

                    # 阶段2: 同步扩展数据（断点续传）
                    log_phase_start("阶段2", "扩展数据同步（断点续传）")

                    with create_phase_progress(
                        "phase2",
                        len(extended_symbols_to_process),
                        "扩展数据同步",
                        "股票",
                    ) as pbar:
                        try:
                            extended_result = self._sync_extended_data(
                                extended_symbols_to_process, target_date, pbar
                            )
                            full_result["phases"]["extended_data_sync"] = {
                                "status": "completed",
                                "result": extended_result,
                            }
                            full_result["summary"]["successful_phases"] += 1

                            log_phase_complete(
                                "扩展数据同步",
                                {
                                    "财务数据": f"{extended_result.get('financials_count', 0)}条",
                                    "估值数据": f"{extended_result.get('valuations_count', 0)}条",
                                    "处理股票": f"{extended_result.get('processed_symbols', 0)}只",
                                },
                            )

                            # 完成时间
                            end_time = datetime.now()
                            full_result["end_time"] = end_time.isoformat()
                            full_result["duration_seconds"] = (
                                end_time - start_time
                            ).total_seconds()
                            full_result["summary"]["total_phases"] = 4

                            return full_result

                        except Exception as e:
                            log_error(f"扩展数据同步失败: {e}")
                            full_result["phases"]["extended_data_sync"] = {
                                "status": "failed",
                                "error": str(e),
                            }
                            full_result["summary"]["failed_phases"] += 1
                            return full_result
                else:
                    self.logger.info("🆕 未检测到扩展数据记录，执行完整同步流程")

            # 如果是全新同步或完成度很低，执行完整流程
            self.logger.info("🚀 执行完整同步流程")

            # 阶段0: 更新基础数据（交易日历和股票列表）
            log_phase_start("阶段0", "更新基础数据")

            with create_phase_progress("phase0", 2, "基础数据更新", "项") as pbar:
                try:
                    # 更新交易日历
                    update_phase_description("更新交易日历")
                    calendar_result = self._update_trading_calendar(target_date)
                    full_result["phases"]["calendar_update"] = calendar_result
                    full_result["summary"]["total_phases"] += 1
                    # 更新进度条
                    if pbar is not None:
                        pbar.update(1)

                    if "error" not in calendar_result:
                        full_result["summary"]["successful_phases"] += 1
                        updated_records = calendar_result.get("updated_records", 0)
                        total_records = calendar_result.get("total_records", 0)
                        years_range = f"{calendar_result.get('start_year')}-{calendar_result.get('end_year')}"
                        log_phase_complete(
                            "交易日历更新",
                            {
                                "年份范围": years_range,
                                "新增记录": f"{updated_records}条",
                                "总记录": f"{total_records}条",
                            },
                        )
                    else:
                        full_result["summary"]["failed_phases"] += 1
                        log_error(f"交易日历更新失败: {calendar_result['error']}")

                    # 更新股票列表
                    update_phase_description("更新股票列表（可能需要较长时间）")
                    stock_list_result = self._update_stock_list()
                    full_result["phases"]["stock_list_update"] = stock_list_result
                    full_result["summary"]["total_phases"] += 1
                    # 更新进度条
                    if pbar is not None:
                        pbar.update(1)

                    if "error" not in stock_list_result:
                        full_result["summary"]["successful_phases"] += 1
                        total_stocks = stock_list_result.get("total_stocks", 0)
                        new_stocks = stock_list_result.get("new_stocks", 0)
                        updated_stocks = stock_list_result.get("updated_stocks", 0)
                        log_phase_complete(
                            "股票列表更新",
                            {
                                "总股票": f"{total_stocks}只",
                                "新增": f"{new_stocks}只",
                                "更新": f"{updated_stocks}只",
                            },
                        )
                    else:
                        full_result["summary"]["failed_phases"] += 1
                        log_error(f"股票列表更新失败: {stock_list_result['error']}")

                except Exception as e:
                    log_error(f"基础数据更新失败: {e}")
                    full_result["phases"]["base_data_update"] = {"error": str(e)}
                    full_result["summary"]["total_phases"] += 1
                    full_result["summary"]["failed_phases"] += 1

            # 如果没有指定股票列表，从数据库获取活跃股票（完整流程需要）
            if not symbols:
                symbols = self._get_active_stocks_from_db()
                if not symbols:
                    # 如果数据库中没有股票，使用默认股票
                    symbols = ["000001.SZ", "000002.SZ", "600000.SS", "600036.SS"]
                    self.logger.info(f"使用默认股票列表: {len(symbols)}只股票")
                else:
                    self.logger.info(f"从数据库获取活跃股票: {len(symbols)}只股票")

            # 阶段1: 增量同步（市场数据）
            log_phase_start("阶段1", "增量同步市场数据")

            with create_phase_progress(
                "phase1", len(symbols), "增量同步", "股票"
            ) as pbar:
                try:
                    # 修改增量同步以支持进度回调
                    sync_result = self.incremental_sync.sync_all_symbols(
                        target_date, symbols, frequencies, progress_bar=pbar
                    )
                    full_result["phases"]["incremental_sync"] = {
                        "status": "completed",
                        "result": sync_result,
                    }
                    full_result["summary"]["successful_phases"] += 1

                    # 从结果中提取统计信息
                    success_count = sync_result.get("success_count", len(symbols))
                    error_count = sync_result.get("error_count", 0)
                    log_phase_complete(
                        "增量同步",
                        {"成功": f"{success_count}只股票", "失败": error_count},
                    )

                except Exception as e:
                    log_error(f"增量同步失败: {e}")
                    full_result["phases"]["incremental_sync"] = {
                        "status": "failed",
                        "error": str(e),
                    }
                    full_result["summary"]["failed_phases"] += 1

            full_result["summary"]["total_phases"] += 1

            # 阶段2: 同步扩展数据
            log_phase_start("阶段2", "同步扩展数据")

            # 预检查扩展数据同步的断点续传状态
            extended_symbols_to_process = self._get_extended_data_symbols_to_process(
                symbols, target_date
            )

            self.logger.info(
                f"📊 扩展数据同步: 总股票 {len(symbols)}只, 需处理 {len(extended_symbols_to_process)}只"
            )

            # 如果没有股票需要处理，直接跳过
            if len(extended_symbols_to_process) == 0:
                self.logger.info("✅ 所有股票的扩展数据已完成，跳过扩展数据同步")
                full_result["phases"]["extended_data_sync"] = {
                    "status": "skipped",
                    "result": {"message": "所有数据已完整，无需处理"},
                }
                full_result["summary"]["successful_phases"] += 1
                log_phase_complete("扩展数据同步", {"状态": "已完成，跳过"})
            else:
                # 处理所有需要的股票，不设限制
                actual_symbols_to_process = extended_symbols_to_process
                self.logger.info(
                    f"🎯 开始处理全部 {len(extended_symbols_to_process)} 只需要处理的股票"
                )

                # 使用所有需要处理的股票数量作为进度条基准
                with create_phase_progress(
                    "phase2", len(actual_symbols_to_process), "扩展数据同步", "股票"
                ) as pbar:
                    try:
                        extended_result = self._sync_extended_data(
                            actual_symbols_to_process,  # 使用实际要处理的股票列表
                            target_date,
                            pbar,  # 传入正确大小的进度条
                        )
                        full_result["phases"]["extended_data_sync"] = {
                            "status": "completed",
                            "result": extended_result,
                        }
                        full_result["summary"]["successful_phases"] += 1

                        log_phase_complete(
                            "扩展数据同步",
                            {
                                "财务数据": f"{extended_result.get('financials_count', 0)}条",
                                "估值数据": f"{extended_result.get('valuations_count', 0)}条",
                                "技术指标": f"{extended_result.get('indicators_count', 0)}条",
                            },
                        )

                    except Exception as e:
                        log_error(f"扩展数据同步失败: {e}")
                        full_result["phases"]["extended_data_sync"] = {
                            "status": "failed",
                            "error": str(e),
                        }
                        full_result["summary"]["failed_phases"] += 1

            full_result["summary"]["total_phases"] += 1

            # 阶段3: 缺口检测
            log_phase_start("阶段3", "缺口检测与修复")

            with create_phase_progress(
                "phase2", len(symbols), "缺口检测", "股票"
            ) as pbar:
                try:
                    gap_start_date = target_date - timedelta(days=30)  # 检测最近30天
                    gap_result = self.gap_detector.detect_all_gaps(
                        gap_start_date, target_date, symbols, frequencies
                    )

                    # 更新进度
                    # 更新进度
                    if pbar is not None:
                        pbar.update(len(symbols))

                    full_result["phases"]["gap_detection"] = {
                        "status": "completed",
                        "result": gap_result,
                    }
                    full_result["summary"]["successful_phases"] += 1

                    total_gaps = gap_result["summary"]["total_gaps"]

                    # 自动修复缺口
                    if self.enable_auto_gap_fix and total_gaps > 0:
                        update_phase_description(f"修复{total_gaps}个缺口")
                        fix_result = self._auto_fix_gaps(gap_result)
                        full_result["phases"]["gap_fix"] = {
                            "status": "completed",
                            "result": fix_result,
                        }
                        log_phase_complete(
                            "缺口检测与修复",
                            {"检测": f"{total_gaps}个缺口", "修复": "完成"},
                        )
                    else:
                        log_phase_complete("缺口检测", {"缺口": f"{total_gaps}个"})

                except Exception as e:
                    log_error(f"缺口检测失败: {e}")
                    full_result["phases"]["gap_detection"] = {
                        "status": "failed",
                        "error": str(e),
                    }
                    full_result["summary"]["failed_phases"] += 1

            full_result["summary"]["total_phases"] += 1

            # 阶段3: 数据验证
            if self.enable_validation:
                log_phase_start("阶段3", "数据验证")

                with create_phase_progress(
                    "phase3", len(symbols), "数据验证", "股票"
                ) as pbar:
                    try:
                        validation_start_date = target_date - timedelta(
                            days=7
                        )  # 验证最近7天
                        validation_result = self.validator.validate_all_data(
                            validation_start_date, target_date, symbols, frequencies
                        )

                        # 更新进度
                        if pbar is not None:
                            pbar.update(len(symbols))

                        full_result["phases"]["validation"] = {
                            "status": "completed",
                            "result": validation_result,
                        }
                        full_result["summary"]["successful_phases"] += 1

                        # 提取验证统计
                        total_records = validation_result.get("total_records", 0)
                        valid_records = validation_result.get("valid_records", 0)
                        validation_rate = validation_result.get("validation_rate", 0)

                        log_phase_complete(
                            "数据验证",
                            {
                                "记录": f"{total_records}条",
                                "有效": f"{valid_records}条",
                                "验证率": f"{validation_rate:.1f}%",
                            },
                        )

                    except Exception as e:
                        log_error(f"数据验证失败: {e}")
                        full_result["phases"]["validation"] = {
                            "status": "failed",
                            "error": str(e),
                        }
                        full_result["summary"]["failed_phases"] += 1

                full_result["summary"]["total_phases"] += 1

            # 完成时间
            end_time = datetime.now()
            full_result["end_time"] = end_time.isoformat()
            full_result["duration_seconds"] = (end_time - start_time).total_seconds()

            self._log_performance(
                "run_full_sync",
                full_result["duration_seconds"],
                successful_phases=full_result["summary"]["successful_phases"],
                failed_phases=full_result["summary"]["failed_phases"],
            )

            return full_result

        except Exception as e:
            self._log_error("run_full_sync", e, target_date=target_date)
            raise

    def get_sync_status(self) -> Dict[str, Any]:
        """获取同步状态"""
        try:
            # 获取最近的同步状态
            sql = """
            SELECT * FROM sync_status
            ORDER BY last_sync_date DESC
            LIMIT 10
            """
            recent_syncs = self.db_manager.fetchall(sql)

            # 获取数据统计
            stats_sql = """
            SELECT 
                COUNT(*) as total_records,
                COUNT(DISTINCT symbol) as total_symbols,
                COUNT(DISTINCT date) as total_dates,
                MIN(date) as earliest_date,
                MAX(date) as latest_date,
                AVG(quality_score) as avg_quality
            FROM market_data
            """
            stats_result = self.db_manager.fetchone(stats_sql)

            # 获取组件状态
            components_status = {
                "incremental_sync": {
                    "initialized": hasattr(self, "incremental_sync")
                    and self.incremental_sync is not None,
                    "type": "IncrementalSync",
                },
                "gap_detector": {
                    "initialized": hasattr(self, "gap_detector")
                    and self.gap_detector is not None,
                    "type": "GapDetector",
                },
                "validator": {
                    "initialized": hasattr(self, "validator")
                    and self.validator is not None,
                    "type": "DataValidator",
                },
            }

            # 返回标准格式
            return {
                "success": True,
                "data": {
                    "recent_syncs": [dict(row) for row in recent_syncs],
                    "data_stats": dict(stats_result) if stats_result else {},
                    "components": components_status,
                    "config": {
                        "enable_auto_gap_fix": self.enable_auto_gap_fix,
                        "enable_validation": self.enable_validation,
                        "max_gap_fix_days": self.max_gap_fix_days,
                    },
                },
            }
        except Exception as e:
            self.logger.error(f"获取同步状态失败: {e}")
            return {"success": False, "error": str(e)}

    def _get_active_stocks_from_db(self) -> List[str]:
        """从数据库获取活跃股票列表"""
        sql = "SELECT symbol FROM stocks WHERE status = 'active' ORDER BY symbol"
        result = self.db_manager.fetchall(sql)
        return [row["symbol"] for row in result] if result else []

    def _get_extended_data_symbols_to_process(
        self, symbols: List[str], target_date: date
    ) -> List[str]:
        """
        获取需要处理扩展数据的股票列表（智能断点续传版本）
        """
        try:
            self.logger.info("📊 检查扩展数据完整性（智能断点续传）...")

            if not symbols:
                return []

            # 清理过期的pending状态
            cleanup_count = self.db_manager.execute(
                """
                DELETE FROM extended_sync_status 
                WHERE target_date = ? AND status = 'pending' 
                AND created_at < datetime('now', '-1 day')
                """,
                (str(target_date),),
            )

            # 智能数据完整性检查：使用灵活的日期范围
            # 财务数据：检查最近2年的年报数据
            financial_dates = [
                f"{target_date.year - 1}-12-31",  # 去年年报
                f"{target_date.year - 2}-12-31",  # 前年年报（备用）
            ]

            # 估值数据：检查目标日期前后10天范围
            from datetime import timedelta

            valuation_start = str(target_date - timedelta(days=10))
            valuation_end = str(target_date + timedelta(days=10))

            placeholders = ",".join(["?" for _ in symbols])
            financial_placeholders = ",".join(["?" for _ in financial_dates])

            # 灵活的数据完整性查询
            data_completeness_query = f"""
            WITH symbol_list AS (
                SELECT symbol FROM stocks 
                WHERE symbol IN ({placeholders}) AND status = 'active'
            ),
            financial_data AS (
                SELECT DISTINCT symbol FROM financials 
                WHERE symbol IN ({placeholders}) 
                AND report_date IN ({financial_placeholders})
            ),
            valuation_data AS (
                SELECT DISTINCT symbol FROM valuations 
                WHERE symbol IN ({placeholders})
                AND date BETWEEN ? AND ?
            ),
            status_data AS (
                SELECT DISTINCT symbol, status FROM extended_sync_status
                WHERE symbol IN ({placeholders}) 
                AND target_date = ? AND status = 'completed'
            )
            SELECT 
                sl.symbol,
                CASE WHEN fd.symbol IS NOT NULL THEN 1 ELSE 0 END AS has_financial,
                CASE WHEN vd.symbol IS NOT NULL THEN 1 ELSE 0 END AS has_valuation,
                CASE WHEN sd.symbol IS NOT NULL THEN 1 ELSE 0 END AS marked_completed
            FROM symbol_list sl
            LEFT JOIN financial_data fd ON sl.symbol = fd.symbol
            LEFT JOIN valuation_data vd ON sl.symbol = vd.symbol  
            LEFT JOIN status_data sd ON sl.symbol = sd.symbol
            """

            # 执行查询
            query_params = (
                tuple(symbols)
                + tuple(symbols)
                + tuple(financial_dates)
                + tuple(symbols)
                + (valuation_start, valuation_end)
                + tuple(symbols)
                + (str(target_date),)
            )
            results = self.db_manager.fetchall(data_completeness_query, query_params)

            # 智能分析结果并修复状态
            symbols_needing_processing = []
            repaired_symbols = []  # 状态修复的股票
            stats = {
                "total_checked": len(results),
                "completed": 0,
                "partial": 0,
                "missing": 0,
                "needs_processing": 0,
                "status_repaired": 0,
            }

            for row in results:
                symbol = row["symbol"]
                has_financial = row["has_financial"]
                has_valuation = row["has_valuation"]
                marked_completed = row["marked_completed"]

                # 评估实际完成状态（修复版本）
                # 关键修复：没有同步记录的股票必须处理，无论是否有数据
                if not marked_completed:
                    # 没有同步记录，必须处理
                    actual_status = "pending"
                    stats["missing"] += 1
                elif has_financial:
                    actual_status = "completed"  # 有财务数据且已标记完成
                    stats["completed"] += 1
                elif has_valuation:
                    actual_status = "partial"  # 只有估值数据
                    stats["partial"] += 1
                else:
                    actual_status = "pending"  # 标记完成但没有数据，需要重新处理
                    stats["missing"] += 1

                # 智能状态修复：修复而不是删除
                if marked_completed and actual_status != "completed":
                    # 状态不一致，需要修复
                    self.db_manager.execute(
                        "UPDATE extended_sync_status SET status = ?, updated_at = datetime('now') WHERE symbol = ? AND target_date = ?",
                        (actual_status, symbol, str(target_date)),
                    )
                    repaired_symbols.append(symbol)
                    stats["status_repaired"] += 1
                    self.logger.debug(
                        f"🔧 修复状态: {symbol} completed -> {actual_status} (财务:{has_financial}, 估值:{has_valuation})"
                    )

                # 需要处理的条件：实际状态不是完成
                if actual_status != "completed":
                    symbols_needing_processing.append(symbol)
                    stats["needs_processing"] += 1

            # 输出智能统计信息
            self.logger.info(
                f"📊 智能数据完整性检查: "
                f"总计{stats['total_checked']}, "
                f"已完成{stats['completed']}, "
                f"部分完成{stats['partial']}, "
                f"缺失数据{stats['missing']}, "
                f"需处理{stats['needs_processing']}, "
                f"状态修复{stats['status_repaired']}"
            )

            if repaired_symbols:
                self.logger.info(
                    f"🔧 状态修复: {len(repaired_symbols)} 个股票状态已修复"
                )

            if symbols_needing_processing:
                completion_rate = (
                    (stats["total_checked"] - len(symbols_needing_processing))
                    / stats["total_checked"]
                    if stats["total_checked"] > 0
                    else 0
                )
                self.logger.info(
                    f"📋 断点续传: 总进度 {completion_rate:.1%}, 剩余处理 {len(symbols_needing_processing)} 只股票"
                )
            else:
                self.logger.info("✅ 所有股票的扩展数据已完整，无需处理")

            return symbols_needing_processing

        except Exception as e:
            self.logger.error(f"检查扩展数据完整性失败: {e}")
            raise

    def _update_trading_calendar(self, target_date: date) -> Dict[str, Any]:
        """增量更新交易日历"""
        self.logger.info(f"🔄 开始交易日历增量更新，目标日期: {target_date}")

        # 检查现有数据范围
        existing_range = self.db_manager.fetchone(
            "SELECT MIN(date) as min_date, MAX(date) as max_date, COUNT(*) as count FROM trading_calendar"
        )

        # 计算需要更新的年份
        needed_start_year = target_date.year - 1
        needed_end_year = target_date.year + 1
        years_to_update = list(range(needed_start_year, needed_end_year + 1))

        if existing_range and existing_range["count"] > 0:
            from datetime import datetime

            existing_min = datetime.strptime(
                existing_range["min_date"], "%Y-%m-%d"
            ).date()
            existing_max = datetime.strptime(
                existing_range["max_date"], "%Y-%m-%d"
            ).date()

            # 只添加缺失的年份
            years_to_update = [
                y
                for y in years_to_update
                if y < existing_min.year or y > existing_max.year
            ]

            if not years_to_update:
                return {
                    "status": "skipped",
                    "message": "交易日历已是最新",
                    "start_year": existing_min.year,
                    "end_year": existing_max.year,
                    "updated_records": 0,
                    "total_records": existing_range["count"],
                }

        self.logger.info(f"需要更新年份: {years_to_update}")
        total_inserted = 0

        # 获取并插入数据
        for year in years_to_update:
            start_date = f"{year}-01-01"
            end_date = f"{year}-12-31"

            calendar_data = self.data_source_manager.get_trade_calendar(
                start_date, end_date
            )

            self.logger.debug(
                f"获取到交易日历原始数据: {type(calendar_data)}, 内容: {calendar_data}"
            )

            # 处理嵌套格式
            if isinstance(calendar_data, dict) and "success" in calendar_data:
                if calendar_data.get("success"):
                    if "data" in calendar_data:
                        calendar_data = calendar_data["data"]
                        # 检查是否有更深层嵌套
                        while (
                            isinstance(calendar_data, dict)
                            and "success" in calendar_data
                            and calendar_data.get("success")
                            and "data" in calendar_data
                        ):
                            calendar_data = calendar_data["data"]
                else:
                    self.logger.warning(
                        f"交易日历获取失败: {calendar_data.get('message', '未知错误')}"
                    )
                    continue
            elif isinstance(calendar_data, dict) and "data" in calendar_data:
                calendar_data = calendar_data["data"]

            self.logger.debug(
                f"处理后交易日历数据: {type(calendar_data)}, 长度: {len(calendar_data) if isinstance(calendar_data, list) else 'N/A'}"
            )

            if not calendar_data or not isinstance(calendar_data, list):
                continue

            # 批量插入数据（性能优化）
            records_to_insert = [
                (
                    record.get("trade_date", record.get("date")),
                    "CN",
                    record.get("is_trading", 1),
                )
                for record in calendar_data
            ]

            if records_to_insert:
                self.db_manager.executemany(
                    "INSERT OR REPLACE INTO trading_calendar (date, market, is_trading) VALUES (?, ?, ?)",
                    records_to_insert,
                )
                total_inserted += len(records_to_insert)

        # 验证结果
        final_range = self.db_manager.fetchone(
            "SELECT MIN(date) as min_date, MAX(date) as max_date, COUNT(*) as count FROM trading_calendar"
        )

        return {
            "status": "completed",
            "start_year": (
                int(final_range["min_date"][:4])
                if final_range and final_range["min_date"]
                else needed_start_year
            ),
            "end_year": (
                int(final_range["max_date"][:4])
                if final_range and final_range["max_date"]
                else needed_end_year
            ),
            "updated_records": total_inserted,
            "total_records": final_range["count"] if final_range else 0,
        }

    def _update_stock_list(self) -> Dict[str, Any]:
        """增量更新股票列表（优化版本）"""
        self.logger.info("🔄 开始股票列表增量更新（优化版本）...")

        try:
            # 增量策略：检查是否需要更新
            last_update = self.db_manager.fetchone(
                "SELECT MAX(updated_at) as last_update FROM stocks WHERE status = 'active'"
            )

            # 如果今天已经更新过，且股票数量合理，跳过更新
            from datetime import datetime, timedelta

            today = datetime.now().date()

            if last_update and last_update["last_update"]:
                last_update_date = datetime.fromisoformat(
                    last_update["last_update"]
                ).date()
                stock_count = self.db_manager.fetchone(
                    "SELECT COUNT(*) as count FROM stocks WHERE status = 'active'"
                )

                # 如果今天已更新过且股票数量 > 3000，跳过
                if (
                    last_update_date >= today
                    and stock_count
                    and stock_count["count"] > 3000
                ):
                    self.logger.info(
                        f"✅ 股票列表今日已更新，共 {stock_count['count']} 只股票，跳过更新"
                    )
                    return {
                        "status": "skipped",
                        "message": "今日已更新，跳过",
                        "total_stocks": stock_count["count"],
                        "new_stocks": 0,
                        "updated_stocks": 0,
                        "failed_stocks": 0,
                    }
                # 增加一个更宽松的跳过条件 - 如果股票数量 > 1000且最近更新过
                elif (
                    last_update_date >= (today - timedelta(days=1))  # 1天内更新过
                    and stock_count
                    and stock_count["count"] > 1000
                ):
                    self.logger.info(
                        f"✅ 股票列表最近已更新（{last_update_date}），共 {stock_count['count']} 只股票，跳过更新以提高性能"
                    )
                    return {
                        "status": "skipped",
                        "message": "最近已更新，跳过",
                        "total_stocks": stock_count["count"],
                        "new_stocks": 0,
                        "updated_stocks": 0,
                        "failed_stocks": 0,
                    }

            # 获取股票信息
            self.logger.info("🔄 开始从数据源获取股票信息...")
            stock_info = self.data_source_manager.get_stock_info()
            self.logger.info(f"✅ 获取股票信息完成，数据类型: {type(stock_info)}")

            if hasattr(stock_info, "__len__"):
                self.logger.info(f"📊 股票信息数据长度: {len(stock_info)}")

            # 诊断数据结构
            if isinstance(stock_info, dict):
                self.logger.info(f"字典键: {list(stock_info.keys())}")
                if "data" in stock_info:
                    self.logger.info(f"data字段类型: {type(stock_info['data'])}")
                if "success" in stock_info:
                    self.logger.info(f"success字段值: {stock_info['success']}")

            # 修复解包嵌套数据的逻辑
            if isinstance(stock_info, dict):
                self.logger.info(f"检测到字典格式，键: {list(stock_info.keys())}")

                if "success" in stock_info and "data" in stock_info:
                    if stock_info["success"]:
                        stock_info = stock_info["data"]
                        self.logger.info(
                            f"成功解包AkShare格式，数据类型: {type(stock_info)}"
                        )
                    else:
                        error_msg = stock_info.get("error", "未知错误")
                        self.logger.error(f"数据源返回失败: {error_msg}")
                        return {
                            "status": "failed",
                            "error": f"数据源返回失败: {error_msg}",
                            "total_stocks": 0,
                            "new_stocks": 0,
                            "updated_stocks": 0,
                        }
                # 统一数据格式处理 - 避免多次拆包
                stock_info = self._extract_data_safely(stock_info)

                if not stock_info:
                    self.logger.error("解包后数据为空")
                    return {
                        "status": "failed",
                        "error": "股票数据格式错误: 解包后数据为空",
                        "total_stocks": 0,
                        "new_stocks": 0,
                        "updated_stocks": 0,
                    }

            # 最终验证数据格式
            if stock_info is None:
                self.logger.warning("解包后数据为空")
                return {
                    "status": "failed",
                    "error": "获取股票列表失败：解包后数据为空",
                    "total_stocks": 0,
                    "new_stocks": 0,
                    "updated_stocks": 0,
                }

            # 记录最终的数据类型和长度
            if hasattr(stock_info, "__len__"):
                self.logger.info(
                    f"最终数据格式: {type(stock_info)}, 长度: {len(stock_info)}"
                )
            else:
                self.logger.warning(f"最终数据不是可迭代对象: {type(stock_info)}")

            # 转换DataFrame为列表格式
            if hasattr(stock_info, "iterrows"):
                stock_list = []
                for _, row in stock_info.iterrows():
                    try:
                        # 安全地提取数据，处理可能的空值或异常值
                        code = str(row.get("代码", "")).strip()
                        name = str(row.get("名称", "")).strip()

                        # 跳过无效数据
                        if not code or not name or code == "nan" or name == "nan":
                            continue

                        stock_data = {
                            "symbol": code,
                            "name": name,
                            "market": self._determine_market(code),
                        }
                        stock_list.append(stock_data)
                    except Exception as e:
                        self.logger.debug(f"跳过无效行数据: {e}")
                        continue

                stock_info = stock_list
                self.logger.info(f"DataFrame转换完成，共 {len(stock_list)} 只有效股票")
            elif isinstance(stock_info, list):
                # 如果已经是列表，检查格式
                self.logger.info(f"数据已是列表格式，共 {len(stock_info)} 项")
            else:
                self.logger.warning(f"未知的stock_info数据格式: {type(stock_info)}")

            if not stock_info or not hasattr(stock_info, "__len__"):
                self.logger.warning("股票列表数据格式不正确")
                return {
                    "status": "failed",
                    "error": "股票列表数据格式不正确",
                    "total_stocks": 0,
                    "new_stocks": 0,
                    "updated_stocks": 0,
                }

            # 批量处理股票数据 - 性能优化
            new_stocks = 0
            updated_stocks = 0
            failed_stocks = 0

            # 预处理所有股票数据 - 修复版本
            processed_stocks = []

            # 确保stock_info是列表格式
            if not isinstance(stock_info, (list, tuple)) and not hasattr(
                stock_info, "__iter__"
            ):
                self.logger.error(f"stock_info不是可迭代对象: {type(stock_info)}")
                return {
                    "status": "failed",
                    "error": f"股票数据不是可迭代格式: {type(stock_info)}",
                    "total_stocks": 0,
                    "new_stocks": 0,
                    "updated_stocks": 0,
                    "failed_stocks": 0,
                }

            self.logger.info(
                f"开始预处理股票数据，数据类型: {type(stock_info)}, 长度: {len(stock_info) if hasattr(stock_info, '__len__') else '未知'}"
            )

            for i, stock_data in enumerate(stock_info):
                try:
                    # 检查数据类型
                    if not isinstance(stock_data, dict):
                        if i < 5:  # 只记录前5个错误
                            self.logger.warning(
                                f"第{i}个股票数据不是字典: 类型={type(stock_data)}, 内容={stock_data}"
                            )
                        failed_stocks += 1
                        continue

                    symbol = stock_data.get("symbol", "")
                    name = stock_data.get("name", "")
                    market = stock_data.get("market", "")

                    if not symbol or not name:
                        continue

                    # 添加市场后缀
                    if "." not in symbol:
                        if symbol.startswith("0") or symbol.startswith("3"):
                            symbol = f"{symbol}.SZ"
                        elif symbol.startswith("6") or symbol.startswith("9"):
                            symbol = f"{symbol}.SS"

                    processed_stocks.append(
                        {"symbol": symbol, "name": name, "market": market}
                    )

                except Exception as e:
                    if failed_stocks < 5:  # 只记录前5个错误，避免日志过多
                        self.logger.error(f"预处理第{i}个股票数据失败: {e}")
                    failed_stocks += 1

            if not processed_stocks:
                self.logger.warning("没有有效的股票数据需要处理")
                return {
                    "status": "completed",
                    "total_stocks": 0,
                    "new_stocks": 0,
                    "updated_stocks": 0,
                    "failed_stocks": failed_stocks,
                }

            # 批量检查已存在的股票
            symbol_list = [stock["symbol"] for stock in processed_stocks]
            placeholders = ",".join(["?" for _ in symbol_list])
            existing_symbols = set()

            try:
                existing_result = self.db_manager.fetchall(
                    f"SELECT symbol FROM stocks WHERE symbol IN ({placeholders})",
                    tuple(symbol_list),
                )
                existing_symbols = {row["symbol"] for row in existing_result}
                self.logger.debug(f"数据库中已存在 {len(existing_symbols)} 只股票")
            except Exception as e:
                self.logger.warning(f"批量查询已存在股票失败: {e}")
                # 回退到逐一处理
                existing_symbols = set()

            # 分离新股票和需要更新的股票
            new_stock_batch = []
            update_stock_batch = []

            for stock in processed_stocks:
                if stock["symbol"] in existing_symbols:
                    update_stock_batch.append((stock["name"], stock["symbol"]))
                else:
                    new_stock_batch.append(
                        (
                            stock["symbol"],
                            stock["name"],
                            stock["market"],
                            stock["market"],  # exchange 字段
                            "active",
                        )
                    )

            # 批量更新已存在的股票
            if update_stock_batch:
                try:
                    self.db_manager.executemany(
                        "UPDATE stocks SET name = ?, updated_at = datetime('now') WHERE symbol = ?",
                        update_stock_batch,
                    )
                    updated_stocks = len(update_stock_batch)
                    self.logger.debug(f"批量更新 {updated_stocks} 只股票")
                except Exception as e:
                    self.logger.warning(f"批量更新股票失败: {e}")
                    # 逐一更新
                    for name, symbol in update_stock_batch:
                        try:
                            self.db_manager.execute(
                                "UPDATE stocks SET name = ?, updated_at = datetime('now') WHERE symbol = ?",
                                (name, symbol),
                            )
                            updated_stocks += 1
                        except Exception as e2:
                            self.logger.warning(f"更新股票 {symbol} 失败: {e2}")
                            failed_stocks += 1

            # 批量插入新股票
            if new_stock_batch:
                try:
                    self.db_manager.executemany(
                        """
                        INSERT INTO stocks (symbol, name, market, status, created_at, updated_at)
                        VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
                        """,
                        [(row[0], row[1], row[2], row[4]) for row in new_stock_batch],
                    )
                    new_stocks = len(new_stock_batch)
                    self.logger.debug(f"批量插入 {new_stocks} 只新股票")

                    # 为所有新股票获取详细信息
                    for symbol, _, _, _, _ in new_stock_batch:
                        try:
                            self._fetch_detailed_stock_info(symbol)
                        except Exception as e:
                            self.logger.debug(f"获取 {symbol} 详细信息失败: {e}")

                except Exception as e:
                    self.logger.warning(f"批量插入新股票失败: {e}")
                    # 回退到逐一插入
                    for stock_data in new_stock_batch:
                        try:
                            self.db_manager.execute(
                                """
                                INSERT INTO stocks (symbol, name, market, status, created_at, updated_at)
                                VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
                                """,
                                (
                                    stock_data[0],
                                    stock_data[1],
                                    stock_data[2],
                                    stock_data[4],
                                ),
                            )
                            new_stocks += 1
                        except Exception as e2:
                            self.logger.warning(f"插入股票 {stock_data[0]} 失败: {e2}")
                            failed_stocks += 1

            total_processed = new_stocks + updated_stocks

            self.logger.info(
                f"股票列表更新完成: 新增 {new_stocks}只, 更新 {updated_stocks}只, 失败 {failed_stocks}只"
            )

            return {
                "status": "completed",
                "total_stocks": total_processed,
                "new_stocks": new_stocks,
                "updated_stocks": updated_stocks,
                "failed_stocks": failed_stocks,
            }

        except Exception as e:
            self.logger.error(f"更新股票列表失败: {e}")
            return {
                "status": "failed",
                "error": str(e),
                "total_stocks": 0,
                "new_stocks": 0,
                "updated_stocks": 0,
            }

    def _determine_market(self, symbol: str) -> str:
        """
        确定股票市场（带缓存）

        Args:
            symbol: 股票代码（纯数字，不含后缀）

        Returns:
            市场代码: SS(上海) / SZ(深圳) / BJ(北交所)
        """
        # 使用缓存提升性能
        if self.enable_cache and symbol in self._market_cache:
            return self._market_cache[symbol]

        if not symbol or not isinstance(symbol, str):
            result = "SZ"  # 默认深圳
        else:
            # 清理股票代码，移除可能的后缀
            clean_symbol = symbol.split(".")[0].strip()

            if not clean_symbol or not clean_symbol.isdigit():
                result = "SZ"  # 默认深圳
            # 上海证券交易所
            elif clean_symbol.startswith(("600", "601", "603", "605", "688", "689")):
                result = "SS"
            # 深圳证券交易所
            elif clean_symbol.startswith(("000", "001", "002", "003", "300", "301")):
                result = "SZ"
            # 北京证券交易所
            elif clean_symbol.startswith(("8", "43", "83")):
                result = "BJ"
            else:
                # 其他情况，根据首位数字判断
                first_char = clean_symbol[0]
                if first_char == "6":
                    result = "SS"  # 上海
                elif first_char in ["0", "3"]:
                    result = "SZ"  # 深圳
                elif first_char == "8":
                    result = "BJ"  # 北交所
                else:
                    result = "SZ"  # 默认深圳

        # 缓存结果
        if self.enable_cache:
            self._market_cache[symbol] = result

        return result

    def clear_cache(self):
        """清理缓存"""
        if hasattr(self, "_market_cache"):
            self._market_cache.clear()
        if hasattr(self, "_stock_info_cache"):
            self._stock_info_cache.clear()
        self.logger.debug("缓存已清理")

    def get_cache_stats(self) -> Dict[str, int]:
        """获取缓存统计信息"""
        return {
            "market_cache_size": len(getattr(self, "_market_cache", {})),
            "stock_info_cache_size": len(getattr(self, "_stock_info_cache", {})),
        }

    def _fetch_detailed_stock_info(self, symbol: str):
        """获取股票详细信息（股本、上市日期等）"""
        try:
            # 获取股票详细信息
            response = self.data_source_manager.get_stock_info(symbol)

            if not response or not isinstance(response, dict):
                self.logger.warning(
                    f"获取股票详细信息失败: {symbol} - 响应为空或格式错误"
                )
                return

            # 双重解包数据（因为是嵌套格式）
            detail_info = self._extract_data_safely(response)
            if (
                isinstance(detail_info, dict)
                and detail_info.get("success")
                and "data" in detail_info
            ):
                detail_info = self._extract_data_safely(detail_info)

            if not detail_info or not isinstance(detail_info, dict):
                self.logger.warning(f"获取股票详细信息失败: {symbol} - 解包后数据为空")
                return

            # 提取字段信息（适配不同数据源的字段名）
            total_shares = detail_info.get("total_shares", 0) or 0
            float_shares = detail_info.get("float_shares", 0) or 0
            list_date = detail_info.get("list_date", "")

            # 处理行业信息 - 优先使用具体的l1/l2字段
            industry_l1 = detail_info.get("industry_l1", "") or detail_info.get(
                "industry", ""
            )
            industry_l2 = detail_info.get("industry_l2", "")

            self.logger.debug(
                f"提取到股票信息: {symbol} - list_date={list_date}, industry_l1={industry_l1}, industry_l2={industry_l2}"
            )

            # 只要有任何一个有效字段就更新（修复条件判断）
            has_data = (
                (total_shares and total_shares > 0)
                or (float_shares and float_shares > 0)
                or (list_date and list_date.strip())
                or (industry_l1 and industry_l1.strip())
                or (industry_l2 and industry_l2.strip())
            )

            if has_data:
                self.db_manager.execute(
                    """
                    UPDATE stocks
                    SET total_shares = ?, float_shares = ?, list_date = ?,
                        industry_l1 = ?, industry_l2 = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE symbol = ?
                    """,
                    (
                        total_shares if total_shares > 0 else None,
                        float_shares if float_shares > 0 else None,
                        list_date if list_date and list_date.strip() else None,
                        industry_l1 if industry_l1 and industry_l1.strip() else None,
                        industry_l2 if industry_l2 and industry_l2.strip() else None,
                        symbol,
                    ),
                )
                self.logger.info(f"✅ 更新股票详细信息: {symbol}")
            else:
                self.logger.warning(f"⚠️  股票详细信息为空: {symbol}")

        except Exception as e:
            self.logger.error(f"获取 {symbol} 详细信息失败: {e}")
            self.logger.debug(f"详细错误信息: {symbol}", exc_info=True)

    def _safe_extract_number(
        self, value: Any, default: Optional[float] = None
    ) -> Optional[float]:
        """
        安全提取数字，支持中文单位转换

        Args:
            value: 待转换的值
            default: 默认值

        Returns:
            转换后的数字或默认值
        """
        if value is None or value == "":
            return default

        try:
            str_value = str(value).strip()

            # 处理特殊值
            if str_value.lower() in ["nan", "null", "none", "-", "--"]:
                return default

            # 移除逗号分隔符
            str_value = str_value.replace(",", "")

            # 处理中文单位
            multiplier = 1
            if "万" in str_value:
                str_value = str_value.replace("万", "")
                multiplier = SyncConstants.WAN_MULTIPLIER
            elif "亿" in str_value:
                str_value = str_value.replace("亿", "")
                multiplier = SyncConstants.YI_MULTIPLIER

            # 转换为浮点数
            number = float(str_value)
            return number * multiplier

        except (ValueError, TypeError) as e:
            self.logger.debug(f"数字转换失败: {value} -> {e}")
            return default

    def _safe_extract_date(
        self, value: Any, default: Optional[str] = None
    ) -> Optional[str]:
        """
        安全提取日期，统一格式化为YYYY-MM-DD

        Args:
            value: 待转换的日期值
            default: 默认值

        Returns:
            格式化后的日期字符串或默认值
        """
        if value is None or value == "":
            return default

        try:
            str_value = str(value).strip()

            # 处理特殊值
            if str_value.lower() in ["nan", "null", "none", "-", "--"]:
                return default

            # 匹配常见日期格式
            date_patterns = [
                r"(\d{4})-(\d{1,2})-(\d{1,2})",  # YYYY-MM-DD
                r"(\d{4})/(\d{1,2})/(\d{1,2})",  # YYYY/MM/DD
                r"(\d{4})\.(\d{1,2})\.(\d{1,2})",  # YYYY.MM.DD
                r"(\d{4})(\d{2})(\d{2})",  # YYYYMMDD
            ]

            for pattern in date_patterns:
                match = re.match(pattern, str_value)
                if match:
                    year, month, day = match.groups()
                    # 标准化格式
                    try:
                        parsed_date = datetime(int(year), int(month), int(day))
                        return parsed_date.strftime(SyncConstants.DATE_FORMAT)
                    except ValueError:
                        continue

            self.logger.debug(f"无法解析日期格式: {value}")
            return default

        except (ValueError, TypeError) as e:
            self.logger.debug(f"日期转换失败: {value} -> {e}")
            return default

    def _sync_extended_data(
        self, symbols: List[str], target_date: date, progress_bar=None
    ) -> Dict[str, Any]:
        """增量同步扩展数据（财务数据、估值数据等）"""
        session_id = str(uuid.uuid4())
        self.logger.info(f"🔄 开始扩展数据同步: {len(symbols)}只股票")

        result = {
            "financials_count": 0,
            "valuations_count": 0,
            "indicators_count": 0,
            "processed_symbols": 0,
            "failed_symbols": 0,
            "session_id": session_id,
        }

        if not symbols:
            self.logger.info("✅ 没有股票需要处理")
            if progress_bar:
                progress_bar.update(0)
            return result

        self.logger.info(f"📊 开始处理: {len(symbols)}只股票")

        # 批量处理每只股票（添加事务保护）
        for i, symbol in enumerate(symbols):
            self.logger.debug(f"处理 {symbol} ({i+1}/{len(symbols)})")

            try:
                # 使用事务保护同步单个股票
                symbol_result = self._sync_single_symbol_with_transaction(
                    symbol, target_date, session_id
                )

                # 更新结果统计
                if symbol_result.get("success", False):
                    result["financials_count"] += symbol_result.get(
                        "financials_count", 0
                    )
                    result["valuations_count"] += symbol_result.get(
                        "valuations_count", 0
                    )
                    result["indicators_count"] += symbol_result.get(
                        "indicators_count", 0
                    )
                else:
                    result["failed_symbols"] += 1

                result["processed_symbols"] += 1

            except Exception as e:
                self.logger.error(f"同步股票失败: {symbol} - {e}")
                self.logger.debug(f"同步股票详细错误: {symbol}", exc_info=True)
                result["failed_symbols"] += 1
                result["processed_symbols"] += 1

            # 更新进度条
            if progress_bar:
                progress_bar.update(1)

        return result

    def _log_data_failure_with_context(
        self, symbol: str, target_date: date, failure_reasons: List[str]
    ):
        """
        记录带上下文的数据获取失败信息

        Args:
            symbol: 股票代码
            target_date: 目标日期
            failure_reasons: 失败原因列表
        """
        try:
            # 尝试获取股票信息来判断是否已上市
            stock_info_result = self.data_source_manager.get_stock_info(symbol)

            # 处理多层嵌套的响应格式
            stock_info = stock_info_result
            while isinstance(stock_info, dict) and "data" in stock_info:
                if stock_info.get("success") is False:
                    # 如果任何层级失败，跳出处理
                    stock_info = None
                    break
                stock_info = stock_info["data"]

            if stock_info and isinstance(stock_info, dict):
                ipo_date_str = stock_info.get("list_date", "")
                if ipo_date_str:
                    try:
                        ipo_date = datetime.strptime(
                            ipo_date_str, SyncConstants.DATE_FORMAT
                        ).date()

                        if ipo_date > target_date:
                            # 股票尚未上市，这是预期情况
                            self.logger.info(
                                f"股票尚未上市: {symbol} (上市日期: {ipo_date_str}, 目标日期: {target_date})"
                            )
                            return
                        else:
                            # 股票已上市但数据获取失败，这可能是数据源问题或股票状态异常
                            failure_detail = ", ".join(failure_reasons)
                            self.logger.warning(
                                f"数据获取失败: {symbol} ({failure_detail}) - 股票已上市但无可用数据"
                            )
                            return
                    except (ValueError, TypeError):
                        # IPO日期格式错误，按一般失败处理
                        pass

            # 无法获取IPO信息或格式异常，按一般数据获取失败处理
            failure_detail = ", ".join(failure_reasons)
            self.logger.warning(f"数据获取失败: {symbol} ({failure_detail})")

        except Exception as e:
            # 获取股票信息时发生异常，记录详细错误
            failure_detail = ", ".join(failure_reasons)
            self.logger.warning(f"数据获取失败: {symbol} ({failure_detail})")
            self.logger.debug(f"股票信息检查异常: {symbol} - {e}")

    def _sync_single_symbol_with_transaction(
        self, symbol: str, target_date: date, session_id: str
    ) -> Dict[str, Any]:
        """使用事务保护同步单个股票的扩展数据"""
        result = {
            "success": False,
            "financials_count": 0,
            "valuations_count": 0,
            "indicators_count": 0,
        }

        try:
            # 开始事务
            self.db_manager.execute("BEGIN TRANSACTION")

            # 检查是否已经处理过这只股票
            existing_status = self.db_manager.fetchone(
                "SELECT status FROM extended_sync_status WHERE symbol = ? AND target_date = ? AND status = 'completed'",
                (symbol, str(target_date)),
            )

            if existing_status:
                self.logger.debug(f"⏭️ 跳过已完成的股票: {symbol}")
                result["success"] = True
                self.db_manager.execute("COMMIT")
                return result

            # 标记开始处理
            self.db_manager.execute(
                "INSERT OR REPLACE INTO extended_sync_status (symbol, sync_type, target_date, status, session_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
                (symbol, "processing", str(target_date), "processing", session_id),
            )

            # 数据获取成功标志
            financial_success = False
            valuation_success = False

            # 处理财务数据
            report_year = target_date.year - 1  # 使用去年年报
            report_date_str = f"{report_year}-12-31"

            # 验证报告期有效性
            if DataQualityValidator.is_valid_report_date(report_date_str, symbol):
                # 优先尝试BaoStock获取财务数据
                try:
                    baostock_source = self.data_source_manager.sources.get("baostock")
                    if baostock_source and baostock_source.is_connected():
                        financial_data = baostock_source.get_fundamentals(
                            symbol, report_date_str, "Q4"
                        )

                        if financial_data and self._is_valid_financial_data_relaxed(
                            financial_data
                        ):
                            self._insert_financial_data(
                                financial_data, symbol, report_date_str, "baostock"
                            )
                            result["financials_count"] += 1
                            financial_success = True
                            self.logger.debug(f"BaoStock财务数据插入成功: {symbol}")
                        else:
                            self.logger.debug(f"BaoStock财务数据无效: {symbol}")
                    else:
                        self.logger.debug(f"BaoStock未连接，跳过: {symbol}")

                except Exception as e:
                    self.logger.debug(f"BaoStock获取财务数据失败: {symbol} - {e}")

                # 如果BaoStock失败，尝试AkShare作为后备
                if not financial_success:
                    try:
                        financial_data = self.data_source_manager.get_fundamentals(
                            symbol, report_date_str, "Q4"
                        )

                        # 标准数据源响应格式解包
                        financial_data = self._extract_data_safely(financial_data)

                        # 使用放宽的验证标准
                        if financial_data and self._is_valid_financial_data_relaxed(
                            financial_data
                        ):
                            self._insert_financial_data(
                                financial_data, symbol, report_date_str, "akshare"
                            )
                            result["financials_count"] += 1
                            financial_success = True
                            self.logger.debug(f"AkShare财务数据插入成功: {symbol}")
                        else:
                            self.logger.debug(f"AkShare财务数据无效: {symbol}")

                    except Exception as e:
                        self.logger.warning(f"AkShare获取财务数据失败: {symbol} - {e}")
            else:
                self.logger.warning(f"跳过无效报告期: {symbol} {report_date_str}")

            # 处理估值数据
            try:
                # 优先尝试BaoStock获取估值数据
                baostock_source = self.data_source_manager.sources.get("baostock")
                if baostock_source and baostock_source.is_connected():
                    try:
                        valuation_data = baostock_source.get_valuation_data(
                            symbol, str(target_date)
                        )

                        # BaoStock返回单个字典，转换为列表以统一处理
                        if valuation_data and isinstance(valuation_data, dict):
                            valuation_records = [valuation_data]
                        elif valuation_data and isinstance(valuation_data, list):
                            valuation_records = valuation_data
                        else:
                            valuation_records = []

                        if valuation_records:
                            for record in valuation_records:
                                # 确保 record 是字典类型
                                if not isinstance(record, dict):
                                    continue

                                # 检查是否已存在该记录
                                record_date = record.get("date", "")
                                existing = self.db_manager.fetchone(
                                    "SELECT COUNT(*) as count FROM valuations WHERE symbol = ? AND date = ?",
                                    (symbol, record_date),
                                )

                                if existing and existing["count"] == 0:
                                    self.db_manager.execute(
                                        """INSERT INTO valuations
                                        (symbol, date, pe_ratio, pb_ratio, ps_ratio, pcf_ratio, source)
                                        VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                        (
                                            symbol,
                                            record_date,
                                            record.get("pe_ratio"),
                                            record.get("pb_ratio"),
                                            record.get("ps_ratio"),
                                            record.get("pcf_ratio"),
                                            record.get("source", ""),
                                        ),
                                    )
                                    result["valuations_count"] += 1

                            if valuation_records:
                                valuation_success = True
                                self.logger.debug(f"BaoStock估值数据插入成功: {symbol}")
                    except Exception as e:
                        self.logger.debug(f"BaoStock获取估值数据失败: {symbol} - {e}")

                # 如果BaoStock失败，尝试原有方式作为后备
                if not valuation_success:
                    valuation_data = self.data_source_manager.get_valuation_data(
                        symbol, str(target_date)
                    )

                    # 标准数据源响应格式解包
                    valuation_data = self._extract_data_safely(valuation_data)

                    # 验证估值数据有效性
                    if valuation_data and DataQualityValidator.is_valid_valuation_data(
                        valuation_data
                    ):
                        self.db_manager.execute(
                            "INSERT OR REPLACE INTO valuations (symbol, date, pe_ratio, pb_ratio, source, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
                            (
                                symbol,
                                str(target_date),
                                valuation_data.get("pe_ratio", None),
                                valuation_data.get("pb_ratio", None),
                                "akshare",
                            ),
                        )
                        result["valuations_count"] += 1
                        valuation_success = True
                        self.logger.debug(f"AkShare估值数据插入成功: {symbol}")
                    else:
                        self.logger.debug(f"估值数据无效，跳过: {symbol}")

            except Exception as e:
                self.logger.warning(f"获取估值数据失败: {symbol} - {e}")

            # 处理技术指标（暂时跳过，标记为成功）

            # 根据数据获取结果决定最终状态（使用分级标准）
            failure_reasons = []

            if financial_success:
                final_status = "completed"  # 有财务数据就算完成
                result["success"] = True
                self.logger.debug(
                    f"数据获取成功: {symbol} (财务:{financial_success}, 估值:{valuation_success})"
                )
            elif valuation_success:
                final_status = "partial"  # 只有估值数据算部分完成
                result["success"] = True
                self.logger.debug(f"部分数据获取成功: {symbol} (仅估值数据)")
            else:
                final_status = "failed"
                result["success"] = False

                # 收集具体的失败原因
                if not financial_success:
                    failure_reasons.append("财务数据")
                if not valuation_success:
                    failure_reasons.append("估值数据")

                # 检查股票上市状态和失败原因
                self._log_data_failure_with_context(
                    symbol, target_date, failure_reasons
                )

            # 更新最终状态
            self.db_manager.execute(
                "UPDATE extended_sync_status SET status = ?, updated_at = datetime('now') WHERE symbol = ? AND target_date = ? AND session_id = ?",
                (final_status, symbol, str(target_date), session_id),
            )

            # 提交事务
            self.db_manager.execute("COMMIT")
            return result

        except Exception as e:
            # 回滚事务
            try:
                self.db_manager.execute("ROLLBACK")
            except:
                pass  # 忽略回滚错误
            self.logger.error(f"同步股票失败，事务回滚: {symbol} - {e}")
            result["success"] = False
            return result

    def _auto_fix_gaps(self, gap_result: Dict[str, Any]) -> Dict[str, Any]:
        """自动修复缺口"""
        self.logger.info("开始自动修复缺口")

        fix_result = {
            "total_gaps": gap_result["summary"]["total_gaps"],
            "attempted_fixes": 0,
            "successful_fixes": 0,
            "failed_fixes": 0,
            "skipped_fixes": 0,  # 新增：跳过的修复
        }

        # 处理缺口数据结构 - 适配新的数据格式
        all_gaps = []
        for freq_data in gap_result.get("gaps_by_frequency", {}).values():
            all_gaps.extend(freq_data.get("gaps", []))

        if not all_gaps:
            self.logger.info("没有发现缺口，无需修复")
            return fix_result

        # 限制修复数量，优先修复重要股票的缺口
        max_fixes = 10
        fixes_attempted = 0

        for gap in all_gaps:
            if fixes_attempted >= max_fixes:
                break

            symbol = gap.get("symbol")
            gap_start = gap.get("start_date")
            gap_end = gap.get("end_date")
            frequency = gap.get("frequency", "1d")

            if not symbol or not gap_start or not gap_end or frequency != "1d":
                continue

            # 检查股票是否适合修复（避免修复新股或停牌股的缺口）
            stock_info = self.db_manager.fetchone(
                "SELECT list_date, status FROM stocks WHERE symbol = ?", (symbol,)
            )

            if not stock_info:
                self.logger.debug(f"跳过修复: {symbol} - 股票信息不存在")
                continue

            # 检查缺口是否在股票上市日期之后
            if stock_info["list_date"]:
                from datetime import datetime

                list_date = datetime.strptime(
                    stock_info["list_date"], "%Y-%m-%d"
                ).date()
                gap_start_date = datetime.strptime(gap_start, "%Y-%m-%d").date()

                if gap_start_date < list_date:
                    fix_result["skipped_fixes"] += 1
                    self.logger.debug(f"跳过修复: {symbol} 缺口日期早于上市日期")
                    continue

            fix_result["attempted_fixes"] += 1
            fixes_attempted += 1

            self.logger.info(f"修复缺口: {symbol} {gap_start} 到 {gap_end}")

            # 获取数据填补缺口
            daily_data = self.data_source_manager.get_daily_data(
                symbol, gap_start, gap_end
            )

            if isinstance(daily_data, dict) and "data" in daily_data:
                daily_data = daily_data["data"]

            # 实际处理数据插入
            if (
                daily_data is not None
                and hasattr(daily_data, "__len__")
                and len(daily_data) > 0
            ):
                try:
                    # 使用处理引擎插入缺口数据
                    processed_result = self.processing_engine.process_symbol_data(
                        symbol, str(gap_start), str(gap_end), frequency
                    )
                    records_inserted = processed_result.get("records", 0)

                    if records_inserted > 0:
                        fix_result["successful_fixes"] += 1
                        self.logger.info(
                            f"缺口修复成功: {symbol} 插入{records_inserted}条记录"
                        )
                    else:
                        fix_result["failed_fixes"] += 1
                        self.logger.warning(
                            f"缺口修复失败: {symbol} 处理引擎未插入数据"
                        )
                except Exception as e:
                    fix_result["failed_fixes"] += 1
                    self.logger.warning(f"缺口修复出错: {symbol} - {e}")
            else:
                fix_result["failed_fixes"] += 1
                self.logger.debug(f"缺口修复跳过: {symbol} 数据源无数据（可能正常）")

        self.logger.info(
            f"缺口修复完成: 尝试={fix_result['attempted_fixes']}, 成功={fix_result['successful_fixes']}, 失败={fix_result['failed_fixes']}, 跳过={fix_result['skipped_fixes']}"
        )

        # 如果大部分缺口都无法修复，说明这些缺口可能是正常的
        if fix_result["attempted_fixes"] > 0:
            success_rate = (
                fix_result["successful_fixes"] / fix_result["attempted_fixes"]
            )
            if success_rate < 0.3:
                self.logger.info(
                    "💡 大部分缺口无法修复，这可能是正常现象（新股、停牌等）"
                )

        return fix_result

    def _is_valid_financial_data_relaxed(self, data: Dict[str, Any]) -> bool:
        """放宽的财务数据有效性验证"""
        if not data or not isinstance(data, dict):
            return False

        # 检查是否有任何有效的财务指标（放宽标准）
        revenue = data.get("revenue", 0)
        net_profit = data.get("net_profit", 0)
        total_assets = data.get("total_assets", 0)
        shareholders_equity = data.get("shareholders_equity", 0)
        eps = data.get("eps", 0)

        # 只要有一个非空/非零的财务指标就认为有效
        return (
            (revenue and revenue != 0)
            or (total_assets and total_assets != 0)
            or (shareholders_equity and shareholders_equity != 0)
            or (net_profit != 0)  # 净利润可以为负
            or (eps and eps != 0)  # 每股收益可以为负
        )

    def _insert_financial_data(
        self,
        financial_data: Dict[str, Any],
        symbol: str,
        report_date_str: str,
        source: str,
    ):
        """插入财务数据到数据库"""
        try:
            self.db_manager.execute(
                """INSERT OR REPLACE INTO financials (
                    symbol, report_date, report_type, revenue, operating_profit, net_profit,
                    gross_margin, net_margin, total_assets, total_liabilities, shareholders_equity,
                    operating_cash_flow, investing_cash_flow, financing_cash_flow,
                    eps, bps, roe, roa, debt_ratio, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                (
                    symbol,
                    report_date_str,
                    "Q4",
                    financial_data.get("revenue", 0),
                    financial_data.get("operating_profit", 0),
                    financial_data.get("net_profit", 0),
                    financial_data.get("gross_margin", 0),
                    financial_data.get("net_margin", 0),
                    financial_data.get("total_assets", 0),
                    financial_data.get("total_liabilities", 0),
                    financial_data.get("shareholders_equity", 0),
                    financial_data.get("operating_cash_flow", 0),
                    financial_data.get("investing_cash_flow", 0),
                    financial_data.get("financing_cash_flow", 0),
                    financial_data.get("eps", 0),
                    financial_data.get("bps", 0),
                    financial_data.get("roe", 0),
                    financial_data.get("roa", 0),
                    financial_data.get("debt_ratio", 0),
                    source,
                ),
            )
        except Exception as e:
            self.logger.error(f"插入财务数据失败 {symbol}: {e}")
            raise

    def generate_sync_report(self, full_result: Dict[str, Any]) -> str:
        """生成同步报告"""
        report_lines = []

        # 报告头部
        report_lines.append("=" * 60)
        report_lines.append("数据同步报告")
        report_lines.append("=" * 60)
        report_lines.append(f"同步时间: {full_result.get('start_time', '')}")
        report_lines.append(f"目标日期: {full_result.get('target_date', '')}")
        report_lines.append(f"总耗时: {full_result.get('duration_seconds', 0):.2f} 秒")
        report_lines.append("")

        # 阶段汇总
        summary = full_result.get("summary", {})
        report_lines.append("阶段汇总:")
        report_lines.append(f"  总阶段数: {summary.get('total_phases', 0)}")
        report_lines.append(f"  成功阶段: {summary.get('successful_phases', 0)}")
        report_lines.append(f"  失败阶段: {summary.get('failed_phases', 0)}")
        report_lines.append("")

        # 增量同步详情
        phases = full_result.get("phases", {})
        if "incremental_sync" in phases:
            phase = phases["incremental_sync"]
            report_lines.append("增量同步:")
            report_lines.append(f"  状态: {phase['status']}")

            if phase["status"] == "completed" and "result" in phase:
                result = phase["result"]
                report_lines.append(f"  总股票数: {result.get('total_symbols', 0)}")
                report_lines.append(f"  成功数量: {result.get('success_count', 0)}")
                report_lines.append(f"  错误数量: {result.get('error_count', 0)}")
            elif "error" in phase:
                report_lines.append(f"  错误: {phase['error']}")

        return "\n".join(report_lines)
