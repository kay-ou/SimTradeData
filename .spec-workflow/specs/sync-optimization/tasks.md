# Tasks Document - sync-optimization

本文档将 sync-optimization 设计分解为可执行的原子任务。每个任务专注于1-3个文件,包含清晰的实现指导和验收标准。

## Phase 1: 连接管理优化 (ConnectionManager)

### Task 1.1: 创建连接管理器核心类

- [x] 1.1 创建 ConnectionManager 核心类
  - **File**: `simtradedata/data_sources/connection_manager.py`
  - **Purpose**: 实现线程安全的 BaoStock 会话管理器,提供会话保活和串行化访问
  - **Details**:
    - 实现 `ConnectionManager` 类,管理 BaoStock 全局单例会话
    - 实现 `ensure_connected()`: 确保会话有效,需要时重连
    - 实现 `acquire_lock()`: 获取线程安全访问锁
    - 实现 `release_lock()`: 释放访问锁
    - 实现 `heartbeat()`: 心跳检测会话有效性
    - 实现 `disconnect()`: 断开会话
    - 实现 `get_stats()`: 获取统计信息(重连次数、平均访问时间等)
  - **_Leverage**:
    - `BaoStockAdapter.connect()`: 建立会话
    - `BaoStockAdapter.disconnect()`: 断开会话
    - `BaoStockAdapter._session_timeout`: 超时配置
    - `threading.Lock`: 线程锁
    - `time`: 超时检测
  - **_Requirements**: Requirement 1 (BaoStock连接管理优化)
  - **Success**:
    - 会话保活正常,避免频繁重连
    - 线程安全,多线程串行访问 BaoStock API
    - 心跳检测能正确识别会话失效
  - **_Prompt**:
```
Implement the task for spec sync-optimization, first run spec-workflow-guide to get the workflow guide then implement the task:

Role: Python 后端开发专家,精通多线程编程和会话管理

Task: 创建 ConnectionManager 类实现 BaoStock 会话管理。要求:
1. 管理 BaoStock 全局单例会话,不是连接池
2. 使用 threading.Lock 保证多线程串行访问 BaoStock API
3. 实现 ensure_connected(): 检查会话有效性,超时才重连
4. 实现 acquire_lock(timeout): 获取访问锁,支持超时
5. 实现 release_lock(): 释放访问锁
6. 实现 heartbeat(): 定期发送轻量级查询验证会话
7. 记录统计信息: 重连次数、平均访问时间、锁等待时间

复用以下组件:
- BaoStockAdapter 的 connect(), disconnect() 方法
- _session_timeout 配置
- threading.Lock 线程锁
- time 模块计时

Restrictions:
- BaoStock 使用全局会话,不支持连接池
- 必须保证线程安全的串行访问
- 避免频繁 login/logout
- 不修改 BaoStockAdapter 接口

Success Criteria:
- 会话长时间保持,减少重连
- 多线程并发时正确串行化访问
- 心跳检测准确识别超时
- 统计信息完整准确
- 锁等待不死锁

After completing this task:
1. Update tasks.md: change `- [ ] 1.1` to `- [-] 1.1` when starting
2. Implement the code following the requirements
3. Test the implementation
4. Update tasks.md: change `- [-] 1.1` to `- [x] 1.1` when completed
```

### Task 1.2: 集成连接管理器到 DataSourceManager

- [x] 1.2 集成 ConnectionManager 到 DataSourceManager
  - **File**: `simtradedata/data_sources/manager.py` (修改现有)
  - **Purpose**: 在 DataSourceManager 中使用连接管理器,优化会话保活
  - **Details**:
    - 在 `__init__()` 中初始化 ConnectionManager
    - 在 BaoStock API 调用前调用 `acquire_lock()`
    - 在 BaoStock API 调用后调用 `release_lock()`
    - 使用 `ensure_connected()` 确保会话有效
    - 添加配置项读取连接管理参数
    - 保持向后兼容,支持禁用连接管理优化
  - **_Leverage**:
    - `ConnectionManager` 类 (Task 1.1)
    - 现有 `Config` 系统
    - 现有 `DataSourceManager` 架构
  - **_Requirements**: Requirement 1 (BaoStock连接管理优化)
  - **Success**:
    - DataSourceManager 透明使用连接管理器
    - 配置可控制启用/禁用
    - 向后兼容,不破坏现有 API
  - **_Prompt**:
```
Implement the task for spec sync-optimization, first run spec-workflow-guide to get the workflow guide then implement the task:

Role: Python 后端开发专家,精通依赖注入和系统集成

Task: 将 ConnectionManager 集成到 DataSourceManager。要求:
1. 在 DataSourceManager.__init__() 中初始化 ConnectionManager
2. 读取配置: performance.connection_manager.enable, session_timeout, heartbeat_interval等
3. 在调用 BaoStock API 前: acquire_lock()
4. 在调用 BaoStock API 后: release_lock()
5. 使用 ensure_connected() 确保会话有效
6. 添加降级逻辑: 连接管理禁用时使用原有逻辑
7. 保持 API 兼容性,不改变方法签名

复用以下组件:
- ConnectionManager (来自 Task 1.1)
- Config 系统读取配置
- 现有 DataSourceManager 架构

Restrictions:
- 不破坏现有 API
- 必须支持启用/禁用连接管理
- 配置变更不影响运行中的系统
- 不修改其他依赖 DataSourceManager 的代码

Success Criteria:
- DataSourceManager 能正确初始化连接管理器
- 连接管理启用时透明使用
- 连接管理禁用时回退到原逻辑
- 现有测试全部通过
- 无 API 破坏性变更

After completing this task:
1. Update tasks.md: change `- [ ] 1.2` to `- [-] 1.2` when starting
2. Implement the code following the requirements
3. Test the implementation
4. Update tasks.md: change `- [-] 1.2` to `- [x] 1.2` when completed
```

