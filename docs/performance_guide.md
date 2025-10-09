# SimTradeData 性能优化指南

## 概述

本指南详细介绍 SimTradeData 的性能优化机制、配置方法和最佳实践。通过系统化的性能优化,SimTradeData 实现了 6 倍的同步性能提升,达到 600 条/秒的数据吞吐量。

## 性能优化模块

### 1. 连接管理优化 (ConnectionManager)

**问题**: BaoStock 数据源频繁连接/断开导致严重性能开销 (约 2 秒/次)

**解决方案**: 会话保活和线程安全访问管理

**性能提升**: 200x (2秒/次 → 0.01秒/次)

#### 配置示例

```yaml
performance:
  connection_manager:
    enable: true
    session_timeout: 600      # 会话超时时间(秒),默认10分钟
    heartbeat_interval: 60    # 心跳检测间隔(秒)
    lock_timeout: 10          # 锁等待超时(秒)
```

#### 最佳实践

- **session_timeout**: 设置为 600 秒(10分钟)足够大多数同步场景
- **heartbeat_interval**: 保持 60 秒,过短会增加开销
- **lock_timeout**: 10 秒适合大多数场景,避免死锁

#### 工作原理

1. **会话保活**: 维护单例会话,避免频繁 login/logout
2. **线程安全**: 使用 `threading.Lock` 串行化 BaoStock API 访问
3. **心跳检测**: 定期发送轻量级查询验证会话有效性
4. **自动重连**: 会话超时后自动重连

### 2. 批量写入优化 (BatchWriter)

**问题**: 逐条数据库写入导致大量事务开销 (约 10ms/条)

**解决方案**: 批量事务提交

**性能提升**: 400x (10ms/条 → 0.025ms/条)

#### 配置示例

```yaml
performance:
  batch_writer:
    enable: true
    batch_size: 100           # 批量写入批次大小
    auto_flush: true          # 自动刷新缓冲区
```

#### 最佳实践

- **batch_size**:
  - 推荐值: 100 (最佳平衡点)
  - 50: 适合内存受限环境
  - 200: 边际改善有限,不推荐

#### 工作原理

1. **缓冲机制**: 使用 `defaultdict` 按表缓冲数据
2. **自动刷新**: 达到 `batch_size` 自动执行批量插入
3. **事务保护**: 整个批次在单个事务中执行,失败自动回滚
4. **表隔离**: 不同表的批次独立处理,互不影响

### 3. 缓存优化 (CacheManager)

**问题**: 频繁数据库查询导致性能瓶颈 (约 5ms/次)

**解决方案**: 智能多级缓存

**性能提升**: 100x (5ms/次 → 0.05ms/次,缓存命中时)

#### 配置示例

```yaml
performance:
  cache:
    enable: true
    max_size_mb: 500
    trading_calendar_ttl: 604800  # 交易日历缓存 TTL: 7天
    stock_metadata_ttl: 86400     # 股票元数据缓存 TTL: 1天
```

#### 缓存策略

| 数据类型 | TTL | 命中率 | 说明 |
|---------|-----|--------|------|
| 交易日历 | 7天 | >95% | 变化极少,长期缓存 |
| 股票元数据 | 1天 | >85% | 日更新,中期缓存 |
| 最后数据日期 | 60秒 | >70% | 频繁查询,短期缓存 |

#### 工作原理

1. **LRU 淘汰**: 使用 `functools.lru_cache` 自动淘汰最少使用项
2. **TTL 过期**: 基于时间的自动过期机制
3. **批量预加载**: 同步开始时批量加载常用数据
4. **一致性保证**: 数据更新时自动刷新缓存

### 4. 性能监控 (PerformanceMonitor)

**问题**: 缺乏可见性,难以识别性能瓶颈

**解决方案**: 阶段化性能监控和报告

**开销**: <1% (对同步性能的影响)

#### 配置示例

```yaml
performance:
  monitor:
    enable: true
    enable_resource_monitoring: false  # 禁用 CPU/内存监控以减少开销
    detailed_logging: true
    report_format: "text"              # text 或 json
```

#### 监控指标

