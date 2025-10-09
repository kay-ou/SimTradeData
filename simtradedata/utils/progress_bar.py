"""
进度条管理器

为全量同步的各个阶段提供清晰的进度显示。
"""

import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Iterator, Optional

logger = logging.getLogger(__name__)


class SyncProgressBar:
    """同步进度条管理器"""

    def __init__(self, disable_logs: bool = True):
        """
        初始化进度条管理器

        Args:
            disable_logs: 是否禁用详细日志输出
        """
        self.disable_logs = disable_logs
        self.current_phase = None
        self.phase_progress_bars = {}
        self.start_time = None
        self._progress_line_active = False

        # 如果禁用日志，设置日志级别为WARNING
        if disable_logs:
            # 设置特定模块的日志级别
            modules_to_quiet = [
                "simtradedata.preprocessor.engine",
                "simtradedata.sync.incremental",
                "simtradedata.data_sources.manager",
                "simtradedata.data_sources.baostock_adapter",
                "simtradedata.data_sources.mootdx_adapter",
                "simtradedata.core.logging_mixin",
                "simtradedata.config.manager",
                "simtradedata.database.manager",
                "simtradedata.data_sources.base",
                "simtradedata.sync.validator",
                "urllib3.connectionpool",
            ]

            for module_name in modules_to_quiet:
                module_logger = logging.getLogger(module_name)
                module_logger.setLevel(logging.WARNING)

    @contextmanager
    def phase_progress(
        self,
        phase_name: str,
        total: int,
        desc: Optional[str] = None,
        unit: str = "item",
        phase_info: str = "",
    ) -> Iterator[Optional["SimpleProgress"]]:
        """
        创建阶段进度条

        Args:
            phase_name: 阶段名称
            total: 总数量
            desc: 描述
            unit: 单位
            phase_info: 阶段信息（如 "阶段1/4"）

        Yields:
            SimpleProgress进度条对象
        """
        if desc is None:
            desc = phase_name

        self.current_phase = phase_name

        # 创建简单的进度显示器（传入阶段信息）
        progress = SimpleProgress(total, desc, phase_info)
        # 立即设置进度管理器引用
        progress.progress_manager = self
        self.phase_progress_bars[phase_name] = progress

        interrupted = False
        try:
            yield progress
        except KeyboardInterrupt:
            # 检测到中断，快速清理并重新抛出
            interrupted = True
            print(f"\r{' ' * 100}\r", end="", flush=True)  # 快速清除进度行
            raise  # 立即重新抛出，不做任何延迟操作
        finally:
            # 如果被中断，跳过所有清理操作
            if not interrupted:
                # 关闭进度条
                try:
                    progress.close()
                except:
                    pass  # 忽略close时的任何错误
            # 清理引用（快速操作）
            if phase_name in self.phase_progress_bars:
                del self.phase_progress_bars[phase_name]

    def update_phase_description(self, desc: str):
        """更新当前阶段的描述"""
        if self.current_phase and self.current_phase in self.phase_progress_bars:
            pbar = self.phase_progress_bars[self.current_phase]
            if hasattr(pbar, "set_description"):
                pbar.set_description(f"🔄 {desc}")

    def log_phase_start(self, phase_name: str, desc: Optional[str] = None):
        """记录阶段开始"""
        self._clear_progress_line()
        if not self.disable_logs:
            logger.info(f"🚀 {phase_name}: {desc or '开始'}")

    def log_phase_complete(
        self, phase_name: str, stats: Optional[Dict[str, Any]] = None
    ):
        """记录阶段完成"""
        self._clear_progress_line()
        if stats:
            stats_str = ", ".join([f"{k}={v}" for k, v in stats.items()])
            logger.info(f"✅ {phase_name}完成: {stats_str}")
        else:
            logger.info(f"✅ {phase_name}完成")

    def log_error(self, message: str):
        """记录错误（总是显示）"""
        self._clear_progress_line()
        logger.error(f"❌ {message}")

    def log_warning(self, message: str):
        """记录警告（总是显示）"""
        self._clear_progress_line()
        logger.warning(f"⚠️  {message}")

    def _clear_progress_line(self):
        """清除当前进度行"""
        if self._progress_line_active:
            print("\r" + " " * 100 + "\r", end="", flush=True)
            self._progress_line_active = False


