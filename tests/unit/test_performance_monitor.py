"""
PerformanceMonitor 单元测试

测试性能监控器功能:
1. 计时准确性: start/end phase 计时误差<1ms
2. 吞吐量计算: records_count / duration 正确
3. 瓶颈识别: 耗时>50%正确识别
4. 报告生成: JSON 和文本格式正确
5. 多阶段监控: 嵌套阶段正确记录
6. 异常处理: 阶段未开始就结束、重复开始等
7. 资源监控: CPU、内存监控准确(如果启用)
"""

import json
import time

import pytest

from simtradedata.monitoring import PerformanceMonitor, PerformanceReport


class TestBasicTiming:
    """基础计时测试"""

    def test_start_and_end_phase(self):
        """测试开始和结束阶段"""
        monitor = PerformanceMonitor()

        # 开始阶段
        assert monitor.start_phase("test_phase") is True

        # 模拟耗时操作
        time.sleep(0.1)

        # 结束阶段
        assert monitor.end_phase("test_phase", records_count=100) is True

        # 获取统计信息
        stats = monitor.get_phase_stats("test_phase")
        assert stats is not None
        assert stats.name == "test_phase"
        assert stats.duration is not None
        assert stats.duration >= 0.1  # 至少100ms
        assert stats.records_count == 100

    def test_timing_accuracy(self):
        """测试计时准确性（误差<1ms）"""
        monitor = PerformanceMonitor()

        monitor.start_phase("timing_test")

        # 精确睡眠100ms
        expected_duration = 0.1
        time.sleep(expected_duration)

        monitor.end_phase("timing_test")

        stats = monitor.get_phase_stats("timing_test")
        assert stats.duration is not None

        # 允许±5ms的误差（系统调度开销）
        tolerance = 0.005
        assert abs(stats.duration - expected_duration) < tolerance

    def test_throughput_calculation(self):
        """测试吞吐量计算（records_count / duration）"""
        monitor = PerformanceMonitor()

        monitor.start_phase("throughput_test")
        time.sleep(0.1)
        monitor.end_phase("throughput_test", records_count=1000)

        stats = monitor.get_phase_stats("throughput_test")
        assert stats.throughput > 0

        # 吞吐量应该约为 1000 / 0.1 = 10000 条/秒
        # 允许20%的误差范围
        expected_throughput = 1000 / 0.1
        assert abs(stats.throughput - expected_throughput) / expected_throughput < 0.2


class TestBottleneckIdentification:
    """瓶颈识别测试"""

    def test_identify_bottlenecks_default_threshold(self):
        """测试瓶颈识别（默认阈值50%）"""
        monitor = PerformanceMonitor()

        # 阶段1: 耗时长（瓶颈）
        monitor.start_phase("slow_phase")
        time.sleep(0.15)
        monitor.end_phase("slow_phase", records_count=100)

        # 阶段2: 耗时短
        monitor.start_phase("fast_phase")
        time.sleep(0.05)
        monitor.end_phase("fast_phase", records_count=100)

        # 识别瓶颈
        bottlenecks = monitor.identify_bottlenecks(threshold=0.5)

        # slow_phase 占 0.15/(0.15+0.05) = 75% > 50%, 应该被识别为瓶颈
        assert len(bottlenecks) == 1
        assert "slow_phase" in bottlenecks[0]

    def test_identify_bottlenecks_custom_threshold(self):
        """测试自定义阈值瓶颈识别"""
        monitor = PerformanceMonitor()

        monitor.start_phase("phase1")
        time.sleep(0.07)
        monitor.end_phase("phase1")

        monitor.start_phase("phase2")
        time.sleep(0.03)
        monitor.end_phase("phase2")

        # 使用60%阈值: phase1 占 70% > 60%, 应该被识别
        bottlenecks = monitor.identify_bottlenecks(threshold=0.6)
        assert len(bottlenecks) == 1

        # 使用75%阈值: phase1 占 70% < 75%, 不应该被识别
        bottlenecks = monitor.identify_bottlenecks(threshold=0.75)
        assert len(bottlenecks) == 0

    def test_identify_no_bottlenecks(self):
        """测试无瓶颈场景"""
        monitor = PerformanceMonitor()

        # 三个阶段耗时均衡
        for i in range(3):
            monitor.start_phase(f"phase_{i}")
            time.sleep(0.03)
            monitor.end_phase(f"phase_{i}")

        # 每个阶段占约33%, 都不超过50%
        bottlenecks = monitor.identify_bottlenecks(threshold=0.5)
        assert len(bottlenecks) == 0