### Task 1.3: 连接管理器单元测试

- [x] 1.3 创建 ConnectionManager 单元测试
  - **File**: `tests/unit/test_connection_manager.py`
  - **Purpose**: 全面测试 ConnectionManager 功能和线程安全性
  - **Details**:
    - 测试会话保活和重连
    - 测试 `acquire_lock()` 和 `release_lock()` 线程安全
    - 测试 `ensure_connected()` 超时检测
    - 测试并发访问串行化
    - 测试心跳检测有效性
    - 测试统计信息准确性
  - **_Leverage**:
    - pytest 测试框架
    - unittest.mock 模拟 BaoStockAdapter
    - threading 测试并发场景
  - **_Requirements**: Requirement 1 (BaoStock连接管理优化)
  - **Success**:
    - 测试覆盖率100%
    - 所有测试通过
    - 并发安全性验证通过
  - **_Prompt**:
```
Implement the task for spec sync-optimization, first run spec-workflow-guide to get the workflow guide then implement the task:

Role: Python 测试工程师,精通 pytest 和并发测试

Task: 创建 ConnectionManager 的全面单元测试。要求:
1. 测试会话保活: 长时间保持会话不重连
2. 测试 ensure_connected(): 超时后重连,未超时不重连
3. 测试 acquire_lock()/release_lock(): 线程安全互斥访问
4. 测试并发场景: 多线程同时请求访问,串行化执行
5. 测试心跳检测: heartbeat() 能识别失效会话
6. 测试统计信息: get_stats() 返回准确数据
7. 使用 mock 模拟 BaoStockAdapter,避免真实连接

复用以下组件:
- pytest 测试框架
- unittest.mock 模拟对象
- threading 并发测试

Restrictions:
- 不依赖真实 BaoStock 连接
- 测试必须独立可重复运行
- 并发测试必须稳定可靠
- 覆盖率必须达到100%

Success Criteria:
- 所有测试用例通过
- 测试覆盖率100%
- 并发测试无竞态条件
- 测试运行时间<10秒

After completing this task:
1. Update tasks.md: change `- [ ] 1.3` to `- [-] 1.3` when starting
2. Implement the code following the requirements
3. Test the implementation
4. Update tasks.md: change `- [-] 1.3` to `- [x] 1.3` when completed
```

### Task 1.4: 连接管理性能基准测试

- [x] 1.4 创建连接管理性能基准测试
  - **File**: `tests/performance/test_connection_manager_benchmark.py`
  - **Purpose**: 验证连接管理性能提升,对比有/无会话保活的性能差异
  - **Details**:
    - 测试场景1: 10次连续连接/断开(频繁重连)
    - 测试场景2: 10次连续获取/释放(会话保活)
    - 测试场景3: 100次并发访问性能
    - 记录耗时、内存使用
    - 生成性能对比报告
  - **_Leverage**:
    - pytest-benchmark
    - ConnectionManager (Task 1.1)
    - BaoStockAdapter
  - **_Requirements**: Requirement 1 (BaoStock连接管理优化)
  - **Success**:
    - 会话保活性能提升 >30%
    - 报告清晰展示性能差异
  - **_Prompt**:
```
Implement the task for spec sync-optimization, first run spec-workflow-guide to get the workflow guide then implement the task:

Role: Python 性能测试专家,精通性能分析和基准测试

Task: 创建连接管理性能基准测试,验证优化效果。要求:
1. 场景1: 无会话保活 - 10次连续 connect/disconnect
2. 场景2: 有会话保活 - 10次连续 acquire_lock/release_lock
3. 场景3: 并发场景 - 100个线程同时请求访问
4. 记录指标: 总耗时、平均耗时、内存使用
5. 生成对比报告: 性能提升百分比
6. 使用真实 BaoStock 连接测试

复用以下组件:
- pytest-benchmark
- ConnectionManager
- BaoStockAdapter

Restrictions:
- 必须使用真实连接,不使用 mock
- 测试环境一致,避免干扰
- 至少运行3次取平均值
- 性能提升必须 >30%

Success Criteria:
- 会话保活场景比频繁重连快 >30%
- 并发场景性能提升显著
- 报告清晰展示性能数据
- 测试可重复运行

After completing this task:
1. Update tasks.md: change `- [ ] 1.4` to `- [-] 1.4` when starting
2. Implement the code following the requirements
3. Test the implementation
4. Update tasks.md: change `- [-] 1.4` to `- [x] 1.4` when completed
```

## Phase 2: 批量写入优化 (BatchWriter)

### Task 2.1: 创建批量写入器核心类

