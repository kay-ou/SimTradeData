# Technology Stack

## Project Type
SimTradeData 是一个 Python 库项目,提供金融数据管理功能。它同时是:
- **数据管理库**: 提供可导入的Python模块
- **CLI工具**: 提供命令行界面进行数据同步和管理
- **API服务**: 提供REST API接口供外部调用
- **数据处理引擎**: 支持批量数据处理和技术指标计算

## Core Technologies

### Primary Language(s)
- **Language**: Python 3.12+
- **Runtime**: CPython 3.12+
- **Language-specific tools**:
  - **Poetry**: 依赖管理和打包工具
  - **pip**: Python包安装器
  - **pytest**: 测试框架

### Key Dependencies/Libraries
核心依赖库:
- **mootdx (0.11.x)**: 通达信本地数据读取库,提供极速OHLCV和财务数据
- **baostock (0.8.x)**: 证券宝官方API,提供权威季度指标和除权除息数据
- **qstock (1.3.x)**: 在线金融数据API,提供240+完整财务字段
- **backtrader (1.9.x)**: 回测框架,提供数据格式标准
- **pyyaml (6.0.x)**: YAML配置文件解析
- **pyfolio-reloaded (0.9.x)**: 策略分析工具

开发依赖:
- **pytest (8.4.x)**: 单元测试和集成测试框架
- **pandas (2.3.x)**: 数据分析和处理
- **flask (3.1.x)**: REST API服务框架
- **flask-cors (6.0.x)**: 跨域资源共享支持
- **black (25.x)**: 代码格式化工具
- **isort (6.x)**: import语句排序工具
- **autoflake (2.3.x)**: 自动清理未使用的导入
- **pre-commit (4.2.x)**: Git钩子管理
- **psutil (7.0.x)**: 系统性能监控

### Application Architecture
SimTradeData 采用分层架构设计:

1. **接口层 (interfaces/)**:
   - PTrade API适配器
   - REST API服务
   - API网关

2. **业务逻辑层 (api/, markets/, extended_data/, preprocessor/)**:
   - API路由器 (router.py)
   - 查询构建器 (query_builders.py)
   - 多市场管理 (multi_market.py)
   - 数据预处理引擎 (engine.py)

3. **数据同步层 (sync/)**:
   - 同步管理器 (manager.py)
   - 增量更新 (incremental.py)
   - 数据验证 (validator.py)
   - 缺口检测 (gap_detector.py)

4. **性能优化层 (performance/)**:
   - 查询优化器 (query_optimizer.py)
   - 缓存管理器 (cache_manager.py)

5. **监控层 (monitoring/)**:
   - 数据质量监控 (data_quality.py)
   - 告警系统 (alert_system.py)

6. **数据存储层 (database/, data_sources/)**:
   - 数据库管理器 (manager.py)
   - 数据源适配器 (mootdx_adapter.py, baostock_adapter.py, qstock_adapter.py)

7. **核心功能层 (core/)**:
   - 配置管理 (config_mixin.py)
   - 日志管理 (logging_mixin.py)
   - 错误处理 (error_handling.py)

### Data Storage (if applicable)
- **Primary storage**: SQLite 3 数据库
  - 11个专用表结构
  - 优化的索引设计
  - 零冗余存储架构
- **Caching**:
  - 内存缓存 (Python字典)
  - 文件缓存 (磁盘临时文件)
- **Data formats**:
  - 数据库: SQLite
  - 配置: YAML
  - 数据交换: Pandas DataFrame
  - API响应: JSON

### External Integrations (if applicable)
- **APIs**:
  - Mootdx本地通达信数据接口
  - BaoStock证券宝在线API
  - QStock在线金融数据API
- **Protocols**:
  - HTTP/REST (Flask REST API)
  - 本地文件系统访问 (Mootdx)
- **Authentication**:
  - BaoStock: 用户名密码认证
  - QStock: 无需认证
  - Mootdx: 本地文件访问

### Monitoring & Dashboard Technologies (if applicable)
- **Dashboard Framework**: CLI命令行界面 (Python argparse)
- **Real-time Communication**: 标准输出流,实时进度条
- **Visualization Libraries**:
  - 进度条: tqdm/自定义进度条
  - 表格输出: 自定义格式化器
- **State Management**:
  - SQLite数据库作为状态持久化
  - 内存状态管理 (Python对象)

## Development Environment

### Build & Development Tools
- **Build System**: Poetry build系统
- **Package Management**: Poetry (pyproject.toml)
- **Development workflow**:
  - Poetry shell激活虚拟环境
  - Poetry run执行Python脚本
  - 实时代码热重载 (开发模式)

### Code Quality Tools
- **Static Analysis**: Python内置类型检查
- **Formatting**:
  - Black: 代码格式化 (行长120字符)
  - isort: import语句排序
  - autoflake: 清理未使用的导入