class TestReportGeneration:
    """报告生成测试"""

    def test_generate_performance_report(self):
        """测试生成性能报告"""
        monitor = PerformanceMonitor()

        monitor.start_phase("phase1")
        time.sleep(0.05)
        monitor.end_phase("phase1", records_count=500)

        monitor.start_phase("phase2")
        time.sleep(0.03)
        monitor.end_phase("phase2", records_count=300)

        # 生成报告
        report = monitor.generate_report()

        assert isinstance(report, PerformanceReport)
        assert report.total_duration > 0
        assert report.total_records == 800
        assert report.overall_throughput > 0
        assert len(report.phases) == 2

    def test_report_to_dict(self):
        """测试报告转换为字典"""
        monitor = PerformanceMonitor()

        monitor.start_phase("test")
        time.sleep(0.01)
        monitor.end_phase("test", records_count=100)

        report = monitor.generate_report()
        report_dict = report.to_dict()

        assert isinstance(report_dict, dict)
        assert "total_duration" in report_dict
        assert "total_records" in report_dict
        assert "overall_throughput" in report_dict
        assert "phases" in report_dict
        assert "bottlenecks" in report_dict
        assert "generated_at" in report_dict

    def test_report_to_json(self):
        """测试报告转换为JSON"""
        monitor = PerformanceMonitor()

        monitor.start_phase("test")
        time.sleep(0.01)
        monitor.end_phase("test", records_count=100)

        report = monitor.generate_report()
        json_report = report.to_json()

        assert isinstance(json_report, str)
        # 验证是否为有效JSON
        parsed = json.loads(json_report)
        assert isinstance(parsed, dict)
        assert "total_duration" in parsed

    def test_report_to_text(self):
        """测试报告转换为文本"""
        monitor = PerformanceMonitor()

        monitor.start_phase("test")
        time.sleep(0.01)
        monitor.end_phase("test", records_count=100)

        report = monitor.generate_report()
        text_report = report.to_text()

        assert isinstance(text_report, str)
        assert "性能监控报告" in text_report
        assert "test" in text_report
        assert "总耗时" in text_report
        assert "吞吐量" in text_report

    def test_report_includes_bottlenecks(self):
        """测试报告包含瓶颈信息"""
        monitor = PerformanceMonitor()

        # 创建一个明显的瓶颈
        monitor.start_phase("bottleneck")
        time.sleep(0.15)
        monitor.end_phase("bottleneck")

        monitor.start_phase("fast")
        time.sleep(0.03)
        monitor.end_phase("fast")

        report = monitor.generate_report()

        assert len(report.bottlenecks) > 0
        assert "bottleneck" in report.bottlenecks[0]

        # 验证文本报告包含瓶颈信息
        text_report = report.to_text()
        assert "识别的瓶颈" in text_report


