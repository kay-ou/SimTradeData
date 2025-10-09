"""
增量同步器

负责检测最后数据日期，计算增量数据范围，执行批量数据同步。
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from ..config import Config
from ..core import extract_data_safely
from ..data_sources import DataSourceManager
from ..database import DatabaseManager
from ..database.batch_writer import BatchWriter
from ..monitoring import PerformanceMonitor
from ..performance.cache_manager import CacheManager
from ..preprocessor import DataProcessingEngine

logger = logging.getLogger(__name__)


class IncrementalSync:
    """增量同步器"""

    def __init__(
        self,
        db_manager: DatabaseManager,
        data_source_manager: DataSourceManager,
        processing_engine: DataProcessingEngine,
        config: Config = None,
    ):
        """
        初始化增量同步器

        Args:
            db_manager: 数据库管理器
            data_source_manager: 数据源管理器
            processing_engine: 数据处理引擎
            config: 配置对象
        """
        self.db_manager = db_manager
        self.data_source_manager = data_source_manager
        self.processing_engine = processing_engine
        self.config = config or Config()

        # 同步配置
        self.max_sync_days = self.config.get("sync.max_sync_days", 30)
        self.batch_size = self.config.get("sync.batch_size", 50)
        self.max_workers = self.config.get("sync.max_workers", 3)
        self.sync_frequencies = self.config.get("sync.frequencies", ["1d"])
        self.enable_parallel = self.config.get("sync.enable_parallel", True)

        # 智能补充配置（默认禁用，可通过配置启用）
        self.enable_smart_backfill = self.config.get(
            "sync.enable_smart_backfill", False
        )
        self.backfill_batch_size = self.config.get("sync.backfill_batch_size", 50)
        self.backfill_sample_size = self.config.get("sync.backfill_sample_size", 10)

        # 批量写入配置
        self.enable_batch_writer = self.config.get(
            "performance.batch_writer.enable", True
        )
        self.batch_write_size = self.config.get(
            "performance.batch_writer.batch_size", 100
        )

        # 缓存配置
        self.enable_cache = self.config.get("performance.cache.enable", True)

        # 初始化缓存管理器
        self.cache_manager = None
        if self.enable_cache:
            try:
                self.cache_manager = CacheManager(config=self.config)
                logger.info("缓存管理器初始化成功")
            except Exception as e:
                logger.warning(f"缓存管理器初始化失败: {e}，将禁用缓存功能")
                self.enable_cache = False

        # 性能监控配置
        self.enable_performance_monitor = self.config.get(
            "performance.monitor.enable", True
        )
        self.enable_resource_monitoring = self.config.get(
            "performance.monitor.enable_resource_monitoring", False
        )

        # 初始化性能监控器
        self.performance_monitor = None
        if self.enable_performance_monitor:
            try:
                self.performance_monitor = PerformanceMonitor(
                    enable_resource_monitoring=self.enable_resource_monitoring
                )
                logger.info("性能监控器初始化成功")
            except Exception as e:
                logger.warning(f"性能监控器初始化失败: {e}，将禁用性能监控功能")
                self.enable_performance_monitor = False

        # 同步统计
        self.sync_stats = {
            "total_symbols": 0,
            "success_count": 0,
            "error_count": 0,
            "skipped_count": 0,
            "sync_date_ranges": {},
            "errors": [],
        }

        logger.info("增量同步器初始化完成")

    def sync_all_symbols(
        self,
        target_date: date = None,
        symbols: List[str] = None,
        frequencies: List[str] = None,
        progress_bar=None,
    ) -> Dict[str, Any]:
        """
        同步所有股票的增量数据

        Args:
            target_date: 目标日期，默认为今天
            symbols: 股票代码列表，默认为所有活跃股票
            frequencies: 频率列表，默认为配置中的频率

        Returns:
            Dict[str, Any]: 同步结果
        """
        if target_date is None:
            target_date = datetime.now().date()

        if frequencies is None:
            frequencies = self.sync_frequencies

        try:
            logger.info(f"开始增量同步: 目标日期={target_date}, 频率={frequencies}")

            # 🎯 开始性能监控
            if self.enable_performance_monitor and self.performance_monitor:
                self.performance_monitor.start_phase("total")

            # 重置统计
            self._reset_stats()

            # 获取需要同步的股票列表
            if symbols is None:
                symbols = self._get_active_symbols()

            self.sync_stats["total_symbols"] = len(symbols)

            # 🚀 缓存预加载阶段
            if self.enable_cache and self.cache_manager:
                try:
                    logger.info("开始预加载缓存...")

                    # 预加载交易日历（最近2年）
                    calendar_start = target_date - timedelta(days=730)  # 2年
                    calendar_result = self.cache_manager.load_trading_calendar(
                        self.db_manager, calendar_start, target_date, market="CN"
                    )

                    # 处理 unified_error_handler 包装的返回值
                    if isinstance(calendar_result, dict) and "data" in calendar_result:
                        calendar_count = calendar_result["data"]
                    else:
                        calendar_count = calendar_result

                    logger.info(f"预加载交易日历: {calendar_count} 天")

                    # 预加载活跃股票元数据
                    if symbols:
                        metadata_result = self.cache_manager.load_stock_metadata_batch(
                            self.db_manager, symbols
                        )

                        # 处理 unified_error_handler 包装的返回值
                        if (
                            isinstance(metadata_result, dict)
                            and "data" in metadata_result
                        ):
                            metadata_count = metadata_result["data"]
                        else:
                            metadata_count = metadata_result

                        logger.info(f"预加载股票元数据: {metadata_count} 只股票")

                except Exception as cache_error:
                    logger.warning(
                        f"缓存预加载失败: {cache_error}，将继续使用数据库查询"
                    )

            # 🚀 智能补充阶段：检查并补充历史数据的衍生字段
            backfill_stats = {
                "enabled": self.enable_smart_backfill,
                "checked_symbols": 0,
                "needs_backfill_symbols": 0,
                "backfilled_symbols": 0,
                "backfilled_records": 0,
                "backfill_errors": 0,
            }

            if self.enable_smart_backfill:
                logger.info("开始智能数据质量检查和补充...")

                # 🎯 开始智能补充阶段监控
                if self.enable_performance_monitor and self.performance_monitor:
                    self.performance_monitor.start_phase("smart_backfill")

                # 检查前几只股票来估算整体情况
                sample_size = min(self.backfill_sample_size, len(symbols))
                sample_symbols = symbols[:sample_size]
                needs_backfill_count = 0

                for symbol in sample_symbols:
                    quality_check = self.check_data_quality(symbol, frequencies[0])
                    if quality_check.get("needs_backfill", False):
                        needs_backfill_count += 1

                # 如果样本中有需要补充的数据，则对所有股票进行智能补充
                if needs_backfill_count > 0:
                    backfill_ratio = needs_backfill_count / sample_size
                    estimated_total = int(len(symbols) * backfill_ratio)
                    logger.info(
                        f"检测到数据质量问题：样本中 {needs_backfill_count}/{sample_size} 只股票需要补充衍生字段"
                    )
                    logger.info(
                        f"预估全部 {len(symbols)} 只股票中约 {estimated_total} 只需要补充，开始智能补充..."
                    )

                    # 对所有股票进行智能补充（分批处理以避免内存问题）
                    batch_size = self.backfill_batch_size

                    for i in range(0, len(symbols), batch_size):
                        batch_symbols = symbols[i : i + batch_size]
                        batch_num = i // batch_size + 1
                        total_batches = (len(symbols) + batch_size - 1) // batch_size

                        logger.info(
                            f"智能补充批次 {batch_num}/{total_batches}: 处理 {len(batch_symbols)} 只股票"
                        )

                        for symbol in batch_symbols:
                            try:
                                # 快速检查是否需要补充
                                quality_check = self.check_data_quality(
                                    symbol, frequencies[0]
                                )
                                backfill_stats["checked_symbols"] += 1

                                if quality_check.get("needs_backfill", False):
                                    backfill_stats["needs_backfill_symbols"] += 1

                                    # 执行智能补充
                                    backfill_result = self.smart_backfill_symbol(
                                        symbol, frequencies[0]
                                    )

                                    if backfill_result.get("success", False):
                                        backfill_stats["backfilled_symbols"] += 1
                                        backfill_stats[
                                            "backfilled_records"
                                        ] += backfill_result.get("updated_count", 0)
                                    else:
                                        backfill_stats["backfill_errors"] += 1

                            except Exception as e:
                                logger.warning(f"智能补充股票 {symbol} 时出错: {e}")
                                backfill_stats["backfill_errors"] += 1

                        # 注意：不在这里更新主进度条，因为正常增量同步阶段会更新
                        # 避免重复更新导致进度超过100%

                    logger.info(
                        f"智能补充完成: 检查了 {backfill_stats['checked_symbols']} 只股票，"
                        f"补充了 {backfill_stats['backfilled_symbols']} 只股票的 {backfill_stats['backfilled_records']} 条记录"
                    )

                    # 🎯 结束智能补充阶段监控
                    if self.enable_performance_monitor and self.performance_monitor:
                        self.performance_monitor.end_phase(
                            "smart_backfill", backfill_stats["backfilled_records"]
                        )

                else:
                    logger.info("样本检查显示数据质量良好，跳过智能补充阶段")
            else:
                logger.info("智能补充功能已禁用，跳过数据质量检查")

            # 📈 正常增量同步阶段

            # 🎯 开始增量同步阶段监控
            if self.enable_performance_monitor and self.performance_monitor:
                self.performance_monitor.start_phase("incremental_sync")

            # 按频率同步
            for frequency in frequencies:
                freq_result = self._sync_frequency_data(
                    symbols, target_date, frequency, progress_bar
                )
                self.sync_stats["sync_date_ranges"][frequency] = freq_result

            # 🎯 结束增量同步阶段监控
            if self.enable_performance_monitor and self.performance_monitor:
                self.performance_monitor.end_phase(
                    "incremental_sync", self.sync_stats["success_count"]
                )

            # 更新同步状态
            self._update_sync_status(target_date, self.sync_stats)

            # 将智能补充统计信息添加到结果中
            self.sync_stats["smart_backfill"] = backfill_stats

            # 修正成功率计算：如果有成功的股票处理，即使有部分错误也应当整体标记为部分成功
            effective_success = self.sync_stats["success_count"] > 0
            effective_error = self.sync_stats["error_count"] > 0

            if effective_success and not effective_error:
                result_status = "completed"
            elif effective_success and effective_error:
                result_status = "partial_success"
            else:
                result_status = "failed"

            logger.info(
                f"增量同步完成: 成功={self.sync_stats['success_count']}, "
                f"错误={self.sync_stats['error_count']}, "
                f"跳过={self.sync_stats['skipped_count']}, "
                f"整体状态={result_status}"
            )

            if backfill_stats["backfilled_symbols"] > 0:
                logger.info(
                    f"智能补充完成: 补充了 {backfill_stats['backfilled_symbols']} 只股票的 "
                    f"{backfill_stats['backfilled_records']} 条历史记录的衍生字段"
                )

            # 🎯 结束总体监控并生成报告
            if self.enable_performance_monitor and self.performance_monitor:
                self.performance_monitor.end_phase(
                    "total", self.sync_stats["success_count"]
                )

                # 生成性能报告
                try:
                    report = self.performance_monitor.generate_report()

                    # 记录文本格式报告到日志
                    logger.info("\n" + report.to_text())

                    # 识别瓶颈并记录优化建议
                    bottlenecks = report.bottlenecks
                    if bottlenecks:
                        logger.warning("⚠️  检测到性能瓶颈:")
                        for bottleneck in bottlenecks:
                            logger.warning(f"  - {bottleneck}")
                        logger.info(
                            "💡 优化建议: 考虑调整相关配置参数以提升瓶颈阶段的性能"
                        )

                    # 将报告添加到同步统计中
                    self.sync_stats["performance_report"] = report.to_dict()

                except Exception as report_error:
                    logger.warning(f"生成性能报告失败: {report_error}")

            return self.sync_stats.copy()

        except Exception as e:
            logger.error(f"增量同步失败: {e}")
            raise

    def check_data_quality(self, symbol: str, frequency: str = "1d") -> Dict[str, Any]:
        """
        检查股票数据质量，特别是衍生字段的完整性

        Args:
            symbol: 股票代码
            frequency: 频率

        Returns:
            Dict[str, Any]: 数据质量报告
        """
        try:
            sql = """
            SELECT 
                COUNT(*) as total_records,
                COUNT(CASE WHEN change_percent IS NULL THEN 1 END) as null_change_percent,
                COUNT(CASE WHEN prev_close IS NULL THEN 1 END) as null_prev_close,
                COUNT(CASE WHEN amplitude IS NULL THEN 1 END) as null_amplitude,
                COUNT(CASE WHEN source LIKE '%enhanced' THEN 1 END) as enhanced_records,
                MIN(date) as earliest_date,
                MAX(date) as latest_date
            FROM market_data 
            WHERE symbol = ? AND frequency = ?
            """

            result = self.db_manager.fetchone(sql, (symbol, frequency))

            if result:
                total = result["total_records"]
                null_derived = result["null_change_percent"]

                return {
                    "symbol": symbol,
                    "total_records": total,
                    "null_change_percent": null_derived,
                    "null_prev_close": result["null_prev_close"],
                    "null_amplitude": result["null_amplitude"],
                    "enhanced_records": result["enhanced_records"],
                    "earliest_date": result["earliest_date"],
                    "latest_date": result["latest_date"],
                    "needs_backfill": null_derived > 0,
                    "backfill_ratio": null_derived / total if total > 0 else 0,
                }
            else:
                return {
                    "symbol": symbol,
                    "total_records": 0,
                    "needs_backfill": False,
                    "backfill_ratio": 0,
                }

        except Exception as e:
            logger.error(f"检查数据质量失败 {symbol}: {e}")
            return {
                "symbol": symbol,
                "total_records": 0,
                "needs_backfill": False,
                "error": str(e),
            }

    def smart_backfill_symbol(
        self, symbol: str, frequency: str = "1d"
    ) -> Dict[str, Any]:
        """
        智能补充单个股票的衍生字段数据

        Args:
            symbol: 股票代码
            frequency: 频率

        Returns:
            Dict[str, Any]: 补充结果
        """
        try:
            logger.info(f"开始智能补充股票数据: {symbol}")

            # 获取该股票的所有数据，按日期排序
            data_sql = """
            SELECT date, open, high, low, close, volume, amount
            FROM market_data 
            WHERE symbol = ? AND frequency = ?
            ORDER BY date
            """

            records = self.db_manager.fetchall(data_sql, (symbol, frequency))

            if not records:
                return {
                    "symbol": symbol,
                    "success": False,
                    "updated_count": 0,
                    "message": "无数据记录",
                }

            # 转换为DataFrame进行批量计算
            import numpy as np
            import pandas as pd

            df = pd.DataFrame([dict(record) for record in records])
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date")

            # 确保数值列为float类型
            numeric_columns = ["open", "high", "low", "close", "volume", "amount"]
            for col in numeric_columns:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

            # 计算前一日收盘价
            df["prev_close_new"] = df["close"].shift(1)

            # 计算涨跌额
            df["change_amount_new"] = df["close"] - df["prev_close_new"]

            # 计算涨跌幅（百分比）
            df["change_percent_new"] = np.where(
                df["prev_close_new"] > 0,
                (df["change_amount_new"] / df["prev_close_new"] * 100).round(4),
                0.0,
            )

            # 计算振幅
            df["amplitude_new"] = np.where(
                df["prev_close_new"] > 0,
                ((df["high"] - df["low"]) / df["prev_close_new"] * 100).round(4),
                0.0,
            )

            # 计算涨跌停价格
            df["high_limit_new"] = np.where(
                df["prev_close_new"] > 0, (df["prev_close_new"] * 1.1).round(2), None
            )
            df["low_limit_new"] = np.where(
                df["prev_close_new"] > 0, (df["prev_close_new"] * 0.9).round(2), None
            )

            # 判断涨停跌停
            df["is_limit_up_new"] = False
            df["is_limit_down_new"] = False

            valid_high_limit = df["high_limit_new"].notna()
            valid_low_limit = df["low_limit_new"].notna()

            if valid_high_limit.any():
                df.loc[valid_high_limit, "is_limit_up_new"] = (
                    df.loc[valid_high_limit, "close"]
                    >= df.loc[valid_high_limit, "high_limit_new"]
                )
            if valid_low_limit.any():
                df.loc[valid_low_limit, "is_limit_down_new"] = (
                    df.loc[valid_low_limit, "close"]
                    <= df.loc[valid_low_limit, "low_limit_new"]
                )

            # 第一行设为默认值
            if len(df) > 0:
                first_idx = df.index[0]
                df.loc[
                    first_idx,
                    [
                        "prev_close_new",
                        "change_amount_new",
                        "change_percent_new",
                        "amplitude_new",
                        "high_limit_new",
                        "low_limit_new",
                        "is_limit_up_new",
                        "is_limit_down_new",
                    ],
                ] = [None, 0.0, 0.0, 0.0, None, None, False, False]

            # 批量更新数据库
            updated_count = 0

            # 尝试使用批量写入优化
            if self.enable_batch_writer:
                try:
                    logger.debug(
                        f"使用 BatchWriter 批量更新 {symbol} 的 {len(df)} 条记录"
                    )

                    # 初始化 BatchWriter
                    batch_writer = BatchWriter(
                        self.db_manager,
                        batch_size=self.batch_write_size,
                        auto_flush=False,  # 手动控制刷新
                    )

                    # 准备批量更新的 SQL
                    # 注意: SQLite 不支持 UPDATE 的 executemany，需要使用 INSERT OR REPLACE
                    # 首先获取完整记录，然后用 INSERT OR REPLACE 更新
                    for _, row in df.iterrows():
                        try:
                            # 构建更新记录（只包含需要更新的字段）
                            update_record = {
                                "symbol": symbol,
                                "date": row["date"].strftime("%Y-%m-%d"),
                                "frequency": frequency,
                                "prev_close": (
                                    row["prev_close_new"]
                                    if pd.notna(row["prev_close_new"])
                                    else None
                                ),
                                "change_amount": (
                                    row["change_amount_new"]
                                    if pd.notna(row["change_amount_new"])
                                    else 0.0
                                ),
                                "change_percent": (
                                    row["change_percent_new"]
                                    if pd.notna(row["change_percent_new"])
                                    else 0.0
                                ),
                                "amplitude": (
                                    row["amplitude_new"]
                                    if pd.notna(row["amplitude_new"])
                                    else 0.0
                                ),
                                "high_limit": (
                                    row["high_limit_new"]
                                    if pd.notna(row["high_limit_new"])
                                    else None
                                ),
                                "low_limit": (
                                    row["low_limit_new"]
                                    if pd.notna(row["low_limit_new"])
                                    else None
                                ),
                                "is_limit_up": bool(row["is_limit_up_new"]),
                                "is_limit_down": bool(row["is_limit_down_new"]),
                            }

                            # 使用专用的 UPDATE SQL 执行批量更新
                            batch_writer.add_record(
                                "_update_market_data", update_record
                            )

                        except Exception as e:
                            logger.warning(
                                f"准备批量更新记录失败 {symbol} {row['date']}: {e}"
                            )

                    # 手动刷新：使用自定义 UPDATE SQL
                    if batch_writer.get_buffer_size("_update_market_data") > 0:
                        update_sql = """
                        UPDATE market_data
                        SET prev_close = ?, change_amount = ?, change_percent = ?, amplitude = ?,
                            high_limit = ?, low_limit = ?, is_limit_up = ?, is_limit_down = ?,
                            source = CASE WHEN source LIKE '%enhanced' THEN source ELSE 'smart_backfilled_enhanced' END,
                            quality_score = 100
                        WHERE symbol = ? AND date = ? AND frequency = ?
                        """

                        records = batch_writer._buffer["_update_market_data"]
                        params_list = [
                            (
                                rec["prev_close"],
                                rec["change_amount"],
                                rec["change_percent"],
                                rec["amplitude"],
                                rec["high_limit"],
                                rec["low_limit"],
                                rec["is_limit_up"],
                                rec["is_limit_down"],
                                rec["symbol"],
                                rec["date"],
                                rec["frequency"],
                            )
                            for rec in records
                        ]

                        # 使用 execute_batch 执行批量 UPDATE
                        updated_count = batch_writer.execute_batch(
                            update_sql, params_list, use_transaction=True
                        )

                        logger.info(
                            f"BatchWriter 批量更新完成 {symbol}: {updated_count} 条记录"
                        )

                        # 获取批量写入统计
                        batch_stats = batch_writer.get_stats()
                        logger.debug(
                            f"批量写入统计: 总记录={batch_stats['total_records']}, "
                            f"批次数={batch_stats['total_batches']}, "
                            f"平均批次大小={batch_stats['avg_batch_size']:.1f}, "
                            f"平均刷新时间={batch_stats['avg_flush_time']:.2f}ms"
                        )

                except Exception as batch_error:
                    logger.warning(
                        f"BatchWriter 批量更新失败 {symbol}: {batch_error}, 降级到逐条更新"
                    )
                    # 降级到逐条更新
                    self._fallback_update_records(df, symbol, frequency, updated_count)
                    updated_count = self._count_updated_records(df, symbol, frequency)

            else:
                # 批量写入未启用，使用逐条更新
                logger.debug(f"BatchWriter 未启用，使用逐条更新 {symbol}")
                updated_count = self._fallback_update_records(
                    df, symbol, frequency, updated_count
                )

            logger.info(f"智能补充完成 {symbol}: 更新 {updated_count} 条记录")

            return {
                "symbol": symbol,
                "success": True,
                "updated_count": updated_count,
                "total_records": len(df),
                "message": f"成功更新 {updated_count} 条记录",
            }

        except Exception as e:
            logger.error(f"智能补充失败 {symbol}: {e}")
            return {
                "symbol": symbol,
                "success": False,
                "updated_count": 0,
                "error": str(e),
            }

    def _fallback_update_records(
        self, df, symbol: str, frequency: str, initial_count: int = 0
    ) -> int:
        """
        降级逐条更新记录

        Args:
            df: DataFrame 包含更新数据
            symbol: 股票代码
            frequency: 频率
            initial_count: 初始计数

        Returns:
            int: 更新的记录数
        """
        updated_count = initial_count
        update_sql = """
        UPDATE market_data
        SET prev_close = ?, change_amount = ?, change_percent = ?, amplitude = ?,
            high_limit = ?, low_limit = ?, is_limit_up = ?, is_limit_down = ?,
            source = CASE WHEN source LIKE '%enhanced' THEN source ELSE 'smart_backfilled_enhanced' END,
            quality_score = 100
        WHERE symbol = ? AND date = ? AND frequency = ?
        """

        import pandas as pd

        for _, row in df.iterrows():
            try:
                params = (
                    row["prev_close_new"] if pd.notna(row["prev_close_new"]) else None,
                    (
                        row["change_amount_new"]
                        if pd.notna(row["change_amount_new"])
                        else 0.0
                    ),
                    (
                        row["change_percent_new"]
                        if pd.notna(row["change_percent_new"])
                        else 0.0
                    ),
                    row["amplitude_new"] if pd.notna(row["amplitude_new"]) else 0.0,
                    row["high_limit_new"] if pd.notna(row["high_limit_new"]) else None,
                    row["low_limit_new"] if pd.notna(row["low_limit_new"]) else None,
                    bool(row["is_limit_up_new"]),
                    bool(row["is_limit_down_new"]),
                    symbol,
                    row["date"].strftime("%Y-%m-%d"),
                    frequency,
                )

                self.db_manager.execute(update_sql, params)
                updated_count += 1

            except Exception as e:
                logger.warning(f"更新记录失败 {symbol} {row['date']}: {e}")

        return updated_count

    def _count_updated_records(self, df, symbol: str, frequency: str) -> int:
        """
        统计实际更新的记录数

        Args:
            df: DataFrame
            symbol: 股票代码
            frequency: 频率

        Returns:
            int: 更新的记录数
        """
        try:
            # 查询更新后的记录数（source包含'enhanced'）
            sql = """
            SELECT COUNT(*) as count
            FROM market_data
            WHERE symbol = ? AND frequency = ?
              AND source LIKE '%enhanced'
            """
            result = self.db_manager.fetchone(sql, (symbol, frequency))
            return result["count"] if result else len(df)
        except Exception:
            return len(df)

    def sync_symbol_range(
        self, symbol: str, start_date: date, end_date: date, frequency: str = "1d"
    ) -> Dict[str, Any]:
        """
        同步单个股票的日期范围数据

        Args:
            symbol: 股票代码
            start_date: 开始日期
            end_date: 结束日期
            frequency: 频率

        Returns:
            Dict[str, Any]: 同步结果
        """
        try:
            logger.info(
                f"同步股票范围数据: {symbol} {start_date} 到 {end_date} {frequency}"
            )

            result = {
                "symbol": symbol,
                "start_date": str(start_date),
                "end_date": str(end_date),
                "frequency": frequency,
                "success_count": 0,
                "error_count": 0,
                "sync_dates": [],
            }

            # 批量同步整个日期范围
            try:
                # 使用数据处理引擎批量处理日期范围数据
                process_result = self.processing_engine.process_stock_data(
                    symbol, start_date, end_date, frequency, force_update=True
                )

                # 统一数据格式处理 - 避免多次拆包
                actual_result = extract_data_safely(process_result)

                # 统计结果
                result["success_count"] = len(actual_result.get("processed_dates", []))
                result["error_count"] = len(actual_result.get("failed_dates", []))
                result["sync_dates"] = actual_result.get("processed_dates", [])

                logger.debug(
                    f"批量处理结果: 成功={result['success_count']}, 失败={result['error_count']}"
                )

            except Exception as e:
                logger.error(f"批量同步失败 {symbol} {start_date}-{end_date}: {e}")
                result["error_count"] = 1

            logger.info(
                f"股票范围同步完成: {symbol}, 成功={result['success_count']}, "
                f"错误={result['error_count']}"
            )

            # 更新缓存：如果同步成功，更新最后数据日期缓存
            if result["success_count"] > 0 and self.enable_cache and self.cache_manager:
                try:
                    self.cache_manager.set_last_data_date(symbol, frequency, end_date)
                    logger.debug(f"已更新缓存: {symbol} 最后数据日期={end_date}")
                except Exception as cache_error:
                    logger.warning(f"更新缓存失败: {cache_error}")

            return result

        except Exception as e:
            logger.error(f"股票范围同步失败 {symbol}: {e}")
            raise

    def get_last_data_date(self, symbol: str, frequency: str = "1d") -> Optional[date]:
        """
        获取股票的最后数据日期

        Args:
            symbol: 股票代码
            frequency: 频率

        Returns:
            Optional[date]: 最后数据日期，如果没有数据则返回None
        """
        try:
            # 优先使用缓存
            if self.enable_cache and self.cache_manager:
                cached_date = self.cache_manager.get_last_data_date(symbol, frequency)

                # 处理 unified_error_handler 包装的返回值
                if isinstance(cached_date, dict) and "data" in cached_date:
                    cached_date = cached_date["data"]

                if cached_date is not None:
                    return cached_date

            # 缓存未命中，查询数据库
            sql = """
            SELECT MAX(date) as last_date
            FROM market_data
            WHERE symbol = ? AND frequency = ?
            """

            result = self.db_manager.fetchone(sql, (symbol, frequency))

            if result and result["last_date"]:
                last_date = datetime.strptime(result["last_date"], "%Y-%m-%d").date()

                # 更新缓存
                if self.enable_cache and self.cache_manager:
                    self.cache_manager.set_last_data_date(symbol, frequency, last_date)

                return last_date
            else:
                return None

        except Exception as e:
            logger.error(f"获取最后数据日期失败 {symbol}: {e}")
            return None

    def calculate_sync_range(
        self, symbol: str, target_date: date, frequency: str = "1d"
    ) -> Tuple[Optional[date], date]:
        """
        计算增量同步的日期范围

        Args:
            symbol: 股票代码
            target_date: 目标日期
            frequency: 频率

        Returns:
            Tuple[Optional[date], date]: (开始日期, 结束日期)
        """
        try:
            # 获取最后数据日期
            last_date = self.get_last_data_date(symbol, frequency)

            if last_date is None:
                # 没有历史数据，从配置的默认开始日期同步
                default_start = self.config.get("sync.default_start_date", "2020-01-01")
                start_date = datetime.strptime(default_start, "%Y-%m-%d").date()

                # 限制最大同步天数
                max_start = target_date - timedelta(days=self.max_sync_days)
                if start_date < max_start:
                    start_date = max_start

                logger.info(f"首次同步 {symbol}: {start_date} 到 {target_date}")
                return start_date, target_date
            else:
                # 有历史数据，从最后日期的下一天开始
                start_date = last_date + timedelta(days=1)

                # 检查是否尝试同步未来日期
                today = datetime.now().date()
                if target_date > today:
                    target_date = today
                    logger.debug(f"目标日期调整为今天: {target_date}")

                if start_date > target_date:
                    # 已经是最新数据
                    logger.debug(f"数据已是最新 {symbol}: 最后日期={last_date}")
                    return None, target_date

                logger.debug(f"增量同步 {symbol}: {start_date} 到 {target_date}")
                return start_date, target_date

        except Exception as e:
            logger.error(f"计算同步范围失败 {symbol}: {e}")
            return None, target_date

    def _sync_frequency_data(
        self, symbols: List[str], target_date: date, frequency: str, progress_bar=None
    ) -> Dict[str, Any]:
        """同步特定频率的数据"""
        logger.info(f"同步频率数据: {frequency}, 股票数量: {len(symbols)}")

        result = {
            "frequency": frequency,
            "total_symbols": len(symbols),
            "success_count": 0,
            "error_count": 0,
            "skipped_count": 0,
            "sync_ranges": {},
        }

        # 使用流水线模式：下载串行，处理并发
        result.update(
            self._sync_pipeline(symbols, target_date, frequency, progress_bar)
        )

        # 更新总统计
        self.sync_stats["success_count"] += result["success_count"]
        self.sync_stats["error_count"] += result["error_count"]
        self.sync_stats["skipped_count"] += result["skipped_count"]

        return result

    def _sync_sequential(
        self, symbols: List[str], target_date: date, frequency: str, progress_bar=None
    ) -> Dict[str, Any]:
        """串行同步"""
        result = {
            "success_count": 0,
            "error_count": 0,
            "skipped_count": 0,
            "sync_ranges": {},
        }

        for symbol in symbols:
            try:
                # 计算同步范围
                start_date, end_date = self.calculate_sync_range(
                    symbol, target_date, frequency
                )

                if start_date is None:
                    result["skipped_count"] += 1
                    # 更新进度条
                    if progress_bar:
                        progress_bar.update(1)
                    continue

                # 同步数据
                sync_result = self.sync_symbol_range(
                    symbol, start_date, end_date, frequency
                )

                if sync_result["success_count"] > 0:
                    result["success_count"] += 1
                    result["sync_ranges"][symbol] = {
                        "start_date": str(start_date),
                        "end_date": str(end_date),
                        "sync_count": sync_result["success_count"],
                    }
                else:
                    result["error_count"] += 1

                # 更新进度条
                if progress_bar:
                    progress_bar.update(1)

            except Exception as e:
                logger.error(f"串行同步失败 {symbol}: {e}")
                result["error_count"] += 1
                self.sync_stats["errors"].append(
                    {"symbol": symbol, "frequency": frequency, "error": str(e)}
                )
                # 更新进度条
                if progress_bar:
                    progress_bar.update(1)

        return result

    def _sync_parallel(
        self, symbols: List[str], target_date: date, frequency: str, progress_bar=None
    ) -> Dict[str, Any]:
        """并行同步"""
        result = {
            "success_count": 0,
            "error_count": 0,
            "skipped_count": 0,
            "sync_ranges": {},
        }

        # 分批处理
        symbol_batches = [
            symbols[i : i + self.batch_size]
            for i in range(0, len(symbols), self.batch_size)
        ]

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # 提交任务
            future_to_batch = {
                executor.submit(
                    self._sync_symbol_batch, batch, target_date, frequency
                ): batch
                for batch in symbol_batches
            }

            # 收集结果
            for future in as_completed(future_to_batch):
                batch = future_to_batch[future]
                try:
                    batch_result = future.result()
                    result["success_count"] += batch_result["success_count"]
                    result["error_count"] += batch_result["error_count"]
                    result["skipped_count"] += batch_result["skipped_count"]
                    result["sync_ranges"].update(batch_result["sync_ranges"])

                except Exception as e:
                    logger.error(f"并行同步批次失败: {batch}, 错误: {e}")
                    result["error_count"] += len(batch)

        return result

    def _sync_symbol_batch(
        self, symbols: List[str], target_date: date, frequency: str
    ) -> Dict[str, Any]:
        """同步股票批次"""
        batch_result = {
            "success_count": 0,
            "error_count": 0,
            "skipped_count": 0,
            "sync_ranges": {},
        }

        for symbol in symbols:
            try:
                # 计算同步范围
                start_date, end_date = self.calculate_sync_range(
                    symbol, target_date, frequency
                )

                if start_date is None:
                    batch_result["skipped_count"] += 1
                    continue

                # 同步数据
                sync_result = self.sync_symbol_range(
                    symbol, start_date, end_date, frequency
                )

                if sync_result["success_count"] > 0:
                    batch_result["success_count"] += 1
                    batch_result["sync_ranges"][symbol] = {
                        "start_date": str(start_date),
                        "end_date": str(end_date),
                        "sync_count": sync_result["success_count"],
                    }
                else:
                    batch_result["error_count"] += 1

            except Exception as e:
                logger.error(f"批次同步失败 {symbol}: {e}")
                batch_result["error_count"] += 1

        return batch_result

    def _get_active_symbols(self) -> List[str]:
        """获取活跃股票列表"""
        try:
            sql = """
            SELECT symbol FROM stocks
            WHERE status = 'active'
            ORDER BY symbol
            """
            results = self.db_manager.fetchall(sql)

            if results:
                return [row["symbol"] for row in results]
            else:
                logger.warning("数据库中无活跃股票")
                return []

        except Exception as e:
            logger.error(f"获取活跃股票列表失败: {e}")
            return []

    def _is_trading_day(self, target_date: date) -> bool:
        """检查是否为交易日"""
        try:
            # 优先使用缓存
            if self.enable_cache and self.cache_manager:
                cached_result = self.cache_manager.is_trading_day(
                    target_date, market="CN"
                )

                # 处理 unified_error_handler 包装的返回值
                if isinstance(cached_result, dict) and "data" in cached_result:
                    cached_result = cached_result["data"]

                if cached_result is not None:
                    return cached_result

            # 缓存未命中，查询数据库
            sql = """
            SELECT is_trading FROM trading_calendar
            WHERE date = ? AND market = 'CN'
            """
            result = self.db_manager.fetchone(sql, (str(target_date),))

            if result:
                return bool(result["is_trading"])
            else:
                # 不再使用简化fallback，必须有正确的交易日历数据
                raise RuntimeError(f"交易日历数据缺失，日期: {target_date}")

        except Exception as e:
            logger.error(f"检查交易日失败: {e}")
            # 不再使用简化fallback，必须有正确的交易日历数据
            raise RuntimeError(f"无法获取交易日历数据: {e}")

    def _reset_stats(self):
        """重置同步统计"""
        self.sync_stats = {
            "total_symbols": 0,
            "success_count": 0,
            "error_count": 0,
            "skipped_count": 0,
            "sync_date_ranges": {},
            "errors": [],
        }

    def _update_sync_status(self, target_date: date, stats: Dict[str, Any]):
        """更新同步状态"""
        try:
            # 确定同步状态
            effective_success = stats["success_count"] > 0
            effective_error = stats["error_count"] > 0

            if effective_success and not effective_error:
                sync_status = "completed"
            elif effective_success and effective_error:
                sync_status = "partial_success"
            else:
                sync_status = "failed"

            # 构建错误消息
            error_msg = f"成功={stats['success_count']}, 错误={stats['error_count']}, 跳过={stats['skipped_count']}"

            # 使用成功数量作为记录数（简化统计）
            actual_records_count = stats["success_count"]

            # 插入汇总记录
            sql = """
            INSERT OR REPLACE INTO sync_status
            (symbol, frequency, last_sync_date, last_data_date, status,
             error_message, total_records, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """

            self.db_manager.execute(
                sql,
                (
                    "ALL_SYMBOLS",  # symbol
                    "1d",  # frequency
                    str(target_date),  # last_sync_date
                    str(target_date),  # last_data_date
                    sync_status,  # status
                    error_msg,  # error_message
                    actual_records_count,  # total_records
                    datetime.now().isoformat(),  # updated_at
                ),
            )

            logger.info(
                f"同步状态已更新: {sync_status}, 记录数: {actual_records_count}"
            )

        except Exception as e:
            logger.error(f"更新同步状态失败: {e}")

    def get_sync_stats(self) -> Dict[str, Any]:
        """获取同步统计信息"""
        return self.sync_stats.copy()

    def _sync_pipeline(
        self, symbols: List[str], target_date: date, frequency: str, progress_bar=None
    ) -> Dict[str, Any]:
        """
        流水线同步：下载串行，处理并发

        Args:
            symbols: 股票代码列表
            target_date: 目标日期
            frequency: 频率
            progress_bar: 进度条

        Returns:
            同步结果
        """
        result = {
            "success_count": 0,
            "error_count": 0,
            "skipped_count": 0,
            "sync_ranges": {},
        }

        # 计算需要同步的股票和日期范围
        sync_tasks = []
        for symbol in symbols:
            try:
                start_date, end_date = self.calculate_sync_range(
                    symbol, target_date, frequency
                )

                if start_date is None:
                    result["skipped_count"] += 1
                    if progress_bar:
                        progress_bar.update(1)
                    continue

                sync_tasks.append((symbol, start_date, end_date))

            except Exception as e:
                logger.error(f"计算同步范围失败 {symbol}: {e}")
                result["error_count"] += 1
                if progress_bar:
                    progress_bar.update(1)

        if not sync_tasks:
            return result

        # 使用数据处理引擎的流水线模式
        # 将同步任务转换为处理引擎需要的格式
        task_symbols = [task[0] for task in sync_tasks]

        # 为了简化，这里使用最早的开始日期和目标日期
        min_start_date = min(task[1] for task in sync_tasks)

        try:
            # 调用数据处理引擎的流水线处理
            pipeline_result = self.processing_engine.process_symbols_batch_pipeline(
                symbols=task_symbols,
                start_date=min_start_date,
                end_date=target_date,
                batch_size=5,  # 减少批次大小到5只股票
                max_workers=2,  # 减少处理线程到2个
                progress_bar=progress_bar,  # 传递进度条
            )

            # 转换结果格式 - 处理嵌套的统一错误处理返回格式
            if isinstance(pipeline_result, dict) and pipeline_result.get(
                "success", True
            ):
                data = pipeline_result.get("data", pipeline_result)
                result["success_count"] = data.get("success_count", 0)
                result["error_count"] += data.get("error_count", 0)
                processed_symbols = data.get("processed_symbols", [])
            else:
                result["error_count"] += len(task_symbols)
                processed_symbols = []

            # 为成功的股票创建同步范围记录
            for symbol in processed_symbols:
                # 找到对应的任务
                for task_symbol, start_date, end_date in sync_tasks:
                    if task_symbol == symbol:
                        result["sync_ranges"][symbol] = {
                            "start_date": str(start_date),
                            "end_date": str(end_date),
                            "sync_count": 1,  # 简化处理
                        }
                        break

            # 进度条已经在流水线处理中更新了，这里不需要重复更新

        except Exception as e:
            logger.error(f"流水线同步失败: {e}")
            result["error_count"] += len(sync_tasks)
            # 如果整个流水线失败，更新所有剩余进度
            if progress_bar:
                progress_bar.update(len(task_symbols))

        return result