- **Testing Framework**:
  - pytest: 单元测试和集成测试
  - pytest markers: 测试分类 (unit, integration, sync, performance)
  - 测试覆盖率: 100% (125 passed, 4 skipped)
- **Documentation**: Markdown文档 + Python docstrings

### Version Control & Collaboration
- **VCS**: Git
- **Branching Strategy**: 主干开发 (main分支)
- **Code Review Process**: Pull Request审查

### Dashboard Development (if applicable)
- **Live Reload**: 不适用 (CLI工具)
- **Port Management**: Flask默认5000端口,可配置
- **Multi-Instance Support**: 支持多实例运行

## Deployment & Distribution (if applicable)
- **Target Platform(s)**:
  - Linux (主要平台,WSL2测试通过)
  - macOS (兼容)
  - Windows (通过WSL2)
- **Distribution Method**:
  - Poetry打包发布
  - 本地pip安装
  - 源码直接使用
- **Installation Requirements**:
  - Python 3.12+
  - SQLite 3
  - 足够磁盘空间 (建议10GB+用于数据存储)
- **Update Mechanism**: pip/poetry更新

## Technical Requirements & Constraints

### Performance Requirements
- **查询响应时间**: <100ms (单股票单年数据)
- **数据同步速度**: >500条/秒
- **内存使用**: <2GB (正常运行)
- **存储空间**: 约5-10GB (完整A股历史数据)
- **并发支持**: 支持多线程查询

### Compatibility Requirements
- **Platform Support**:
  - Linux: Ubuntu 20.04+ (主要支持)
  - macOS: 10.15+ (兼容)
  - Windows: 通过WSL2支持
- **Dependency Versions**:
  - Python: >=3.12, <4.0
  - SQLite: 3.x
  - 所有依赖版本锁定在pyproject.toml
- **Standards Compliance**:
  - PTrade API标准
  - PEP 8代码规范
  - SQLite标准

### Security & Compliance
- **Security Requirements**:
  - 本地数据存储,无网络暴露
  - 数据源API密钥安全存储 (环境变量)
  - 无用户认证 (单用户工具)
- **Compliance Standards**: 不适用 (个人工具)
- **Threat Model**:
  - 本地文件访问安全
  - 数据源API密钥保护
  - SQL注入防护 (参数化查询)

### Scalability & Reliability
- **Expected Load**:
  - 单用户使用
  - 支持5000+股票数据
  - 10年+历史数据
- **Availability Requirements**:
  - 本地工具,无在线服务
  - 数据源故障自动切换
  - 断点续传支持
- **Growth Projections**:
  - 支持港股、美股扩展
  - 支持更多数据源接入
  - 支持分布式部署

## Technical Decisions & Rationale

### Decision Log
1. **SQLite vs PostgreSQL**:
   - 选择SQLite: 单用户工具,无需复杂数据库部署,文件级数据库易于备份和迁移
   - 放弃PostgreSQL: 部署复杂,对于单用户场景过重

2. **Poetry vs pip**:
   - 选择Poetry: 现代化依赖管理,锁文件支持,虚拟环境管理优秀
   - 兼容pip: 仍支持pip安装

3. **Mootdx优先策略**:
   - Mootdx作为主数据源: 本地访问速度极快,49个核心字段覆盖
   - QStock补充: 240+字段完整覆盖三大报表详细科目
   - BaoStock备用: 官方权威数据,季度聚合指标

4. **零冗余架构**:
   - 11个专用表设计: 完全消除数据冗余,节省30%存储空间
   - 单一职责原则: 每个表只存储其特定领域的数据
   - 性能权衡: 查询需要JOIN但整体性能仍提升2-5倍

5. **同步vs实时**:
   - 选择同步模式: 离线数据足够用于回测,无需实时行情
   - 增量更新: 每日同步增量数据,减少网络负载
   - 缺口修复: 智能检测和修复历史数据缺口

## Known Limitations
- **实时数据**: 当前仅支持离线数据同步,不支持实时tick级数据
  - 影响: 无法用于实盘高频交易
  - 解决方案: 未来版本可集成WebSocket实时数据流

- **数据源限制**: 依赖第三方数据源,可能存在限流和服务中断
  - 影响: 数据同步可能失败或变慢
  - 解决方案: 多数据源故障转移,断点续传机制

- **单机部署**: 当前仅支持单机部署,不支持分布式
  - 影响: 无法横向扩展,处理能力受限于单机性能
  - 解决方案: 未来版本可考虑分布式架构

- **港股美股**: 港股美股支持尚不完整
  - 影响: 仅A股数据完整,港股美股数据可能缺失
  - 解决方案: 逐步完善多市场数据源集成

