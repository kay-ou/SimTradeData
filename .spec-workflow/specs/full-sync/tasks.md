# Full-Sync Tasks 任务清单

本文档将 Full-Sync 的设计分解为可执行的任务。由于 Full-Sync 已经在 `simtradedata/sync/manager.py` 中实现，这些任务主要是**完善和优化**现有实现。

---

## 阶段 1: 代码审查与规范检查 ✅ (已完成 - 代码层面)

- [x] 1.1 审查交易日历更新逻辑 ✅
  - 文件: `simtradedata/sync/manager.py:941-1062` (_update_trading_calendar)
  - ✅ 增量更新策略已实现并符合设计
  - ✅ 批量插入优化已正确实现
  - ✅ 错误处理基本符合数据同步规范
  - _Leverage: 数据同步规范 (.spec-workflow/steering/data-sync-standards.md)_
  - _Requirements: 需求 1_

- [x] 1.2 审查股票列表更新逻辑 ✅
  - 文件: `simtradedata/sync/manager.py:1064-1364` (_update_stock_list)
  - ✅ 指数代码过滤已实现（基于数字范围判断）
  - ✅ 批量操作优化已正确实现
  - ✅ 增量更新策略符合设计
  - _Leverage: 数据同步规范 (.spec-workflow/steering/data-sync-standards.md)_
  - _Requirements: 需求 1_

- [x] 1.3 审查扩展数据同步逻辑 ✅
  - 文件: `simtradedata/sync/manager.py:1596-1808` (_sync_extended_data)
  - ✅ 批量模式判断逻辑已实现（待处理 >= 50 或总库存 >= 500）
  - ✅ 事务使用正确（使用 transaction() 上下文管理器）
  - ✅ 数据验证符合规范
  - _Leverage: 数据同步规范 (.spec-workflow/steering/data-sync-standards.md)_
  - _Requirements: 需求 3, 需求 4_

- [x] 1.4 审查断点续传逻辑 ✅
  - 文件: `simtradedata/sync/manager.py:772-939` (_get_extended_data_symbols_to_process)
  - ✅ 智能完整性检查已实现（财务数据检查最近 2 年年报，估值数据检查目标日期前后 10 天）
  - ✅ 状态修复机制已实现
  - ✅ 断点续传逻辑可靠
  - _Leverage: design.md 断点续传架构_
  - _Requirements: 需求 3, 需求 5_

---

## 阶段 2: 数据验证增强 ✅ (已完成)

- [x] 2.1 完善财务数据验证 ✅
  - 文件: `simtradedata/sync/manager.py:57-74` (DataQualityValidator.is_valid_financial_data)
  - ✅ 验证逻辑符合数据同步规范 1.2 节（放宽原则）
  - ✅ 修复了 None 值处理 bug
  - ✅ 添加了 18 个单元测试（包括参数化测试）
  - ✅ 测试覆盖：负值、零值、None、边界情况
  - _测试文件: tests/sync/test_data_quality_validator.py_

- [x] 2.2 完善估值数据验证 ✅
  - 文件: `simtradedata/sync/manager.py:76-99` (DataQualityValidator.is_valid_valuation_data)
  - ✅ 验证逻辑符合数据同步规范 1.3 节（宽松原则）
  - ✅ 检查所有估值指标（PE, PB, PS, PCF）
  - ✅ 添加了 19 个单元测试（包括参数化测试）
  - ✅ 测试覆盖：负数 PE、零值、None、所有指标组合
  - _测试文件: tests/sync/test_data_quality_validator.py_

- [x] 2.3 添加报告期有效性验证测试 ✅
  - 文件: `simtradedata/sync/manager.py:100-117` (DataQualityValidator.is_valid_report_date)
  - ✅ 添加了 16 个单元测试（包括参数化测试）
  - ✅ 测试覆盖：未来日期、MIN_REPORT_YEAR 前、无效格式、边界情况
  - ✅ 所有测试通过，覆盖率 100%
  - _测试文件: tests/sync/test_data_quality_validator.py_

**阶段 2 总结**:
- 创建了 `tests/sync/test_data_quality_validator.py`
- 共 53 个测试用例，100% 通过率
- 发现并修复了 1 个 bug（None 值处理）

