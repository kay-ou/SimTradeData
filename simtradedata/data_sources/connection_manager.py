"""
BaoStock连接管理器

提供线程安全的BaoStock会话管理,实现会话保活和串行化访问。
"""

import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    BaoStock连接管理器

    管理BaoStock全局单例会话,提供:
    1. 会话保活: 避免频繁login/logout
    2. 线程安全: 使用锁保证串行访问BaoStock API
    3. 智能重连: 只在会话真正超时时重连
    4. 心跳检测: 定期检测会话有效性
    5. 统计信息: 记录重连次数、访问时间等

    注意: BaoStock使用全局单例会话,不支持连接池
    """

    def __init__(
        self,
        adapter,  # BaoStockAdapter实例
        session_timeout: int = 600,  # 会话超时(秒)
        heartbeat_interval: int = 60,  # 心跳间隔(秒)
        lock_timeout: float = 10.0,  # 锁等待超时(秒)
    ):
        """
        初始化连接管理器

        Args:
            adapter: BaoStockAdapter实例
            session_timeout: 会话超时时间(秒),默认600秒(10分钟)
            heartbeat_interval: 心跳检测间隔(秒),默认60秒
            lock_timeout: 锁等待超时时间(秒),默认10秒
        """
        self.adapter = adapter
        self.session_timeout = session_timeout
        self.heartbeat_interval = heartbeat_interval
        self.lock_timeout = lock_timeout

        # 线程锁,保证串行访问BaoStock API
        self._lock = threading.Lock()

        # 统计信息
        self._stats = {
            "reconnect_count": 0,  # 重连次数
            "access_count": 0,  # 访问次数
            "total_wait_time": 0.0,  # 总等待时间
            "total_access_time": 0.0,  # 总访问时间
            "last_heartbeat_time": None,  # 最后心跳时间
            "lock_timeout_count": 0,  # 锁超时次数
        }

        # 最后访问时间,用于判断是否需要心跳
        self._last_access_time = None

    def ensure_connected(self) -> bool:
        """
        确保会话有效,需要时重连

        策略:
        1. 如果未连接,建立连接
        2. 如果已连接但超时,重新连接
        3. 如果已连接未超时,保持现有连接

        Returns:
            bool: 连接是否成功
        """
        # 检查是否已连接
        if not self.adapter.is_connected():
            logger.debug("BaoStock未连接,正在建立连接...")
            self._stats["reconnect_count"] += 1  # 记录重连尝试
            try:
                self.adapter.connect()
                self._last_access_time = datetime.now()
                logger.info("BaoStock连接建立成功")
                return True
            except Exception as e:
                logger.error(f"BaoStock连接失败: {e}")
                return False

        # 检查会话是否超时
        if self.adapter._last_connect_time:
            elapsed = (datetime.now() - self.adapter._last_connect_time).total_seconds()
            if elapsed > self.session_timeout:
                logger.warning(
                    f"BaoStock会话已超时({elapsed:.0f}秒 > {self.session_timeout}秒),正在重新连接..."
                )
                self._stats["reconnect_count"] += 1  # 记录重连尝试
                try:
                    self.adapter.disconnect()
                    self.adapter.connect()
                    self._last_access_time = datetime.now()
                    logger.info("BaoStock会话重连成功")
                    return True
                except Exception as e:
                    logger.error(f"BaoStock重连失败: {e}")
                    return False

        # 会话有效,无需重连
        self._last_access_time = datetime.now()
        return True

    def acquire_lock(self, timeout: Optional[float] = None) -> bool:
        """
        获取访问锁,实现线程安全的串行访问

        Args:
            timeout: 锁等待超时时间(秒),None使用默认值

        Returns:
            bool: 是否成功获取锁
        """
        if timeout is None:
            timeout = self.lock_timeout

        start_time = time.time()
        acquired = self._lock.acquire(timeout=timeout)
        wait_time = time.time() - start_time

        if acquired:
            self._stats["access_count"] += 1
            self._stats["total_wait_time"] += wait_time
            logger.debug(f"获取访问锁成功,等待时间: {wait_time:.3f}秒")
            return True
        else:
            self._stats["lock_timeout_count"] += 1
            logger.warning(f"获取访问锁超时({timeout}秒)")
            return False

    def release_lock(self):
        """释放访问锁"""
        try:
            self._lock.release()
            logger.debug("释放访问锁")
        except RuntimeError as e:
            # 锁未被持有时尝试释放会抛出RuntimeError
            logger.warning(f"释放锁失败(锁未被持有): {e}")

    def heartbeat(self) -> bool:
        """
        心跳检测:定期发送轻量级查询验证会话有效性

        策略:
        1. 检查距离上次访问是否超过心跳间隔
        2. 如果超过,发送轻量级查询(查询最近一天交易日历)
        3. 如果查询失败,标记会话失效

        Returns:
            bool: 会话是否有效
        """
        # 检查是否需要心跳
        if self._last_access_time:
            elapsed = (datetime.now() - self._last_access_time).total_seconds()
            if elapsed < self.heartbeat_interval:
                # 最近刚访问过,无需心跳
                return True

        # 执行心跳检测
        logger.debug("执行BaoStock心跳检测...")
        try:
            # 使用轻量级查询:查询最近一天交易日历
            from datetime import date, timedelta

            today = date.today()
            yesterday = today - timedelta(days=1)

            # 获取访问锁
            if not self.acquire_lock():
                logger.warning("心跳检测获取锁失败")
                return False

            try:
                # 调用轻量级API
                result = self.adapter.get_trade_calendar(
                    start_date=yesterday, end_date=today
                )

                # 更新统计信息
                self._stats["last_heartbeat_time"] = datetime.now()
                self._last_access_time = datetime.now()

                logger.debug("BaoStock心跳检测成功")
                return True

            finally:
                self.release_lock()

        except Exception as e:
            logger.warning(f"BaoStock心跳检测失败: {e}")
            # 标记会话失效
            self.adapter._connected = False
            return False

    def disconnect(self):
        """断开连接"""
        try:
            self.adapter.disconnect()
            logger.info("BaoStock连接已断开")
        except Exception as e:
            logger.error(f"断开BaoStock连接失败: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """
        获取统计信息

        Returns:
            Dict包含:
            - reconnect_count: 重连次数
            - access_count: 访问次数
            - avg_wait_time: 平均等待时间(毫秒)
            - avg_access_time: 平均访问时间(毫秒)
            - last_heartbeat_time: 最后心跳时间
            - lock_timeout_count: 锁超时次数
        """
        stats = self._stats.copy()

        # 计算平均值
        if stats["access_count"] > 0:
            stats["avg_wait_time"] = (
                stats["total_wait_time"] / stats["access_count"] * 1000
            )  # 转换为毫秒
            if stats["total_access_time"] > 0:
                stats["avg_access_time"] = (
                    stats["total_access_time"] / stats["access_count"] * 1000
                )
            else:
                stats["avg_access_time"] = 0.0
        else:
            stats["avg_wait_time"] = 0.0
            stats["avg_access_time"] = 0.0

        # 格式化时间
        if stats["last_heartbeat_time"]:
            stats["last_heartbeat_time"] = stats["last_heartbeat_time"].isoformat()

        return stats