- **阶段耗时**: 各个同步阶段的精确耗时
- **吞吐量**: 每秒处理的记录数
- **瓶颈识别**: 自动识别耗时占比 >50% 的阶段
- **自定义指标**: 支持记录缓存命中率、数据库查询次数等

#### 报告示例

```
=== 性能监控报告 ===
总耗时: 120.45 秒
总记录: 72,500 条
总吞吐量: 601.87 记录/秒

阶段统计:
- 智能补充: 35.2秒 (29%), 20,000条, 568 条/秒
- 增量同步: 72.8秒 (60%), 50,000条, 687 条/秒
- 数据验证: 12.5秒 (10%), 2,500条, 200 条/秒

识别的瓶颈:
- 增量同步 (60% 耗时) - 建议: 检查网络延迟和数据源性能
```

## 同步系统优化

### 智能补充 (Smart Backfill)

**问题**: 全量补充数据效率低,资源消耗大

**解决方案**: 基于抽样的智能补充

**效率提升**: 10x (仅补充有缺口的数据)

#### 配置示例

```yaml
sync:
  enable_smart_backfill: true
  backfill_batch_size: 50      # 补充批次大小
  backfill_sample_size: 10     # 抽样检查大小
  max_sync_days: 30            # 限制单次同步范围
```

#### 工作原理

1. **抽样检测**: 随机抽样 10 个交易日检查数据完整性
2. **精准补充**: 仅对检测到缺口的股票执行补充
3. **批量处理**: 批量下载和写入,减少 I/O 开销
4. **范围限制**: 限制单次同步范围,避免超时

### 并发优化

#### 配置示例

```yaml
sync:
  max_workers: 3               # 并发工作线程数
  batch_size: 50               # 同步批次大小
```

#### 最佳实践

| max_workers | CPU使用率 | 性能 | 说明 |
|------------|----------|------|------|
| 1 | 25% | 基准 | 串行处理 |
| 2 | 50% | 1.6x | 良好 |
| **3** | 75% | **2.2x** | **最优** ✓ |
| 4 | 95% | 2.3x | 边际改善小 |
| 5 | 100% | 2.3x | 资源竞争,无改善 |

**推荐**: `max_workers=3` 是最佳平衡点

## 配置调优指南

### 1. 根据环境调整

#### 高性能服务器
```yaml
sync:
  max_workers: 3
  batch_size: 75

performance:
  batch_writer:
    batch_size: 100
  cache:
    max_size_mb: 500
```

#### 内存受限环境
```yaml
sync:
  max_workers: 2
  batch_size: 25

performance:
  batch_writer:
    batch_size: 50
  cache:
    max_size_mb: 200
```

#### 网络受限环境
```yaml
sync:
  max_workers: 2              # 减少并发连接
  max_sync_days: 7            # 缩小同步范围

performance:
  connection_manager:
    session_timeout: 300      # 缩短超时避免僵尸连接
```

### 2. 性能调优步骤

1. **启用性能监控**
   ```yaml
   performance:
     monitor:
       enable: true
       detailed_logging: true
   ```

2. **运行基准测试**
   ```bash
   poetry run pytest tests/performance/ -v
   ```

3. **分析性能报告**
   - 查看各阶段耗时占比
   - 识别瓶颈阶段
   - 检查吞吐量是否达标

4. **逐步调优参数**
   - 每次只调整一个参数
   - 运行测试验证效果
   - 记录性能变化

5. **验证数据一致性**
   ```bash
   poetry run pytest tests/sync/ -v
   ```

## 性能监控最佳实践

### 1. 日常监控

**关键指标**:
- 同步吞吐量: 目标 >500 条/秒
- 缓存命中率: 目标 >80%
- 内存使用: 目标 <1.5GB
- 错误率: 目标 <1%

**监控方式**:
```python
from simtradedata.monitoring import PerformanceMonitor

monitor = PerformanceMonitor()
monitor.start_phase("sync")
# ... 执行同步 ...
monitor.end_phase("sync", records_count=10000)

report = monitor.generate_report()
print(report.to_text())
```

### 2. 故障排查

#### 性能下降

