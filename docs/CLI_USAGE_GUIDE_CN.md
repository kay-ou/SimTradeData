# SimTradeData CLI Usage Guide

**[中文](CLI_USAGE_GUIDE.md)** | **[English](CLI_USAGE_GUIDE_EN.md)**

SimTradeData provides a powerful command-line interface (CLI) for various data management tasks including database initialization, data synchronization, queries, and system monitoring.

## 🚀 快速开始

### 环境准备

```bash
# 克隆项目
git clone <repository-url>
cd SimTradeData

# 安装依赖
poetry install

# 激活虚拟环境
poetry shell
```

### 数据库初始化

```bash
# 创建数据库和表结构
poetry run python scripts/init_database.py --db-path data/simtradedata.db

# 验证数据库创建
ls -la data/simtradedata.db
```

### 验证安装

```bash
# 检查CLI可用性
poetry run python -m simtradedata --help

# 运行基础测试
poetry run python -m pytest tests/test_database.py -v
```

## 📋 命令概览

### 数据同步命令

```bash
# 全量同步 - 同步指定日期的所有数据
poetry run python -m simtradedata full-sync --target-date 2024-01-24

# 全量同步 - 同步指定股票
poetry run python -m simtradedata full-sync --symbols 000001.SZ 000002.SZ

# 全量同步 - 同步所有股票
poetry run python -m simtradedata full-sync --all-stocks

# 全量同步 - 指定多个频率
poetry run python -m simtradedata full-sync --frequencies 1d 1h

# 增量同步 - 指定日期范围
poetry run python -m simtradedata incremental --start-date 2024-01-01 --end-date 2024-01-31

# 增量同步 - 指定股票和频率
poetry run python -m simtradedata incremental --start-date 2024-01-01 --symbols 000001.SZ --frequency 1d
```

### 缺口检测和修复命令

```bash
# 缺口检测和修复 - 指定日期范围
poetry run python -m simtradedata gap-fix --start-date 2024-01-01 --end-date 2024-01-31

# 缺口修复 - 指定股票
poetry run python -m simtradedata gap-fix --start-date 2024-01-01 --symbols 000001.SZ 000002.SZ

# 缺口修复 - 指定频率
poetry run python -m simtradedata gap-fix --start-date 2024-01-01 --frequencies 1d 1h
```

### 断点续传命令

```bash
# 断点续传 - 恢复指定股票的同步
poetry run python -m simtradedata resume --symbol 000001.SZ

# 断点续传 - 指定频率
poetry run python -m simtradedata resume --symbol 000001.SZ --frequency 1d
```

### 状态查询命令

```bash
# 查看当前同步状态
poetry run python -m simtradedata status
```

## 🔧 配置选项

### 命令行参数

所有命令都支持以下全局参数：

```bash
# 指定数据库路径
poetry run python -m simtradedata full-sync --db-path /path/to/database.db

# 指定配置文件路径
poetry run python -m simtradedata full-sync --config /path/to/config.yaml

# 启用详细输出
poetry run python -m simtradedata full-sync --verbose

# 安静模式（最小化输出）
poetry run python -m simtradedata full-sync --quiet

# 禁用进度条
poetry run python -m simtradedata full-sync --no-progress
```

### 配置文件示例

创建 `config.yaml` 配置文件：

```yaml
database:
  path: "data/simtradedata.db"

data_sources:
  baostock:
    enabled: true
    priority: 1
  mootdx:
    enabled: true
    priority: 2
  qstock:
    enabled: true
    priority: 3

logging:
  level: "INFO"
  file: "logs/simtradedata.log"
```

## 🔍 实际用法示例

### 基本工作流程

```bash
# 1. 创建数据库
poetry run python scripts/init_database.py --db-path data/simtradedata.db

# 2. 全量同步今日数据
poetry run python -m simtradedata full-sync

# 3. 同步指定股票的历史数据
poetry run python -m simtradedata full-sync --symbols 000001.SZ 000002.SZ --target-date 2024-01-01

# 4. 增量更新最近一周数据
poetry run python -m simtradedata incremental --start-date 2024-01-01 --end-date 2024-01-07

# 5. 修复数据缺口
poetry run python -m simtradedata gap-fix --start-date 2024-01-01 --end-date 2024-01-31

# 6. 查看同步状态
poetry run python -m simtradedata status
```

### 高级使用场景

