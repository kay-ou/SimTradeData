# SimTradeData 规范工作流配置

本目录包含 SimTradeData 项目的 Spec Workflow MCP Server 配置和文档模板。

## 📁 目录结构

```
.spec-workflow/
├── README.md                    # 本文件 - 工作流说明
├── MIGRATION_PLAN.md            # 文档迁移方案
├── config.toml                  # 主配置文件
├── config.example.toml          # 配置示例
│
├── steering/                    # 指导性文档(项目级)
│   ├── product.md              # 产品概述 - 产品目标、用户、特性
│   ├── tech.md                 # 技术栈 - 技术选型、架构、依赖
│   ├── structure.md            # 项目结构 - 目录组织、命名规范
│   ├── architecture.md         # 架构设计 - 分层架构、模块划分
│   └── data-sync-standards.md  # 数据同步规范 - 验证、事务、日志规范
│
├── templates/                   # 功能特性模板(功能级)
│   ├── requirements-template.md # 需求文档模板
│   ├── design-template.md      # 设计文档模板
│   └── tasks-template.md       # 任务文档模板
│
├── specs/                       # 具体功能规范
│   ├── full-sync/              # 全量同步功能
│   │   └── analysis.md         # 需求分析
│   └── sync-optimization/      # 同步优化功能
│       └── summary.md          # 优化总结
│
├── archive/                     # 归档文档
│   └── fixes/                  # 问题修复记录
│       └── 2025-10-02/        # 按日期归档
│
└── approvals/                   # 审批记录(自动生成)
```

## 📋 文档说明

### Steering Documents (指导性文档)

这些文档定义了整个项目的方向和规范,所有新功能都应遵循这些原则:

#### 1. product.md - 产品概述
- **用途**: 定义产品愿景、目标用户、核心特性
- **内容**:
  - 产品目标: 高性能金融数据系统
  - 目标用户: 量化交易开发者、策略研究人员等
  - 核心特性: 零冗余架构、完整PTrade支持等
  - 成功指标: 100%测试覆盖、0%冗余等
- **更新频率**: 较少,仅在产品战略调整时更新

#### 2. tech.md - 技术栈
- **用途**: 定义技术选型和架构标准
- **内容**:
  - 编程语言: Python 3.12+
  - 核心依赖: mootdx, baostock, qstock等
  - 架构模式: 分层架构(7层)
  - 技术决策记录
- **更新频率**: 偶尔,在引入新技术时更新

#### 3. structure.md - 项目结构
- **用途**: 定义代码组织和规范
- **内容**:
  - 目录结构和职责
  - 命名规范(snake_case, PascalCase等)
  - 导入模式和代码组织
  - 文档标准
- **更新频率**: 较少,在重构架构时更新

#### 4. architecture.md - 架构设计 ⭐
- **用途**: 详细的架构设计指南
- **内容**:
  - 零冗余存储架构设计
  - 分层架构详解(7层)
  - 数据源优先级策略
  - 模块职责和交互
  - PTrade API适配方案
- **更新频率**: 较少,在架构演进时更新
- **来源**: 从docs/Architecture_Guide.md迁移

#### 5. data-sync-standards.md - 数据同步规范 ⭐
- **用途**: 定义数据同步的标准和规范
- **内容**:
  - 数据验证规范(股票代码、日期范围)
  - 事务管理规范(ACID保证)
  - 批量导入规范(性能优化)
  - 日志规范(结构化日志)
  - 错误处理规范(重试策略)
  - 数据清理规范(重复数据处理)
- **更新频率**: 偶尔,在规范升级时更新
- **来源**: 从docs/DATA_SYNC_STANDARDS.md迁移

### Feature Templates (功能模板)

这些是创建新功能规范时使用的模板:

#### 1. requirements-template.md - 需求文档模板
- **用途**: 定义功能需求和验收标准
- **使用方法**:
  ```bash
  mkdir -p .spec-workflow/specs/数据查询优化
  cp .spec-workflow/templates/requirements-template.md \
     .spec-workflow/specs/数据查询优化/requirements.md
  ```
- **包含**:
  - User Stories (用户故事)
  - Acceptance Criteria (验收标准)
  - Non-Functional Requirements (非功能需求)