- [x] 2.1 创建 BatchWriter 核心类
  - **File**: `simtradedata/database/batch_writer.py`
  - **Purpose**: 实现数据库批量写入优化器,减少事务开销
  - **Details**:
    - 实现 `BatchWriter` 类,使用 `defaultdict` 按表缓冲数据
    - 实现 `add_record(table, data)`: 添加记录到缓冲区
    - 实现 `flush(table)`: 刷新指定表的批次
    - 实现 `flush_all()`: 刷新所有表
    - 实现自动刷新: 达到 batch_size 自动 flush
    - 实现 `execute_batch()`: 批量执行 SQL
    - 使用事务保证原子性
  - **_Leverage**:
    - `DatabaseManager.executemany()`: 批量执行
    - `DatabaseManager.transaction()`: 事务管理
    - `collections.defaultdict`: 缓冲区
  - **_Requirements**: Requirement 2 (数据库批量写入优化)
  - **Success**:
    - 批量写入功能正确
    - 事务回滚正常
    - 不同表数据隔离
  - **_Prompt**:
```
Implement the task for spec sync-optimization, first run spec-workflow-guide to get the workflow guide then implement the task:

Role: Python 数据库专家,精通 SQLite 和事务处理

Task: 创建 BatchWriter 类实现数据库批量写入优化。要求:
1. 使用 defaultdict 按表名缓冲数据: {table_name: [records]}
2. add_record(table, data): 添加记录,达到 batch_size 自动 flush
3. flush(table): 使用事务批量执行该表的 INSERT OR REPLACE
4. flush_all(): 刷新所有表的缓冲数据
5. execute_batch(sql, params_list): 通用批量执行方法
6. 错误处理: 批次失败回滚,不影响其他批次
7. 支持配置 batch_size, auto_flush

复用以下组件:
- DatabaseManager.executemany() 批量执行
- DatabaseManager.transaction() 事务管理
- collections.defaultdict 缓冲区

Restrictions:
- 不修改 DatabaseManager 接口
- 事务失败必须回滚
- 批量操作保持幂等性(INSERT OR REPLACE)
- 内存使用可控(限制缓冲区大小)

Success Criteria:
- 批量写入功能正确,数据完整
- 事务失败正确回滚
- 不同表数据独立处理
- 性能显著优于逐条写入

After completing this task:
1. Update tasks.md: change `- [ ] 2.1` to `- [-] 2.1` when starting
2. Implement the code following the requirements
3. Test the implementation
4. Update tasks.md: change `- [-] 2.1` to `- [x] 2.1` when completed
```

### Task 2.2: 集成批量写入器到 IncrementalSync

- [x] 2.2 集成 BatchWriter 到 IncrementalSync
  - **File**: `simtradedata/sync/incremental.py` (修改现有)
  - **Purpose**: 在智能补充中使用批量写入替换逐条 UPDATE
  - **Details**:
    - 在 `smart_backfill_symbol()` 中使用 BatchWriter
    - 替换逐条 `execute()` 为批量 `add_record()`
    - 在方法结尾调用 `flush_all()`
    - 添加配置项控制启用/禁用批量写入
    - 保留降级逻辑: 批量写入失败回退到逐条
  - **_Leverage**:
    - `BatchWriter` 类 (Task 2.1)
    - 现有 `smart_backfill_symbol()` 逻辑
    - `Config` 系统
  - **_Requirements**: Requirement 2 (数据库批量写入优化)
  - **Success**:
    - 智能补充使用批量写入
    - 性能显著提升
    - 数据一致性保持
  - **_Prompt**:
```
Implement the task for spec sync-optimization, first run spec-workflow-guide to get the workflow guide then implement the task:

Role: Python 后端开发专家,精通代码重构和性能优化

Task: 将 BatchWriter 集成到 IncrementalSync.smart_backfill_symbol()。要求:
1. 初始化 BatchWriter 实例
2. 遍历 DataFrame 时使用 batch_writer.add_record() 替换逐条 execute()
3. 在方法结尾调用 batch_writer.flush_all()
4. 读取配置 performance.batch_writer.enable 控制启用
5. 添加降级逻辑: 批量写入失败回退到逐条 UPDATE
6. 保持方法签名和返回值不变
7. 更新日志记录批量写入统计

复用以下组件:
- BatchWriter (来自 Task 2.1)
- 现有 smart_backfill_symbol() 逻辑
- Config 系统

Restrictions:
- 不破坏现有功能
- 数据一致性必须保持
- 必须支持启用/禁用
- 错误处理健壮

Success Criteria:
- smart_backfill_symbol() 使用批量写入
- 数据正确性100%一致
- 性能提升 5-10倍
- 批量写入失败能降级
- 现有测试全部通过

After completing this task:
1. Update tasks.md: change `- [ ] 2.2` to `- [-] 2.2` when starting
2. Implement the code following the requirements
3. Test the implementation
4. Update tasks.md: change `- [-] 2.2` to `- [x] 2.2` when completed
```

### Task 2.3: 批量写入器单元测试

- [x] 2.3 创建 BatchWriter 单元测试
  - **File**: `tests/unit/test_batch_writer.py`
  - **Purpose**: 全面测试 BatchWriter 功能和事务处理
  - **Details**:
    - 测试 add_record() 添加记录
    - 测试自动刷新: 达到 batch_size 自动 flush
    - 测试 flush() 批量执行
    - 测试事务回滚: 批次失败正确回滚
    - 测试不同表隔离
    - 测试 flush_all()
  - **_Leverage**:
    - pytest 测试框架
    - 临时 SQLite 数据库
    - DatabaseManager
  - **_Requirements**: Requirement 2 (数据库批量写入优化)
  - **Success**:
    - 测试覆盖率100%
    - 所有测试通过
    - 事务处理正确
  - **_Prompt**:
