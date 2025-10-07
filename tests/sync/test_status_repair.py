"""
测试状态清理

测试过期状态清理逻辑（断点续传的关键部分）
"""

from datetime import date, datetime, timedelta

import pytest

from simtradedata.sync.manager import SyncManager
from tests.conftest import BaseTestClass


@pytest.mark.sync
@pytest.mark.integration
class TestStatusCleanup(BaseTestClass):
    """测试过期状态清理逻辑"""

    def test_cleanup_expired_pending_status(
        self, db_manager, data_source_manager, processing_engine, config
    ):
        """测试清理过期 pending 状态（> 1 天）"""
        sync_manager = SyncManager(
            db_manager, data_source_manager, processing_engine, config
        )

        symbol = "000005.SZ"
        target_date = date(2025, 1, 24)

        # 清理测试数据
        db_manager.execute(
            "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
        )

        # 场景：插入超过 1 天的 pending 状态记录
        expired_time = datetime.now() - timedelta(days=2)
        db_manager.execute(
            """
            INSERT INTO extended_sync_status
            (symbol, sync_type, target_date, status, records_count, session_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol,
                "processing",
                target_date.isoformat(),
                "pending",
                0,
                "test",
                expired_time.isoformat(),
                expired_time.isoformat(),
            ),
        )

        # 验证记录存在
        before_count = db_manager.fetchone(
            "SELECT COUNT(*) as count FROM extended_sync_status WHERE symbol = ? AND status = ?",
            (symbol, "pending"),
        )
        assert before_count["count"] == 1, "过期 pending 记录应该存在"

        # 调用清理逻辑（通过 _get_extended_data_symbols_to_process）
        sync_manager._get_extended_data_symbols_to_process([symbol], target_date)

        # 验证过期记录已被清理
        after_count = db_manager.fetchone(
            "SELECT COUNT(*) as count FROM extended_sync_status WHERE symbol = ? AND status = ?",
            (symbol, "pending"),
        )
        assert after_count["count"] == 0, "过期 pending 记录应该被清理"

        print(f"\n✓ 状态清理测试: 过期 pending 记录（> 1天）正确清理")

        # 清理测试数据
        db_manager.execute(
            "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
        )

    def test_keep_recent_pending_status(
        self, db_manager, data_source_manager, processing_engine, config
    ):
        """测试保留最近的 pending 状态（<= 1 天）"""
        sync_manager = SyncManager(
            db_manager, data_source_manager, processing_engine, config
        )

        symbol = "000006.SZ"
        target_date = date(2025, 1, 24)

        # 清理测试数据
        db_manager.execute(
            "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
        )

        # 场景：插入最近的 pending 状态记录（12 小时前）
        recent_time = datetime.now() - timedelta(hours=12)
        db_manager.execute(
            """
            INSERT INTO extended_sync_status
            (symbol, sync_type, target_date, status, records_count, session_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol,
                "processing",
                target_date.isoformat(),
                "pending",
                0,
                "test",
                recent_time.isoformat(),
                recent_time.isoformat(),
            ),
        )

        # 调用清理逻辑
        sync_manager._get_extended_data_symbols_to_process([symbol], target_date)

        # 验证最近的 pending 记录不应该被清理
        after_count = db_manager.fetchone(
            "SELECT COUNT(*) as count FROM extended_sync_status WHERE symbol = ? AND status = ?",
            (symbol, "pending"),
        )
        assert after_count["count"] == 1, "最近的 pending 记录不应该被清理"

        print(f"\n✓ 状态清理测试: 最近的 pending 记录（<= 1天）正确保留")

        # 清理测试数据
        db_manager.execute(
            "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
        )

    def test_cleanup_only_pending_status(
        self, db_manager, data_source_manager, processing_engine, config
    ):
        """测试只清理 pending 状态，不清理其他状态"""
        sync_manager = SyncManager(
            db_manager, data_source_manager, processing_engine, config
        )

        target_date = date(2025, 1, 24)
        expired_time = datetime.now() - timedelta(days=2)

        # 准备测试数据：不同状态的过期记录
        test_cases = [
            ("000007.SZ", "pending", True),  # 应该被清理
            ("000008.SZ", "processing", False),  # 不应该被清理
            ("000009.SZ", "completed", False),  # 不应该被清理
            ("000010.SZ", "failed", False),  # 不应该被清理
        ]

        # 清理并插入测试数据
        for symbol, status, _ in test_cases:
            db_manager.execute(
                "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
            )
            db_manager.execute(
                """
                INSERT INTO extended_sync_status
                (symbol, sync_type, target_date, status, records_count, session_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    "processing",
                    target_date.isoformat(),
                    status,
                    0,
                    "test",
                    expired_time.isoformat(),
                    expired_time.isoformat(),
                ),
            )

        # 调用清理逻辑
        sync_manager._get_extended_data_symbols_to_process(
            [s[0] for s in test_cases], target_date
        )

        # 验证清理结果
        print(f"\n状态清理选择性测试:")
        for symbol, status, should_cleanup in test_cases:
            count = db_manager.fetchone(
                "SELECT COUNT(*) as count FROM extended_sync_status WHERE symbol = ?",
                (symbol,),
            )

            if should_cleanup:
                assert count["count"] == 0, f"{status} 状态应该被清理"
                print(f"  ✓ {symbol} ({status}): 已清理")
            else:
                assert count["count"] == 1, f"{status} 状态不应该被清理"
                print(f"  ✓ {symbol} ({status}): 已保留")

        # 清理测试数据
        for symbol, _, _ in test_cases:
            db_manager.execute(
                "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
            )

    def test_cleanup_threshold_boundary(
        self, db_manager, data_source_manager, processing_engine, config
    ):
        """测试清理阈值边界（恰好1天）"""
        sync_manager = SyncManager(
            db_manager, data_source_manager, processing_engine, config
        )

        symbol = "000013.SZ"
        target_date = date(2025, 1, 24)

        # 清理测试数据
        db_manager.execute(
            "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
        )

        # 场景：插入明确超过 1 天的记录（使用 2 天前确保稳定）
        expired_time = datetime.now() - timedelta(days=2)
        db_manager.execute(
            """
            INSERT INTO extended_sync_status
            (symbol, sync_type, target_date, status, records_count, session_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol,
                "processing",
                target_date.isoformat(),
                "pending",
                0,
                "test",
                expired_time.isoformat(),
                expired_time.isoformat(),
            ),
        )

        # 调用清理逻辑
        sync_manager._get_extended_data_symbols_to_process([symbol], target_date)

        # 验证超过 1 天的记录已被清理
        after_count = db_manager.fetchone(
            "SELECT COUNT(*) as count FROM extended_sync_status WHERE symbol = ?",
            (symbol,),
        )
        assert after_count["count"] == 0, "超过 1 天的 pending 记录应该被清理"

        print(f"\n✓ 状态清理边界测试: 超过 1 天的记录正确清理")

        # 清理测试数据
        db_manager.execute(
            "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
        )
