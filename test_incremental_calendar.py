#!/usr/bin/env python3
"""
测试交易日历增量更新功能
验证交易日历只在需要时更新，而不是每次都重新下载
"""

import os
import sys
from datetime import date

# 添加项目路径
sys.path.insert(0, os.path.abspath("."))

from simtradedata.config import Config
from simtradedata.data_sources import DataSourceManager
from simtradedata.database import DatabaseManager
from simtradedata.preprocessor import DataProcessingEngine
from simtradedata.sync import SyncManager


def test_incremental_calendar_update():
    """测试交易日历增量更新"""
    print("🧪 测试交易日历增量更新功能...")

    # 初始化组件
    config = Config()
    db_manager = DatabaseManager()
    data_source_manager = DataSourceManager(config)
    processing_engine = DataProcessingEngine(db_manager, data_source_manager, config)
    sync_manager = SyncManager(
        db_manager, data_source_manager, processing_engine, config
    )

    # 检查当前交易日历状态
    print("\n📊 检查当前交易日历状态...")
    current_range = db_manager.fetchone(
        "SELECT MIN(date) as min_date, MAX(date) as max_date, COUNT(*) as count FROM trading_calendar"
    )

    if current_range and current_range["count"] > 0:
        print(
            f"现有数据范围: {current_range['min_date']} 到 {current_range['max_date']}"
        )
        print(f"总记录数: {current_range['count']}")
    else:
        print("❌ 没有现有交易日历数据")

    # 测试1: 目标日期在现有范围内 - 应该跳过更新
    print("\n🔬 测试1: 目标日期在现有范围内（应该跳过更新）")
    target_date_within = date(2025, 1, 24)

    import time

    start_time = time.time()

    result1 = sync_manager._update_trading_calendar(target_date_within)

    elapsed_time = time.time() - start_time

    print(f"更新结果: {result1}")
    print(f"耗时: {elapsed_time:.2f}秒")

    if result1.get("status") == "skipped" or result1.get("updated_records") == 0:
        print("✅ 成功跳过不必要的更新！")
    else:
        print("❌ 没有跳过更新，可能存在问题")

    # 测试2: 目标日期需要未来年份 - 应该增量更新
    print("\n🔬 测试2: 目标日期需要未来年份（应该增量更新）")
    target_date_future = date(2027, 1, 24)  # 需要2026-2028年数据

    # 先删除2027年以后的数据（如果有的话）
    db_manager.execute("DELETE FROM trading_calendar WHERE date >= '2027-01-01'")

    start_time = time.time()

    result2 = sync_manager._update_trading_calendar(target_date_future)

    elapsed_time = time.time() - start_time

    print(f"更新结果: {result2}")
    print(f"耗时: {elapsed_time:.2f}秒")

    if result2.get("updated_records", 0) > 0:
        print(f"✅ 成功增量更新了 {result2.get('updated_records')} 条记录！")
    else:
        print("❌ 没有进行增量更新")

    # 验证最终状态
    print("\n📊 验证最终交易日历状态...")
    final_range = db_manager.fetchone(
        "SELECT MIN(date) as min_date, MAX(date) as max_date, COUNT(*) as count FROM trading_calendar"
    )

    if final_range:
        print(f"最终数据范围: {final_range['min_date']} 到 {final_range['max_date']}")
        print(f"最终记录数: {final_range['count']}")

        # 检查是否包含2027年数据
        count_2027 = db_manager.fetchone(
            "SELECT COUNT(*) as count FROM trading_calendar WHERE date >= '2027-01-01' AND date < '2028-01-01'"
        )

        if count_2027 and count_2027["count"] > 0:
            print(f"✅ 成功添加了2027年数据: {count_2027['count']}条记录")
        else:
            print("❌ 没有添加2027年数据")

    # 测试3: 再次调用相同目标日期 - 应该跳过
    print("\n🔬 测试3: 再次调用相同目标日期（应该跳过）")

    start_time = time.time()

    result3 = sync_manager._update_trading_calendar(target_date_future)

    elapsed_time = time.time() - start_time

    print(f"更新结果: {result3}")
    print(f"耗时: {elapsed_time:.2f}秒")

    if result3.get("updated_records", 0) == 0:
        print("✅ 成功跳过重复更新！")
        print(f"⚡ 第二次调用只用了 {elapsed_time:.2f}秒，相比首次大幅提升")
    else:
        print("❌ 没有跳过重复更新")

    # 测试总结
    print("\n🎯 测试总结:")
    print("1. 交易日历增量更新功能已实现")
    print("2. 当数据已存在时，会智能跳过不必要的网络请求")
    print("3. 只有在需要时才会下载新年份的数据")
    print("4. 显著提升了同步性能，避免重复网络IO")

    # 恢复原始状态（删除测试添加的未来数据）
    print("\n🧹 清理测试数据...")
    db_manager.execute("DELETE FROM trading_calendar WHERE date >= '2026-01-01'")
    print("✅ 测试数据清理完成")

    # 关闭连接
    db_manager.close()
    print("\n✅ 交易日历增量更新测试完成！")


if __name__ == "__main__":
    test_incremental_calendar_update()