```
Implement the task for spec sync-optimization, first run spec-workflow-guide to get the workflow guide then implement the task:

Role: Python 测试工程师,精通数据库测试和事务验证

Task: 创建 BatchWriter 的全面单元测试。要求:
1. 测试 add_record(): 正确添加到缓冲区
2. 测试自动刷新: batch_size=3, 添加3条自动 flush
3. 测试 flush(table): 只刷新指定表
4. 测试 flush_all(): 刷新所有表
5. 测试事务回滚: 模拟 SQL 错误,验证回滚
6. 测试不同表隔离: 表A失败不影响表B
7. 测试批量写入 vs 逐条写入性能对比
8. 使用临时 SQLite 数据库测试

复用以下组件:
- pytest 测试框架
- DatabaseManager
- 临时数据库 fixture

Restrictions:
- 测试独立可重复
- 不依赖外部数据库
- 事务测试严格验证
- 覆盖率100%

Success Criteria:
- 所有测试用例通过
- 测试覆盖率100%
- 事务回滚验证正确
- 性能对比显示显著提升

After completing this task:
1. Update tasks.md: change `- [ ] 2.3` to `- [-] 2.3` when starting
2. Implement the code following the requirements
3. Test the implementation
4. Update tasks.md: change `- [-] 2.3` to `- [x] 2.3` when completed
```

### Task 2.4: 批量写入性能对比测试

- [x] 2.4 创建批量写入性能对比测试
  - **File**: `tests/performance/test_batch_writer_benchmark.py`
  - **Purpose**: 验证批量写入性能提升,对比逐条 vs 批量
  - **Details**:
    - 测试场景1: 逐条 INSERT 1000条记录
    - 测试场景2: 批量 INSERT 1000条记录(batch_size=100)
    - 测试场景3: 逐条 UPDATE 1000条记录
    - 测试场景4: 批量 UPDATE 1000条记录(batch_size=100)
    - 记录耗时对比
    - 生成性能报告
  - **_Leverage**:
    - pytest-benchmark
    - BatchWriter (Task 2.1)
    - DatabaseManager
  - **_Requirements**: Requirement 2 (数据库批量写入优化)
  - **Success**:
    - 批量写入速度提升 5-10倍
    - 报告清晰展示性能差异
  - **_Prompt**:
```
Implement the task for spec sync-optimization, first run spec-workflow-guide to get the workflow guide then implement the task:

Role: Python 性能测试专家,精通数据库性能分析

Task: 创建批量写入性能基准测试,验证优化效果。要求:
1. 场景1: 逐条 INSERT 1000条 market_data 记录
2. 场景2: 批量 INSERT 1000条(batch_size=100)
3. 场景3: 逐条 UPDATE 1000条记录
4. 场景4: 批量 UPDATE 1000条(batch_size=100)
5. 记录指标: 总耗时、每秒操作数(ops/sec)
6. 生成对比报告: 性能提升倍数
7. 使用真实 SQLite 数据库测试

复用以下组件:
- pytest-benchmark
- BatchWriter
- DatabaseManager

Restrictions:
- 测试环境一致
- 数据量足够大(1000条)
- 至少运行3次取平均值
- 性能提升必须 5-10倍

Success Criteria:
- 批量 INSERT 比逐条快 5-10倍
- 批量 UPDATE 比逐条快 5-10倍
- 报告清晰展示性能数据
- 测试可重复运行

After completing this task:
1. Update tasks.md: change `- [ ] 2.4` to `- [-] 2.4` when starting
2. Implement the code following the requirements
3. Test the implementation
4. Update tasks.md: change `- [-] 2.4` to `- [x] 2.4` when completed
```

## Phase 3: 缓存优化 (CacheManager Enhancement)

### Task 3.1: 增强 CacheManager 支持交易日历缓存

- [x] 3.1 增强 CacheManager 支持交易日历缓存
  - **File**: `simtradedata/performance/cache_manager.py` (修改现有)
  - **Purpose**: 添加交易日历缓存,减少数据库查询
  - **Details**:
    - 添加 `load_trading_calendar()`: 批量加载交易日历
    - 添加 `is_trading_day()`: 查询是否交易日(优先缓存)
    - 使用 `lru_cache` 装饰器
    - 添加 TTL 过期机制(7天)
    - 添加缓存统计: 命中率、缓存大小
  - **_Leverage**:
    - 现有 `CacheManager` 基础设施
    - `functools.lru_cache`
    - `DatabaseManager`
  - **_Requirements**: Requirement 4 (智能缓存机制)
  - **Success**:
    - 交易日历缓存工作正常
    - 缓存命中率 >90%
    - TTL 过期正确
  - **_Prompt**:
```
Implement the task for spec sync-optimization, first run spec-workflow-guide to get the workflow guide then implement the task:

Role: Python 缓存专家,精通缓存策略和性能优化

Task: 增强 CacheManager 支持交易日历缓存。要求:
1. 添加 load_trading_calendar(start_date, end_date): 批量加载并缓存
2. 添加 is_trading_day(trade_date, market='CN'): 查询缓存优先
3. 使用 lru_cache 装饰器缓存查询结果
4. 添加 TTL 机制: 缓存7天后过期
5. 添加 get_cache_stats(): 返回命中率、缓存大小
6. 缓存容量控制: LRU 淘汰,最大1000个日期
7. 线程安全

复用以下组件:
- 现有 CacheManager 基础设施
- functools.lru_cache
- DatabaseManager 查询交易日历

Restrictions:
- 不破坏现有缓存功能
- 内存使用可控(<50MB)
- 线程安全
- TTL 必须准确

Success Criteria:
- 交易日历缓存工作正常
- 缓存命中率 >90%
- TTL 过期正确触发
- get_cache_stats() 返回准确数据

After completing this task:
1. Update tasks.md: change `- [ ] 3.1` to `- [-] 3.1` when starting
2. Implement the code following the requirements
3. Test the implementation
4. Update tasks.md: change `- [-] 3.1` to `- [x] 3.1` when completed
```