---

## 阶段 3: 事务管理优化 ✅ (已完成)

- [x] 3.1 审查单股票事务保护 ✅
  - 文件: `simtradedata/sync/manager.py:1869-2070` (_sync_single_symbol_with_transaction)
  - 确认使用 transaction() 上下文管理器
  - 验证事务原子性
  - 确保状态更新正确
  - _Leverage: 数据同步规范 2.1 节（事务管理规范）_
  - _Requirements: 需求 3_
  - _Prompt: Implement the task for spec full-sync, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 数据库事务专家，精通 ACID 原则和 SQLite 事务 | Task: 审查 _sync_single_symbol_with_transaction 方法（行 1869-2070），确认是否正确使用 db_manager.transaction() 上下文管理器（数据同步规范 2.1 节），验证事务原子性（标记 processing -> 数据同步 -> 更新状态），检查异常处理是否会导致事务回滚 | Restrictions: 不要修改事务逻辑；必须确认异常时自动回滚；验证状态更新的原子性 | Success: 确认事务使用正确，异常处理符合规范，提供事务性能优化建议_

- [x] 3.2 添加事务失败场景测试 ✅
  - 文件: 新建 `tests/sync/test_transaction_handling.py`
  - ✅ 创建了 7 个测试用例，100% 通过率
  - ✅ 测试覆盖：事务回滚、提交、原子性、并发事务、嵌套事务
  - ✅ 发现并修复了 financials 表 schema 问题（缺少 source 字段）
  - _测试文件: tests/sync/test_transaction_handling.py_

**阶段 3 总结**:
- 创建了 `tests/sync/test_transaction_handling.py`
- 共 7 个测试用例，100% 通过率
- 验证了事务原子性、回滚和并发处理
- 修复了 1 个 schema 问题

---

## 阶段 4: 批量导入优化验证 ✅ (已完成)

- [x] 4.1 验证批量模式判断逻辑 ✅
  - 文件: `simtradedata/sync/manager.py:1629-1658` (批量模式判断)
  - ✅ 创建了 7 个测试用例（6 个通过，1 个跳过）
  - ✅ 测试覆盖：阈值检测、决策逻辑、数据完整性、回退机制
  - ✅ 验证了批量模式阈值（待处理 >= 50 或 总库存 >= 500）
  - _测试文件: tests/sync/test_batch_performance.py_

- [x] 4.2 完善批量导入回退机制 ✅
  - 文件: `simtradedata/sync/manager.py:1762-1766` (批量导入失败处理)
  - ✅ 测试了批量导入回退机制
  - ✅ 验证批量失败时正确回退到逐个模式
  - _测试文件: tests/sync/test_batch_performance.py (test_batch_fallback_mechanism)_

- [x] 4.3 优化批量数据清理逻辑 ✅
  - 文件: `simtradedata/sync/manager.py:1730-1751` (字段映射和数据清理)
  - ✅ 测试了批量数据完整性
  - ✅ 验证字段映射和数据一致性
  - _测试文件: tests/sync/test_batch_performance.py (test_batch_import_data_integrity)_

**阶段 4 总结**:
- 创建了 `tests/sync/test_batch_performance.py`
- 共 7 个测试用例（6 通过 + 1 跳过性能对比测试）
- 验证了批量模式判断、回退和数据完整性

---

## 阶段 5: 断点续传增强 ✅ (已完成)

- [x] 5.1 优化状态修复逻辑 ✅
  - 文件: `simtradedata/sync/manager.py:889-920` (状态修复逻辑)
  - ✅ 创建了 4 个测试用例，100% 通过率
  - ✅ 测试覆盖：过期状态清理、状态选择性清理、时间边界
  - _测试文件: tests/sync/test_status_repair.py_

- [x] 5.2 完善过期状态清理 ✅
  - 文件: `simtradedata/sync/manager.py:785-792` (清理过期 pending 状态)
  - ✅ 验证了 1 天阈值的清理逻辑
  - ✅ 测试了保留最近记录和清理过期记录
  - _测试文件: tests/sync/test_status_repair.py_

