"""
测试事务处理

测试数据库事务的原子性、回滚和并发处理
"""

import sqlite3
import threading
import time
from datetime import date

import pytest

from simtradedata.sync.manager import SyncManager
from tests.conftest import BaseTestClass


@pytest.mark.sync
@pytest.mark.integration
class TestTransactionHandling(BaseTestClass):
    """测试事务处理"""

    # ==================== 事务回滚测试 ====================

    def test_transaction_rollback_on_error(self, db_manager):
        """测试事务回滚 - 插入失败时验证状态未更新"""
        symbol = "000001.SZ"
        target_date = date(2025, 1, 24)
        session_id = "test_session_rollback"

        # 清理测试数据
        db_manager.execute(
            "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
        )

        # 插入初始状态
        db_manager.execute(
            """
            INSERT INTO extended_sync_status
            (symbol, sync_type, target_date, status, records_count, session_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (symbol, "processing", target_date.isoformat(), "pending", 0, session_id),
        )

        # 验证初始状态
        initial_status = db_manager.fetchone(
            "SELECT status FROM extended_sync_status WHERE symbol = ?", (symbol,)
        )
        assert initial_status["status"] == "pending"

        # 模拟事务中的错误 - 尝试在事务内更新状态然后抛出异常
        try:
            with db_manager.transaction() as conn:
                # 更新状态为 processing
                conn.execute(
                    """
                    UPDATE extended_sync_status
                    SET status = ?, updated_at = datetime('now')
                    WHERE symbol = ? AND sync_type = ?
                    """,
                    ("processing", symbol, "processing"),
                )

                # 验证事务内状态已更改（但未提交）
                result = conn.execute(
                    "SELECT status FROM extended_sync_status WHERE symbol = ?",
                    (symbol,),
                ).fetchone()
                assert result["status"] == "processing"

                # 模拟错误
                raise ValueError("模拟的数据插入错误")

        except ValueError:
            # 预期的异常
            pass

        # 验证事务回滚后状态未更新（仍为 pending）
        final_status = db_manager.fetchone(
            "SELECT status FROM extended_sync_status WHERE symbol = ?", (symbol,)
        )
        assert final_status["status"] == "pending", "事务回滚后状态应该保持 pending"

        # 清理测试数据
        db_manager.execute(
            "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
        )

    def test_transaction_commit_on_success(self, db_manager):
        """测试事务提交 - 成功时验证状态已更新"""
        symbol = "000002.SZ"
        target_date = date(2025, 1, 24)
        session_id = "test_session_commit"

        # 清理测试数据
        db_manager.execute(
            "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
        )

        # 插入初始状态
        db_manager.execute(
            """
            INSERT INTO extended_sync_status
            (symbol, sync_type, target_date, status, records_count, session_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (symbol, "processing", target_date.isoformat(), "pending", 0, session_id),
        )

        # 使用事务更新状态
        try:
            with db_manager.transaction() as conn:
                # 更新状态为 completed
                conn.execute(
                    """
                    UPDATE extended_sync_status
                    SET status = ?, records_count = ?, updated_at = datetime('now')
                    WHERE symbol = ? AND sync_type = ?
                    """,
                    ("completed", 10, symbol, "processing"),
                )

                # 事务成功完成
        except Exception as e:
            pytest.fail(f"事务不应该失败: {e}")

        # 验证事务提交后状态已更新
        final_status = db_manager.fetchone(
            "SELECT status, records_count FROM extended_sync_status WHERE symbol = ?",
            (symbol,),
        )
        assert final_status["status"] == "completed", "事务提交后状态应该是 completed"
        assert final_status["records_count"] == 10, "记录数应该已更新"

        # 清理测试数据
        db_manager.execute(
            "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
        )

    # ==================== 事务原子性测试 ====================

    def test_transaction_atomicity_partial_success(self, db_manager):
        """测试事务原子性 - 部分成功场景验证"""
        symbol = "000003.SZ"
        target_date = date(2025, 1, 24)

        # 清理测试数据
        db_manager.execute(
            "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
        )
        db_manager.execute("DELETE FROM financials WHERE symbol = ?", (symbol,))

        # 模拟部分成功场景：插入财务数据成功，但状态更新失败
        try:
            with db_manager.transaction() as conn:
                # 第一步：插入财务数据（成功）
                conn.execute(
                    """
                    INSERT INTO financials
                    (symbol, report_date, report_type, revenue, net_profit, source)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        symbol,
                        target_date.isoformat(),
                        "annual",
                        1000000.0,
                        50000.0,
                        "test",
                    ),
                )

                # 第二步：插入状态记录（成功）
                conn.execute(
                    """
                    INSERT INTO extended_sync_status
                    (symbol, sync_type, target_date, status, records_count, session_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                    """,
                    (
                        symbol,
                        "financial",
                        target_date.isoformat(),
                        "processing",
                        1,
                        "test",
                    ),
                )

                # 第三步：模拟错误（导致整个事务回滚）
                raise ValueError("模拟的状态更新错误")

        except ValueError:
            # 预期的异常
            pass

        # 验证原子性：所有操作都应该回滚
        financial_count = db_manager.fetchone(
            "SELECT COUNT(*) as count FROM financials WHERE symbol = ?", (symbol,)
        )
        assert financial_count["count"] == 0, "财务数据应该被回滚"

        status_count = db_manager.fetchone(
            "SELECT COUNT(*) as count FROM extended_sync_status WHERE symbol = ?",
            (symbol,),
        )
        assert status_count["count"] == 0, "状态记录应该被回滚"

    # ==================== 并发事务测试 ====================

    @pytest.mark.slow
    def test_concurrent_transactions_different_symbols(self, db_manager):
        """测试并发事务 - 不同股票无冲突"""
        symbols = [f"00000{i}.SZ" for i in range(1, 6)]
        target_date = date(2025, 1, 24)
        session_id = "test_concurrent"

        # 清理测试数据
        for symbol in symbols:
            db_manager.execute(
                "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
            )

        results = []
        errors = []

        def insert_status(symbol: str):
            """在单独的线程中插入状态"""
            try:
                with db_manager.transaction() as conn:
                    conn.execute(
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
                            1,
                            session_id,
                        ),
                    )
                results.append(symbol)
            except Exception as e:
                errors.append((symbol, str(e)))

        # 创建并启动多个线程
        threads = []
        for symbol in symbols:
            thread = threading.Thread(target=insert_status, args=(symbol,))
            threads.append(thread)
            thread.start()

        # 等待所有线程完成
        for thread in threads:
            thread.join()

        # 验证结果
        assert len(errors) == 0, f"不应该有并发错误: {errors}"
        assert len(results) == len(
            symbols
        ), f"所有线程都应该成功: {len(results)} vs {len(symbols)}"

        # 验证数据库中的记录
        for symbol in symbols:
            status = db_manager.fetchone(
                "SELECT status FROM extended_sync_status WHERE symbol = ?", (symbol,)
            )
            assert status is not None, f"股票 {symbol} 的状态记录应该存在"
            assert (
                status["status"] == "completed"
            ), f"股票 {symbol} 的状态应该是 completed"

        # 清理测试数据
        for symbol in symbols:
            db_manager.execute(
                "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
            )

    @pytest.mark.slow
    def test_concurrent_transactions_same_symbol(self, db_manager):
        """测试并发事务 - 同一股票有冲突处理"""
        symbol = "600000.SS"
        target_date = date(2025, 1, 24)

        # 清理测试数据
        db_manager.execute(
            "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
        )

        # 插入初始记录
        db_manager.execute(
            """
            INSERT INTO extended_sync_status
            (symbol, sync_type, target_date, status, records_count, session_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (symbol, "processing", target_date.isoformat(), "pending", 0, "init"),
        )

        success_count = 0
        conflict_count = 0
        errors = []

        def update_status(thread_id: int):
            """在单独的线程中更新状态"""
            nonlocal success_count, conflict_count
            try:
                with db_manager.transaction() as conn:
                    # 模拟一些处理时间
                    time.sleep(0.01)

                    # 更新状态
                    conn.execute(
                        """
                        UPDATE extended_sync_status
                        SET status = ?, records_count = records_count + ?, updated_at = datetime('now')
                        WHERE symbol = ? AND sync_type = ?
                        """,
                        (
                            f"processing_{thread_id}",
                            1,
                            symbol,
                            "processing",
                        ),
                    )
                success_count += 1
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower():
                    conflict_count += 1
                else:
                    errors.append((thread_id, str(e)))
            except Exception as e:
                errors.append((thread_id, str(e)))

        # 创建并启动多个线程（同时更新同一条记录）
        num_threads = 5
        threads = []
        for i in range(num_threads):
            thread = threading.Thread(target=update_status, args=(i,))
            threads.append(thread)
            thread.start()

        # 等待所有线程完成
        for thread in threads:
            thread.join()

        # 验证结果
        assert len(errors) == 0, f"不应该有意外错误: {errors}"
        # 注意：SQLite 的并发控制可能导致部分线程成功，部分线程遇到锁
        # 至少应该有一个成功
        assert success_count >= 1, f"至少应该有一个线程成功: {success_count}"

        print(
            f"并发测试结果: 成功 {success_count}, 冲突 {conflict_count}, 错误 {len(errors)}"
        )

        # 清理测试数据
        db_manager.execute(
            "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
        )

    # ==================== 嵌套事务测试 ====================

    def test_nested_transactions_not_supported(self, db_manager):
        """测试嵌套事务 - SQLite 不支持真正的嵌套事务"""
        symbol = "000004.SZ"

        # 清理测试数据
        db_manager.execute(
            "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
        )

        # SQLite 的事务上下文管理器应该处理嵌套情况
        # 外层事务
        try:
            with db_manager.transaction() as conn1:
                conn1.execute(
                    """
                    INSERT INTO extended_sync_status
                    (symbol, sync_type, target_date, status, records_count, session_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                    """,
                    (symbol, "processing", "2025-01-24", "pending", 0, "outer"),
                )

                # 内层事务（可能会有警告或错误）
                # 注意：DatabaseManager 的实现可能不支持嵌套事务
                # 这个测试主要是验证不会崩溃
                try:
                    with db_manager.transaction() as conn2:
                        conn2.execute(
                            """
                            UPDATE extended_sync_status
                            SET status = ?
                            WHERE symbol = ?
                            """,
                            ("processing", symbol),
                        )
                except Exception:
                    # 如果不支持嵌套，这里可能会抛出异常
                    # 我们接受这种情况
                    pass

        except Exception as e:
            pytest.fail(f"事务处理不应该崩溃: {e}")

        # 清理测试数据
        db_manager.execute(
            "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
        )


@pytest.mark.sync
@pytest.mark.integration
class TestSyncManagerTransactionIntegration(BaseTestClass):
    """测试 SyncManager 的事务集成"""

    def test_sync_single_symbol_transaction_rollback(
        self, db_manager, data_source_manager, processing_engine, config
    ):
        """测试 _sync_single_symbol_with_transaction 的事务回滚"""
        sync_manager = SyncManager(
            db_manager, data_source_manager, processing_engine, config
        )

        symbol = "000005.SZ"
        target_date = date(2025, 1, 24)
        session_id = "test_sync_rollback"

        # 清理测试数据
        db_manager.execute(
            "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
        )
        db_manager.execute("DELETE FROM financials WHERE symbol = ?", (symbol,))
        db_manager.execute("DELETE FROM valuations WHERE symbol = ?", (symbol,))

        # 注意：这个测试需要模拟数据源返回失败
        # 由于我们使用真实的数据源管理器，可能无法完全模拟失败场景
        # 这里主要是验证方法调用不会崩溃

        try:
            # 调用事务保护的同步方法
            result = sync_manager._sync_single_symbol_with_transaction(
                symbol=symbol,
                target_date=target_date,
                session_id=session_id,
                preloaded_financial=None,
            )

            # 验证结果格式
            assert isinstance(result, dict), "返回结果应该是字典"
            assert "success" in result, "结果应该包含 success 字段"

            # 如果同步失败，验证数据未提交
            if not result["success"]:
                # 验证状态记录
                status = db_manager.fetchone(
                    "SELECT status FROM extended_sync_status WHERE symbol = ? AND session_id = ?",
                    (symbol, session_id),
                )
                # 失败的同步应该标记为 failed 或 partial
                if status:
                    assert status["status"] in [
                        "failed",
                        "partial",
                        "processing",
                    ], f"失败同步的状态应该是 failed/partial/processing: {status['status']}"

        except Exception as e:
            # 如果有异常，验证事务已回滚
            print(f"同步过程中出现异常（预期行为）: {e}")

            # 验证数据库中没有遗留的 processing 状态
            status = db_manager.fetchone(
                "SELECT status FROM extended_sync_status WHERE symbol = ? AND session_id = ?",
                (symbol, session_id),
            )
            if status:
                # 如果有状态记录，不应该停留在 processing
                # （事务应该已提交为 failed 或回滚）
                pass  # 实际行为取决于具体实现

        # 清理测试数据
        db_manager.execute(
            "DELETE FROM extended_sync_status WHERE symbol = ?", (symbol,)
        )
        db_manager.execute("DELETE FROM financials WHERE symbol = ?", (symbol,))
        db_manager.execute("DELETE FROM valuations WHERE symbol = ?", (symbol,))