### Task 3.2: 增强 CacheManager 支持股票元数据缓存

- [x] 3.2 增强 CacheManager 支持股票元数据缓存
  - **File**: `simtradedata/performance/cache_manager.py` (继续修改)
  - **Purpose**: 添加股票元数据缓存,包括最后数据日期
  - **Details**:
    - 添加 `get_last_data_date()`: 查询最后数据日期(缓存)
    - 添加 `set_last_data_date()`: 更新缓存
    - 添加 `get_stock_metadata()`: 查询股票元数据
    - 添加 `load_stock_metadata_batch()`: 批量预加载
    - TTL: 最后数据日期60秒, 元数据1天
  - **_Leverage**:
    - Task 3.1 的缓存基础
    - `functools.lru_cache`
    - `DatabaseManager`
  - **_Requirements**: Requirement 4 (智能缓存机制)
  - **Success**:
    - 元数据缓存工作正常
    - 缓存命中率 >70%
    - TTL 过期正确
  - **_Prompt**:
```
Implement the task for spec sync-optimization, first run spec-workflow-guide to get the workflow guide then implement the task:

Role: Python 缓存专家,精通缓存策略和数据一致性

Task: 增强 CacheManager 支持股票元数据缓存。要求:
1. 添加 get_last_data_date(symbol, frequency): 查询最后数据日期
2. 添加 set_last_data_date(symbol, frequency, last_date): 更新缓存
3. 添加 get_stock_metadata(symbol): 查询股票基本信息
4. 添加 load_stock_metadata_batch(symbols): 批量预加载
5. TTL 配置: last_data_date=60秒, metadata=1天
6. 缓存一致性: 数据更新时自动刷新缓存
7. 内存控制: LRU 淘汰, 最大5000只股票

复用以下组件:
- Task 3.1 的缓存基础设施
- functools.lru_cache
- DatabaseManager 查询元数据

Restrictions:
- 缓存一致性是强制要求
- 内存使用<100MB
- TTL 必须准确
- 线程安全

Success Criteria:
- 元数据缓存工作正常
- 缓存命中率 >70%
- set_last_data_date() 正确更新缓存
- 批量预加载性能良好

After completing this task:
1. Update tasks.md: change `- [ ] 3.2` to `- [-] 3.2` when starting
2. Implement the code following the requirements
3. Test the implementation
4. Update tasks.md: change `- [-] 3.2` to `- [x] 3.2` when completed
```

### Task 3.3: 集成缓存到 IncrementalSync

- [x] 3.3 集成 CacheManager 到 IncrementalSync
  - **File**: `simtradedata/sync/incremental.py` (修改现有)
  - **Purpose**: 在同步流程中使用缓存,减少数据库查询
  - **Details**:
    - 在 `__init__()` 中初始化 CacheManager
    - 在 `sync_all_symbols()` 开始时预加载缓存
    - 修改 `get_last_data_date()` 使用缓存
    - 修改 `_is_trading_day()` 使用缓存
    - 数据更新后调用 `set_last_data_date()`
  - **_Leverage**:
    - `CacheManager` (Task 3.1, 3.2)
    - 现有 `IncrementalSync` 逻辑
  - **_Requirements**: Requirement 4 (智能缓存机制)
  - **Success**:
    - 缓存集成透明
    - 数据库查询减少 60-80%
    - 数据一致性保持
  - **_Prompt**:
```
Implement the task for spec sync-optimization, first run spec-workflow-guide to get the workflow guide then implement the task:

Role: Python 后端开发专家,精通系统集成和缓存应用

Task: 将 CacheManager 集成到 IncrementalSync。要求:
1. 在 __init__() 中初始化 CacheManager
2. 在 sync_all_symbols() 开始时预加载:
   - 交易日历(最近2年)
   - 活跃股票元数据
3. 修改 get_last_data_date(): 先查缓存,未命中查数据库并缓存
4. 修改 _is_trading_day(): 使用 cache_manager.is_trading_day()
5. 在 sync_symbol_range() 完成后更新缓存
6. 读取配置 performance.cache.enable 控制启用
7. 添加降级逻辑: 缓存禁用或失败时直接查数据库

复用以下组件:
- CacheManager (Task 3.1, 3.2)
- 现有 IncrementalSync 逻辑
- Config 系统

Restrictions:
- 不破坏现有功能
- 缓存失败不影响同步
- 数据一致性必须保持
- 支持启用/禁用缓存

Success Criteria:
- 缓存透明集成到同步流程
- 数据库查询减少 60-80%
- 缓存命中率符合预期
- 数据一致性100%
- 现有测试全部通过

After completing this task:
1. Update tasks.md: change `- [ ] 3.3` to `- [-] 3.3` when starting
2. Implement the code following the requirements
3. Test the implementation
4. Update tasks.md: change `- [-] 3.3` to `- [x] 3.3` when completed
```

### Task 3.4: 缓存功能单元测试和性能测试

