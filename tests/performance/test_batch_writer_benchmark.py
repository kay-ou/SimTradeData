"""
BatchWriter 性能基准测试

对比批量写入和逐条写入的性能差异，验证批量写入优化效果。

测试场景:
1. 场景1: 逐条 INSERT 1000条 market_data 记录
2. 场景2: 批量 INSERT 1000条记录 (batch_size=100)
3. 场景3: 逐条 UPDATE 1000条记录
4. 场景4: 批量 UPDATE 1000条记录 (batch_size=100)

成功标准:
- 批量 INSERT 比逐条快 5-10倍
- 批量 UPDATE 比逐条快 5-10倍
"""

import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest

from simtradedata.database.batch_writer import BatchWriter
from simtradedata.database.manager import DatabaseManager


@pytest.fixture(scope="module")
def benchmark_db():
    """创建用于性能测试的临时数据库"""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = Path(f.name)

    manager = DatabaseManager(db_path=str(db_path))

    # 创建测试表
    manager.execute(
        """
        CREATE TABLE IF NOT EXISTS market_data (
            symbol TEXT,
            date TEXT,
            frequency TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            amount REAL,
            prev_close REAL,
            change_amount REAL,
            change_percent REAL,
            amplitude REAL,
            high_limit REAL,
            low_limit REAL,
            is_limit_up INTEGER,
            is_limit_down INTEGER,
            source TEXT,
            quality_score INTEGER,
            PRIMARY KEY (symbol, date, frequency)
        )
    """
    )

    yield manager

    # 清理
    manager.close()
    if db_path.exists():
        db_path.unlink()


def generate_test_data(count: int, symbol: str = "000001.SZ", start_date: date = None):
    """
    生成测试数据

    Args:
        count: 生成数据条数
        symbol: 股票代码
        start_date: 起始日期

    Returns:
        List[Dict]: 测试数据列表
    """
    if start_date is None:
        start_date = date(2024, 1, 1)

    data = []
    for i in range(count):
        current_date = start_date + timedelta(days=i)
        data.append(
            {
                "symbol": symbol,
                "date": current_date.strftime("%Y-%m-%d"),
                "frequency": "1d",
                "open": 10.0 + i * 0.01,
                "high": 10.2 + i * 0.01,
                "low": 9.8 + i * 0.01,
                "close": 10.1 + i * 0.01,
                "volume": 1000000 + i * 1000,
                "amount": 10000000.0 + i * 10000,
                "prev_close": None,
                "change_amount": 0.0,
                "change_percent": 0.0,
                "amplitude": 0.0,
                "high_limit": None,
                "low_limit": None,
                "is_limit_up": 0,
                "is_limit_down": 0,
                "source": "test",
                "quality_score": 100,
            }
        )
    return data


class TestBatchInsertPerformance:
    """批量 INSERT 性能测试"""

    def test_sequential_insert_1000_records(self, benchmark, benchmark_db):
        """场景1: 逐条 INSERT 1000条记录"""

        def sequential_insert():
            # 清空表
            benchmark_db.execute("DELETE FROM market_data")

            # 生成测试数据
            test_data = generate_test_data(1000, symbol="000001.SZ")

            # 逐条插入
            insert_sql = """
            INSERT INTO market_data (
                symbol, date, frequency, open, high, low, close, volume, amount,
                prev_close, change_amount, change_percent, amplitude,
                high_limit, low_limit, is_limit_up, is_limit_down, source, quality_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """

            for record in test_data:
                params = (
                    record["symbol"],
                    record["date"],
                    record["frequency"],
                    record["open"],
                    record["high"],
                    record["low"],
                    record["close"],
                    record["volume"],
                    record["amount"],
                    record["prev_close"],
                    record["change_amount"],
                    record["change_percent"],
                    record["amplitude"],
                    record["high_limit"],
                    record["low_limit"],
                    record["is_limit_up"],
                    record["is_limit_down"],
                    record["source"],
                    record["quality_score"],
                )
                benchmark_db.execute(insert_sql, params)

            # 验证数据
            count = benchmark_db.get_table_count("market_data")
            assert count == 1000

        # 运行基准测试（至少3次）
        benchmark.pedantic(sequential_insert, iterations=3, rounds=3)

    def test_batch_insert_1000_records(self, benchmark, benchmark_db):
        """场景2: 批量 INSERT 1000条记录 (batch_size=100)"""

        def batch_insert():
            # 清空表
            benchmark_db.execute("DELETE FROM market_data")

            # 生成测试数据
            test_data = generate_test_data(1000, symbol="000002.SZ")

            # 使用 BatchWriter 批量插入
            batch_writer = BatchWriter(benchmark_db, batch_size=100, auto_flush=True)

            for record in test_data:
                batch_writer.add_record("market_data", record)

            # 刷新剩余数据
            batch_writer.flush_all()

            # 验证数据
            count = benchmark_db.get_table_count("market_data")
            assert count == 1000

        # 运行基准测试（至少3次）
        benchmark.pedantic(batch_insert, iterations=3, rounds=3)