- [x] 5.3 添加断点续传集成测试 ✅
  - 文件: 新建 `tests/sync/test_resume_integration.py`
  - ✅ 创建了 5 个集成测试用例，100% 通过率
  - ✅ 测试覆盖：部分完成恢复、全部完成跳过、首次同步、full-sync集成、空列表边界
  - ✅ 验证了断点续传进度计算和阶段跳过逻辑
  - _测试文件: tests/sync/test_resume_integration.py_

**阶段 5 总结**:
- 创建了 `tests/sync/test_status_repair.py` 和 `tests/sync/test_resume_integration.py`
- 共 9 个测试用例（4 个状态清理 + 5 个断点续传集成），100% 通过率
- 验证了过期状态清理、断点续传恢复和进度计算
- 发现并修复了 SQLite 时间边界测试问题（使用 2 天间隔确保稳定性）

---

## 阶段 6: 缺口检测优化 ⚠️ (代码完成，测试不足)

- [x] 6.1 审查缺口检测逻辑 ✅ (代码层面)
  - 文件: `simtradedata/sync/gap_detector.py` (GapDetector.detect_all_gaps)
  - 确认缺口检测算法正确
  - 验证交易日历依赖
  - 添加缺口检测测试
  - _Leverage: GapDetector 实现_
  - _Requirements: 需求 6_
  - _Prompt: Implement the task for spec full-sync, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 数据完整性工程师，精通时间序列数据和缺口检测 | Task: 审查 simtradedata/sync/gap_detector.py 中的 detect_all_gaps 方法，验证缺口检测算法正确性（依赖交易日历，排除非交易日），确认检测范围合理（最近 30 天），编写单元测试验证缺口检测逻辑（tests/sync/test_gap_detector.py） | Restrictions: 不要修改检测算法核心；必须依赖交易日历；测试用例覆盖连续缺口、单日缺口、无缺口等场景 | Success: 缺口检测算法正确，测试用例覆盖所有场景，无误报和漏报_

- [x] 6.2 完善自动修复逻辑 ✅ (代码层面)
  - 文件: `simtradedata/sync/manager.py:2072-2189` (_auto_fix_gaps)
  - 确认修复限制合理（最大 10 个）
  - 验证上市日期检查逻辑
  - 添加修复效果统计
  - _Leverage: design.md 缺口修复架构_
  - _Requirements: 需求 6_
  - _Prompt: Implement the task for spec full-sync, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 数据修复工程师，精通数据补全和异常处理 | Task: 审查 _auto_fix_gaps 方法（行 2072-2189），验证修复限制合理性（最大 10 个是否够用），确认上市日期检查逻辑正确（缺口早于上市日期应跳过），优化修复效果统计（添加成功率、平均修复时间），编写测试用例验证修复逻辑（tests/sync/test_gap_fix.py） | Restrictions: 不要修改修复限制（除非有明确理由）；必须检查上市日期；测试用例使用真实数据库 | Success: 修复逻辑正确，限制合理，测试用例验证修复效果_

---

## 阶段 7: 日志和监控完善

- [ ] 7.1 审查日志级别使用
  - 文件: `simtradedata/sync/manager.py` (全文)
  - 确认日志级别符合数据同步规范 4.1 节
  - 调整不合理的日志级别
  - 添加关键节点日志
  - _Leverage: 数据同步规范 4.1 节（日志规范）_
  - _Requirements: 需求 8_
  - _Prompt: Implement the task for spec full-sync, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 日志和监控专家，精通日志最佳实践 | Task: 审查 simtradedata/sync/manager.py 中所有日志调用，确认日志级别符合数据同步规范 4.1 节（DEBUG: 调试信息；INFO: 正常运行；WARNING: 需要关注；ERROR: 错误），调整不合理的日志级别（如标准失败响应应为 DEBUG 而非 WARNING），添加关键节点日志（阶段开始/结束、批量模式切换、断点续传决策） | Restrictions: 不要删除现有日志；遵循日志规范；避免日志过多影响性能 | Success: 所有日志级别符合规范，关键节点有日志，日志信息清晰有用_