- [x] 3.4 创建缓存功能测试
  - **File**: `tests/unit/test_cache_manager_enhancement.py` 和 `tests/performance/test_cache_benchmark.py`
  - **Purpose**: 测试缓存功能和性能提升
  - **Details**:
    - 单元测试: 缓存命中/未命中、TTL过期、LRU淘汰
    - 性能测试: 对比有/无缓存的查询性能
    - 集成测试: 验证缓存一致性
  - **_Leverage**:
    - pytest
    - CacheManager (Task 3.1, 3.2)
  - **_Requirements**: Requirement 4 (智能缓存机制)
  - **Success**:
    - 测试覆盖率100%
    - 缓存命中性能提升 >100倍
  - **_Prompt**:
```
Implement the task for spec sync-optimization, first run spec-workflow-guide to get the workflow guide then implement the task:

Role: Python 测试工程师,精通缓存测试和性能分析

Task: 创建缓存功能的全面测试。要求:
1. 单元测试 (test_cache_manager_enhancement.py):
   - 测试 is_trading_day() 缓存命中/未命中
   - 测试 get_last_data_date() 缓存和更新
   - 测试 TTL 过期: 7天后交易日历过期
   - 测试 LRU 淘汰: 超过容量时淘汰
   - 测试批量预加载性能
   - 测试 get_cache_stats() 准确性

2. 性能测试 (test_cache_benchmark.py):
   - 场景1: 无缓存查询1000次交易日历
   - 场景2: 有缓存查询1000次交易日历
   - 场景3: 无缓存查询1000次最后数据日期
   - 场景4: 有缓存查询1000次最后数据日期
   - 对比性能提升倍数

复用以下组件:
- pytest, pytest-benchmark
- CacheManager
- DatabaseManager

Restrictions:
- 测试独立可重复
- TTL 测试准确
- 性能提升必须显著(>100倍)

Success Criteria:
- 单元测试覆盖率100%
- TTL 和 LRU 测试通过
- 缓存命中性能提升 >100倍
- 所有测试通过

After completing this task:
1. Update tasks.md: change `- [ ] 3.4` to `- [-] 3.4` when starting
2. Implement the code following the requirements
3. Test the implementation
4. Update tasks.md: change `- [-] 3.4` to `- [x] 3.4` when completed
```

## Phase 4: 性能监控 (PerformanceMonitor)

### Task 4.1: 创建性能监控器核心类

- [x] 4.1 创建 PerformanceMonitor 核心类
  - **File**: `simtradedata/monitoring/performance_monitor.py`
  - **Purpose**: 实现性能监控器,记录同步各阶段性能指标
  - **Details**:
    - 实现 `PerformanceMonitor` 类
    - 实现 `start_phase()`: 开始计时
    - 实现 `end_phase()`: 结束计时,记录统计
    - 实现 `record_metric()`: 记录自定义指标
    - 实现 `get_phase_stats()`: 获取阶段统计
    - 实现 `generate_report()`: 生成性能报告(JSON/文本)
    - 实现 `identify_bottlenecks()`: 识别瓶颈(耗时>50%)
  - **_Leverage**:
    - `time` 模块计时
    - `psutil` 资源监控(可选)
    - `collections.defaultdict`
  - **_Requirements**: Requirement 5 (性能监控和分析)
  - **Success**:
    - 性能监控功能完整
    - 报告清晰易读
    - 瓶颈识别准确
  - **_Prompt**:
```
Implement the task for spec sync-optimization, first run spec-workflow-guide to get the workflow guide then implement the task:

Role: Python 性能监控专家,精通性能分析和指标收集

Task: 创建 PerformanceMonitor 类实现性能监控。要求:
1. start_phase(name): 记录阶段开始时间和资源使用
2. end_phase(name, records_count): 计算耗时、吞吐量
3. record_metric(name, value): 记录自定义指标
4. get_phase_stats(name): 返回阶段统计 PhaseStats
5. generate_report(): 生成 PerformanceReport 对象
6. identify_bottlenecks(): 识别耗时占比>50%的阶段
7. 支持 JSON 和文本两种报告格式
8. 可选: 使用 psutil 监控 CPU、内存

复用以下组件:
- time 模块计时
- psutil 资源监控(可选)
- collections.defaultdict 存储指标

Restrictions:
- 性能监控不影响同步性能
- 内存使用最小化
- 线程安全
- 不依赖外部服务

Success Criteria:
- 计时准确(<1ms误差)
- 报告格式清晰易读
- 瓶颈识别逻辑正确
- 资源监控准确(如果启用)

After completing this task:
1. Update tasks.md: change `- [ ] 4.1` to `- [-] 4.1` when starting
2. Implement the code following the requirements
3. Test the implementation
4. Update tasks.md: change `- [-] 4.1` to `- [x] 4.1` when completed
```

### Task 4.2: 集成性能监控到同步流程

- [x] 4.2 集成 PerformanceMonitor 到同步流程
  - **File**: `simtradedata/sync/incremental.py` (修改现有)
  - **Purpose**: 在同步各阶段记录性能指标
  - **Details**:
    - 在 `sync_all_symbols()` 中集成监控器
    - 监控阶段: 智能补充、增量同步、数据验证
    - 在每个阶段开始/结束时调用 start_phase/end_phase
    - 同步完成后生成报告并记录日志
    - 配置控制启用/禁用监控
  - **_Leverage**:
    - `PerformanceMonitor` (Task 4.1)
    - 现有 `IncrementalSync` 流程
  - **_Requirements**: Requirement 5 (性能监控和分析)
  - **Success**:
    - 监控透明集成
    - 报告信息完整
    - 不影响同步性能
  - **_Prompt**:
