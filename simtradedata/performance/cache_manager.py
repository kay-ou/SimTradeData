"""
缓存管理器

提供多级缓存、缓存策略和缓存性能优化功能。
包含交易日历缓存和股票元数据缓存。
"""

# 标准库导入
import logging
import pickle
import threading
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from datetime import date, datetime
from typing import Any, Dict, List, Optional

# 项目内导入
from ..config import Config
from ..core import BaseManager, ValidationError, unified_error_handler

logger = logging.getLogger(__name__)


class CacheBackend(ABC):
    """缓存后端抽象基类"""

    @abstractmethod
    def get(self, key: str) -> Optional[Any]:
        """获取缓存值"""

    @abstractmethod
    def set(self, key: str, value: Any, ttl: int = None) -> bool:
        """设置缓存值"""

    @abstractmethod
    def delete(self, key: str) -> bool:
        """删除缓存值"""

    @abstractmethod
    def clear(self) -> bool:
        """清空缓存"""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """检查键是否存在"""


class MemoryCache(CacheBackend):
    """内存缓存后端"""

    def __init__(self, max_size: int = 1000, default_ttl: int = 3600):
        """
        初始化内存缓存

        Args:
            max_size: 最大缓存条目数
            default_ttl: 默认TTL（秒）
        """
        self.max_size = max_size
        self.default_ttl = default_ttl
        self.cache = OrderedDict()
        self.lock = threading.RLock()

    def get(self, key: str) -> Optional[Any]:
        """获取缓存值"""
        with self.lock:
            if key not in self.cache:
                return None

            entry = self.cache[key]

            # 检查是否过期
            if entry["expires_at"] and time.time() > entry["expires_at"]:
                del self.cache[key]
                return None

            # LRU: 移动到末尾
            self.cache.move_to_end(key)
            return entry["value"]

    def set(self, key: str, value: Any, ttl: int = None) -> bool:
        """设置缓存值"""
        with self.lock:
            try:
                # 计算过期时间
                expires_at = None
                if ttl is not None:
                    expires_at = time.time() + ttl
                elif self.default_ttl > 0:
                    expires_at = time.time() + self.default_ttl

                # 检查缓存大小限制
                if key not in self.cache and len(self.cache) >= self.max_size:
                    # LRU淘汰：删除最旧的条目
                    self.cache.popitem(last=False)

                # 添加或更新缓存
                self.cache[key] = {
                    "value": value,
                    "expires_at": expires_at,
                    "created_at": time.time(),
                }

                # 移动到末尾（最新）
                self.cache.move_to_end(key)

                return True

            except Exception as e:
                logger.error(f"设置内存缓存失败: {e}")
                return False

    def delete(self, key: str) -> bool:
        """删除缓存值"""
        with self.lock:
            if key in self.cache:
                del self.cache[key]
                return True
            return False

    def clear(self) -> bool:
        """清空缓存"""
        with self.lock:
            self.cache.clear()
            return True

    def exists(self, key: str) -> bool:
        """检查键是否存在"""
        return self.get(key) is not None

    def cleanup_expired(self) -> int:
        """清理过期条目"""
        with self.lock:
            current_time = time.time()
            expired_keys = []

            for key, entry in self.cache.items():
                if entry["expires_at"] and current_time > entry["expires_at"]:
                    expired_keys.append(key)

            for key in expired_keys:
                del self.cache[key]

            return len(expired_keys)

    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计"""
        with self.lock:
            return {
                "size": len(self.cache),
                "max_size": self.max_size,
                "memory_usage": self._estimate_memory_usage(),
            }

    def _estimate_memory_usage(self) -> int:
        """估算内存使用量"""
        try:
            total_size = 0
            for key, entry in self.cache.items():
                total_size += len(pickle.dumps(key))
                total_size += len(pickle.dumps(entry["value"]))
            return total_size
        except Exception:
            return 0


class CacheManager(BaseManager):
    """缓存管理器"""

    def __init__(self, config: Optional[Config] = None, **kwargs):
        """
        初始化缓存管理器

        Args:
            config: 配置对象
        """
        super().__init__(config=config, **kwargs)

    def _init_specific_config(self):
        """初始化缓存管理器特定配置"""
        self.enable_l1_cache = self._get_config("cache_manager.enable_l1_cache", True)
        self.enable_l2_cache = self._get_config("cache_manager.enable_l2_cache", True)
        self.l1_max_size = self._get_config("cache_manager.l1_max_size", 1000)
        self.l2_max_size = self._get_config("cache_manager.l2_max_size", 10000)
        self.default_ttl = self._get_config("cache_manager.default_ttl", 3600)

    def _init_components(self):
        """初始化组件"""
        # 初始化缓存层
        self.l1_cache = (
            MemoryCache(self.l1_max_size, self.default_ttl)
            if self.enable_l1_cache
            else None
        )
        self.l2_cache = (
            MemoryCache(self.l2_max_size, self.default_ttl * 2)
            if self.enable_l2_cache
            else None
        )

        # 缓存统计
        self.stats = {
            "l1_hits": 0,
            "l1_misses": 0,
            "l2_hits": 0,
            "l2_misses": 0,
            "sets": 0,
            "deletes": 0,
            "evictions": 0,
        }

        # 缓存策略
        self.cache_strategies = {
            "stock_info": {"ttl": 86400, "level": "l2"},  # 24小时
            "daily_data": {"ttl": 3600, "level": "l1"},  # 1小时
            "realtime_data": {"ttl": 60, "level": "l1"},  # 1分钟
            "fundamentals": {"ttl": 21600, "level": "l2"},  # 6小时
            "technical_indicators": {"ttl": 1800, "level": "l1"},  # 30分钟
            "trading_calendar": {"ttl": 604800, "level": "l2"},  # 7天
            "last_data_date": {"ttl": 60, "level": "l1"},  # 60秒
            "stock_metadata": {"ttl": 86400, "level": "l2"},  # 1天
        }

        # 交易日历专用缓存（使用字典以支持快速查找）
        self._trading_calendar_cache = {}
        self._trading_calendar_loaded_at = None
        self._trading_calendar_lock = threading.RLock()

        # 股票元数据专用缓存
        self._last_data_date_cache = {}  # {(symbol, frequency): (date, timestamp)}
        self._stock_metadata_cache = {}  # {symbol: (metadata, timestamp)}
        self._metadata_lock = threading.RLock()

        # 启动清理线程
        self.cleanup_thread = threading.Thread(target=self._cleanup_worker, daemon=True)
        self.cleanup_thread.start()

    def _get_required_attributes(self) -> List[str]:
        """必需属性列表"""
        return ["stats", "cache_strategies", "cleanup_thread"]

    @unified_error_handler(return_dict=True)
    def get(self, key: str, data_type: str = "default") -> Optional[Any]:
        """
        获取缓存值

        Args:
            key: 缓存键
            data_type: 数据类型

        Returns:
            Optional[Any]: 缓存值
        """
        if not key:
            raise ValidationError("缓存键不能为空")

        # 生成完整的缓存键
        full_key = self._generate_cache_key(key, data_type)

        # L1缓存查找
        if self.l1_cache:
            value = self.l1_cache.get(full_key)
            if value is not None:
                self.stats["l1_hits"] += 1
                return value
            else:
                self.stats["l1_misses"] += 1

        # L2缓存查找
        if self.l2_cache:
            value = self.l2_cache.get(full_key)
            if value is not None:
                self.stats["l2_hits"] += 1

                # 提升到L1缓存
                if self.l1_cache:
                    strategy = self.cache_strategies.get(data_type, {})
                    ttl = strategy.get("ttl", self.default_ttl)
                    self.l1_cache.set(full_key, value, ttl)

                return value
            else:
                self.stats["l2_misses"] += 1

        return None

    @unified_error_handler(return_dict=True)
    def set(
        self, key: str, value: Any, data_type: str = "default", ttl: int = None
    ) -> bool:
        """
        设置缓存值

        Args:
            key: 缓存键
            value: 缓存值
            data_type: 数据类型
            ttl: 生存时间

        Returns:
            bool: 是否成功
        """
        if not key:
            raise ValidationError("缓存键不能为空")

        # 生成完整的缓存键
        full_key = self._generate_cache_key(key, data_type)

        # 获取缓存策略
        strategy = self.cache_strategies.get(data_type, {})
        cache_ttl = ttl or strategy.get("ttl", self.default_ttl)
        cache_level = strategy.get("level", "l1")

        success = False

        # 根据策略选择缓存层
        if cache_level == "l1" and self.l1_cache:
            success = self.l1_cache.set(full_key, value, cache_ttl)
        elif cache_level == "l2" and self.l2_cache:
            success = self.l2_cache.set(full_key, value, cache_ttl)
        else:
            # 默认存储到L1
            if self.l1_cache:
                success = self.l1_cache.set(full_key, value, cache_ttl)

        if success:
            self.stats["sets"] += 1

        return success

    @unified_error_handler(return_dict=True)
    def delete(self, key: str, data_type: str = "default") -> bool:
        """
        删除缓存值

        Args:
            key: 缓存键
            data_type: 数据类型

        Returns:
            bool: 是否成功
        """
        if not key:
            raise ValidationError("缓存键不能为空")

        full_key = self._generate_cache_key(key, data_type)

        success = False

        # 从所有缓存层删除
        if self.l1_cache:
            success |= self.l1_cache.delete(full_key)

        if self.l2_cache:
            success |= self.l2_cache.delete(full_key)

        if success:
            self.stats["deletes"] += 1

        return success

    @unified_error_handler(return_dict=True)
    def clear(self, data_type: str = None) -> bool:
        """
        清空缓存

        Args:
            data_type: 数据类型（可选，为None时清空所有）

        Returns:
            bool: 是否成功
        """
        if data_type is None:
            # 清空所有缓存
            success = True
            if self.l1_cache:
                success &= self.l1_cache.clear()
            if self.l2_cache:
                success &= self.l2_cache.clear()
            return success
        else:
            # 清空特定类型的缓存
            success = True
            try:
                # 生成类型前缀
                prefix = f"{data_type}:"

                # 从L1缓存删除匹配的键
                if hasattr(self.l1_cache, "keys"):
                    keys_to_delete = [
                        key for key in self.l1_cache.keys() if key.startswith(prefix)
                    ]
                    for key in keys_to_delete:
                        del self.l1_cache[key]
                else:
                    # 如果l1_cache是字典类型
                    keys_to_delete = [
                        key for key in self.l1_cache if key.startswith(prefix)
                    ]
                    for key in keys_to_delete:
                        del self.l1_cache[key]

                # 从L2缓存删除匹配的键
                l2_keys_to_delete = []
                if self.l2_cache:
                    if hasattr(self.l2_cache, "keys"):
                        l2_keys_to_delete = [
                            key
                            for key in self.l2_cache.keys()
                            if key.startswith(prefix)
                        ]
                        for key in l2_keys_to_delete:
                            del self.l2_cache[key]
                    else:
                        # 如果l2_cache是字典类型
                        l2_keys_to_delete = [
                            key for key in self.l2_cache if key.startswith(prefix)
                        ]
                        for key in l2_keys_to_delete:
                            del self.l2_cache[key]

                self.logger.info(
                    f"清理了 {len(keys_to_delete)} 个 L1 缓存项和 {len(l2_keys_to_delete)} 个 L2 缓存项，类型: {data_type}"
                )

            except Exception as e:
                self.logger.error(f"清理特定类型缓存失败: {e}")
                success = False

            return success

    @unified_error_handler(return_dict=True)
    def exists(self, key: str, data_type: str = "default") -> bool:
        """
        检查缓存是否存在

        Args:
            key: 缓存键
            data_type: 数据类型

        Returns:
            bool: 是否存在
        """
        if not key:
            raise ValidationError("缓存键不能为空")

        get_result = self.get(key, data_type)
        if isinstance(get_result, dict) and "data" in get_result:
            return get_result["data"] is not None
        return get_result is not None

    @unified_error_handler(return_dict=True)
    def get_cache_stats(self) -> Dict[str, Any]:
        """获取缓存统计"""
        total_requests = (
            self.stats["l1_hits"]
            + self.stats["l1_misses"]
            + self.stats["l2_hits"]
            + self.stats["l2_misses"]
        )

        l1_hit_rate = (
            self.stats["l1_hits"] / total_requests if total_requests > 0 else 0
        )
        l2_hit_rate = (
            self.stats["l2_hits"] / total_requests if total_requests > 0 else 0
        )
        overall_hit_rate = (
            (self.stats["l1_hits"] + self.stats["l2_hits"]) / total_requests
            if total_requests > 0
            else 0
        )

        stats = {
            "cache_manager": "SimTradeData Manager",
            "version": "1.0.0",
            "total_requests": total_requests,
            "overall_hit_rate": overall_hit_rate,
            "l1_cache": {
                "enabled": self.enable_l1_cache,
                "hits": self.stats["l1_hits"],
                "misses": self.stats["l1_misses"],
                "hit_rate": l1_hit_rate,
            },
            "l2_cache": {
                "enabled": self.enable_l2_cache,
                "hits": self.stats["l2_hits"],
                "misses": self.stats["l2_misses"],
                "hit_rate": l2_hit_rate,
            },
            "operations": {
                "sets": self.stats["sets"],
                "deletes": self.stats["deletes"],
                "evictions": self.stats["evictions"],
            },
        }

        # 添加缓存后端统计
        if self.l1_cache:
            stats["l1_cache"].update(self.l1_cache.get_stats())

        if self.l2_cache:
            stats["l2_cache"].update(self.l2_cache.get_stats())

        return stats

    def _generate_cache_key(self, key: str, data_type: str) -> str:
        """生成缓存键"""
        return f"{data_type}:{key}"

    def _cleanup_worker(self):
        """清理工作线程"""
        while True:
            try:
                time.sleep(300)  # 每5分钟清理一次

                expired_count = 0

                # 清理L1缓存
                if self.l1_cache:
                    expired_count += self.l1_cache.cleanup_expired()

                # 清理L2缓存
                if self.l2_cache:
                    expired_count += self.l2_cache.cleanup_expired()

                if expired_count > 0:
                    self.stats["evictions"] += expired_count
                    self.logger.debug(f"清理过期缓存: {expired_count} 个条目")

            except Exception as e:
                self._log_error("_cleanup_worker", e)

    @unified_error_handler(return_dict=True)
    def add_cache_strategy(self, data_type: str, ttl: int, level: str = "l1"):
        """
        添加缓存策略

        Args:
            data_type: 数据类型
            ttl: 生存时间
            level: 缓存级别
        """
        if not data_type:
            raise ValidationError("数据类型不能为空")

        if ttl is None or ttl < 0:
            raise ValidationError("TTL必须为非负数")

        if level not in ["l1", "l2"]:
            raise ValidationError("缓存级别必须为l1或l2")

        self.cache_strategies[data_type] = {"ttl": ttl, "level": level}
        self.logger.info(f"添加缓存策略: {data_type} -> TTL={ttl}, Level={level}")
        return True

    @unified_error_handler(return_dict=True)
    def get_cache_strategies(self) -> Dict[str, Dict[str, Any]]:
        """获取缓存策略"""
        return self.cache_strategies.copy()

    # ================== Task 3.1: 交易日历缓存 ==================

    @unified_error_handler(return_dict=True)
    def load_trading_calendar(
        self, db_manager, start_date: date, end_date: date, market: str = "CN"
    ) -> int:
        """
        批量加载交易日历到缓存

        Args:
            db_manager: 数据库管理器
            start_date: 开始日期
            end_date: 结束日期
            market: 市场代码（默认CN）

        Returns:
            int: 加载的日期数量
        """
        with self._trading_calendar_lock:
            try:
                # 查询交易日历
                sql = """
                SELECT date, is_trading
                FROM trading_calendar
                WHERE market = ? AND date BETWEEN ? AND ?
                ORDER BY date
                """

                params = (market, str(start_date), str(end_date))
                results = db_manager.fetchall(sql, params)

                # 加载到缓存
                loaded_count = 0
                for row in results:
                    calendar_date = row["date"]
                    is_trading = bool(row["is_trading"])

                    # 缓存键: market:YYYY-MM-DD
                    cache_key = f"{market}:{calendar_date}"
                    self._trading_calendar_cache[cache_key] = is_trading
                    loaded_count += 1

                # 更新加载时间
                self._trading_calendar_loaded_at = time.time()

                self.logger.info(
                    f"加载交易日历缓存: {start_date} 到 {end_date}, 共 {loaded_count} 天"
                )
                return loaded_count

            except Exception as e:
                self.logger.error(f"加载交易日历缓存失败: {e}")
                raise

    @unified_error_handler(return_dict=True)
    def is_trading_day(self, trade_date: date, market: str = "CN") -> Optional[bool]:
        """
        查询是否为交易日（优先使用缓存）

        Args:
            trade_date: 交易日期
            market: 市场代码

        Returns:
            Optional[bool]: True=交易日, False=非交易日, None=未找到
        """
        with self._trading_calendar_lock:
            # 检查 TTL 是否过期（7天）
            if self._trading_calendar_loaded_at is not None:
                age = time.time() - self._trading_calendar_loaded_at
                if age > 604800:  # 7天 = 604800秒
                    self.logger.debug("交易日历缓存已过期,需要重新加载")
                    self._trading_calendar_cache.clear()
                    self._trading_calendar_loaded_at = None

            # 缓存键
            cache_key = f"{market}:{trade_date}"

            # 查询缓存
            if cache_key in self._trading_calendar_cache:
                self.stats["l1_hits"] += 1
                return self._trading_calendar_cache[cache_key]

            self.stats["l1_misses"] += 1
            return None

    @unified_error_handler(return_dict=True)
    def clear_trading_calendar_cache(self):
        """清空交易日历缓存"""
        with self._trading_calendar_lock:
            count = len(self._trading_calendar_cache)
            self._trading_calendar_cache.clear()
            self._trading_calendar_loaded_at = None
            self.logger.info(f"清空交易日历缓存: {count} 条记录")
            return count

    # ================== Task 3.2: 股票元数据缓存 ==================

    @unified_error_handler(return_dict=True)
    def get_last_data_date(self, symbol: str, frequency: str = "1d") -> Optional[date]:
        """
        查询股票最后数据日期（优先使用缓存）

        Args:
            symbol: 股票代码
            frequency: 频率

        Returns:
            Optional[date]: 最后数据日期，未找到返回None
        """
        with self._metadata_lock:
            cache_key = (symbol, frequency)

            # 检查缓存
            if cache_key in self._last_data_date_cache:
                cached_date, cached_at = self._last_data_date_cache[cache_key]

                # 检查 TTL（60秒）
                age = time.time() - cached_at
                if age < 60:
                    self.stats["l1_hits"] += 1
                    return cached_date
                else:
                    # 过期，删除缓存
                    del self._last_data_date_cache[cache_key]

            self.stats["l1_misses"] += 1
            return None

    @unified_error_handler(return_dict=True)
    def set_last_data_date(self, symbol: str, frequency: str, last_date: date) -> bool:
        """
        更新股票最后数据日期缓存

        Args:
            symbol: 股票代码
            frequency: 频率
            last_date: 最后数据日期

        Returns:
            bool: 是否成功
        """
        with self._metadata_lock:
            cache_key = (symbol, frequency)
            self._last_data_date_cache[cache_key] = (last_date, time.time())
            self.stats["sets"] += 1
            self.logger.debug(
                f"更新最后数据日期缓存: {symbol} {frequency} -> {last_date}"
            )
            return True

    @unified_error_handler(return_dict=True)
    def get_stock_metadata(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        查询股票元数据（优先使用缓存）

        Args:
            symbol: 股票代码

        Returns:
            Optional[Dict]: 股票元数据，未找到返回None
        """
        with self._metadata_lock:
            # 检查缓存
            if symbol in self._stock_metadata_cache:
                metadata, cached_at = self._stock_metadata_cache[symbol]

                # 检查 TTL（1天 = 86400秒）
                age = time.time() - cached_at
                if age < 86400:
                    self.stats["l1_hits"] += 1
                    return metadata
                else:
                    # 过期，删除缓存
                    del self._stock_metadata_cache[symbol]

            self.stats["l1_misses"] += 1
            return None

    @unified_error_handler(return_dict=True)
    def set_stock_metadata(self, symbol: str, metadata: Dict[str, Any]) -> bool:
        """
        设置股票元数据缓存

        Args:
            symbol: 股票代码
            metadata: 元数据

        Returns:
            bool: 是否成功
        """
        with self._metadata_lock:
            self._stock_metadata_cache[symbol] = (metadata, time.time())
            self.stats["sets"] += 1
            self.logger.debug(f"设置股票元数据缓存: {symbol}")
            return True

    @unified_error_handler(return_dict=True)
    def load_stock_metadata_batch(self, db_manager, symbols: List[str]) -> int:
        """
        批量预加载股票元数据

        Args:
            db_manager: 数据库管理器
            symbols: 股票代码列表

        Returns:
            int: 加载的数量
        """
        with self._metadata_lock:
            try:
                # 查询股票基本信息
                placeholders = ",".join(["?" for _ in symbols])
                sql = f"""
                SELECT symbol, name, industry, list_date, status
                FROM stocks
                WHERE symbol IN ({placeholders})
                """

                results = db_manager.fetchall(sql, tuple(symbols))

                # 加载到缓存
                loaded_count = 0
                current_time = time.time()
                for row in results:
                    # sqlite3.Row 使用索引访问
                    metadata = {
                        "symbol": row["symbol"],
                        "name": row["name"] if "name" in row.keys() else None,
                        "industry": (
                            row["industry"] if "industry" in row.keys() else None
                        ),
                        "list_date": (
                            row["list_date"] if "list_date" in row.keys() else None
                        ),
                        "status": row["status"] if "status" in row.keys() else None,
                    }
                    self._stock_metadata_cache[row["symbol"]] = (
                        metadata,
                        current_time,
                    )
                    loaded_count += 1

                self.logger.info(f"批量加载股票元数据: {loaded_count} 只股票")
                return loaded_count

            except Exception as e:
                self.logger.error(f"批量加载股票元数据失败: {e}")
                raise

    @unified_error_handler(return_dict=True)
    def clear_metadata_cache(self):
        """清空元数据缓存"""
        with self._metadata_lock:
            last_date_count = len(self._last_data_date_cache)
            metadata_count = len(self._stock_metadata_cache)

            self._last_data_date_cache.clear()
            self._stock_metadata_cache.clear()

            self.logger.info(
                f"清空元数据缓存: {last_date_count} 个最后日期, {metadata_count} 个股票元数据"
            )
            return last_date_count + metadata_count

    @unified_error_handler(return_dict=True)
    def get_enhanced_cache_stats(self) -> Dict[str, Any]:
        """
        获取增强的缓存统计信息（包括交易日历和元数据）

        Returns:
            Dict: 缓存统计信息
        """
        base_stats = self.get_cache_stats()

        # 添加专用缓存统计
        with self._trading_calendar_lock:
            calendar_stats = {
                "size": len(self._trading_calendar_cache),
                "loaded_at": (
                    datetime.fromtimestamp(self._trading_calendar_loaded_at).isoformat()
                    if self._trading_calendar_loaded_at
                    else None
                ),
                "ttl_seconds": 604800,
                "is_expired": (
                    (time.time() - self._trading_calendar_loaded_at > 604800)
                    if self._trading_calendar_loaded_at
                    else True
                ),
            }

        with self._metadata_lock:
            metadata_stats = {
                "last_data_date_cache_size": len(self._last_data_date_cache),
                "stock_metadata_cache_size": len(self._stock_metadata_cache),
            }

        # 合并统计信息
        if isinstance(base_stats, dict) and "data" in base_stats:
            enhanced_stats = base_stats["data"].copy()
        else:
            enhanced_stats = base_stats.copy()

        enhanced_stats["trading_calendar_cache"] = calendar_stats
        enhanced_stats["metadata_cache"] = metadata_stats

        return enhanced_stats