- [ ] 7.2 完善进度条显示
  - 文件: `simtradedata/sync/manager.py` (使用 create_phase_progress 的地方)
  - 确认进度条使用正确
  - 优化进度描述信息
  - 添加进度回调测试
  - _Leverage: utils/progress_bar.py_
  - _Requirements: 需求 8_
  - _Prompt: Implement the task for spec full-sync, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 用户体验工程师，精通进度显示和用户反馈 | Task: 审查 simtradedata/sync/manager.py 中所有使用 create_phase_progress 的地方，确认进度条使用正确（total 参数准确，update 调用及时），优化进度描述信息（使用 update_phase_description 提供详细状态），编写测试用例验证进度回调（tests/sync/test_progress_callback.py） | Restrictions: 不要修改进度条工具代码；确保 total 参数准确；测试用例验证进度更新正确 | Success: 进度条显示准确，描述信息清晰，测试用例验证进度正确_

- [ ] 7.3 添加性能监控埋点
  - 文件: `simtradedata/sync/manager.py` (关键方法)
  - 添加性能监控日志
  - 记录各阶段耗时
  - 生成性能报告
  - _Leverage: BaseManager._log_performance 方法_
  - _Requirements: 需求 8_
  - _Prompt: Implement the task for spec full-sync, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 性能监控工程师，精通性能分析和指标采集 | Task: 在 simtradedata/sync/manager.py 的关键方法中添加性能监控埋点，使用 BaseManager._log_performance 方法记录各阶段耗时（基础数据更新、增量同步、扩展数据同步、缺口检测、验证），优化 run_full_sync 返回结果，添加每个阶段的 duration_seconds，编写性能分析脚本（scripts/analyze_sync_performance.py） | Restrictions: 不要影响正常功能；性能日志使用 INFO 级别；性能分析脚本生成可视化报告 | Success: 性能监控完整，各阶段耗时清晰，性能分析脚本生成有用报告_

---

## 阶段 8: 测试覆盖完善

- [ ] 8.1 补充单元测试覆盖
  - 文件: `tests/sync/test_sync_manager.py` (补充)
  - 为未覆盖的方法添加单元测试
  - 覆盖边界情况和异常场景
  - 提高测试覆盖率到 95%+
  - _Leverage: 现有测试用例_
  - _Requirements: 所有需求_
  - _Prompt: Implement the task for spec full-sync, first run spec-workflow-guide to get the workflow guide then implement the task: Role: QA 工程师，精通单元测试和测试覆盖率分析 | Task: 分析 simtradedata/sync/manager.py 的测试覆盖率（使用 pytest-cov），识别未覆盖的方法和分支，在 tests/sync/test_sync_manager.py 中补充单元测试，覆盖边界情况（空列表、None 参数、异常日期）和异常场景（数据源失败、数据库错误），目标覆盖率 95%+ | Restrictions: 使用 Mock 隔离外部依赖；测试用例独立可重复；遵循现有测试代码风格 | Success: 测试覆盖率达到 95%+，所有公共方法有测试，边界情况和异常场景被覆盖_

- [ ] 8.2 添加集成测试场景
  - 文件: 新建 `tests/sync/test_full_sync_scenarios.py`
  - 测试首次同步场景
  - 测试增量同步场景
  - 测试错误恢复场景
  - _Leverage: design.md 测试策略_
  - _Requirements: 所有需求_
  - _Prompt: Implement the task for spec full-sync, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 集成测试工程师，精通端到端场景测试 | Task: 创建 tests/sync/test_full_sync_scenarios.py，编写完整的集成测试场景，包括：1) 首次同步（空数据库，完整同步所有数据），2) 增量同步（已有数据，增量更新），3) 错误恢复（模拟数据源失败、网络超时等异常），使用真实数据库和 Mock 数据源，验证整个流程的正确性 | Restrictions: 使用 pytest 标记为 integration；测试用例之间独立；清理测试数据 | Success: 所有场景测试通过，验证 run_full_sync 在各种场景下的正确性_