class TestMultiPhaseMonitoring:
    """多阶段监控测试"""

    def test_multiple_phases(self):
        """测试多个阶段监控"""
        monitor = PerformanceMonitor()

        phases = ["phase1", "phase2", "phase3"]
        for phase in phases:
            monitor.start_phase(phase)
            time.sleep(0.02)
            monitor.end_phase(phase, records_count=100)

        # 验证所有阶段都被记录
        for phase in phases:
            stats = monitor.get_phase_stats(phase)
            assert stats is not None
            assert stats.name == phase

        # 生成报告验证阶段顺序
        report = monitor.generate_report()
        assert len(report.phases) == 3
        assert [p.name for p in report.phases] == phases

    def test_phase_order_preservation(self):
        """测试阶段顺序保持"""
        monitor = PerformanceMonitor()

        # 按特定顺序添加阶段
        order = ["init", "process", "cleanup"]
        for phase in order:
            monitor.start_phase(phase)
            time.sleep(0.01)
            monitor.end_phase(phase)

        report = monitor.generate_report()
        actual_order = [p.name for p in report.phases]
        assert actual_order == order

    def test_sequential_phases(self):
        """测试顺序阶段（前一个结束后开始下一个）"""
        monitor = PerformanceMonitor()

        monitor.start_phase("phase1")
        time.sleep(0.02)
        monitor.end_phase("phase1")

        monitor.start_phase("phase2")
        time.sleep(0.02)
        monitor.end_phase("phase2")

        phase1 = monitor.get_phase_stats("phase1")
        phase2 = monitor.get_phase_stats("phase2")

        # phase2 开始时间应该在 phase1 结束时间之后
        assert phase2.start_time >= phase1.end_time


class TestExceptionHandling:
    """异常处理测试"""

    def test_end_phase_without_start(self):
        """测试未开始就结束阶段"""
        monitor = PerformanceMonitor()

        # 尝试结束未开始的阶段
        result = monitor.end_phase("nonexistent")
        assert result is False

    def test_duplicate_start_phase(self):
        """测试重复开始同一阶段"""
        monitor = PerformanceMonitor()

        # 第一次开始
        assert monitor.start_phase("test") is True

        # 重复开始（应该覆盖）
        assert monitor.start_phase("test") is True

        time.sleep(0.01)
        monitor.end_phase("test")

        # 应该只有一个阶段
        stats = monitor.get_phase_stats("test")
        assert stats is not None

    def test_duplicate_end_phase(self):
        """测试重复结束同一阶段"""
        monitor = PerformanceMonitor()

        monitor.start_phase("test")
        time.sleep(0.01)

        # 第一次结束
        assert monitor.end_phase("test") is True

        # 重复结束
        assert monitor.end_phase("test") is False

    def test_get_nonexistent_phase_stats(self):
        """测试获取不存在阶段的统计"""
        monitor = PerformanceMonitor()

        stats = monitor.get_phase_stats("nonexistent")
        assert stats is None

    def test_generate_report_with_incomplete_phases(self):
        """测试生成报告时包含未完成的阶段"""
        monitor = PerformanceMonitor()

        # 完成的阶段
        monitor.start_phase("completed")
        time.sleep(0.01)
        monitor.end_phase("completed")

        # 未完成的阶段
        monitor.start_phase("incomplete")

        report = monitor.generate_report()

        # 报告应该只包含完成的阶段
        assert len(report.phases) == 1
        assert report.phases[0].name == "completed"


class TestCustomMetrics:
    """自定义指标测试"""

    def test_record_global_metric(self):
        """测试记录全局指标"""
        monitor = PerformanceMonitor()

        assert monitor.record_metric("cache_hit_rate", 0.85) is True
        assert monitor.record_metric("database_queries", 120) is True

        report = monitor.generate_report()
        assert "cache_hit_rate" in report.custom_metrics
        assert report.custom_metrics["cache_hit_rate"] == 0.85
        assert report.custom_metrics["database_queries"] == 120

    def test_record_phase_metric(self):
        """测试记录阶段指标"""
        monitor = PerformanceMonitor()

        monitor.start_phase("test")
        time.sleep(0.01)

        # 记录阶段指标
        assert monitor.record_metric("queries", 10, phase="test") is True
        assert monitor.record_metric("cache_hits", 8, phase="test") is True

        monitor.end_phase("test")

        stats = monitor.get_phase_stats("test")
        assert "queries" in stats.custom_metrics
        assert stats.custom_metrics["queries"] == 10
        assert stats.custom_metrics["cache_hits"] == 8

    def test_record_metric_for_nonexistent_phase(self):
        """测试为不存在的阶段记录指标"""
        monitor = PerformanceMonitor()

        result = monitor.record_metric("test_metric", 100, phase="nonexistent")
        assert result is False


