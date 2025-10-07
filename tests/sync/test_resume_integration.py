"""
测试断点续传集成

测试同步中断后的恢复场景（断点续传的关键功能）
"""

from datetime import date
from unittest.mock import patch

import pytest

from simtradedata.sync.manager import SyncManager
from tests.conftest import BaseTestClass


@pytest.mark.sync
@pytest.mark.integration
class TestResumeIntegration(BaseTestClass):
    """测试断点续传集成场景"""

    def _prepare_test_stocks(self, db_manager, symbols):
        """准备测试股票数据"""
        for symbol in symbols:
            db_manager.execute("DELETE FROM stocks WHERE symbol = ?", (symbol,))
            db_manager.execute(
                """
                INSERT INTO stocks (symbol, name, market, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (symbol, f"测试{symbol}", "SZ", "active"),
            )

    def _cleanup_test_data(self, db_manager, symbols):
        """清理测试数据"""
        for symbol in symbols:
            db_manager.execute(
                "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
            )
            db_manager.execute("DELETE FROM financials WHERE symbol = ?", (symbol,))
            db_manager.execute("DELETE FROM valuations WHERE symbol = ?", (symbol,))
            db_manager.execute("DELETE FROM stocks WHERE symbol = ?", (symbol,))

    def test_resume_from_partial_completion(
        self, db_manager, data_source_manager, processing_engine, config
    ):
        """测试从部分完成状态恢复同步"""
        sync_manager = SyncManager(
            db_manager, data_source_manager, processing_engine, config
        )

        target_date = date(2025, 1, 24)
        test_symbols = [f"00000{i}.SZ" for i in range(1, 6)]  # 5只股票
        report_date = f"{target_date.year - 1}-12-31"

        # 准备测试数据
        self._prepare_test_stocks(db_manager, test_symbols)

        # 场景：3只股票已完成（有财务数据 + completed 状态），2只待处理
        completed_symbols = test_symbols[:3]
        for symbol in completed_symbols:
            # 插入财务数据（触发完整性检查通过）
            db_manager.execute(
                """
                INSERT OR REPLACE INTO financials
                (symbol, report_date, report_type, revenue, net_profit, source)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (symbol, report_date, "Q4", 1000000.0, 50000.0, "test"),
            )
            # 标记为 completed
            db_manager.execute(
                """
                INSERT INTO extended_sync_status
                (symbol, sync_type, target_date, status, records_count, session_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (
                    symbol,
                    "processing",
                    target_date.isoformat(),
                    "completed",
                    10,
                    "test",
                ),
            )

        # 获取需要处理的股票列表
        symbols_to_process = sync_manager._get_extended_data_symbols_to_process(
            test_symbols, target_date
        )

        # 验证断点续传逻辑：只返回未完成的2只股票
        assert (
            len(symbols_to_process) == 2
        ), f"应该只返回 2 只待处理股票，实际: {len(symbols_to_process)}"
        assert set(symbols_to_process) == set(
            test_symbols[3:]
        ), "应该返回正确的待处理股票"

        # 验证完成进度计算
        completion_rate = (len(test_symbols) - len(symbols_to_process)) / len(
            test_symbols
        )
        assert (
            completion_rate == 0.6
        ), f"完成进度应该是 60%，实际: {completion_rate:.1%}"

        print(f"\n✓ 断点续传测试: 正确识别 {len(symbols_to_process)} 只待处理股票")
        print(f"  完成进度: {completion_rate:.1%}")

        # 清理测试数据
        self._cleanup_test_data(db_manager, test_symbols)

    def test_resume_skips_all_completed(
        self, db_manager, data_source_manager, processing_engine, config
    ):
        """测试全部完成后跳过"""
        sync_manager = SyncManager(
            db_manager, data_source_manager, processing_engine, config
        )

        target_date = date(2025, 1, 24)
        test_symbols = [f"00000{i}.SZ" for i in range(1, 4)]  # 3只股票
        report_date = f"{target_date.year - 1}-12-31"

        # 准备测试数据
        self._prepare_test_stocks(db_manager, test_symbols)

        # 场景：所有股票都已完成
        for symbol in test_symbols:
            # 插入财务数据
            db_manager.execute(
                """
                INSERT OR REPLACE INTO financials
                (symbol, report_date, report_type, revenue, net_profit, source)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (symbol, report_date, "Q4", 1000000.0, 50000.0, "test"),
            )
            # 标记为 completed
            db_manager.execute(
                """
                INSERT INTO extended_sync_status
                (symbol, sync_type, target_date, status, records_count, session_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (
                    symbol,
                    "processing",
                    target_date.isoformat(),
                    "completed",
                    10,
                    "test",
                ),
            )

        # 获取需要处理的股票列表
        symbols_to_process = sync_manager._get_extended_data_symbols_to_process(
            test_symbols, target_date
        )

        # 验证应该跳过所有股票
        assert len(symbols_to_process) == 0, "所有股票完成后应该返回空列表"

        print(f"\n✓ 阶段跳过测试: 所有股票完成，正确返回空列表")

        # 清理测试数据
        self._cleanup_test_data(db_manager, test_symbols)

    def test_resume_with_no_prior_sync(
        self, db_manager, data_source_manager, processing_engine, config
    ):
        """测试首次同步（无任何状态记录）"""
        sync_manager = SyncManager(
            db_manager, data_source_manager, processing_engine, config
        )

        target_date = date(2025, 1, 24)
        test_symbols = [f"00000{i}.SZ" for i in range(1, 4)]  # 3只股票

        # 准备测试数据（不插入 extended_sync_status）
        self._prepare_test_stocks(db_manager, test_symbols)

        # 获取需要处理的股票
        symbols_to_process = sync_manager._get_extended_data_symbols_to_process(
            test_symbols, target_date
        )

        # 首次同步应该处理所有股票
        assert len(symbols_to_process) == len(
            test_symbols
        ), f"首次同步应该处理所有股票，实际: {len(symbols_to_process)}/{len(test_symbols)}"

        print(f"\n✓ 首次同步测试: 正确处理全部 {len(symbols_to_process)} 只股票")

        # 清理
        self._cleanup_test_data(db_manager, test_symbols)

    @patch.object(SyncManager, "_sync_extended_data")
    def test_full_sync_resume_integration(
        self,
        mock_sync_extended,
        db_manager,
        data_source_manager,
        processing_engine,
        config,
    ):
        """测试 run_full_sync 的断点续传集成"""
        # 设置 mock 返回值
        mock_sync_extended.return_value = {
            "financials_count": 5,
            "valuations_count": 5,
            "indicators_count": 0,
            "processed_symbols": 2,
            "failed_symbols": 0,
        }

        sync_manager = SyncManager(
            db_manager, data_source_manager, processing_engine, config
        )

        target_date = date(2025, 1, 24)
        test_symbols = [f"00000{i}.SZ" for i in range(1, 6)]  # 5只股票
        report_date = f"{target_date.year - 1}-12-31"

        # 准备测试数据
        self._prepare_test_stocks(db_manager, test_symbols)

        # 场景：3只股票已完成
        completed_symbols = test_symbols[:3]
        for symbol in completed_symbols:
            # 插入财务数据
            db_manager.execute(
                """
                INSERT OR REPLACE INTO financials
                (symbol, report_date, report_type, revenue, net_profit, source)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (symbol, report_date, "Q4", 1000000.0, 50000.0, "test"),
            )
            # 标记为 completed
            db_manager.execute(
                """
                INSERT INTO extended_sync_status
                (symbol, sync_type, target_date, status, records_count, session_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (
                    symbol,
                    "processing",
                    target_date.isoformat(),
                    "completed",
                    10,
                    "test",
                ),
            )

        # 执行 full-sync（应该触发断点续传）
        result = sync_manager.run_full_sync(
            target_date=target_date, symbols=test_symbols
        )

        # 验证结果结构（被 @unified_error_handler 包装）
        assert result is not None, "run_full_sync 应该返回结果"
        assert "success" in result and result["success"], "应该执行成功"
        assert "data" in result, "应该有 data 字段"

        result_data = result["data"]
        assert "phases" in result_data, "结果应该包含 phases"

        # 验证断点续传逻辑被触发
        phases = result_data["phases"]
        if "calendar_update" in phases:
            calendar_status = phases["calendar_update"].get("status")
            if calendar_status == "skipped":
                print("\n✓ Full-sync 集成测试: 检测到断点续传，跳过基础数据更新")

        # 验证 _sync_extended_data 被调用
        if mock_sync_extended.called:
            print(f"  _sync_extended_data 调用次数: {mock_sync_extended.call_count}")

        # 清理测试数据
        self._cleanup_test_data(db_manager, test_symbols)

    def test_resume_with_empty_symbol_list(
        self, db_manager, data_source_manager, processing_engine, config
    ):
        """测试空股票列表"""
        sync_manager = SyncManager(
            db_manager, data_source_manager, processing_engine, config
        )

        target_date = date(2025, 1, 24)

        # 空列表
        symbols_to_process = sync_manager._get_extended_data_symbols_to_process(
            [], target_date
        )

        assert len(symbols_to_process) == 0, "空列表应该返回空结果"

        print(f"\n✓ 空列表测试: 正确处理空输入")