- [ ] 8.3 添加性能基准测试
  - 文件: 新建 `tests/sync/test_full_sync_benchmark.py`
  - 测试批量模式性能
  - 测试大规模数据同步性能
  - 生成性能基准报告
  - _Leverage: pytest-benchmark_
  - _Requirements: 需求 4_
  - _Prompt: Implement the task for spec full-sync, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 性能测试工程师，精通基准测试和性能分析 | Task: 创建 tests/sync/test_full_sync_benchmark.py，编写性能基准测试，包括：1) 批量模式 vs 逐个模式性能对比（测试 100、500、1000 只股票），2) 大规模数据同步测试（测试 5000+ 只股票的同步速度），3) 内存使用测试，使用 pytest-benchmark 生成性能报告，验证性能目标（>500 条/秒） | Restrictions: 使用 pytest 标记为 performance；使用真实数据库；测试环境隔离 | Success: 性能测试通过，批量模式显著快于逐个模式（>3 倍），大规模同步达到性能目标_

---

## 阶段 9: 文档和示例

- [ ] 9.1 更新 API 文档
  - 文件: `docs/api/sync_manager.md` (新建或更新)
  - 文档化 run_full_sync 方法
  - 添加参数说明和返回值说明
  - 提供使用示例
  - _Leverage: 现有 API 文档结构_
  - _Requirements: 需求 8_
  - _Prompt: Implement the task for spec full-sync, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 技术文档工程师，精通 API 文档编写 | Task: 创建或更新 docs/api/sync_manager.md，文档化 run_full_sync 方法，包括：方法签名、参数说明（target_date, symbols, frequencies）、返回值说明（完整的 JSON 结构和字段含义）、异常说明、使用示例（首次同步、增量同步、断点续传），遵循 Markdown 格式和现有文档风格 | Restrictions: 遵循现有文档结构；使用中文；提供可运行的代码示例 | Success: API 文档完整清晰，示例代码可运行，用户能轻松理解和使用_

- [ ] 9.2 编写使用指南
  - 文件: `docs/guides/full_sync_guide.md` (新建)
  - 编写完整的使用指南
  - 添加最佳实践建议
  - 提供故障排查指南
  - _Leverage: requirements.md 和 design.md_
  - _Requirements: 所有需求_
  - _Prompt: Implement the task for spec full-sync, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 技术文档专家，精通用户指南编写 | Task: 创建 docs/guides/full_sync_guide.md，编写完整的 Full-Sync 使用指南，包括：1) 功能概述，2) 快速开始（CLI 命令和 Python API），3) 配置选项说明，4) 最佳实践（何时使用批量模式、如何优化性能、断点续传建议），5) 故障排查（常见错误和解决方案），6) 性能优化建议 | Restrictions: 使用中文；提供实际可运行的示例；遵循 Markdown 格式 | Success: 使用指南完整实用，用户能快速上手，故障排查有效_

- [ ] 9.3 更新架构文档
  - 文件: `docs/architecture/sync_architecture.md` (更新)
  - 更新 Full-Sync 架构描述
  - 添加流程图和序列图
  - 说明设计决策
  - _Leverage: design.md 架构图_
  - _Requirements: 所有需求_
  - _Prompt: Implement the task for spec full-sync, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 系统架构师，精通架构文档编写和技术写作 | Task: 更新 docs/architecture/sync_architecture.md，补充 Full-Sync 架构描述，包括：1) 架构概览（分层架构、组件关系），2) 执行流程图（基于 design.md 的 Mermaid 图），3) 断点续传架构（状态管理、完整性检查），4) 批量导入优化（判断逻辑、回退机制），5) 设计决策说明（为什么使用事务、为什么批量阈值是 50/500） | Restrictions: 使用中文；使用 Mermaid 绘制图表；说明清晰逻辑严密 | Success: 架构文档完整准确，图表清晰，设计决策有理有据_

---

## 阶段 10: 最终验证和发布

