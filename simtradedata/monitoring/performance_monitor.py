"""
性能监控器

实现性能监控器，记录同步各阶段性能指标，识别瓶颈。

功能：
- 阶段计时和统计
- 自定义指标记录
- 性能报告生成（JSON/文本）
- 瓶颈识别（耗时>50%）
- 可选的资源监控（CPU、内存）
"""

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class PhaseStats:
    """阶段统计信息"""

    name: str  # 阶段名称
    start_time: float  # 开始时间（时间戳）
    end_time: Optional[float] = None  # 结束时间（时间戳）
    duration: Optional[float] = None  # 耗时（秒）
    records_count: int = 0  # 记录数
    throughput: float = 0.0  # 吞吐量（记录/秒）
    cpu_usage_start: Optional[float] = None  # CPU使用率（开始）
    cpu_usage_end: Optional[float] = None  # CPU使用率（结束）
    memory_usage_start: Optional[int] = None  # 内存使用（开始，字节）
    memory_usage_end: Optional[int] = None  # 内存使用（结束，字节）
    custom_metrics: Dict[str, Any] = field(default_factory=dict)  # 自定义指标

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "name": self.name,
            "start_time": (
                datetime.fromtimestamp(self.start_time).isoformat()
                if self.start_time
                else None
            ),
            "end_time": (
                datetime.fromtimestamp(self.end_time).isoformat()
                if self.end_time
                else None
            ),
            "duration": round(self.duration, 4) if self.duration else None,
            "records_count": self.records_count,
            "throughput": round(self.throughput, 2),
            "cpu_usage_start": self.cpu_usage_start,
            "cpu_usage_end": self.cpu_usage_end,
            "memory_usage_start": self.memory_usage_start,
            "memory_usage_end": self.memory_usage_end,
            "custom_metrics": self.custom_metrics,
        }


@dataclass
class PerformanceReport:
    """性能报告"""

    total_duration: float  # 总耗时（秒）
    total_records: int  # 总记录数
    overall_throughput: float  # 总体吞吐量（记录/秒）
    phases: List[PhaseStats]  # 各阶段统计
    bottlenecks: List[str]  # 瓶颈阶段列表
    custom_metrics: Dict[str, Any] = field(default_factory=dict)  # 全局自定义指标
    generated_at: str = field(
        default_factory=lambda: datetime.now().isoformat()
    )  # 生成时间

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "total_duration": round(self.total_duration, 4),
            "total_records": self.total_records,
            "overall_throughput": round(self.overall_throughput, 2),
            "phases": [phase.to_dict() for phase in self.phases],
            "bottlenecks": self.bottlenecks,
            "custom_metrics": self.custom_metrics,
            "generated_at": self.generated_at,
        }

    def to_json(self, indent: int = 2) -> str:
        """转换为JSON格式"""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def to_text(self) -> str:
        """转换为文本格式"""
        lines = []
        lines.append("=" * 80)
        lines.append("性能监控报告")
        lines.append("=" * 80)
        lines.append(f"生成时间: {self.generated_at}")
        lines.append(f"总耗时: {self.total_duration:.4f}秒")
        lines.append(f"总记录数: {self.total_records}")
        lines.append(f"总体吞吐量: {self.overall_throughput:.2f} 记录/秒")
        lines.append("")

        lines.append("阶段统计:")
        lines.append("-" * 80)
        for phase in self.phases:
            lines.append(f"阶段: {phase.name}")
            lines.append(f"  耗时: {phase.duration:.4f}秒")
            lines.append(f"  记录数: {phase.records_count}")
            lines.append(f"  吞吐量: {phase.throughput:.2f} 记录/秒")
            if self.total_duration > 0:
                percentage = (phase.duration / self.total_duration) * 100
                lines.append(f"  占比: {percentage:.2f}%")
            if phase.cpu_usage_start is not None and phase.cpu_usage_end is not None:
                lines.append(
                    f"  CPU使用率: {phase.cpu_usage_start:.2f}% -> {phase.cpu_usage_end:.2f}%"
                )
            if (
                phase.memory_usage_start is not None
                and phase.memory_usage_end is not None
            ):
                memory_mb_start = phase.memory_usage_start / (1024 * 1024)
                memory_mb_end = phase.memory_usage_end / (1024 * 1024)
                lines.append(
                    f"  内存使用: {memory_mb_start:.2f}MB -> {memory_mb_end:.2f}MB"
                )
            if phase.custom_metrics:
                lines.append(f"  自定义指标: {phase.custom_metrics}")
            lines.append("")

        if self.bottlenecks:
            lines.append("识别的瓶颈:")
            lines.append("-" * 80)
            for bottleneck in self.bottlenecks:
                lines.append(f"  ⚠️  {bottleneck}")
            lines.append("")

        if self.custom_metrics:
            lines.append("全局自定义指标:")
            lines.append("-" * 80)
            for key, value in self.custom_metrics.items():
                lines.append(f"  {key}: {value}")
            lines.append("")

        lines.append("=" * 80)
        return "\n".join(lines)


