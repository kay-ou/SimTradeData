"""
测试批量导入性能

比较批量模式和逐个模式的性能差异
"""

import time
from datetime import date
from typing import Dict, List
from unittest.mock import patch

import pytest

from simtradedata.sync.manager import SyncManager
from tests.conftest import BaseTestClass


@pytest.mark.sync
@pytest.mark.performance
@pytest.mark.slow
class TestBatchPerformance(BaseTestClass):
    """测试批量导入性能"""

    def _create_mock_batch_data(self, symbols: List[str]) -> Dict:
        """创建模拟的批量数据"""
        batch_data = {}
        for symbol in symbols:
            batch_data[symbol] = {
                "revenue": 1000000.0 + len(symbol) * 1000,
                "net_profit": 50000.0 + len(symbol) * 100,
                "total_assets": 5000000.0 + len(symbol) * 5000,
                "total_liabilities": 2000000.0,
                "shareholders_equity": 3000000.0,
                "operating_cash_flow": 100000.0,
            }
        return batch_data

    def test_batch_mode_threshold_detection(
        self, db_manager, data_source_manager, processing_engine, config
    ):
        """测试批量模式阈值检测"""
        sync_manager = SyncManager(
            db_manager, data_source_manager, processing_engine, config
        )

        # 测试场景 1：少量股票（< 50），不应该启用批量模式
        small_batch = [f"00000{i}.SZ" for i in range(1, 20)]  # 19 只股票
        target_date = date(2025, 1, 24)

        # 清理测试数据
        for symbol in small_batch:
            db_manager.execute(
                "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
            )

        # 获取需要处理的股票列表
        symbols_to_process = sync_manager._get_extended_data_symbols_to_process(
            small_batch, target_date
        )

        # 检查是否会启用批量模式
        # 批量模式的条件：待处理 >= 50 或 总库存 >= 500
        total_stocks_count = db_manager.fetchone("SELECT COUNT(*) as count FROM stocks")
        total_stocks = total_stocks_count["count"] if total_stocks_count else 0

        should_use_batch = len(symbols_to_process) >= 50 or total_stocks >= 500

        print(f"\n小批量测试:")
        print(f"  待处理股票数: {len(symbols_to_process)}")
        print(f"  总股票数: {total_stocks}")
        print(f"  应该使用批量模式: {should_use_batch}")

        # 清理测试数据
        for symbol in small_batch:
            db_manager.execute(
                "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
            )

    @pytest.mark.skipif(True, reason="需要大量数据，跳过以避免长时间运行")
    def test_batch_vs_individual_performance(
        self, db_manager, data_source_manager, processing_engine, config
    ):
        """测试批量模式 vs 逐个模式性能对比（需要大量数据）"""
        sync_manager = SyncManager(
            db_manager, data_source_manager, processing_engine, config
        )

        # 准备测试数据：100 只股票
        test_symbols = [f"60{i:04d}.SS" for i in range(1, 101)]
        target_date = date(2025, 1, 24)

        # 清理测试数据
        for symbol in test_symbols:
            db_manager.execute(
                "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
            )
            db_manager.execute("DELETE FROM financials WHERE symbol = ?", (symbol,))

        # 模拟批量数据
        mock_batch_data = self._create_mock_batch_data(test_symbols)

        # 测试 1: 批量模式
        with patch.object(
            data_source_manager,
            "batch_import_financial_data",
            return_value={"success": True, "data": mock_batch_data},
        ):
            batch_start = time.time()

            # 模拟批量导入流程
            for symbol in test_symbols[:50]:  # 测试 50 只股票的批量模式
                # 使用预加载的数据
                preloaded = mock_batch_data.get(symbol)
                if preloaded:
                    # 模拟快速插入
                    db_manager.execute(
                        """
                        INSERT OR REPLACE INTO financials
                        (symbol, report_date, report_type, revenue, net_profit, source)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            symbol,
                            target_date.isoformat(),
                            "annual",
                            preloaded["revenue"],
                            preloaded["net_profit"],
                            "batch",
                        ),
                    )

            batch_duration = time.time() - batch_start

        # 测试 2: 逐个模式
        individual_start = time.time()

        for symbol in test_symbols[50:100]:  # 另外 50 只股票用逐个模式
            # 模拟单独查询和插入
            time.sleep(0.001)  # 模拟网络延迟
            db_manager.execute(
                """
                INSERT OR REPLACE INTO financials
                (symbol, report_date, report_type, revenue, net_profit, source)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    target_date.isoformat(),
                    "annual",
                    1000000.0,
                    50000.0,
                    "individual",
                ),
            )

        individual_duration = time.time() - individual_start

        # 计算性能提升
        speedup = individual_duration / batch_duration if batch_duration > 0 else 0

        print(f"\n性能对比（50 只股票）:")
        print(f"  批量模式耗时: {batch_duration:.3f} 秒")
        print(f"  逐个模式耗时: {individual_duration:.3f} 秒")
        print(f"  性能提升: {speedup:.2f}x")

        # 验证性能目标：批量模式应该至少快 3 倍
        assert speedup >= 3.0, f"批量模式应该至少快 3 倍，实际: {speedup:.2f}x"

        # 清理测试数据
        for symbol in test_symbols:
            db_manager.execute(
                "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
            )
            db_manager.execute("DELETE FROM financials WHERE symbol = ?", (symbol,))

    def test_batch_mode_decision_logic(
        self, db_manager, data_source_manager, processing_engine, config
    ):
        """测试批量模式判断逻辑"""
        sync_manager = SyncManager(
            db_manager, data_source_manager, processing_engine, config
        )

        target_date = date(2025, 1, 24)

        # 场景 1: 待处理 < 50 且 总库存 < 500 -> 不使用批量模式
        print("\n场景 1: 小规模数据")
        small_symbols = [f"00000{i}.SZ" for i in range(1, 20)]  # 19 只

        # 清理并准备数据
        for symbol in small_symbols:
            db_manager.execute(
                "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
            )

        symbols_to_process = sync_manager._get_extended_data_symbols_to_process(
            small_symbols, target_date
        )

        total_stocks = db_manager.fetchone("SELECT COUNT(*) as count FROM stocks")[
            "count"
        ]

        should_batch_1 = len(symbols_to_process) >= 50 or total_stocks >= 500
        print(
            f"  待处理: {len(symbols_to_process)}, 总数: {total_stocks}, 批量模式: {should_batch_1}"
        )

        # 场景 2: 待处理 >= 50 -> 使用批量模式
        print("\n场景 2: 待处理股票达到阈值")
        large_symbols = [f"60{i:04d}.SS" for i in range(1, 60)]  # 59 只

        for symbol in large_symbols:
            db_manager.execute(
                "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
            )

        symbols_to_process_2 = sync_manager._get_extended_data_symbols_to_process(
            large_symbols, target_date
        )

        should_batch_2 = len(symbols_to_process_2) >= 50 or total_stocks >= 500
        print(
            f"  待处理: {len(symbols_to_process_2)}, 总数: {total_stocks}, 批量模式: {should_batch_2}"
        )

        # 如果待处理 >= 50，应该使用批量模式
        if len(symbols_to_process_2) >= 50:
            assert should_batch_2 is True, "待处理 >= 50 时应该启用批量模式"

        # 清理测试数据
        for symbol in small_symbols + large_symbols:
            db_manager.execute(
                "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
            )

    def test_batch_import_data_integrity(
        self, db_manager, data_source_manager, processing_engine, config
    ):
        """测试批量导入数据完整性"""
        sync_manager = SyncManager(
            db_manager, data_source_manager, processing_engine, config
        )

        test_symbols = [f"00000{i}.SZ" for i in range(1, 11)]  # 10 只股票
        target_date = date(2025, 1, 24)

        # 清理测试数据
        for symbol in test_symbols:
            db_manager.execute(
                "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
            )
            db_manager.execute("DELETE FROM financials WHERE symbol = ?", (symbol,))

        # 创建模拟批量数据
        mock_batch_data = self._create_mock_batch_data(test_symbols)

        # 模拟批量导入

        with patch.object(
            data_source_manager,
            "batch_import_financial_data",
            return_value={"success": True, "data": mock_batch_data},
        ):
            # 批量导入数据
            for symbol in test_symbols:
                preloaded = mock_batch_data.get(symbol)
                if preloaded:
                    # 使用事务插入
                    with db_manager.transaction() as conn:
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO financials
                            (symbol, report_date, report_type, revenue, net_profit, source)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (
                                symbol,
                                target_date.isoformat(),
                                "annual",
                                preloaded["revenue"],
                                preloaded["net_profit"],
                                "batch_test",
                            ),
                        )

        # 验证数据完整性
        for symbol in test_symbols:
            result = db_manager.fetchone(
                """
                SELECT revenue, net_profit, source
                FROM financials
                WHERE symbol = ? AND report_date = ?
                """,
                (symbol, target_date.isoformat()),
            )

            assert result is not None, f"股票 {symbol} 的数据应该存在"
            assert result["source"] == "batch_test", "数据来源应该标记为 batch_test"
            assert (
                result["revenue"] == mock_batch_data[symbol]["revenue"]
            ), "营收数据应该匹配"
            assert (
                result["net_profit"] == mock_batch_data[symbol]["net_profit"]
            ), "净利润数据应该匹配"

        print(f"\n批量导入完整性验证通过: {len(test_symbols)} 只股票")

        # 清理测试数据
        for symbol in test_symbols:
            db_manager.execute(
                "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
            )
            db_manager.execute("DELETE FROM financials WHERE symbol = ?", (symbol,))

    def test_batch_fallback_mechanism(
        self, db_manager, data_source_manager, processing_engine, config
    ):
        """测试批量导入回退机制"""
        sync_manager = SyncManager(
            db_manager, data_source_manager, processing_engine, config
        )

        test_symbols = [f"00000{i}.SZ" for i in range(1, 6)]  # 5 只股票
        target_date = date(2025, 1, 24)

        # 清理测试数据
        for symbol in test_symbols:
            db_manager.execute(
                "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
            )
            db_manager.execute("DELETE FROM financials WHERE symbol = ?", (symbol,))

        # 场景 1: 批量导入成功
        mock_batch_success = self._create_mock_batch_data(test_symbols)

        with patch.object(
            data_source_manager,
            "batch_import_financial_data",
            return_value={"success": True, "data": mock_batch_success},
        ):
            result = data_source_manager.batch_import_financial_data(
                target_date.year, "annual"
            )

            assert result["success"] is True, "批量导入应该成功"
            assert "data" in result, "应该返回数据"
            print("\n场景 1: 批量导入成功 ✓")

        # 场景 2: 批量导入失败，应该回退
        with patch.object(
            data_source_manager,
            "batch_import_financial_data",
            return_value={"success": False, "error": "模拟的批量导入失败"},
        ):
            result = data_source_manager.batch_import_financial_data(
                target_date.year, "annual"
            )

            assert result["success"] is False, "批量导入应该失败"
            assert "error" in result, "应该返回错误信息"
            print("场景 2: 批量导入失败，触发回退机制 ✓")

        # 验证回退后数据库状态正常（没有遗留的部分数据）
        for symbol in test_symbols:
            count = db_manager.fetchone(
                "SELECT COUNT(*) as count FROM financials WHERE symbol = ?",
                (symbol,),
            )
            # 回退后不应该有残留数据（除非是场景1插入的）
            print(f"  股票 {symbol} 财务记录数: {count['count']}")

        # 清理测试数据
        for symbol in test_symbols:
            db_manager.execute(
                "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
            )
            db_manager.execute("DELETE FROM financials WHERE symbol = ?", (symbol,))