#### 2. design-template.md - 设计文档模板
- **用途**: 描述功能的技术设计
- **包含**:
  - 架构设计和组件划分
  - 接口定义
  - 数据模型
  - 错误处理策略
  - 测试策略

#### 3. tasks-template.md - 任务文档模板
- **用途**: 将设计分解为具体开发任务
- **包含**:
  - 任务列表(带状态跟踪)
  - 任务依赖关系
  - 文件路径和具体工作
  - 测试任务

## 🚀 使用工作流

### 步骤 1: 了解项目背景

开发新功能前,先阅读steering文档:

```bash
# 了解产品愿景
cat .spec-workflow/steering/product.md

# 了解技术标准
cat .spec-workflow/steering/tech.md

# 了解项目结构
cat .spec-workflow/steering/structure.md
```

### 步骤 2: 创建功能规范

使用模板创建新功能的规范文档:

```bash
# 1. 创建功能目录
FEATURE_NAME="数据查询优化"
mkdir -p .spec-workflow/specs/$FEATURE_NAME

# 2. 复制模板
cp .spec-workflow/templates/requirements-template.md \
   .spec-workflow/specs/$FEATURE_NAME/requirements.md

cp .spec-workflow/templates/design-template.md \
   .spec-workflow/specs/$FEATURE_NAME/design.md

cp .spec-workflow/templates/tasks-template.md \
   .spec-workflow/specs/$FEATURE_NAME/tasks.md

# 3. 编辑文档,填写具体内容
```

### 步骤 3: 编写需求 (requirements.md)

1. 定义User Stories
2. 编写Acceptance Criteria (WHEN-THEN-IF格式)
3. 确认与product.md的对齐
4. 定义非功能需求

### 步骤 4: 设计方案 (design.md)

1. 基于requirements.md设计架构
2. 确认符合tech.md和structure.md规范
3. 识别可复用的组件
4. 定义接口和数据模型
5. 规划错误处理和测试

### 步骤 5: 分解任务 (tasks.md)

1. 将design拆分为具体任务
2. 定义任务依赖顺序
3. 为每个任务指定文件和需求
4. 跟踪任务状态: `[ ]` `[-]` `[x]`

### 步骤 6: 实施和跟踪

1. 按tasks.md顺序实施
2. 完成一个任务后更新状态为`[x]`
3. 遇到问题及时记录
4. 保持文档和代码同步

## ⚙️ 配置说明

### config.toml 配置项

```toml
# 项目根目录
projectDir = "/home/kay/dev/SimTradeData"

# Dashboard端口
port = 3000

# 是否自动启动Dashboard
autoStartDashboard = false

# 界面语言
lang = "zh"
```

完整配置说明见 `config.toml` 文件。

## 📚 最佳实践

### 1. 保持文档同步
- 代码变更时同步更新相关文档
- 定期审查steering文档是否需要更新

### 2. 遵循模板结构
- 不要随意修改模板格式
- 保持文档的一致性和可读性

### 3. 详细记录决策
- 重要技术决策记录在tech.md的Decision Log
- 设计权衡记录在design.md

### 4. 使用检查清单
- 每个模板末尾都有审查检查清单
- 完成文档后逐项检查

### 5. 持续改进
- 根据实际使用情况优化模板
- 记录lessons learned

## 🔧 维护指南

### 更新Steering文档

当需要更新项目级文档时:

1. 创建feature分支
2. 更新相关steering文档
3. 评估对现有功能的影响
4. 提交PR并团队审查
5. 更新后通知团队

### 创建新模板

如果需要新的文档模板:

1. 在templates/目录创建新模板
2. 遵循现有模板的格式
3. 添加使用说明和示例
4. 更新本README

## 📖 参考资源

- [Spec Workflow MCP Server 文档](https://github.com/anthropics/spec-workflow-mcp)
- [SimTradeData 架构指南](../docs/Architecture_Guide.md)
- [SimTradeData 开发者指南](../docs/DEVELOPER_GUIDE.md)

## 🤝 贡献

改进工作流模板:

1. Fork项目
2. 创建feature分支
3. 修改模板或配置
4. 提交PR并说明改进理由

---

**维护者**: SimTradeData Team
**最后更新**: 2025-10-02