class PerformanceMonitor:
    """性能监控器"""

    def __init__(self, enable_resource_monitoring: bool = False):
        """
        初始化性能监控器

        Args:
            enable_resource_monitoring: 是否启用资源监控（CPU、内存）
        """
        self.enable_resource_monitoring = enable_resource_monitoring
        self.phases: Dict[str, PhaseStats] = {}  # 阶段统计
        self.phase_order: List[str] = []  # 阶段顺序
        self.custom_metrics: Dict[str, Any] = {}  # 全局自定义指标
        self.lock = threading.RLock()  # 线程锁

        # 尝试导入 psutil（可选）
        self.psutil = None
        if self.enable_resource_monitoring:
            try:
                import psutil

                self.psutil = psutil
                logger.info("psutil 已启用，将监控 CPU 和内存使用")
            except ImportError:
                logger.warning("psutil 未安装，资源监控将被禁用")
                self.enable_resource_monitoring = False

    def start_phase(self, name: str) -> bool:
        """
        开始计时阶段

        Args:
            name: 阶段名称

        Returns:
            bool: 是否成功
        """
        with self.lock:
            try:
                # 检查是否已存在
                if name in self.phases:
                    logger.warning(f"阶段 {name} 已存在，将覆盖")

                # 记录开始时间
                start_time = time.perf_counter()

                # 记录资源使用（可选）
                cpu_usage = None
                memory_usage = None
                if self.enable_resource_monitoring and self.psutil:
                    cpu_usage = self.psutil.cpu_percent(interval=0.1)
                    memory_usage = self.psutil.virtual_memory().used

                # 创建阶段统计
                self.phases[name] = PhaseStats(
                    name=name,
                    start_time=start_time,
                    cpu_usage_start=cpu_usage,
                    memory_usage_start=memory_usage,
                )

                # 记录顺序
                if name not in self.phase_order:
                    self.phase_order.append(name)

                logger.debug(f"阶段 {name} 开始计时")
                return True

            except Exception as e:
                logger.error(f"开始阶段 {name} 失败: {e}")
                return False

    def end_phase(self, name: str, records_count: int = 0) -> bool:
        """
        结束计时阶段

        Args:
            name: 阶段名称
            records_count: 记录数量

        Returns:
            bool: 是否成功
        """
        with self.lock:
            try:
                # 检查阶段是否存在
                if name not in self.phases:
                    logger.error(f"阶段 {name} 不存在，无法结束")
                    return False

                phase = self.phases[name]

                # 检查是否已结束
                if phase.end_time is not None:
                    logger.warning(f"阶段 {name} 已结束")
                    return False

                # 记录结束时间
                end_time = time.perf_counter()
                phase.end_time = end_time
                phase.duration = end_time - phase.start_time

                # 记录记录数和吞吐量
                phase.records_count = records_count
                if phase.duration > 0:
                    phase.throughput = records_count / phase.duration

                # 记录资源使用（可选）
                if self.enable_resource_monitoring and self.psutil:
                    phase.cpu_usage_end = self.psutil.cpu_percent(interval=0.1)
                    phase.memory_usage_end = self.psutil.virtual_memory().used

                logger.debug(
                    f"阶段 {name} 结束: 耗时={phase.duration:.4f}秒, 记录数={records_count}, 吞吐量={phase.throughput:.2f} 记录/秒"
                )
                return True

            except Exception as e:
                logger.error(f"结束阶段 {name} 失败: {e}")
                return False

    def record_metric(self, name: str, value: Any, phase: Optional[str] = None) -> bool:
        """
        记录自定义指标

        Args:
            name: 指标名称
            value: 指标值
            phase: 阶段名称（可选，为None时记录为全局指标）

        Returns:
            bool: 是否成功
        """
        with self.lock:
            try:
                if phase is None:
                    # 全局指标
                    self.custom_metrics[name] = value
                    logger.debug(f"记录全局指标: {name}={value}")
                else:
                    # 阶段指标
                    if phase not in self.phases:
                        logger.warning(f"阶段 {phase} 不存在，无法记录指标")
                        return False

                    self.phases[phase].custom_metrics[name] = value
                    logger.debug(f"记录阶段 {phase} 指标: {name}={value}")

                return True

            except Exception as e:
                logger.error(f"记录指标 {name} 失败: {e}")
                return False

    def get_phase_stats(self, name: str) -> Optional[PhaseStats]:
        """
        获取阶段统计

        Args:
            name: 阶段名称

        Returns:
            Optional[PhaseStats]: 阶段统计，不存在返回None
        """
        with self.lock:
            return self.phases.get(name)

    def generate_report(self) -> PerformanceReport:
        """
        生成性能报告

        Returns:
            PerformanceReport: 性能报告
        """
        with self.lock:
            # 计算总耗时和总记录数
            total_duration = 0.0
            total_records = 0
            phases_list = []

            for phase_name in self.phase_order:
                phase = self.phases.get(phase_name)
                if phase and phase.duration is not None:
                    total_duration += phase.duration
                    total_records += phase.records_count
                    phases_list.append(phase)

            # 计算总体吞吐量
            overall_throughput = (
                total_records / total_duration if total_duration > 0 else 0.0
            )

            # 识别瓶颈
            bottlenecks = self.identify_bottlenecks()

            # 生成报告
            report = PerformanceReport(
                total_duration=total_duration,
                total_records=total_records,
                overall_throughput=overall_throughput,
                phases=phases_list,
                bottlenecks=bottlenecks,
                custom_metrics=self.custom_metrics.copy(),
            )

            return report

    def identify_bottlenecks(self, threshold: float = 0.5) -> List[str]:
        """
        识别瓶颈阶段（耗时占比>threshold）

        Args:
            threshold: 阈值（默认0.5，即50%）

        Returns:
            List[str]: 瓶颈阶段列表
        """
        with self.lock:
            # 计算总耗时
            total_duration = sum(
                phase.duration
                for phase in self.phases.values()
                if phase.duration is not None
            )

            if total_duration == 0:
                return []

            # 识别耗时占比超过阈值的阶段
            bottlenecks = []
            for phase in self.phases.values():
                if phase.duration is not None:
                    percentage = phase.duration / total_duration
                    if percentage > threshold:
                        bottlenecks.append(
                            f"{phase.name} (耗时: {phase.duration:.4f}秒, 占比: {percentage*100:.2f}%)"
                        )

            return bottlenecks

    def reset(self):
        """重置监控器"""
        with self.lock:
            self.phases.clear()
            self.phase_order.clear()
            self.custom_metrics.clear()
            logger.debug("性能监控器已重置")

    def get_stats_summary(self) -> Dict[str, Any]:
        """
        获取统计摘要

        Returns:
            Dict: 统计摘要
        """
        with self.lock:
            return {
                "total_phases": len(self.phases),
                "completed_phases": sum(
                    1 for phase in self.phases.values() if phase.end_time is not None
                ),
                "custom_metrics_count": len(self.custom_metrics),
                "enable_resource_monitoring": self.enable_resource_monitoring,
            }
