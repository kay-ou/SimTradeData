"""
ConnectionManager单元测试

测试连接管理器的核心功能:
1. 会话保活和重连
2. 线程安全锁机制
3. 心跳检测有效性
4. 超时处理
5. 并发安全性
"""

import threading
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from simtradedata.data_sources.connection_manager import ConnectionManager


@pytest.fixture
def mock_adapter():
    """创建模拟的BaoStockAdapter"""
    adapter = MagicMock()
    adapter.is_connected.return_value = False
    adapter._last_connect_time = None
    adapter._connected = False
    return adapter


@pytest.fixture
def connection_manager(mock_adapter):
    """创建ConnectionManager实例"""
    return ConnectionManager(
        adapter=mock_adapter,
        session_timeout=600,  # 10分钟
        heartbeat_interval=60,  # 1分钟
        lock_timeout=5.0,  # 5秒
    )


class TestConnectionManager:
    """ConnectionManager测试套件"""

    def test_init(self, connection_manager, mock_adapter):
        """测试初始化"""
        assert connection_manager.adapter == mock_adapter
        assert connection_manager.session_timeout == 600
        assert connection_manager.heartbeat_interval == 60
        assert connection_manager.lock_timeout == 5.0
        assert connection_manager._stats["reconnect_count"] == 0
        assert connection_manager._stats["access_count"] == 0

    def test_ensure_connected_when_not_connected(
        self, connection_manager, mock_adapter
    ):
        """测试未连接时建立连接"""
        mock_adapter.is_connected.return_value = False
        mock_adapter.connect.return_value = True

        result = connection_manager.ensure_connected()

        assert result is True
        mock_adapter.connect.assert_called_once()
        assert connection_manager._stats["reconnect_count"] == 1

    def test_ensure_connected_when_already_connected(
        self, connection_manager, mock_adapter
    ):
        """测试已连接且未超时时保持连接"""
        mock_adapter.is_connected.return_value = True
        mock_adapter._last_connect_time = datetime.now()

        result = connection_manager.ensure_connected()

        assert result is True
        mock_adapter.connect.assert_not_called()
        mock_adapter.disconnect.assert_not_called()

    def test_ensure_connected_when_session_timeout(
        self, connection_manager, mock_adapter
    ):
        """测试会话超时后重连"""
        mock_adapter.is_connected.return_value = True
        # 设置连接时间为700秒前(超过600秒超时)
        mock_adapter._last_connect_time = datetime.now() - timedelta(seconds=700)

        result = connection_manager.ensure_connected()

        assert result is True
        mock_adapter.disconnect.assert_called_once()
        mock_adapter.connect.assert_called_once()
        assert connection_manager._stats["reconnect_count"] == 1

    def test_ensure_connected_connection_failure(
        self, connection_manager, mock_adapter
    ):
        """测试连接失败处理"""
        mock_adapter.is_connected.return_value = False
        mock_adapter.connect.side_effect = Exception("连接失败")

        result = connection_manager.ensure_connected()

        assert result is False
        assert connection_manager._stats["reconnect_count"] == 1

    def test_acquire_release_lock(self, connection_manager):
        """测试锁的获取和释放"""
        # 获取锁
        acquired = connection_manager.acquire_lock()
        assert acquired is True
        assert connection_manager._stats["access_count"] == 1

        # 释放锁
        connection_manager.release_lock()

        # 再次获取锁
        acquired = connection_manager.acquire_lock()
        assert acquired is True
        assert connection_manager._stats["access_count"] == 2

        connection_manager.release_lock()

    def test_acquire_lock_timeout(self, connection_manager):
        """测试锁获取超时"""

        # 在另一个线程中持有锁
        def hold_lock():
            connection_manager.acquire_lock()
            time.sleep(2)  # 持有锁2秒
            connection_manager.release_lock()

        thread = threading.Thread(target=hold_lock)
        thread.start()

        # 等待确保线程已获取锁
        time.sleep(0.1)

        # 尝试获取锁,超时时间1秒
        start_time = time.time()
        acquired = connection_manager.acquire_lock(timeout=1.0)
        elapsed = time.time() - start_time

        assert acquired is False
        assert elapsed >= 1.0  # 应该等待至少1秒
        assert connection_manager._stats["lock_timeout_count"] == 1

        thread.join()

    def test_concurrent_access(self, connection_manager, mock_adapter):
        """测试并发访问串行化"""
        mock_adapter.is_connected.return_value = True
        mock_adapter._last_connect_time = datetime.now()

        access_order = []
        access_lock = threading.Lock()

        def access_resource(thread_id):
            """模拟访问资源"""
            if connection_manager.acquire_lock():
                try:
                    with access_lock:
                        access_order.append(f"start-{thread_id}")
                    time.sleep(0.1)  # 模拟访问耗时
                    with access_lock:
                        access_order.append(f"end-{thread_id}")
                finally:
                    connection_manager.release_lock()

        # 创建5个线程并发访问
        threads = []
        for i in range(5):
            thread = threading.Thread(target=access_resource, args=(i,))
            threads.append(thread)
            thread.start()

        # 等待所有线程完成
        for thread in threads:
            thread.join()

        # 验证访问是串行的:每个start后面应该紧跟对应的end
        assert len(access_order) == 10  # 5个线程,每个2个事件
        for i in range(0, len(access_order), 2):
            start = access_order[i]
            end = access_order[i + 1]
            # 验证start和end是配对的
            assert start.startswith("start-")
            assert end.startswith("end-")
            assert start.split("-")[1] == end.split("-")[1]

    def test_heartbeat_success(self, connection_manager, mock_adapter):
        """测试心跳检测成功"""
        mock_adapter.is_connected.return_value = True
        mock_adapter._last_connect_time = datetime.now()
        mock_adapter.get_trade_calendar.return_value = [
            {"trade_date": "2025-01-08", "is_trading": 1}
        ]

        # 设置上次访问时间为70秒前(超过60秒心跳间隔)
        connection_manager._last_access_time = datetime.now() - timedelta(seconds=70)

        result = connection_manager.heartbeat()

        assert result is True
        mock_adapter.get_trade_calendar.assert_called_once()
        assert connection_manager._stats["last_heartbeat_time"] is not None

    def test_heartbeat_skip_when_recently_accessed(
        self, connection_manager, mock_adapter
    ):
        """测试最近刚访问过时跳过心跳"""
        mock_adapter.is_connected.return_value = True

        # 设置上次访问时间为30秒前(未超过60秒心跳间隔)
        connection_manager._last_access_time = datetime.now() - timedelta(seconds=30)

        result = connection_manager.heartbeat()

        assert result is True
        mock_adapter.get_trade_calendar.assert_not_called()

    def test_heartbeat_failure(self, connection_manager, mock_adapter):
        """测试心跳检测失败"""
        mock_adapter.is_connected.return_value = True
        mock_adapter._last_connect_time = datetime.now()
        mock_adapter.get_trade_calendar.side_effect = Exception("网络错误")

        # 设置上次访问时间为70秒前
        connection_manager._last_access_time = datetime.now() - timedelta(seconds=70)

        result = connection_manager.heartbeat()

        assert result is False
        assert mock_adapter._connected is False

    def test_disconnect(self, connection_manager, mock_adapter):
        """测试断开连接"""
        connection_manager.disconnect()
        mock_adapter.disconnect.assert_called_once()

    def test_get_stats(self, connection_manager):
        """测试获取统计信息"""
        # 模拟一些访问
        connection_manager._stats["reconnect_count"] = 2
        connection_manager._stats["access_count"] = 10
        connection_manager._stats["total_wait_time"] = 0.5  # 500ms
        connection_manager._stats["total_access_time"] = 1.0  # 1000ms
        connection_manager._stats["lock_timeout_count"] = 1
        connection_manager._stats["last_heartbeat_time"] = datetime.now()

        stats = connection_manager.get_stats()

        assert stats["reconnect_count"] == 2
        assert stats["access_count"] == 10
        assert stats["avg_wait_time"] == 50.0  # 500ms / 10 = 50ms
        assert stats["avg_access_time"] == 100.0  # 1000ms / 10 = 100ms
        assert stats["lock_timeout_count"] == 1
        assert stats["last_heartbeat_time"] is not None

    def test_get_stats_empty(self, connection_manager):
        """测试无访问时的统计信息"""
        stats = connection_manager.get_stats()

        assert stats["reconnect_count"] == 0
        assert stats["access_count"] == 0
        assert stats["avg_wait_time"] == 0.0
        assert stats["avg_access_time"] == 0.0
        assert stats["lock_timeout_count"] == 0
        assert stats["last_heartbeat_time"] is None

    def test_thread_safety_stress(self, connection_manager, mock_adapter):
        """压力测试:大量并发访问"""
        mock_adapter.is_connected.return_value = True
        mock_adapter._last_connect_time = datetime.now()

        success_count = [0]  # 使用列表避免闭包问题
        lock = threading.Lock()

        def concurrent_access():
            """并发访问"""
            if connection_manager.acquire_lock(timeout=2.0):
                try:
                    time.sleep(0.01)  # 模拟短暂访问
                    with lock:
                        success_count[0] += 1
                finally:
                    connection_manager.release_lock()

        # 创建100个线程并发访问
        threads = []
        for _ in range(100):
            thread = threading.Thread(target=concurrent_access)
            threads.append(thread)
            thread.start()

        # 等待所有线程完成
        for thread in threads:
            thread.join()

        # 验证所有访问都成功(串行化)
        assert success_count[0] == 100
        assert connection_manager._stats["access_count"] == 100


class TestConnectionManagerIntegration:
    """ConnectionManager集成测试(需要真实BaoStockAdapter)"""

    @pytest.mark.skip(reason="需要真实BaoStock连接,仅在集成测试时运行")
    def test_real_connection(self):
        """测试真实连接(集成测试)"""
        from simtradedata.data_sources.baostock_adapter import BaoStockAdapter

        adapter = BaoStockAdapter()
        manager = ConnectionManager(adapter)

        # 测试连接
        assert manager.ensure_connected() is True
        assert adapter.is_connected() is True

        # 测试心跳
        assert manager.heartbeat() is True

        # 测试断开
        manager.disconnect()
        assert adapter.is_connected() is False