```bash
# 从文件读取股票代码
poetry run python -m simtradedata full-sync --symbols-file symbols.txt

# 多频率同步
poetry run python -m simtradedata full-sync --frequencies 1d 1h 5m

# 断点续传（恢复中断的同步）
poetry run python -m simtradedata resume --symbol 000001.SZ --frequency 1d

# 详细日志模式
poetry run python -m simtradedata full-sync --verbose

# 静默模式（用于定时任务）
poetry run python -m simtradedata incremental --start-date 2024-01-01 --quiet
```

### 监控和告警

```bash
# 检查告警状态
poetry run python -m simtradedata.monitoring.alert_system check

# 获取数据质量报告
poetry run python -m simtradedata.monitoring.data_quality report

# 查看激活的告警
poetry run python -c "
from simtradedata.database import DatabaseManager
from simtradedata.monitoring import AlertSystem
db = DatabaseManager('data/simtradedata.db')
alerts = AlertSystem(db)
summary = alerts.get_alert_summary()
print(f'激活告警: {summary[\"active_alerts_count\"]}个')
"

# 测试所有告警规则
poetry run python -c "
from simtradedata.database import DatabaseManager
from simtradedata.monitoring import AlertSystem, AlertRuleFactory
db = DatabaseManager('data/simtradedata.db')
alert_system = AlertSystem(db)
rules = AlertRuleFactory.create_all_default_rules(db)
for rule in rules:
    alert_system.add_rule(rule)
triggered = alert_system.check_all_rules()
print(f'触发告警: {len(triggered)}个')
"
```

### 生产环境命令

```bash
# 使用生产配置启动
poetry run python -m simtradedata.cli serve --config production_config.yaml

# 健康检查
poetry run python -m simtradedata.cli health-check

# 数据库优化（生产环境）
poetry run python -c "
from simtradedata.database import DatabaseManager
db = DatabaseManager('data/simtradedata.db')
db.execute('VACUUM;')  # 压缩数据库
db.execute('ANALYZE;')  # 更新统计信息
"

# 查看性能统计
poetry run python -c "
from simtradedata.preprocessor.indicators import TechnicalIndicators
ind = TechnicalIndicators()
stats = ind.get_cache_stats()
print(f'缓存大小: {stats[\"cache_size\"]}/{stats[\"cache_max_size\"]}')
"
```

## 🚨 故障排除

### 常见问题和解决方案

```bash
# 1. 检查数据库是否正确初始化
ls -la data/simtradedata.db

# 2. 验证配置文件语法
python -c "import yaml; yaml.safe_load(open('config.yaml'))"

# 3. 测试数据源连接
poetry run python -c "from simtradedata.data_sources import DataSourceManager; dsm = DataSourceManager(); print('数据源初始化成功')"

# 4. 检查依赖安装
poetry install --sync

# 5. 运行基础测试
poetry run python -m pytest tests/ -v -x
```

### 日志文件位置

- **应用日志**: `logs/simtradedata.log` (如果配置了)
- **Poetry日志**: 使用 `poetry run` 时的标准输出
- **系统日志**: 使用 `--verbose` 参数查看详细信息

### 性能建议

```bash
# 1. 对于大量数据，建议分批同步
poetry run python -m simtradedata full-sync --symbols 000001.SZ --target-date 2024-01-01
poetry run python -m simtradedata full-sync --symbols 000002.SZ --target-date 2024-01-01

# 2. 使用增量同步减少数据量
poetry run python -m simtradedata incremental --start-date 2024-01-01 --end-date 2024-01-07

# 3. 定期运行缺口修复
poetry run python -m simtradedata gap-fix --start-date 2024-01-01 --end-date $(date +%Y-%m-%d)
```

## 📚 更多信息

- [生产部署指南](PRODUCTION_DEPLOYMENT_GUIDE.md) - 完整的生产环境配置和部署指南
- [API 参考文档](API_REFERENCE.md) - API接口详细文档
- [开发者指南](DEVELOPER_GUIDE.md) - 开发者扩展开发指南
- [架构指南](Architecture_Guide.md) - 系统架构和设计文档

## 🆘 获取帮助

```bash
# 查看帮助信息
poetry run python -m simtradedata --help

# 查看子命令帮助
poetry run python -m simtradedata sync --help

# 查看版本信息
poetry run python -m simtradedata --version
```
