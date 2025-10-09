"""
ConnectionManager 性能基准测试

验证连接管理优化效果,对比有/无会话保活的性能差异。

测试场景:
1. 场景1: 无会话保活 - 10次连续 connect/disconnect (频繁重连)
2. 场景2: 有会话保活 - 10次连续 acquire_lock/release_lock (会话保活)
3. 场景3: 并发场景 - 100个线程同时请求访问

成功标准:
- 会话保活场景比频繁重连快 >30%
- 并发场景性能提升显著
"""

import threading
import time
from datetime import datetime

import pytest

from simtradedata.data_sources.baostock_adapter import BaoStockAdapter
from simtradedata.data_sources.connection_manager import ConnectionManager


@pytest.fixture(scope="module")
def real_adapter():
    """创建真实的BaoStockAdapter用于性能测试"""
    adapter = BaoStockAdapter()
    yield adapter
    # 清理
    if adapter.is_connected():
        adapter.disconnect()


@pytest.fixture
def connection_manager(real_adapter):
    """创建ConnectionManager实例"""
    return ConnectionManager(
        adapter=real_adapter,
        session_timeout=600,
        heartbeat_interval=60,
        lock_timeout=5.0,
    )


class TestFrequentReconnectPerformance:
    """频繁重连性能测试"""

    def test_scenario_1_frequent_reconnect(self, benchmark, real_adapter):
        """场景1: 10次连续 connect/disconnect (频繁重连)"""

        def frequent_reconnect():
            """频繁连接和断开"""
            for _ in range(10):
                real_adapter.connect()
                # 模拟简单查询
                try:
                    yesterday = datetime.now().strftime("%Y-%m-%d")
                    today = datetime.now().strftime("%Y-%m-%d")
                    real_adapter.get_trade_calendar(yesterday, today)
                except Exception:
                    pass
                real_adapter.disconnect()

        # 运行基准测试（至少3次）
        benchmark.pedantic(frequent_reconnect, iterations=3, rounds=3)


class TestSessionKeepalivePerformance:
    """会话保活性能测试"""

    def test_scenario_2_session_keepalive(self, benchmark, connection_manager):
        """场景2: 10次连续 acquire_lock/release_lock (会话保活)"""

        def session_keepalive():
            """使用会话保活"""
            for _ in range(10):
                if connection_manager.acquire_lock():
                    try:
                        connection_manager.ensure_connected()
                        # 模拟简单查询
                        try:
                            yesterday = datetime.now().strftime("%Y-%m-%d")
                            today = datetime.now().strftime("%Y-%m-%d")
                            connection_manager.adapter.get_trade_calendar(
                                yesterday, today
                            )
                        except Exception:
                            pass
                    finally:
                        connection_manager.release_lock()

        # 运行基准测试（至少3次）
        benchmark.pedantic(session_keepalive, iterations=3, rounds=3)


class TestConcurrentAccessPerformance:
    """并发访问性能测试"""

    def test_scenario_3_concurrent_access_without_manager(
        self, benchmark, real_adapter
    ):
        """场景3a: 100个线程并发访问 - 无连接管理器（串行连接/断开）"""

        def concurrent_access_without_manager():
            """无连接管理器的并发访问"""
            success_count = [0]
            lock = threading.Lock()

            def worker():
                """工作线程"""
                try:
                    # 每个线程独立连接和断开
                    real_adapter.connect()
                    try:
                        yesterday = datetime.now().strftime("%Y-%m-%d")
                        today = datetime.now().strftime("%Y-%m-%d")
                        real_adapter.get_trade_calendar(yesterday, today)
                        with lock:
                            success_count[0] += 1
                    finally:
                        real_adapter.disconnect()
                except Exception:
                    pass

            threads = []
            for _ in range(100):
                thread = threading.Thread(target=worker)
                threads.append(thread)
                thread.start()

            for thread in threads:
                thread.join()

            return success_count[0]

        # 运行基准测试（至少3次）
        result = benchmark.pedantic(
            concurrent_access_without_manager, iterations=3, rounds=3
        )

    def test_scenario_3_concurrent_access_with_manager(
        self, benchmark, connection_manager
    ):
        """场景3b: 100个线程并发访问 - 使用连接管理器（串行化访问）"""

        def concurrent_access_with_manager():
            """使用连接管理器的并发访问"""
            success_count = [0]
            lock = threading.Lock()

            def worker():
                """工作线程"""
                if connection_manager.acquire_lock(timeout=10.0):
                    try:
                        connection_manager.ensure_connected()
                        try:
                            yesterday = datetime.now().strftime("%Y-%m-%d")
                            today = datetime.now().strftime("%Y-%m-%d")
                            connection_manager.adapter.get_trade_calendar(
                                yesterday, today
                            )
                            with lock:
                                success_count[0] += 1
                        except Exception:
                            pass
                    finally:
                        connection_manager.release_lock()

            threads = []
            for _ in range(100):
                thread = threading.Thread(target=worker)
                threads.append(thread)
                thread.start()

            for thread in threads:
                thread.join()

            return success_count[0]

        # 运行基准测试（至少3次）
        result = benchmark.pedantic(
            concurrent_access_with_manager, iterations=3, rounds=3
        )