- [ ] 10.1 执行完整回归测试
  - 运行所有单元测试和集成测试
  - 验证测试覆盖率达标
  - 确认所有测试通过
  - _Leverage: pytest 和 pytest-cov_
  - _Requirements: 所有需求_
  - _Prompt: Implement the task for spec full-sync, first run spec-workflow-guide to get the workflow guide then implement the task: Role: QA 主管，负责最终质量验证 | Task: 执行完整的回归测试，运行 pytest tests/sync/ -v --cov=simtradedata/sync --cov-report=html，验证测试覆盖率达标（目标 95%+），确认所有测试通过，检查测试报告，识别并修复失败的测试用例，生成测试报告（保存到 docs/reports/full_sync_test_report.md） | Restrictions: 所有测试必须通过；覆盖率必须达标；不得跳过失败测试 | Success: 所有测试通过，覆盖率 95%+，测试报告完整_

- [ ] 10.2 执行性能验证测试
  - 运行性能基准测试
  - 验证性能目标达标
  - 生成性能报告
  - _Leverage: tests/sync/test_full_sync_benchmark.py_
  - _Requirements: 性能需求_
  - _Prompt: Implement the task for spec full-sync, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 性能测试主管，负责性能验证 | Task: 执行性能基准测试，运行 pytest tests/sync/test_full_sync_benchmark.py -v --benchmark-only，验证性能目标达标（同步速度 >500 条/秒，批量模式 >3 倍提升），分析性能报告，识别性能瓶颈，生成性能验证报告（保存到 docs/reports/full_sync_performance_report.md） | Restrictions: 使用真实数据库；测试环境隔离；性能数据准确 | Success: 所有性能指标达标，性能报告完整，无明显性能瓶颈_

- [ ] 10.3 完成规格文档
  - 更新 tasks.md 标记所有任务完成
  - 生成实施总结报告
  - 提交最终审查
  - _Leverage: spec-workflow_
  - _Requirements: 所有需求_
  - _Prompt: Implement the task for spec full-sync, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 项目经理，负责项目收尾和文档整理 | Task: 完成规格文档收尾工作，将 .spec-workflow/specs/full-sync/tasks.md 中所有任务标记为完成（将 [ ] 改为 [x]），生成实施总结报告（包括完成的任务、测试结果、性能数据、遗留问题），更新 .spec-workflow/specs/full-sync/README.md（如果有），提交最终审查 | Restrictions: 确保所有任务确实完成；总结报告真实准确；遗留问题清晰列出 | Success: 所有任务标记完成，总结报告完整，规格文档归档_

---

## 任务说明

### 状态标记

- `[ ]` - 待完成
- `[-]` - 进行中
- `[x]` - 已完成

### 任务优先级

1. **阶段 1-3**: 核心审查和修复（高优先级）
2. **阶段 4-6**: 优化和增强（中优先级）
3. **阶段 7-9**: 监控、测试和文档（中优先级）
4. **阶段 10**: 最终验证（必须完成）

### 依赖关系

- 阶段 1 必须先完成，才能进行后续优化
- 阶段 8 测试覆盖可以与阶段 2-7 并行
- 阶段 9 文档可以在阶段 1-7 基本完成后进行
- 阶段 10 必须在所有其他阶段完成后进行

### 预估工时

- 阶段 1: 2-3 天（4 个审查任务）
- 阶段 2: 1-2 天（3 个验证任务）
- 阶段 3: 1-2 天（2 个事务任务）
- 阶段 4: 2-3 天（3 个批量优化任务）
- 阶段 5: 2-3 天（3 个断点续传任务）
- 阶段 6: 1-2 天（2 个缺口检测任务）
- 阶段 7: 1-2 天（3 个监控任务）
- 阶段 8: 3-4 天（3 个测试任务）
- 阶段 9: 2-3 天（3 个文档任务）
- 阶段 10: 1-2 天（3 个验证任务）

**总计**: 约 16-25 个工作日

---

## 注意事项

1. **不要破坏现有功能**: Full-Sync 已经在生产环境运行，所有修改必须保持向后兼容
2. **遵循数据同步规范**: 所有修改必须符合 `.spec-workflow/steering/data-sync-standards.md` 的规范
3. **测试驱动**: 每个修改都要有对应的测试用例
4. **性能优先**: 不要牺牲性能来换取代码简洁性
5. **日志完整**: 关键操作都要有日志记录，方便排查问题
