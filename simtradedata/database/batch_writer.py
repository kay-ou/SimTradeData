"""
数据库批量写入器

提供高效的批量写入功能,减少事务开销,提升大规模数据同步性能。
"""

import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class BatchWriter:
    """
    数据库批量写入器

    功能:
    1. 按表缓冲数据: 使用 defaultdict 按表名分组缓冲
    2. 自动刷新: 达到 batch_size 自动刷新
    3. 批量执行: 使用 executemany 和事务减少开销
    4. 错误隔离: 单个表失败不影响其他表
    5. 幂等性: 使用 INSERT OR REPLACE 保证幂等

    使用示例:
        writer = BatchWriter(db_manager, batch_size=100)
        writer.add_record("market_data", {"symbol": "000001.SZ", ...})
        writer.flush_all()  # 刷新所有缓冲
    """

    def __init__(
        self,
        db_manager,  # DatabaseManager 实例
        batch_size: int = 100,
        auto_flush: bool = True,
    ):
        """
        初始化批量写入器

        Args:
            db_manager: DatabaseManager 实例
            batch_size: 批次大小,达到此数量自动刷新(默认100)
            auto_flush: 是否自动刷新(默认True)
        """
        self.db_manager = db_manager
        self.batch_size = batch_size
        self.auto_flush = auto_flush

        # 缓冲区: {table_name: [records]}
        self._buffer: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

        # 统计信息
        self._stats = {
            "total_records": 0,  # 总记录数
            "total_batches": 0,  # 总批次数
            "failed_batches": 0,  # 失败批次数
            "total_flush_time": 0.0,  # 总刷新时间
        }

    def add_record(self, table: str, data: Dict[str, Any]) -> int:
        """
        添加记录到缓冲区

        Args:
            table: 表名
            data: 记录数据(字典格式)

        Returns:
            int: 当前表的缓冲区大小

        当缓冲区达到 batch_size 时自动刷新
        """
        if not table:
            raise ValueError("表名不能为空")

        if not isinstance(data, dict):
            raise TypeError("数据必须是字典类型")

        # 添加到缓冲区
        self._buffer[table].append(data)
        buffer_size = len(self._buffer[table])

        # 自动刷新
        if self.auto_flush and buffer_size >= self.batch_size:
            logger.debug(f"表 {table} 缓冲区达到 {buffer_size} 条,自动刷新")
            self.flush(table)
            return 0

        return buffer_size

    def flush(self, table: Optional[str] = None) -> int:
        """
        刷新指定表的缓冲区

        Args:
            table: 表名,为None时刷新所有表

        Returns:
            int: 刷新的记录数

        使用事务批量执行 INSERT OR REPLACE,失败时回滚
        """
        import time

        if table is None:
            return self.flush_all()

        if table not in self._buffer or not self._buffer[table]:
            logger.debug(f"表 {table} 缓冲区为空,跳过刷新")
            return 0

        records = self._buffer[table]
        record_count = len(records)

        logger.debug(f"开始刷新表 {table},共 {record_count} 条记录")

        try:
            start_time = time.time()

            # 获取列名(从第一条记录)
            columns = list(records[0].keys())
            placeholders = ", ".join(["?" for _ in columns])
            columns_str = ", ".join(columns)

            # 构建 SQL: INSERT OR REPLACE
            sql = f"INSERT OR REPLACE INTO {table} ({columns_str}) VALUES ({placeholders})"

            # 准备参数列表
            params_list = [tuple(record[col] for col in columns) for record in records]

            # 使用事务批量执行
            with self.db_manager.transaction():
                self.db_manager.executemany(sql, params_list)

            # 更新统计
            elapsed = time.time() - start_time
            self._stats["total_records"] += record_count
            self._stats["total_batches"] += 1
            self._stats["total_flush_time"] += elapsed

            # 清空缓冲区
            self._buffer[table].clear()

            logger.info(
                f"表 {table} 批量写入成功: {record_count} 条记录, 耗时 {elapsed:.3f}秒"
            )
            return record_count

        except Exception as e:
            logger.error(f"表 {table} 批量写入失败: {e}")
            self._stats["failed_batches"] += 1
            # 保留缓冲区数据,不清空
            raise

    def flush_all(self) -> Dict[str, int]:
        """
        刷新所有表的缓冲区

        Returns:
            Dict[str, int]: {table_name: flushed_count}

        每个表独立刷新,一个表失败不影响其他表
        """
        results = {}
        failed_tables = []

        for table in list(self._buffer.keys()):
            try:
                count = self.flush(table)
                results[table] = count
            except Exception as e:
                logger.warning(f"刷新表 {table} 失败,将在最后重试: {e}")
                failed_tables.append((table, e))
                results[table] = 0

        # 报告失败情况
        if failed_tables:
            logger.warning(f"以下表刷新失败: {[t for t, _ in failed_tables]}")

        return results

    def execute_batch(
        self, sql: str, params_list: List[Tuple], use_transaction: bool = True
    ) -> int:
        """
        通用批量执行方法

        Args:
            sql: SQL 语句
            params_list: 参数列表
            use_transaction: 是否使用事务(默认True)

        Returns:
            int: 执行的记录数

        提供更灵活的批量执行接口,支持自定义 SQL
        """
        import time

        if not sql:
            raise ValueError("SQL 语句不能为空")

        if not params_list:
            logger.debug("参数列表为空,跳过执行")
            return 0

        record_count = len(params_list)
        logger.debug(f"批量执行 SQL: {sql[:100]}..., 共 {record_count} 条记录")

        try:
            start_time = time.time()

            if use_transaction:
                # 使用事务
                with self.db_manager.transaction():
                    self.db_manager.executemany(sql, params_list)
            else:
                # 不使用事务
                self.db_manager.executemany(sql, params_list)

            elapsed = time.time() - start_time
            self._stats["total_records"] += record_count
            self._stats["total_batches"] += 1
            self._stats["total_flush_time"] += elapsed

            logger.info(f"批量执行成功: {record_count} 条记录, 耗时 {elapsed:.3f}秒")
            return record_count

        except Exception as e:
            logger.error(f"批量执行失败: {e}")
            self._stats["failed_batches"] += 1
            raise

    def get_buffer_size(self, table: Optional[str] = None) -> int:
        """
        获取缓冲区大小

        Args:
            table: 表名,为None时返回总大小

        Returns:
            int: 缓冲区记录数
        """
        if table is None:
            return sum(len(records) for records in self._buffer.values())
        return len(self._buffer.get(table, []))

    def clear_buffer(self, table: Optional[str] = None):
        """
        清空缓冲区(不执行写入)

        Args:
            table: 表名,为None时清空所有表
        """
        if table is None:
            self._buffer.clear()
            logger.debug("已清空所有表的缓冲区")
        else:
            if table in self._buffer:
                self._buffer[table].clear()
                logger.debug(f"已清空表 {table} 的缓冲区")

    def get_stats(self) -> Dict[str, Any]:
        """
        获取统计信息

        Returns:
            Dict包含:
            - total_records: 总记录数
            - total_batches: 总批次数
            - failed_batches: 失败批次数
            - avg_batch_size: 平均批次大小
            - avg_flush_time: 平均刷新时间(毫秒)
            - total_flush_time: 总刷新时间(秒)
            - buffer_size: 当前缓冲区大小
        """
        stats = self._stats.copy()

        # 计算平均值
        if stats["total_batches"] > 0:
            stats["avg_batch_size"] = stats["total_records"] / stats["total_batches"]
            stats["avg_flush_time"] = (
                stats["total_flush_time"] / stats["total_batches"] * 1000
            )  # 毫秒
        else:
            stats["avg_batch_size"] = 0
            stats["avg_flush_time"] = 0

        # 添加当前缓冲区大小
        stats["buffer_size"] = self.get_buffer_size()
        # 只返回非空的表
        stats["buffered_tables"] = [
            table for table, records in self._buffer.items() if records
        ]

        return stats

    def __enter__(self):
        """上下文管理器入口"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口,自动刷新缓冲区"""
        try:
            if self.get_buffer_size() > 0:
                logger.info("退出 BatchWriter,刷新剩余缓冲数据")
                self.flush_all()
        except Exception as e:
            logger.error(f"退出时刷新缓冲区失败: {e}")
        return False

    def __repr__(self) -> str:
        """字符串表示"""
        return (
            f"BatchWriter(batch_size={self.batch_size}, "
            f"buffer_size={self.get_buffer_size()}, "
            f"tables={len(self._buffer)})"
        )