```
Implement the task for spec sync-optimization, first run spec-workflow-guide to get the workflow guide then implement the task:

Role: Python 后端开发专家,精通系统集成和监控埋点

Task: 将 PerformanceMonitor 集成到 IncrementalSync。要求:
1. 在 __init__() 中初始化 PerformanceMonitor
2. 在 sync_all_symbols() 开始调用 monitor.start_phase("total")
3. 监控以下阶段:
   - "smart_backfill": 智能补充阶段
   - "incremental_sync": 增量同步阶段
   - "data_validation": 数据验证阶段
4. 每个阶段开始/结束时调用 start_phase/end_phase
5. 同步完成后调用 generate_report() 生成报告
6. 报告记录到日志(INFO级别)
7. 识别瓶颈并记录优化建议
8. 读取配置 performance.monitor.enable 控制启用

复用以下组件:
- PerformanceMonitor (Task 4.1)
- 现有 IncrementalSync 流程
- Config 系统

Restrictions:
- 监控开销 <1%
- 监控失败不影响同步
- 报告生成快速(<100ms)
- 支持启用/禁用

Success Criteria:
- 监控透明集成到同步流程
- 报告信息完整准确
- 瓶颈识别正确
- 性能开销 <1%
- 现有测试全部通过

After completing this task:
1. Update tasks.md: change `- [ ] 4.2` to `- [-] 4.2` when starting
2. Implement the code following the requirements
3. Test the implementation
4. Update tasks.md: change `- [-] 4.2` to `- [x] 4.2` when completed
```

### Task 4.3: 性能监控测试

- [x] 4.3 创建性能监控测试
  - **File**: `tests/unit/test_performance_monitor.py`
  - **Purpose**: 测试性能监控器功能
  - **Details**:
    - 测试计时准确性
    - 测试吞吐量计算
    - 测试瓶颈识别逻辑
    - 测试报告生成格式
    - 测试多阶段监控
  - **_Leverage**:
    - pytest
    - PerformanceMonitor (Task 4.1)
  - **_Requirements**: Requirement 5 (性能监控和分析)
  - **Success**:
    - 测试覆盖率100%
    - 计时误差 <1ms
  - **_Prompt**:
```
Implement the task for spec sync-optimization, first run spec-workflow-guide to get the workflow guide then implement the task:

Role: Python 测试工程师,精通性能测试和验证

Task: 创建 PerformanceMonitor 的全面单元测试。要求:
1. 测试计时准确性: start/end phase 计时误差<1ms
2. 测试吞吐量计算: records_count / duration 正确
3. 测试瓶颈识别: 耗时>50%正确识别
4. 测试报告生成: JSON 和文本格式正确
5. 测试多阶段监控: 嵌套阶段正确记录
6. 测试异常处理: 阶段未开始就结束、重复开始等
7. 测试资源监控: CPU、内存监控准确(如果启用)

复用以下组件:
- pytest 测试框架
- PerformanceMonitor
- time.sleep() 模拟耗时

Restrictions:
- 测试独立可重复
- 计时测试允许合理误差(±5ms)
- 不依赖外部服务

Success Criteria:
- 所有测试通过
- 测试覆盖率100%
- 计时误差 <1ms
- 报告格式验证通过

After completing this task:
1. Update tasks.md: change `- [ ] 4.3` to `- [-] 4.3` when starting
2. Implement the code following the requirements
3. Test the implementation
4. Update tasks.md: change `- [-] 4.3` to `- [x] 4.3` when completed
```

## Phase 5: 集成测试和调优

### Task 5.1: 端到端集成测试

- [x] 5.1 创建端到端集成测试
  - **File**: `tests/integration/test_sync_optimization_e2e.py`
  - **Purpose**: 测试所有优化模块集成后的完整同步流程
  - **Details**:
    - 测试完整流程: 连接池+批量写入+缓存+监控
    - 测试100只股票增量同步
    - 验证数据正确性
    - 验证性能提升
    - 检查性能报告
  - **_Leverage**:
    - 所有优化模块
    - 真实数据库和数据源
  - **_Requirements**: All requirements
  - **Success**:
    - 集成测试通过
    - 性能提升达标
    - 数据一致性100%
  - **_Prompt**:
```
Implement the task for spec sync-optimization, first run spec-workflow-guide to get the workflow guide then implement the task:

Role: Python 集成测试专家,精通端到端测试和系统验证

Task: 创建 sync-optimization 的端到端集成测试。要求:
1. 测试场景: 100只股票完整同步流程
2. 启用所有优化: 连接池、批量写入、缓存、监控
3. 验证数据正确性: 对比优化前后数据100%一致
4. 验证性能提升: 同步速度 >500条/秒
5. 验证内存使用: 峰值 <1GB
6. 检查性能报告: 包含所有阶段统计和瓶颈识别
7. 测试错误恢复: 模拟连接失败、批量写入失败
8. 使用真实 BaoStock 和 SQLite

复用以下组件:
- ConnectionPool, BatchWriter, CacheManager, PerformanceMonitor
- IncrementalSync
- 真实数据库和数据源

Restrictions:
- 测试环境一致
- 使用测试数据库(不污染生产)
- 测试时间 <5分钟
- 数据一致性验证严格

Success Criteria:
- 所有优化模块正常工作
- 同步速度 >500条/秒
- 数据一致性100%
- 内存使用 <1GB
- 性能报告完整准确
- 错误恢复测试通过

After completing this task:
1. Update tasks.md: change `- [ ] 5.1` to `- [-] 5.1` when starting
2. Implement the code following the requirements
3. Test the implementation
4. Update tasks.md: change `- [-] 5.1` to `- [x] 5.1` when completed
```