class TestBatchUpdatePerformance:
    """批量 UPDATE 性能测试"""

    def test_sequential_update_1000_records(self, benchmark, benchmark_db):
        """场景3: 逐条 UPDATE 1000条记录"""

        # 准备数据：先插入1000条记录
        benchmark_db.execute("DELETE FROM market_data")
        test_data = generate_test_data(1000, symbol="000003.SZ")

        batch_writer = BatchWriter(benchmark_db, batch_size=100)
        for record in test_data:
            batch_writer.add_record("market_data", record)
        batch_writer.flush_all()

        def sequential_update():
            # 逐条更新
            update_sql = """
            UPDATE market_data
            SET prev_close = ?, change_amount = ?, change_percent = ?, amplitude = ?
            WHERE symbol = ? AND date = ? AND frequency = ?
            """

            for i, record in enumerate(test_data):
                params = (
                    10.0 + i * 0.01,  # prev_close
                    0.1,  # change_amount
                    1.0,  # change_percent
                    2.0,  # amplitude
                    record["symbol"],
                    record["date"],
                    record["frequency"],
                )
                benchmark_db.execute(update_sql, params)

            # 验证更新
            result = benchmark_db.fetchone(
                "SELECT COUNT(*) as count FROM market_data WHERE prev_close IS NOT NULL"
            )
            assert result["count"] == 1000

        # 运行基准测试（至少3次）
        benchmark.pedantic(sequential_update, iterations=3, rounds=3)

    def test_batch_update_1000_records(self, benchmark, benchmark_db):
        """场景4: 批量 UPDATE 1000条记录 (batch_size=100)"""

        # 准备数据：先插入1000条记录
        benchmark_db.execute("DELETE FROM market_data")
        test_data = generate_test_data(1000, symbol="000004.SZ")

        batch_writer = BatchWriter(benchmark_db, batch_size=100)
        for record in test_data:
            batch_writer.add_record("market_data", record)
        batch_writer.flush_all()

        def batch_update():
            # 批量更新
            update_sql = """
            UPDATE market_data
            SET prev_close = ?, change_amount = ?, change_percent = ?, amplitude = ?
            WHERE symbol = ? AND date = ? AND frequency = ?
            """

            params_list = []
            for i, record in enumerate(test_data):
                params = (
                    10.0 + i * 0.01,  # prev_close
                    0.1,  # change_amount
                    1.0,  # change_percent
                    2.0,  # amplitude
                    record["symbol"],
                    record["date"],
                    record["frequency"],
                )
                params_list.append(params)

            # 使用 BatchWriter.execute_batch 批量执行
            batch_writer = BatchWriter(benchmark_db, batch_size=100)
            batch_writer.execute_batch(update_sql, params_list, use_transaction=True)

            # 验证更新
            result = benchmark_db.fetchone(
                "SELECT COUNT(*) as count FROM market_data WHERE prev_close IS NOT NULL"
            )
            assert result["count"] == 1000

        # 运行基准测试（至少3次）
        benchmark.pedantic(batch_update, iterations=3, rounds=3)


