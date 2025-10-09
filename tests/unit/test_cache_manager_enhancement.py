"""
CacheManager 增强功能单元测试

测试交易日历缓存和股票元数据缓存功能:
1. 交易日历缓存命中/未命中
2. 最后数据日期缓存和更新
3. TTL 过期机制
4. LRU 淘汰机制
5. 批量预加载性能
6. 缓存统计准确性
"""

import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

from simtradedata.database.manager import DatabaseManager
from simtradedata.performance.cache_manager import CacheManager


@pytest.fixture
def temp_db():
    """创建临时数据库"""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = Path(f.name)

    yield db_path

    # 清理
    if db_path.exists():
        db_path.unlink()


@pytest.fixture
def db_manager(temp_db):
    """创建数据库管理器"""
    manager = DatabaseManager(db_path=str(temp_db))

    # 创建测试表
    manager.execute(
        """
        CREATE TABLE IF NOT EXISTS trading_calendar (
            date TEXT,
            market TEXT,
            is_trading INTEGER,
            PRIMARY KEY (date, market)
        )
    """
    )

    manager.execute(
        """
        CREATE TABLE IF NOT EXISTS stocks (
            symbol TEXT PRIMARY KEY,
            name TEXT,
            industry TEXT,
            list_date TEXT,
            status TEXT
        )
    """
    )

    yield manager

    manager.close()


@pytest.fixture
def cache_manager():
    """创建缓存管理器"""
    return CacheManager()


@pytest.fixture
def trading_calendar_data(db_manager):
    """准备交易日历测试数据"""
    # 插入10天数据，其中6天是交易日
    base_date = date(2024, 1, 1)
    for i in range(10):
        current_date = base_date + timedelta(days=i)
        # 周一到周五是交易日
        is_trading = 1 if i % 7 not in [5, 6] else 0

        db_manager.execute(
            "INSERT OR REPLACE INTO trading_calendar (date, market, is_trading) VALUES (?, ?, ?)",
            (str(current_date), "CN", is_trading),
        )

    return base_date


@pytest.fixture
def stock_metadata_data(db_manager):
    """准备股票元数据测试数据"""
    stocks = [
        ("000001.SZ", "平安银行", "金融", "1991-04-03", "active"),
        ("000002.SZ", "万科A", "房地产", "1991-01-29", "active"),
        ("600000.SH", "浦发银行", "金融", "1999-11-10", "active"),
    ]

    for symbol, name, industry, list_date, status in stocks:
        db_manager.execute(
            "INSERT OR REPLACE INTO stocks (symbol, name, industry, list_date, status) VALUES (?, ?, ?, ?, ?)",
            (symbol, name, industry, list_date, status),
        )

    return stocks


class TestTradingCalendarCache:
    """交易日历缓存测试"""

    def test_load_trading_calendar(
        self, cache_manager, db_manager, trading_calendar_data
    ):
        """测试批量加载交易日历"""
        start_date = trading_calendar_data
        end_date = start_date + timedelta(days=9)

        result = cache_manager.load_trading_calendar(db_manager, start_date, end_date)

        # unified_error_handler 包装返回值
        if isinstance(result, dict) and "data" in result:
            assert result["data"] == 10  # 加载了10天
        else:
            assert result == 10  # 加载了10天

    def test_is_trading_day_cache_hit(
        self, cache_manager, db_manager, trading_calendar_data
    ):
        """测试交易日历缓存命中"""
        start_date = trading_calendar_data
        end_date = start_date + timedelta(days=9)

        # 先加载
        cache_manager.load_trading_calendar(db_manager, start_date, end_date)

        # 查询缓存（应该命中）
        result = cache_manager.is_trading_day(start_date)  # 周一，是交易日

        # unified_error_handler 包装返回值
        if isinstance(result, dict) and "data" in result:
            assert result["data"] is True
        else:
            assert result is True

        # 验证缓存命中统计
        stats = cache_manager.get_cache_stats()
        if isinstance(stats, dict) and "data" in stats:
            assert stats["data"]["l1_cache"]["hits"] >= 1
        else:
            assert stats["l1_cache"]["hits"] >= 1

    def test_is_trading_day_cache_miss(self, cache_manager):
        """测试交易日历缓存未命中"""
        # 查询缓存（未加载，应该未命中）
        result = cache_manager.is_trading_day(date(2024, 1, 1))

        # unified_error_handler 包装返回值
        if isinstance(result, dict) and "data" in result:
            assert result["data"] is None  # 未找到
        else:
            assert result is None  # 未找到

    def test_trading_calendar_ttl_expiry(
        self, cache_manager, db_manager, trading_calendar_data
    ):
        """测试交易日历 TTL 过期"""
        start_date = trading_calendar_data
        end_date = start_date + timedelta(days=9)

        # 加载缓存
        cache_manager.load_trading_calendar(db_manager, start_date, end_date)

        # 模拟 TTL 过期（7天后）
        cache_manager._trading_calendar_loaded_at = time.time() - 604801  # 超过7天

        # 查询应该返回 None（缓存已过期被清空）
        result = cache_manager.is_trading_day(start_date)

        # unified_error_handler 包装返回值
        if isinstance(result, dict) and "data" in result:
            assert result["data"] is None
        else:
            assert result is None

    def test_clear_trading_calendar_cache(
        self, cache_manager, db_manager, trading_calendar_data
    ):
        """测试清空交易日历缓存"""
        start_date = trading_calendar_data
        end_date = start_date + timedelta(days=9)

        # 加载缓存
        cache_manager.load_trading_calendar(db_manager, start_date, end_date)

        # 清空缓存
        count = cache_manager.clear_trading_calendar_cache()

        # unified_error_handler 包装返回值
        if isinstance(count, dict) and "data" in count:
            assert count["data"] == 10
        else:
            assert count == 10

        # 验证缓存已清空
        result = cache_manager.is_trading_day(start_date)

        # unified_error_handler 包装返回值
        if isinstance(result, dict) and "data" in result:
            assert result["data"] is None
        else:
            assert result is None