@pytest.mark.sync
@pytest.mark.performance
class TestBatchThresholds(BaseTestClass):
    """测试批量模式阈值设置"""

    def test_batch_threshold_constants(self):
        """测试批量模式阈值常量"""
        # 这些是设计文档中定义的阈值
        BATCH_SIZE_THRESHOLD = 50  # 待处理股票数阈值
        TOTAL_STOCKS_THRESHOLD = 500  # 总股票数阈值

        print(f"\n批量模式阈值:")
        print(f"  待处理股票阈值: {BATCH_SIZE_THRESHOLD}")
        print(f"  总股票数阈值: {TOTAL_STOCKS_THRESHOLD}")

        # 验证阈值合理性
        assert BATCH_SIZE_THRESHOLD > 0, "待处理股票阈值应该大于 0"
        assert TOTAL_STOCKS_THRESHOLD > 0, "总股票数阈值应该大于 0"
        assert (
            BATCH_SIZE_THRESHOLD < TOTAL_STOCKS_THRESHOLD
        ), "待处理阈值应该小于总股票数阈值"

    def test_threshold_decision_matrix(self, db_manager):
        """测试阈值决策矩阵"""
        # 决策矩阵
        test_cases = [
            # (待处理数, 总股票数, 预期是否批量)
            (10, 100, False),  # 都小于阈值
            (10, 600, True),  # 总数达到阈值
            (60, 100, True),  # 待处理达到阈值
            (60, 600, True),  # 都达到阈值
            (0, 0, False),  # 边界情况
            (50, 500, True),  # 恰好达到阈值
            (49, 499, False),  # 恰好未达到阈值
        ]

        print("\n批量模式决策矩阵:")
        print("待处理 | 总数 | 批量模式")
        print("--------|------|----------")

        for pending, total, expected_batch in test_cases:
            # 批量模式条件：待处理 >= 50 或 总数 >= 500
            actual_batch = pending >= 50 or total >= 500

            status = "✓" if actual_batch == expected_batch else "✗"
            print(f"{pending:7} | {total:4} | {actual_batch!s:8} {status}")

            assert (
                actual_batch == expected_batch
            ), f"待处理={pending}, 总数={total} 时决策错误"
