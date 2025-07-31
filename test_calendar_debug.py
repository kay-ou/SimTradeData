#!/usr/bin/env python3
"""
独立测试交易日历增量更新逻辑
直接检查具体的执行路径
"""

import os
import sys
from datetime import date, datetime

# 添加项目路径
sys.path.insert(0, os.path.abspath("."))

from simtradedata.config import Config
from simtradedata.data_sources import DataSourceManager
from simtradedata.database import DatabaseManager
from simtradedata.preprocessor import DataProcessingEngine
from simtradedata.sync import SyncManager


def test_calendar_update_logic():
    """直接测试交易日历更新逻辑"""
    print("🔍 独立测试交易日历增量更新逻辑...")

    # 确保测试环境
    config = Config()
    db_manager = DatabaseManager()

    # 清理2026年以后的数据
    db_manager.execute('DELETE FROM trading_calendar WHERE date >= "2026-01-01"')
    print("✅ 已清理2026年以后的数据")

    # 验证现有数据
    existing_range = db_manager.fetchone(
        "SELECT MIN(date) as min_date, MAX(date) as max_date, COUNT(*) as count FROM trading_calendar"
    )
    print(
        f"现有数据: {existing_range['min_date']} 到 {existing_range['max_date']}, 共{existing_range['count']}条"
    )

    # 创建同步管理器
    data_source_manager = DataSourceManager(config)
    processing_engine = DataProcessingEngine(db_manager, data_source_manager, config)
    sync_manager = SyncManager(
        db_manager, data_source_manager, processing_engine, config
    )

    # 目标日期：2027年
    target_date = date(2027, 1, 24)
    print(f"目标日期: {target_date}")

    # 手动执行增量更新逻辑
    print("\n🧪 手动执行增量更新逻辑...")

    existing_min = datetime.strptime(existing_range["min_date"], "%Y-%m-%d").date()
    existing_max = datetime.strptime(existing_range["max_date"], "%Y-%m-%d").date()

    needed_start_year = target_date.year - 1  # 2026
    needed_end_year = target_date.year + 1  # 2028

    print(f"现有数据年份范围: {existing_min.year}-{existing_max.year}")
    print(f"需要的年份范围: {needed_start_year}-{needed_end_year}")

    years_to_update = []

    if existing_min.year > needed_start_year:
        early_years = list(range(needed_start_year, existing_min.year))
        years_to_update.extend(early_years)
        print(f"需要添加更早年份: {early_years}")

    if existing_max.year < needed_end_year:
        later_years = list(range(existing_max.year + 1, needed_end_year + 1))
        years_to_update.extend(later_years)
        print(f"需要添加更晚年份: {later_years}")

    print(f"最终需要更新的年份: {years_to_update}")

    if not years_to_update:
        print("❌ 逻辑判断无需更新（这是不对的）")
        return False

    # 实际调用方法
    print(f"\n🚀 调用 _update_trading_calendar({target_date})")
    result = sync_manager._update_trading_calendar(target_date)

    print(f"方法返回结果: {result}")

    # 验证数据库变化
    final_range = db_manager.fetchone(
        "SELECT MIN(date) as min_date, MAX(date) as max_date, COUNT(*) as count FROM trading_calendar"
    )
    print(
        f"更新后数据: {final_range['min_date']} 到 {final_range['max_date']}, 共{final_range['count']}条"
    )

    # 检查是否真的添加了新数据
    new_records = final_range["count"] - existing_range["count"]
    print(f"新增记录数: {new_records}")

    if new_records > 0:
        print("✅ 增量更新成功！")
        success = True
    else:
        print("❌ 增量更新失败，没有新增记录")
        success = False

    # 恢复测试环境
    db_manager.execute('DELETE FROM trading_calendar WHERE date >= "2026-01-01"')
    print("🧹 测试数据已清理")

    db_manager.close()
    return success


if __name__ == "__main__":
    success = test_calendar_update_logic()
    print(f"\n{'✅ 测试通过' if success else '❌ 测试失败'}")