class TestPerformanceComparison:
    """性能对比和报告生成"""

    def test_performance_summary(self, real_adapter, connection_manager):
        """生成性能对比总结报告"""

        print("\n" + "=" * 80)
        print("ConnectionManager 性能基准测试总结")
        print("=" * 80)

        results = {}

        # 场景1: 频繁重连 (10次)
        start = time.time()
        for _ in range(10):
            real_adapter.connect()
            try:
                yesterday = datetime.now().strftime("%Y-%m-%d")
                today = datetime.now().strftime("%Y-%m-%d")
                real_adapter.get_trade_calendar(yesterday, today)
            except Exception:
                pass
            real_adapter.disconnect()
        results["frequent_reconnect"] = time.time() - start

        # 场景2: 会话保活 (10次)
        start = time.time()
        for _ in range(10):
            if connection_manager.acquire_lock():
                try:
                    connection_manager.ensure_connected()
                    try:
                        yesterday = datetime.now().strftime("%Y-%m-%d")
                        today = datetime.now().strftime("%Y-%m-%d")
                        connection_manager.adapter.get_trade_calendar(yesterday, today)
                    except Exception:
                        pass
                finally:
                    connection_manager.release_lock()
        results["session_keepalive"] = time.time() - start

        # 计算性能提升
        keepalive_speedup = results["frequent_reconnect"] / results["session_keepalive"]

        # 打印报告
        print("\n场景1: 频繁重连 - 10次 connect/disconnect")
        print(f"  耗时: {results['frequent_reconnect']:.4f}秒")
        print(f"  平均每次: {results['frequent_reconnect'] / 10:.4f}秒")

        print("\n场景2: 会话保活 - 10次 acquire_lock/release_lock")
        print(f"  耗时: {results['session_keepalive']:.4f}秒")
        print(f"  平均每次: {results['session_keepalive'] / 10:.4f}秒")
        print(f"  性能提升: {keepalive_speedup:.2f}x")

        print("\n" + "=" * 80)
        print("性能提升总结")
        print("=" * 80)
        print(f"会话保活性能提升: {keepalive_speedup:.2f}x")

        # 验证性能提升达到预期（>30% = 1.3x）
        assert (
            keepalive_speedup >= 1.3
        ), f"会话保活性能提升 {keepalive_speedup:.2f}x 未达到 1.3倍以上 (30%提升)"

        print("\n✅ 性能提升达标：会话保活性能提升 >30%")
        print("=" * 80)

        # 打印统计信息
        stats = connection_manager.get_stats()
        print("\n连接管理器统计信息:")
        print(f"  重连次数: {stats['reconnect_count']}")
        print(f"  访问次数: {stats['access_count']}")
        print(f"  平均等待时间: {stats['avg_wait_time']:.2f}ms")
        print(f"  平均访问时间: {stats['avg_access_time']:.2f}ms")
        print(f"  锁超时次数: {stats['lock_timeout_count']}")
        print("=" * 80)