class SimpleProgress:
    """进度显示器"""

    def __init__(self, total: int, desc: str = "Processing", phase_info: str = ""):
        self.total = total
        self.desc = desc
        self.phase_info = phase_info  # 新增：阶段信息
        self.current = 0
        self._last_reported = -1
        self.start_time = datetime.now()

        # 引用全局进度条管理器（延迟引用）
        self.progress_manager = None

    def update(self, n: int = 1):
        """更新进度"""
        self.current += n

        # 延迟获取进度管理器引用
        if self.progress_manager is None:
            self.progress_manager = globals().get("sync_progress")

        # 每10%或每5个项目报告一次进度
        percentage = (self.current / self.total) * 100
        report_threshold = int(percentage // 10) * 10

        should_report = (
            (report_threshold > self._last_reported and report_threshold % 10 == 0)
            or (self.current % 5 == 0 and self.current <= 10)  # 前10个项目每5个报告一次
            or (self.current % 50 == 0 and self.current > 10)  # 之后每50个报告一次
            or (self.current == self.total)  # 总是报告完成
        )

        if should_report:
            elapsed = datetime.now() - self.start_time
            if self.current > 0 and elapsed.total_seconds() > 0:
                rate = self.current / elapsed.total_seconds()
                remaining_items = self.total - self.current
                remaining_time = remaining_items / rate if rate > 0 else 0
                if remaining_time < 60:
                    remaining_str = f"{remaining_time:.0f}s"
                elif remaining_time < 3600:
                    remaining_str = f"{remaining_time/60:.1f}m"
                else:
                    remaining_str = f"{remaining_time/3600:.1f}h"
            else:
                remaining_str = "计算中"

            # 创建简洁的进度条
            bar_length = 30
            filled_length = int(bar_length * percentage / 100)
            bar = "█" * filled_length + "░" * (bar_length - filled_length)

            # 构建进度信息（包含阶段信息）
            phase_prefix = f"{self.phase_info} " if self.phase_info else ""
            progress_line = f"{phase_prefix}{self.desc}: [{bar}] {percentage:5.1f}% ({self.current}/{self.total}) 剩余:{remaining_str}"

            # 清除之前的进度行并输出新的进度
            print(f"\r{progress_line:<120}", end="", flush=True)

            # 标记进度行处于活跃状态
            if self.progress_manager:
                self.progress_manager._progress_line_active = True

            self._last_reported = report_threshold

    def set_description(self, desc: str):
        """设置描述"""
        self.desc = desc

    def close(self):
        """关闭进度条"""
        elapsed = datetime.now() - self.start_time
        # 清除当前进度行
        print(f"\r{' ' * 120}\r", end="", flush=True)
        # 输出完成信息到新行（包含阶段信息）
        phase_prefix = f"{self.phase_info} " if self.phase_info else ""
        print(
            f"✅ {phase_prefix}{self.desc}: 完成 {self.current}/{self.total} [耗时: {elapsed.total_seconds():.1f}s]"
        )

        # 重置进度行状态
        if self.progress_manager:
            self.progress_manager._progress_line_active = False


# 全局进度条管理器实例
sync_progress = SyncProgressBar()


@contextmanager
def create_phase_progress(
    phase_name: str,
    total: int,
    desc: Optional[str] = None,
    unit: str = "item",
    phase_info: str = "",
):
    """创建阶段进度条的便捷函数"""
    with sync_progress.phase_progress(
        phase_name, total, desc, unit, phase_info
    ) as pbar:
        yield pbar


def log_phase_start(phase_name: str, desc: Optional[str] = None):
    """记录阶段开始"""
    sync_progress.log_phase_start(phase_name, desc)


def log_phase_complete(phase_name: str, stats: Optional[Dict[str, Any]] = None):
    """记录阶段完成"""
    sync_progress.log_phase_complete(phase_name, stats)


def update_phase_description(desc: str):
    """更新当前阶段描述"""
    sync_progress.update_phase_description(desc)


def log_error(message: str):
    """记录错误"""
    sync_progress.log_error(message)


def log_warning(message: str):
    """记录警告"""
    sync_progress.log_warning(message)