**症状**: 吞吐量显著下降

**检查清单**:
1. 缓存是否启用? `performance.cache.enable=true`
2. 批量写入是否工作? `performance.batch_writer.enable=true`
3. 网络连接是否稳定? 检查日志中的重连次数
4. 数据源是否限流? 查看 API 调用频率

**解决方案**:
```yaml
# 启用所有优化
performance:
  connection_manager:
    enable: true
  batch_writer:
    enable: true
  cache:
    enable: true
```

#### 内存过高

**症状**: 内存使用超过 1.5GB

**检查清单**:
1. `batch_size` 是否过大?
2. `max_workers` 是否过多?
3. 缓存大小是否合理?

**解决方案**:
```yaml
sync:
  batch_size: 25              # 减小批次
  max_workers: 2              # 减少并发

performance:
  batch_writer:
    batch_size: 50            # 减小批量写入
  cache:
    max_size_mb: 200          # 限制缓存大小
```

#### 数据不一致

**症状**: 数据验证失败

**检查清单**:
1. 批量写入事务是否正常?
2. 缓存是否与数据库同步?
3. 并发写入是否有竞态条件?

**解决方案**:
```bash
# 运行数据一致性测试
poetry run pytest tests/integration/test_sync_optimization_e2e.py::TestSyncOptimizationE2E::test_data_consistency_with_optimizations -v

# 检查批量写入统计
poetry run pytest tests/unit/test_batch_writer.py -v
```

## 性能基准

### 优化前后对比

| 指标 | 优化前 | 优化后 | 提升 |
|-----|-------|--------|------|
| 连接开销 | 2秒/次 | 0.01秒/次 | 200x |
| 批量写入 | 10ms/条 | 0.025ms/条 | 400x |
| 缓存查询 | 5ms/次 | 0.05ms/次 | 100x |
| 总体吞吐量 | ~100条/秒 | ~600条/秒 | 6x |
| 内存使用 | 500MB | 800MB | +60% |

### 达成目标

- ✅ 同步速度: 600 条/秒 > 500 条/秒
- ✅ 内存使用: 800MB < 1.5GB
- ✅ 数据一致性: 100% (集成测试验证)

## 进阶优化

### 1. 数据压缩存储 (规划中)

**目标**: 减少磁盘 I/O,提升查询性能

**方案**:
- 使用 SQLite 内置压缩
- 对历史数据分区存储
- 冷数据归档

### 2. 异步 I/O (规划中)

**目标**: 进一步提升并发性能

**方案**:
- 使用 `asyncio` 异步数据下载
- 异步数据库写入
- 异步缓存更新

### 3. 分布式缓存 (规划中)

**目标**: 支持多进程共享缓存

**方案**:
- 集成 Redis 缓存
- 缓存失效通知
- 分布式锁

## 常见问题

### Q1: 如何验证优化效果?

运行性能对比测试:
```bash
poetry run pytest tests/integration/test_sync_optimization_e2e.py::TestSyncOptimizationE2E::test_optimization_vs_baseline_comparison -v
```

### Q2: 优化会影响数据一致性吗?

不会。所有优化都经过严格的数据一致性测试:
```bash
poetry run pytest tests/integration/test_sync_optimization_e2e.py::TestSyncOptimizationE2E::test_data_consistency_with_optimizations -v
```

### Q3: 如何禁用某个优化模块?

在 `config.yaml` 中设置对应的 `enable` 为 `false`:
```yaml
performance:
  connection_manager:
    enable: false  # 禁用连接管理优化
```

### Q4: 性能监控开销有多大?

性能监控开销 <1%,可以放心在生产环境启用。如需进一步降低开销,禁用资源监控:
```yaml
performance:
  monitor:
    enable_resource_monitoring: false
```

## 参考资源

- [性能调优报告](performance_tuning_report.md): 详细的调优过程和测试结果
- [集成测试](../tests/integration/test_sync_optimization_e2e.py): 端到端性能验证
- [架构指南](Architecture_Guide.md): 系统架构和设计原理

---

**最后更新**: 2025-10-09
**版本**: 1.0.0
**维护**: SimTradeData 开发团队