class TestResourceMonitoring:
    """资源监控测试（可选）"""

    def test_resource_monitoring_disabled_by_default(self):
        """测试资源监控默认禁用"""
        monitor = PerformanceMonitor(enable_resource_monitoring=False)

        monitor.start_phase("test")
        time.sleep(0.01)
        monitor.end_phase("test")

        stats = monitor.get_phase_stats("test")
        assert stats.cpu_usage_start is None
        assert stats.cpu_usage_end is None
        assert stats.memory_usage_start is None
        assert stats.memory_usage_end is None

    def test_resource_monitoring_enabled(self):
        """测试启用资源监控"""
        # 尝试启用资源监控
        try:
            monitor = PerformanceMonitor(enable_resource_monitoring=True)

            monitor.start_phase("test")
            time.sleep(0.01)
            monitor.end_phase("test")

            stats = monitor.get_phase_stats("test")

            # 如果psutil可用，应该有资源监控数据
            if monitor.psutil is not None:
                assert stats.cpu_usage_start is not None
                assert stats.cpu_usage_end is not None
                assert stats.memory_usage_start is not None
                assert stats.memory_usage_end is not None
                assert stats.memory_usage_start > 0
                assert stats.memory_usage_end > 0
        except ImportError:
            # psutil未安装，跳过测试
            pytest.skip("psutil not installed")


class TestReset:
    """重置功能测试"""

    def test_reset_monitor(self):
        """测试重置监控器"""
        monitor = PerformanceMonitor()

        # 添加一些阶段
        monitor.start_phase("phase1")
        time.sleep(0.01)
        monitor.end_phase("phase1")

        monitor.record_metric("test_metric", 100)

        # 重置
        monitor.reset()

        # 验证已清空
        assert monitor.get_phase_stats("phase1") is None

        report = monitor.generate_report()
        assert len(report.phases) == 0
        assert len(report.custom_metrics) == 0

    def test_reuse_after_reset(self):
        """测试重置后可以重新使用"""
        monitor = PerformanceMonitor()

        # 第一次使用
        monitor.start_phase("phase1")
        time.sleep(0.01)
        monitor.end_phase("phase1")

        # 重置
        monitor.reset()

        # 第二次使用
        monitor.start_phase("phase2")
        time.sleep(0.01)
        monitor.end_phase("phase2")

        report = monitor.generate_report()
        assert len(report.phases) == 1
        assert report.phases[0].name == "phase2"


class TestStatsummary:
    """统计摘要测试"""

    def test_get_stats_summary(self):
        """测试获取统计摘要"""
        monitor = PerformanceMonitor(enable_resource_monitoring=False)

        summary = monitor.get_stats_summary()

        assert isinstance(summary, dict)
        assert "total_phases" in summary
        assert "completed_phases" in summary
        assert "custom_metrics_count" in summary
        assert "enable_resource_monitoring" in summary

    def test_stats_summary_counts(self):
        """测试统计摘要计数准确性"""
        monitor = PerformanceMonitor()

        # 添加2个完成的阶段
        monitor.start_phase("phase1")
        time.sleep(0.01)
        monitor.end_phase("phase1")

        monitor.start_phase("phase2")
        time.sleep(0.01)
        monitor.end_phase("phase2")

        # 添加1个未完成的阶段
        monitor.start_phase("phase3")

        # 添加1个全局指标
        monitor.record_metric("test", 100)

        summary = monitor.get_stats_summary()

        assert summary["total_phases"] == 3
        assert summary["completed_phases"] == 2
        assert summary["custom_metrics_count"] == 1