class TestPerformanceComparison:
    """性能对比和报告生成"""

    def test_performance_summary(self, benchmark_db):
        """生成性能对比总结报告"""

        print("\n" + "=" * 80)
        print("BatchWriter 性能基准测试总结")
        print("=" * 80)

        # 运行所有测试收集数据
        results = {}

        # 场景1: 逐条 INSERT
        benchmark_db.execute("DELETE FROM market_data")
        test_data = generate_test_data(1000, symbol="000001.SZ")

        import time

        start = time.time()
        insert_sql = """
        INSERT INTO market_data (
            symbol, date, frequency, open, high, low, close, volume, amount,
            prev_close, change_amount, change_percent, amplitude,
            high_limit, low_limit, is_limit_up, is_limit_down, source, quality_score
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        for record in test_data:
            params = (
                record["symbol"],
                record["date"],
                record["frequency"],
                record["open"],
                record["high"],
                record["low"],
                record["close"],
                record["volume"],
                record["amount"],
                record["prev_close"],
                record["change_amount"],
                record["change_percent"],
                record["amplitude"],
                record["high_limit"],
                record["low_limit"],
                record["is_limit_up"],
                record["is_limit_down"],
                record["source"],
                record["quality_score"],
            )
            benchmark_db.execute(insert_sql, params)
        results["sequential_insert"] = time.time() - start

        # 场景2: 批量 INSERT
        benchmark_db.execute("DELETE FROM market_data")
        test_data = generate_test_data(1000, symbol="000002.SZ")

        start = time.time()
        batch_writer = BatchWriter(benchmark_db, batch_size=100, auto_flush=True)
        for record in test_data:
            batch_writer.add_record("market_data", record)
        batch_writer.flush_all()
        results["batch_insert"] = time.time() - start

        # 场景3: 逐条 UPDATE
        benchmark_db.execute("DELETE FROM market_data")
        test_data = generate_test_data(1000, symbol="000003.SZ")
        batch_writer = BatchWriter(benchmark_db, batch_size=100)
        for record in test_data:
            batch_writer.add_record("market_data", record)
        batch_writer.flush_all()

        update_sql = """
        UPDATE market_data
        SET prev_close = ?, change_amount = ?, change_percent = ?, amplitude = ?
        WHERE symbol = ? AND date = ? AND frequency = ?
        """

        start = time.time()
        for i, record in enumerate(test_data):
            params = (
                10.0 + i * 0.01,
                0.1,
                1.0,
                2.0,
                record["symbol"],
                record["date"],
                record["frequency"],
            )
            benchmark_db.execute(update_sql, params)
        results["sequential_update"] = time.time() - start

        # 场景4: 批量 UPDATE
        params_list = []
        for i, record in enumerate(test_data):
            params = (
                10.0 + i * 0.01,
                0.1,
                1.0,
                2.0,
                record["symbol"],
                record["date"],
                record["frequency"],
            )
            params_list.append(params)

        start = time.time()
        batch_writer = BatchWriter(benchmark_db, batch_size=100)
        batch_writer.execute_batch(update_sql, params_list, use_transaction=True)
        results["batch_update"] = time.time() - start

        # 计算性能提升
        insert_speedup = results["sequential_insert"] / results["batch_insert"]
        update_speedup = results["sequential_update"] / results["batch_update"]

        # 打印报告
        print("\n场景1: 逐条 INSERT 1000条记录")
        print(f"  耗时: {results['sequential_insert']:.4f}秒")
        print(f"  吞吐量: {1000 / results['sequential_insert']:.2f} 记录/秒")

        print("\n场景2: 批量 INSERT 1000条记录 (batch_size=100)")
        print(f"  耗时: {results['batch_insert']:.4f}秒")
        print(f"  吞吐量: {1000 / results['batch_insert']:.2f} 记录/秒")
        print(f"  性能提升: {insert_speedup:.2f}x")

        print("\n场景3: 逐条 UPDATE 1000条记录")
        print(f"  耗时: {results['sequential_update']:.4f}秒")
        print(f"  吞吐量: {1000 / results['sequential_update']:.2f} 记录/秒")

        print("\n场景4: 批量 UPDATE 1000条记录 (batch_size=100)")
        print(f"  耗时: {results['batch_update']:.4f}秒")
        print(f"  吞吐量: {1000 / results['batch_update']:.2f} 记录/秒")
        print(f"  性能提升: {update_speedup:.2f}x")

        print("\n" + "=" * 80)
        print("性能提升总结")
        print("=" * 80)
        print(f"INSERT 性能提升: {insert_speedup:.2f}x")
        print(f"UPDATE 性能提升: {update_speedup:.2f}x")

        # 验证性能提升达到预期（5-10倍）
        assert (
            insert_speedup >= 5.0
        ), f"INSERT 性能提升 {insert_speedup:.2f}x 未达到 5倍以上"
        assert (
            update_speedup >= 5.0
        ), f"UPDATE 性能提升 {update_speedup:.2f}x 未达到 5倍以上"

        print("\n✅ 性能提升达标：批量写入性能提升 5-10倍")
        print("=" * 80)
