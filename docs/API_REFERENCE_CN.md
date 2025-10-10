# SimTradeData API Reference

**[中文](API_REFERENCE.md)** | **[English](API_REFERENCE_EN.md)**

## 📖 Overview

SimTradeData 提供多种API接口，包括PTrade兼容接口、REST API和Python API。本文档详细介绍了所有可用的API接口和使用方法。

## 🐍 Python API

### 核心API路由器

#### APIRouter

高性能的数据查询路由器，提供统一的数据访问接口，支持缓存、并发和查询优化。

```python
from simtradedata.api.router import APIRouter
from simtradedata.database.manager import DatabaseManager
from simtradedata.config.manager import Config

# 初始化核心组件
config = Config()
db_manager = DatabaseManager("data/simtradedata.db")
api_router = APIRouter(db_manager, config)
```

#### 核心特性

- **高性能查询**: 优化的SQL生成和执行
- **智能缓存**: 多级缓存策略，提升查询速度
- **并发支持**: 支持高并发查询请求
- **格式化输出**: 自动格式化为DataFrame或JSON
- **错误处理**: 完善的异常处理和日志记录

### 主要API方法

#### 历史数据查询

**get_history(symbols, start_date, end_date, frequency="1d", fields=None)**
- 获取历史行情数据，支持多股票、多频率查询
- 参数:
  - `symbols` (list[str]): 股票代码列表，如 ['000001.SZ', '000002.SZ']
  - `start_date` (str): 开始日期，格式 'YYYY-MM-DD'
  - `end_date` (str): 结束日期，格式 'YYYY-MM-DD'
  - `frequency` (str): 数据频率，支持 '1d', '5m', '15m', '30m', '60m'
  - `fields` (list[str], optional): 指定返回字段
- 返回: pandas.DataFrame

```python
# 获取单只股票日线数据
data = api_router.get_history(
    symbols=['000001.SZ'],
    start_date='2024-01-01',
    end_date='2024-01-31',
    frequency='1d'
)

# 获取多只股票分钟数据
data = api_router.get_history(
    symbols=['000001.SZ', '000002.SZ'],
    start_date='2024-01-01',
    end_date='2024-01-31',
    frequency='5m'
)
```

#### 实时数据查询

**get_snapshot(symbols, fields=None)**
- 获取股票快照数据
- 参数:
  - `symbols` (list[str]): 股票代码列表
  - `fields` (list[str], optional): 指定返回字段
- 返回: pandas.DataFrame

```python
# 获取股票快照
snapshot = api_router.get_snapshot(['000001.SZ', '000002.SZ'])
```

#### 财务数据查询

**get_financials(symbols, start_date, end_date)**
- 获取财务数据
- 参数:
  - `symbols` (list[str]): 股票代码列表
  - `start_date` (str): 开始日期
  - `end_date` (str): 结束日期
- 返回: pandas.DataFrame

```python
# 获取财务数据
financials = api_router.get_financials(
    symbols=['000001.SZ'],
    start_date='2024-01-01',
    end_date='2024-12-31'
)
```

### 数据同步API

```python
from simtradedata.sync import SyncManager

# 初始化同步管理器
sync_manager = SyncManager(db_manager, data_source_manager)

# 增量同步
result = sync_manager.incremental_sync(
    symbol='000001.SZ',
    start_date='2024-01-01',
    end_date='2024-01-31'
)

# 历史回填
result = sync_manager.historical_backfill(
    symbol='000001.SZ',
    target_date='2024-01-01'
)
```

### 监控API

```python
from simtradedata.monitoring import AlertSystem, DataQualityMonitor

# 数据质量监控
quality_monitor = DataQualityMonitor(db_manager)
quality_score = quality_monitor.evaluate_source_quality('baostock', '000001.SZ', 'ohlcv')

# 告警系统
alert_system = AlertSystem(db_manager)
alerts = alert_system.check_all_rules()
summary = alert_system.get_alert_summary()
```

## 🔌 PTrade兼容接口

SimTradeData 提供与PTrade兼容的API接口，详见 [PTrade API参考文档](PTrade_API_mini_Reference.md)。

### 使用示例

```python
from simtradedata.interfaces import PTradeAPIAdapter

adapter = PTradeAPIAdapter(db_manager, config)

# 获取股票列表
stocks = adapter.get_stock_list('SZ')

# 获取价格数据
prices = adapter.get_price('000001.SZ', '2024-01-01', '2024-01-31')

# 获取股票信息
info = adapter.get_stock_info('000001.SZ')
```

## 🌐 REST API

> **注**: REST API 服务器功能可选，需要单独启动。SimTradeData 主要提供 Python API。

### 基础信息

- **Base URL**: `http://localhost:8080/api/v1`
- **Content-Type**: `application/json`

### 主要端点

详细的 REST API 文档请参考源代码中的 `simtradedata/interfaces/rest_api.py`。

## 📊 数据源管理API

```python
from simtradedata.data_sources import DataSourceManager

# 初始化数据源管理器
ds_manager = DataSourceManager(config)

# 健康检查
health = ds_manager.health_check()

# 获取可用数据源
available = ds_manager.get_available_sources()

# 获取系统状态
status = ds_manager.get_status()
```



## 📊 性能API

### 缓存管理

```python
from simtradedata.performance import CacheManager

cache = CacheManager(config)

# 设置缓存
cache.set('key', data, ttl=600)

# 获取缓存
data = cache.get('key')

# 获取缓存统计
stats = cache.get_stats()
print(f"命中率: {stats['hit_rate']}%")
```

### 技术指标计算

```python
from simtradedata.preprocessor.indicators import TechnicalIndicators

indicators = TechnicalIndicators()

# 计算MACD
macd = indicators.calculate_macd(close_prices)

# 计算RSI
rsi = indicators.calculate_rsi(close_prices)

# 获取缓存统计
stats = indicators.get_cache_stats()
```

## 📈 监控与健康检查

### 数据库健康检查

```python
from simtradedata.database import DatabaseManager
from simtradedata.config import Config

config = Config()
db = DatabaseManager(config.get('database.path'))

# 检查数据库连接
try:
    result = db.fetchone("SELECT 1")
    print("✅ 数据库连接正常")
except Exception as e:
    print(f"❌ 数据库连接失败: {e}")

# 检查表状态
tables = ['stocks', 'market_data', 'trading_calendar']
for table in tables:
    count = db.fetchone(f"SELECT COUNT(*) as count FROM {table}")
    print(f"✅ {table}: {count['count']} 条记录")
```

### 数据质量监控

```python
from simtradedata.monitoring import DataQualityMonitor

monitor = DataQualityMonitor(db_manager)

# 评估数据源质量
quality = monitor.evaluate_source_quality('baostock', '000001.SZ', 'ohlcv')
print(f"质量评分: {quality['overall_score']}")

# 获取数据源排名
ranking = monitor.get_source_ranking('ohlcv')
```

### 告警系统

```python
from simtradedata.monitoring import AlertSystem, AlertRuleFactory, ConsoleNotifier

# 初始化告警系统
alert_system = AlertSystem(db_manager)
alert_system.add_notifier(ConsoleNotifier())

# 添加默认告警规则
rules = AlertRuleFactory.create_all_default_rules(db_manager)
for rule in rules:
    alert_system.add_rule(rule)

# 检查告警
alerts = alert_system.check_all_rules()

# 获取告警摘要
summary = alert_system.get_alert_summary()
print(f"激活告警: {summary['active_alerts_count']}个")
```

---

*SimTradeData API Reference - 完整的API接口文档*