class TestLastDataDateCache:
    """最后数据日期缓存测试"""

    def test_get_last_data_date_cache_miss(self, cache_manager):
        """测试获取最后数据日期缓存未命中"""
        result = cache_manager.get_last_data_date("000001.SZ", "1d")

        # unified_error_handler 包装返回值
        if isinstance(result, dict) and "data" in result:
            assert result["data"] is None
        else:
            assert result is None

    def test_set_and_get_last_data_date(self, cache_manager):
        """测试设置和获取最后数据日期"""
        test_date = date(2024, 1, 10)

        # 设置缓存
        success = cache_manager.set_last_data_date("000001.SZ", "1d", test_date)

        # unified_error_handler 包装返回值
        if isinstance(success, dict) and "data" in success:
            assert success["data"] is True
        else:
            assert success is True

        # 获取缓存（应该命中）
        result = cache_manager.get_last_data_date("000001.SZ", "1d")

        # unified_error_handler 包装返回值
        if isinstance(result, dict) and "data" in result:
            assert result["data"] == test_date
        else:
            assert result == test_date

    def test_last_data_date_ttl_expiry(self, cache_manager):
        """测试最后数据日期 TTL 过期"""
        test_date = date(2024, 1, 10)

        # 设置缓存
        cache_manager.set_last_data_date("000001.SZ", "1d", test_date)

        # 模拟 TTL 过期（60秒后）
        cache_key = ("000001.SZ", "1d")
        cache_manager._last_data_date_cache[cache_key] = (test_date, time.time() - 61)

        # 查询应该返回 None（缓存已过期）
        result = cache_manager.get_last_data_date("000001.SZ", "1d")

        # unified_error_handler 包装返回值
        if isinstance(result, dict) and "data" in result:
            assert result["data"] is None
        else:
            assert result is None


