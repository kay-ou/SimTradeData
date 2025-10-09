"""
BatchWriter 单元测试

测试批量写入器的核心功能:
1. add_record() 添加记录到缓冲区
2. 自动刷新: 达到 batch_size 自动 flush
3. flush(table) 批量执行指定表
4. flush_all() 刷新所有表
5. 事务回滚: 批次失败正确回滚
6. 不同表隔离: 表A失败不影响表B
7. 上下文管理器: 退出时自动刷新
"""

import logging
import tempfile
from pathlib import Path

import pytest

from simtradedata.database.batch_writer import BatchWriter
from simtradedata.database.manager import DatabaseManager

logger = logging.getLogger(__name__)


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
        CREATE TABLE IF NOT EXISTS test_table_a (
            id INTEGER PRIMARY KEY,
            name TEXT,
            value INTEGER
        )
    """
    )

    manager.execute(
        """
        CREATE TABLE IF NOT EXISTS test_table_b (
            id INTEGER PRIMARY KEY,
            category TEXT,
            amount REAL
        )
    """
    )

    yield manager

    manager.close()


@pytest.fixture
def batch_writer(db_manager):
    """创建批量写入器"""
    return BatchWriter(db_manager, batch_size=3, auto_flush=True)


class TestBatchWriterBasic:
    """基础功能测试"""

    def test_initialization(self, db_manager):
        """测试初始化"""
        writer = BatchWriter(db_manager, batch_size=100, auto_flush=True)

        assert writer.db_manager == db_manager
        assert writer.batch_size == 100
        assert writer.auto_flush is True
        assert writer.get_buffer_size() == 0

        stats = writer.get_stats()
        assert stats["total_records"] == 0
        assert stats["total_batches"] == 0
        assert stats["failed_batches"] == 0

    def test_add_record(self, batch_writer):
        """测试添加记录到缓冲区"""
        # 添加第一条记录
        buffer_size = batch_writer.add_record(
            "test_table_a", {"id": 1, "name": "test1", "value": 100}
        )
        assert buffer_size == 1
        assert batch_writer.get_buffer_size("test_table_a") == 1

        # 添加第二条记录
        buffer_size = batch_writer.add_record(
            "test_table_a", {"id": 2, "name": "test2", "value": 200}
        )
        assert buffer_size == 2
        assert batch_writer.get_buffer_size("test_table_a") == 2

    def test_add_record_validation(self, batch_writer):
        """测试添加记录的参数验证"""
        # 表名为空
        with pytest.raises(ValueError, match="表名不能为空"):
            batch_writer.add_record("", {"id": 1})

        # 数据类型错误
        with pytest.raises(TypeError, match="数据必须是字典类型"):
            batch_writer.add_record("test_table_a", "not a dict")


class TestAutoFlush:
    """自动刷新测试"""

    def test_auto_flush_on_batch_size(self, batch_writer, db_manager):
        """测试达到 batch_size 自动刷新"""
        # batch_size = 3
        # 添加前两条不刷新
        batch_writer.add_record(
            "test_table_a", {"id": 1, "name": "test1", "value": 100}
        )
        batch_writer.add_record(
            "test_table_a", {"id": 2, "name": "test2", "value": 200}
        )
        assert batch_writer.get_buffer_size("test_table_a") == 2

        # 第三条触发自动刷新
        buffer_size = batch_writer.add_record(
            "test_table_a", {"id": 3, "name": "test3", "value": 300}
        )
        assert buffer_size == 0  # 刷新后缓冲区清空

        # 验证数据已写入数据库
        count = db_manager.get_table_count("test_table_a")
        assert count == 3

        # 验证统计信息
        stats = batch_writer.get_stats()
        assert stats["total_records"] == 3
        assert stats["total_batches"] == 1

    def test_no_auto_flush_when_disabled(self, db_manager):
        """测试禁用自动刷新"""
        writer = BatchWriter(db_manager, batch_size=3, auto_flush=False)

        # 添加3条记录,不应自动刷新
        writer.add_record("test_table_a", {"id": 1, "name": "test1", "value": 100})
        writer.add_record("test_table_a", {"id": 2, "name": "test2", "value": 200})
        buffer_size = writer.add_record(
            "test_table_a", {"id": 3, "name": "test3", "value": 300}
        )

        assert buffer_size == 3  # 缓冲区未清空
        assert db_manager.get_table_count("test_table_a") == 0  # 数据未写入


class TestFlush:
    """手动刷新测试"""

    def test_flush_single_table(self, batch_writer, db_manager):
        """测试刷新单个表"""
        # 添加记录到表A
        batch_writer.add_record(
            "test_table_a", {"id": 1, "name": "test1", "value": 100}
        )
        batch_writer.add_record(
            "test_table_a", {"id": 2, "name": "test2", "value": 200}
        )

        # 添加记录到表B
        batch_writer.add_record(
            "test_table_b", {"id": 1, "category": "cat1", "amount": 10.5}
        )

        # 只刷新表A
        count = batch_writer.flush("test_table_a")
        assert count == 2

        # 验证表A数据已写入
        assert db_manager.get_table_count("test_table_a") == 2

        # 验证表B缓冲区未清空
        assert batch_writer.get_buffer_size("test_table_b") == 1
        assert db_manager.get_table_count("test_table_b") == 0

    def test_flush_all_tables(self, batch_writer, db_manager):
        """测试刷新所有表"""
        # 添加记录到表A
        batch_writer.add_record(
            "test_table_a", {"id": 1, "name": "test1", "value": 100}
        )
        batch_writer.add_record(
            "test_table_a", {"id": 2, "name": "test2", "value": 200}
        )

        # 添加记录到表B
        batch_writer.add_record(
            "test_table_b", {"id": 1, "category": "cat1", "amount": 10.5}
        )
        batch_writer.add_record(
            "test_table_b", {"id": 2, "category": "cat2", "amount": 20.5}
        )

        # 刷新所有表
        results = batch_writer.flush_all()

        assert results["test_table_a"] == 2
        assert results["test_table_b"] == 2

        # 验证数据已写入
        assert db_manager.get_table_count("test_table_a") == 2
        assert db_manager.get_table_count("test_table_b") == 2

        # 验证缓冲区已清空
        assert batch_writer.get_buffer_size() == 0

    def test_flush_empty_buffer(self, batch_writer):
        """测试刷新空缓冲区"""
        count = batch_writer.flush("test_table_a")
        assert count == 0

    def test_insert_or_replace_idempotency(self, batch_writer, db_manager):
        """测试 INSERT OR REPLACE 幂等性"""
        # 第一次写入
        batch_writer.add_record(
            "test_table_a", {"id": 1, "name": "test1", "value": 100}
        )
        batch_writer.flush("test_table_a")

        # 第二次写入相同ID但不同值
        batch_writer.add_record(
            "test_table_a", {"id": 1, "name": "test1_updated", "value": 200}
        )
        batch_writer.flush("test_table_a")

        # 验证只有一条记录,且值已更新
        assert db_manager.get_table_count("test_table_a") == 1

        row = db_manager.fetchone("SELECT * FROM test_table_a WHERE id = 1")
        assert row["name"] == "test1_updated"
        assert row["value"] == 200


class TestTransactionRollback:
    """事务回滚测试"""

    def test_flush_with_sql_error(self, batch_writer, db_manager):
        """测试SQL错误导致事务回滚"""
        # 先添加一条包含所有列的记录作为模板
        batch_writer.add_record(
            "test_table_a", {"id": 1, "name": "test1", "value": 100}
        )

        # 添加一条缺少必要列的记录(缺少id列,会导致SQL构建失败)
        # 由于第一条记录定义了列顺序,第二条记录必须包含相同的列
        # 这里我们通过列顺序不一致来触发错误
        batch_writer._buffer["test_table_a"].append(
            {"name": "test2"}
        )  # 直接操作内部缓冲区

        # 刷新应该失败(列不匹配)
        with pytest.raises(KeyError):  # 会抛出KeyError因为缺少列
            batch_writer.flush("test_table_a")

        # 验证缓冲区保留(未清空)
        assert batch_writer.get_buffer_size("test_table_a") == 2

        # 验证数据库中没有数据(事务回滚)
        assert db_manager.get_table_count("test_table_a") == 0

        # 验证失败统计
        stats = batch_writer.get_stats()
        assert stats["failed_batches"] == 1


class TestTableIsolation:
    """表隔离测试"""

    def test_table_failure_isolation(self, batch_writer, db_manager):
        """测试表A失败不影响表B"""
        # 添加正常记录到表A
        batch_writer.add_record(
            "test_table_a", {"id": 1, "name": "test1", "value": 100}
        )

        # 直接操作缓冲区添加错误记录(缺少必要列)
        batch_writer._buffer["test_table_a"].append({"name": "test2"})  # 缺少id列

        # 添加正常记录到表B
        batch_writer.add_record(
            "test_table_b", {"id": 1, "category": "cat1", "amount": 10.5}
        )
        batch_writer.add_record(
            "test_table_b", {"id": 2, "category": "cat2", "amount": 20.5}
        )

        # 刷新所有表
        results = batch_writer.flush_all()

        # 表A失败
        assert results["test_table_a"] == 0
        assert db_manager.get_table_count("test_table_a") == 0

        # 表B成功
        assert results["test_table_b"] == 2
        assert db_manager.get_table_count("test_table_b") == 2


class TestContextManager:
    """上下文管理器测试"""

    def test_context_manager_auto_flush(self, db_manager):
        """测试上下文管理器退出时自动刷新"""
        with BatchWriter(db_manager, batch_size=100) as writer:
            writer.add_record("test_table_a", {"id": 1, "name": "test1", "value": 100})
            writer.add_record("test_table_a", {"id": 2, "name": "test2", "value": 200})

            # 此时数据未写入
            assert db_manager.get_table_count("test_table_a") == 0

        # 退出上下文后自动刷新
        assert db_manager.get_table_count("test_table_a") == 2

    def test_context_manager_with_exception(self, db_manager):
        """测试上下文管理器遇到异常时的行为"""
        try:
            with BatchWriter(db_manager, batch_size=100) as writer:
                writer.add_record(
                    "test_table_a", {"id": 1, "name": "test1", "value": 100}
                )

                # 模拟异常
                raise ValueError("Test exception")
        except ValueError:
            pass

        # 即使有异常,退出时也应尝试刷新
        assert db_manager.get_table_count("test_table_a") == 1


class TestStatistics:
    """统计信息测试"""

    def test_get_stats(self, batch_writer, db_manager):
        """测试获取统计信息"""
        # 禁用自动刷新,手动控制刷新时机
        writer = BatchWriter(db_manager, batch_size=100, auto_flush=False)

        # 执行一些操作
        writer.add_record("test_table_a", {"id": 1, "name": "test1", "value": 100})
        writer.add_record("test_table_a", {"id": 2, "name": "test2", "value": 200})
        writer.flush("test_table_a")

        writer.add_record("test_table_b", {"id": 1, "category": "cat1", "amount": 10.5})
        writer.add_record("test_table_b", {"id": 2, "category": "cat2", "amount": 20.5})
        writer.flush("test_table_b")

        # 获取统计信息
        stats = writer.get_stats()

        assert stats["total_records"] == 4
        assert stats["total_batches"] == 2
        assert stats["failed_batches"] == 0
        assert stats["buffer_size"] == 0
        assert stats["avg_batch_size"] == 2.0
        assert stats["avg_flush_time"] > 0
        assert stats["total_flush_time"] > 0
        assert "test_table_a" not in stats["buffered_tables"]
        assert "test_table_b" not in stats["buffered_tables"]


class TestBufferManagement:
    """缓冲区管理测试"""

    def test_get_buffer_size(self, batch_writer):
        """测试获取缓冲区大小"""
        # 添加记录到不同表
        batch_writer.add_record(
            "test_table_a", {"id": 1, "name": "test1", "value": 100}
        )
        batch_writer.add_record(
            "test_table_a", {"id": 2, "name": "test2", "value": 200}
        )
        batch_writer.add_record(
            "test_table_b", {"id": 1, "category": "cat1", "amount": 10.5}
        )

        # 测试单表缓冲区大小
        assert batch_writer.get_buffer_size("test_table_a") == 2
        assert batch_writer.get_buffer_size("test_table_b") == 1

        # 测试总缓冲区大小
        assert batch_writer.get_buffer_size() == 3

    def test_clear_buffer(self, batch_writer):
        """测试清空缓冲区"""
        # 添加记录
        batch_writer.add_record(
            "test_table_a", {"id": 1, "name": "test1", "value": 100}
        )
        batch_writer.add_record(
            "test_table_b", {"id": 1, "category": "cat1", "amount": 10.5}
        )

        # 清空单表缓冲区
        batch_writer.clear_buffer("test_table_a")
        assert batch_writer.get_buffer_size("test_table_a") == 0
        assert batch_writer.get_buffer_size("test_table_b") == 1

        # 清空所有表缓冲区
        batch_writer.clear_buffer()
        assert batch_writer.get_buffer_size() == 0


class TestExecuteBatch:
    """通用批量执行测试"""

    def test_execute_batch_with_transaction(self, batch_writer, db_manager):
        """测试使用事务的批量执行"""
        sql = "INSERT OR REPLACE INTO test_table_a (id, name, value) VALUES (?, ?, ?)"
        params_list = [
            (1, "test1", 100),
            (2, "test2", 200),
            (3, "test3", 300),
        ]

        count = batch_writer.execute_batch(sql, params_list, use_transaction=True)

        assert count == 3
        assert db_manager.get_table_count("test_table_a") == 3

        stats = batch_writer.get_stats()
        assert stats["total_records"] == 3
        assert stats["total_batches"] == 1

    def test_execute_batch_without_transaction(self, batch_writer, db_manager):
        """测试不使用事务的批量执行"""
        sql = "INSERT OR REPLACE INTO test_table_a (id, name, value) VALUES (?, ?, ?)"
        params_list = [
            (1, "test1", 100),
            (2, "test2", 200),
        ]

        count = batch_writer.execute_batch(sql, params_list, use_transaction=False)

        assert count == 2
        assert db_manager.get_table_count("test_table_a") == 2

    def test_execute_batch_empty_params(self, batch_writer):
        """测试空参数列表"""
        sql = "INSERT INTO test_table_a (id, name, value) VALUES (?, ?, ?)"
        count = batch_writer.execute_batch(sql, [], use_transaction=True)
        assert count == 0


class TestRepr:
    """字符串表示测试"""

    def test_repr(self, batch_writer):
        """测试 __repr__ 方法"""
        batch_writer.add_record(
            "test_table_a", {"id": 1, "name": "test1", "value": 100}
        )
        batch_writer.add_record(
            "test_table_b", {"id": 1, "category": "cat1", "amount": 10.5}
        )

        repr_str = repr(batch_writer)

        assert "BatchWriter" in repr_str
        assert "batch_size=3" in repr_str
        assert "buffer_size=2" in repr_str
        assert "tables=2" in repr_str