### Task 5.2: 性能调优和参数优化

- [x] 5.2 性能调优和参数优化
  - **File**: 多个配置文件和代码调整
  - **Purpose**: 根据测试结果调优参数,达到 >500条/秒 目标
  - **Details**:
    - 运行性能基准测试
    - 分析瓶颈和性能报告
    - 调整参数: pool_size, batch_size, max_workers 等
    - 验证调优效果
    - 更新默认配置
  - **_Leverage**:
    - PerformanceMonitor 报告
    - 所有性能测试
  - **_Requirements**: All requirements
  - **Success**:
    - 同步速度达到 >500条/秒
    - 内存使用 <1.5GB
    - 参数配置最优
  - **_Prompt**:
```
Implement the task for spec sync-optimization, first run spec-workflow-guide to get the workflow guide then implement the task:

Role: Python 性能优化专家,精通系统调优和瓶颈分析

Task: 根据测试结果进行性能调优,达成优化目标。要求:
1. 运行所有性能基准测试,收集数据
2. 分析 PerformanceMonitor 报告,识别瓶颈
3. 调优参数:
   - connection_pool.pool_size: 测试 1-4 的最优值
   - batch_writer.batch_size: 测试 50-200 的最优值
   - sync.max_workers: 测试 2-4 的最优值
   - sync.batch_size: 测试 25-100 的最优值
4. 每次调整后运行性能测试验证效果
5. 找到最优参数组合
6. 更新 config.yaml 默认配置
7. 生成调优报告: 参数对比、性能提升曲线

复用以下组件:
- 所有性能测试
- PerformanceMonitor 报告
- Config 系统

Restrictions:
- 测试环境稳定一致
- 每个参数至少测试3次
- 内存使用不超过1.5GB
- 调优过程记录完整

Success Criteria:
- 同步速度 >500条/秒
- 内存使用 <1.5GB
- 找到最优参数组合
- 调优报告清晰展示过程和结果

After completing this task:
1. Update tasks.md: change `- [ ] 5.2` to `- [-] 5.2` when starting
2. Implement the code following the requirements
3. Test the implementation
4. Update tasks.md: change `- [-] 5.2` to `- [x] 5.2` when completed
```

### Task 5.3: 文档更新和代码清理

- [x] 5.3 文档更新和代码清理
  - **File**: 多个文档文件和代码优化
  - **Purpose**: 更新文档,清理临时代码,完善注释
  - **Details**:
    - 更新 README: 添加性能优化说明
    - 更新配置文档: 详细说明优化配置项
    - 添加性能优化指南
    - 清理临时代码和调试日志
    - 完善代码注释和类型注解
    - 运行代码质量检查: pylint, mypy
  - **_Leverage**:
    - 现有文档结构
    - pylint, mypy 工具
  - **_Requirements**: All requirements
  - **Success**:
    - 文档完整准确
    - 代码质量高
    - 无临时代码残留
  - **_Prompt**:
```
Implement the task for spec sync-optimization, first run spec-workflow-guide to get the workflow guide then implement the task:

Role: Python 技术文档专家和代码质量专家

Task: 完成文档更新和代码清理。要求:
1. 更新文档:
   - README.md: 添加性能优化章节
   - docs/configuration.md: 详细说明优化配置
   - docs/performance_guide.md: 性能优化指南(新建)
   - API 文档: 新增类和方法的文档字符串
2. 代码清理:
   - 删除临时调试代码和 print 语句
   - 删除未使用的导入和变量
   - 统一代码风格: 运行 black 格式化
3. 代码质量:
   - 完善类型注解: 所有函数参数和返回值
   - 完善文档字符串: 所有公共类和方法
   - 运行 pylint: 评分 >9.0
   - 运行 mypy: 无类型错误
4. 生成优化前后对比报告

复用以下组件:
- 现有文档结构
- black, pylint, mypy 工具

Restrictions:
- 不改变功能代码逻辑
- 文档准确性优先
- 遵循项目文档规范

Success Criteria:
- 所有文档更新完整
- 代码 pylint 评分 >9.0
- mypy 检查无错误
- 无临时代码残留
- 对比报告清晰展示优化效果

After completing this task:
1. Update tasks.md: change `- [ ] 5.3` to `- [-] 5.3` when starting
2. Implement the code following the requirements
3. Test the implementation
4. Update tasks.md: change `- [-] 5.3` to `- [x] 5.3` when completed
```

## Summary

本任务分解文档将 sync-optimization 设计分解为 15 个原子任务,分 5 个阶段实施:

- **Phase 1** (4 tasks): 连接管理优化 - 预期减少重连开销30-50%
- **Phase 2** (4 tasks): 批量写入优化 - 预期提升5-10倍
- **Phase 3** (4 tasks): 缓存优化 - 减少60-80%数据库查询
- **Phase 4** (3 tasks): 性能监控 - 提供可见性和分析
- **Phase 5** (3 tasks): 集成调优 - 最终达成 >500条/秒 目标

每个任务都包含详细的 `_Prompt` 字段,提供实施指导,确保任务顺利完成。