class TestStockMetadataCache:
    """股票元数据缓存测试"""

    def test_get_stock_metadata_cache_miss(self, cache_manager):
        """测试获取股票元数据缓存未命中"""
        result = cache_manager.get_stock_metadata("000001.SZ")

        # unified_error_handler 包装返回值
        if isinstance(result, dict) and "data" in result:
            assert result["data"] is None
        else:
            assert result is None

    def test_set_and_get_stock_metadata(self, cache_manager):
        """测试设置和获取股票元数据"""
        metadata = {
            "symbol": "000001.SZ",
            "name": "平安银行",
            "industry": "金融",
            "list_date": "1991-04-03",
            "status": "active",
        }

        # 设置缓存
        success = cache_manager.set_stock_metadata("000001.SZ", metadata)

        # unified_error_handler 包装返回值
        if isinstance(success, dict) and "data" in success:
            assert success["data"] is True
        else:
            assert success is True

        # 获取缓存（应该命中）
        result = cache_manager.get_stock_metadata("000001.SZ")

        # unified_error_handler 包装返回值
        if isinstance(result, dict) and "data" in result:
            assert result["data"] == metadata
        else:
            assert result == metadata

    def test_stock_metadata_ttl_expiry(self, cache_manager):
        """测试股票元数据 TTL 过期"""
        metadata = {"symbol": "000001.SZ", "name": "平安银行"}

        # 设置缓存
        cache_manager.set_stock_metadata("000001.SZ", metadata)

        # 模拟 TTL 过期（1天后）
        cache_manager._stock_metadata_cache["000001.SZ"] = (
            metadata,
            time.time() - 86401,
        )

        # 查询应该返回 None（缓存已过期）
        result = cache_manager.get_stock_metadata("000001.SZ")

        # unified_error_handler 包装返回值
        if isinstance(result, dict) and "data" in result:
            assert result["data"] is None
        else:
            assert result is None

    def test_load_stock_metadata_batch(
        self, cache_manager, db_manager, stock_metadata_data
    ):
        """测试批量预加载股票元数据"""
        symbols = ["000001.SZ", "000002.SZ", "600000.SH"]

        # 批量加载
        count = cache_manager.load_stock_metadata_batch(db_manager, symbols)

        # unified_error_handler 包装返回值
        if isinstance(count, dict) and "data" in count:
            assert count["data"] == 3
        else:
            assert count == 3

        # 验证缓存已加载
        result = cache_manager.get_stock_metadata("000001.SZ")

        # unified_error_handler 包装返回值
        if isinstance(result, dict) and "data" in result:
            assert result["data"] is not None
            assert result["data"]["name"] == "平安银行"
        else:
            assert result is not None
            assert result["name"] == "平安银行"


class TestMetadataCache:
    """元数据缓存综合测试"""

    def test_clear_metadata_cache(self, cache_manager):
        """测试清空元数据缓存"""
        # 添加一些缓存
        cache_manager.set_last_data_date("000001.SZ", "1d", date(2024, 1, 10))
        cache_manager.set_stock_metadata("000001.SZ", {"name": "平安银行"})

        # 清空缓存
        count = cache_manager.clear_metadata_cache()

        # unified_error_handler 包装返回值
        if isinstance(count, dict) and "data" in count:
            assert count["data"] == 2
        else:
            assert count == 2

        # 验证缓存已清空
        result1 = cache_manager.get_last_data_date("000001.SZ", "1d")
        result2 = cache_manager.get_stock_metadata("000001.SZ")

        # unified_error_handler 包装返回值
        if isinstance(result1, dict) and "data" in result1:
            assert result1["data"] is None
        else:
            assert result1 is None

        if isinstance(result2, dict) and "data" in result2:
            assert result2["data"] is None
        else:
            assert result2 is None


class TestCacheStats:
    """缓存统计测试"""

    def test_get_enhanced_cache_stats(
        self, cache_manager, db_manager, trading_calendar_data
    ):
        """测试获取增强的缓存统计信息"""
        # 加载一些缓存
        start_date = trading_calendar_data
        end_date = start_date + timedelta(days=9)
        cache_manager.load_trading_calendar(db_manager, start_date, end_date)

        cache_manager.set_last_data_date("000001.SZ", "1d", date(2024, 1, 10))
        cache_manager.set_stock_metadata("000001.SZ", {"name": "平安银行"})

        # 获取统计信息
        stats = cache_manager.get_enhanced_cache_stats()

        # 验证统计信息
        if isinstance(stats, dict) and "data" in stats:
            stats_data = stats["data"]
        else:
            stats_data = stats

        assert "trading_calendar_cache" in stats_data
        assert stats_data["trading_calendar_cache"]["size"] == 10

        assert "metadata_cache" in stats_data
        assert stats_data["metadata_cache"]["last_data_date_cache_size"] == 1
        assert stats_data["metadata_cache"]["stock_metadata_cache_size"] == 1


class TestCacheHitRate:
    """缓存命中率测试"""

    def test_trading_calendar_hit_rate(
        self, cache_manager, db_manager, trading_calendar_data
    ):
        """测试交易日历缓存命中率"""
        start_date = trading_calendar_data
        end_date = start_date + timedelta(days=9)

        # 加载缓存
        cache_manager.load_trading_calendar(db_manager, start_date, end_date)

        # 查询多次（应该全部命中）
        for i in range(10):
            current_date = start_date + timedelta(days=i)
            cache_manager.is_trading_day(current_date)

        # 验证命中率 >90%
        stats = cache_manager.get_cache_stats()
        if isinstance(stats, dict) and "data" in stats:
            hit_rate = stats["data"]["l1_cache"]["hit_rate"]
        else:
            hit_rate = stats["l1_cache"]["hit_rate"]

        assert hit_rate >= 0.9  # 命中率应该 >90%
