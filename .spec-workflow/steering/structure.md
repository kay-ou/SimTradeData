# Project Structure

## Directory Organization

```
simtradedata/
├── simtradedata/              # 主源代码目录
│   ├── interfaces/           # 用户接口层
│   │   ├── __init__.py
│   │   ├── ptrade_api.py    # PTrade API适配器
│   │   ├── rest_api.py      # REST API服务
│   │   └── api_gateway.py   # API网关
│   │
│   ├── api/                  # API路由系统
│   │   ├── __init__.py
│   │   ├── router.py        # 主路由器
│   │   ├── query_builders.py # 查询构建器
│   │   ├── formatters.py    # 数据格式化器
│   │   └── cache.py         # 缓存管理
│   │
│   ├── markets/              # 多市场管理
│   │   ├── __init__.py
│   │   ├── multi_market.py  # 多市场管理器
│   │   ├── hk_market.py     # 港股市场
│   │   ├── us_market.py     # 美股市场
│   │   ├── currency.py      # 货币处理
│   │   └── timezone_handler.py # 时区处理
│   │
│   ├── extended_data/        # 扩展数据处理
│   │   ├── __init__.py
│   │   ├── sector_data.py   # 行业数据
│   │   ├── etf_data.py      # ETF数据
│   │   ├── technical_indicators.py # 技术指标
│   │   └── data_aggregator.py # 数据聚合器
│   │
│   ├── preprocessor/         # 数据预处理引擎
│   │   ├── __init__.py
│   │   ├── engine.py        # 处理引擎
│   │   ├── cleaner.py       # 数据清洗
│   │   ├── converter.py     # 数据转换
│   │   ├── indicators.py    # 指标计算
│   │   └── scheduler.py     # 调度器
│   │
│   ├── sync/                 # 数据同步层
│   │   ├── __init__.py
│   │   ├── manager.py       # 同步管理器
│   │   ├── incremental.py   # 增量更新
│   │   ├── validator.py     # 数据验证
│   │   └── gap_detector.py  # 缺口检测
│   │
│   ├── performance/          # 性能优化层
│   │   ├── __init__.py
│   │   ├── query_optimizer.py # 查询优化器
│   │   └── cache_manager.py   # 缓存管理器
│   │
│   ├── monitoring/           # 监控运维层
│   │   ├── __init__.py
│   │   ├── data_quality.py  # 数据质量监控
│   │   ├── alert_system.py  # 告警系统
│   │   └── alert_rules.py   # 告警规则
│   │
│   ├── database/             # 数据库管理
│   │   ├── __init__.py
│   │   ├── manager.py       # 数据库管理器
│   │   └── schema.py        # 表结构定义
│   │
│   ├── data_sources/         # 数据源管理
│   │   ├── __init__.py
│   │   ├── base.py          # 基础适配器
│   │   ├── manager.py       # 数据源管理器
│   │   ├── mootdx_adapter.py      # Mootdx适配器
│   │   ├── mootdx_column_mappings.py # Mootdx字段映射
│   │   ├── mootdx_finvalue_fields.py # Mootdx财务字段
│   │   ├── baostock_adapter.py    # BaoStock适配器
│   │   └── qstock_adapter.py      # QStock适配器
│   │
│   ├── core/                 # 核心功能
│   │   ├── __init__.py
│   │   ├── base_manager.py  # 基础管理器
│   │   ├── config_mixin.py  # 配置混入
│   │   ├── logging_mixin.py # 日志混入
│   │   └── error_handling.py # 错误处理
│   │
│   ├── config/               # 配置管理
│   │   ├── __init__.py
│   │   ├── manager.py       # 配置管理器
│   │   ├── defaults.py      # 默认配置
│   │   └── production.py    # 生产配置
│   │
│   ├── utils/                # 工具函数
│   │   ├── __init__.py
│   │   ├── trading_hours.py # 交易时间
│   │   └── progress_bar.py  # 进度条
│   │
│   ├── __init__.py           # 包初始化
│   ├── __main__.py           # 主入口
│   └── cli.py                # CLI命令行
│
├── tests/                    # 测试目录
│   ├── unit/                # 单元测试
│   ├── integration/         # 集成测试
│   ├── sync/                # 同步测试
│   └── performance/         # 性能测试
│
├── docs/                     # 文档目录
│   ├── Architecture_Guide.md      # 架构指南
│   ├── API_REFERENCE.md          # API参考
│   ├── DEVELOPER_GUIDE.md        # 开发者指南
│   ├── CLI_USAGE_GUIDE.md        # CLI使用指南
│   ├── PTrade_API_mini_Reference.md # PTrade API参考
│   ├── reference/                # 参考文档
│   │   ├── mootdx_api/          # Mootdx API文档
│   │   ├── baostock_api/        # BaoStock API文档
│   │   └── qstock_api/          # QStock API文档
│   └── archive/                  # 归档文档
│
├── scripts/                  # 脚本目录
│   └── init_database.py     # 数据库初始化脚本
│
├── data/                     # 数据目录
│   └── simtradedata.db      # SQLite数据库文件
│
├── .spec-workflow/           # 规范工作流
│   └── templates/           # 模板文件
│
├── pyproject.toml           # Poetry配置文件
├── README.md                # 项目说明
└── .gitignore               # Git忽略配置
```

## Naming Conventions

### Files
- **模块文件**: snake_case (例: `database_manager.py`, `query_builder.py`)
- **服务/管理器**: `*_manager.py`, `*_adapter.py`, `*_handler.py`
- **工具/辅助**: `*_utils.py`, `*_helper.py`
- **测试文件**: `test_*.py` (pytest标准)

### Code
- **类名**: PascalCase (例: `DatabaseManager`, `APIRouter`)
- **函数/方法**: snake_case (例: `get_history()`, `sync_data()`)
- **常量**: UPPER_SNAKE_CASE (例: `MAX_RETRIES`, `DEFAULT_TIMEOUT`)
- **私有变量**: 前缀下划线 (例: `_internal_cache`)
- **变量**: snake_case (例: `stock_code`, `start_date`)

## Import Patterns

### Import Order
1. 标准库导入
2. 第三方库导入 (pandas, numpy等)
3. 本地应用导入 (simtradedata模块)
4. 相对导入

### Module/Package Organization
```python
# 1. 标准库
import os
import sys
from datetime import datetime

# 2. 第三方库
import pandas as pd
import numpy as np

# 3. 本地应用
from simtradedata.database.manager import DatabaseManager
from simtradedata.config.manager import Config

# 4. 相对导入
from .base import BaseAdapter
from ..utils import helpers
```

## Code Structure Patterns

### Module/Class Organization
```python
"""模块文档字符串 - 说明模块用途"""

# 1. 导入
import os
from typing import List, Dict

# 2. 常量定义
MAX_RETRIES = 3
DEFAULT_TIMEOUT = 30

# 3. 类型定义
StockCode = str
DateStr = str

# 4. 主类实现
class DatabaseManager:
    """数据库管理器"""

    def __init__(self):
        pass

    # 公共方法
    def public_method(self):
        pass

    # 私有方法
    def _private_method(self):
        pass

# 5. 辅助函数
def helper_function():
    pass

# 6. 模块级导出
__all__ = ['DatabaseManager']
```

### Function/Method Organization
```python
def sync_stock_data(self, symbol: str, start_date: str, end_date: str):
    """同步股票数据

    Args:
        symbol: 股票代码
        start_date: 开始日期
        end_date: 结束日期

    Returns:
        同步的数据条数
    """
    # 1. 输入验证
    if not self._validate_symbol(symbol):
        raise ValueError(f"Invalid symbol: {symbol}")

    # 2. 核心逻辑
    data = self._fetch_data(symbol, start_date, end_date)
    cleaned_data = self._clean_data(data)

    # 3. 错误处理
    try:
        count = self._save_data(cleaned_data)
    except Exception as e:
        self.logger.error(f"Failed to save data: {e}")
        raise

    # 4. 返回结果
    return count
```

### File Organization Principles
- **单一职责**: 每个文件只负责一个明确的功能
- **分层清晰**: 按功能分层组织目录
- **模块化**: 功能独立,易于测试和复用
- **文档完整**: 每个模块都有清晰的文档字符串

## Code Organization Principles

1. **Single Responsibility**: 每个模块、类、函数只做一件事
2. **Modularity**: 代码组织成可复用的模块
3. **Testability**: 代码结构便于测试,依赖注入
4. **Consistency**: 遵循项目既定的代码模式

## Module Boundaries

模块边界清晰,依赖关系单向:

- **接口层** → **业务逻辑层** → **数据层**
  - 上层可依赖下层,下层不依赖上层
  - 例: `interfaces/` 可导入 `api/`, 但 `api/` 不应导入 `interfaces/`

- **核心模块vs扩展模块**:
  - 核心: `database/`, `core/`, `config/`
  - 扩展: `extended_data/`, `markets/`
  - 扩展模块可依赖核心,核心不依赖扩展

- **数据源适配器隔离**:
  - `data_sources/` 中每个适配器独立
  - 通过 `base.py` 统一接口
  - 不允许适配器之间直接依赖

- **测试隔离**:
  - 测试代码不应被生产代码导入
  - 使用pytest的marker机制分类测试

## Code Size Guidelines

遵循适度的文件和函数大小:

- **文件大小**: 建议 <500行,超过应考虑拆分
- **函数/方法大小**: 建议 <50行,超过应拆分为多个小函数
- **类复杂度**: 单个类建议 <20个方法
- **嵌套深度**: 最大嵌套层级 ≤ 4层

## Dashboard/Monitoring Structure (if applicable)

当前项目为CLI工具,无独立Dashboard组件。监控功能集成在:

```
simtradedata/
└── monitoring/              # 监控模块
    ├── data_quality.py     # 数据质量监控
    ├── alert_system.py     # 告警系统
    └── alert_rules.py      # 告警规则
```

### Separation of Concerns
- 监控模块独立于核心业务逻辑
- 通过日志系统输出监控信息
- 可选启用/禁用,不影响核心功能
- 未来可扩展为独立Dashboard服务

## Documentation Standards

文档规范:

- **所有公共API必须有docstring**: 使用Google风格docstring
- **复杂逻辑必须有注释**: 解释为什么这样做,不是做什么
- **每个模块必须有模块级docstring**: 说明模块用途和主要功能
- **README文件**: 主要模块目录应有README说明
- **中文注释**: 所有注释和文档字符串使用中文
- **类型提示**: 使用Python类型提示增强代码可读性

示例:
```python
def get_stock_history(
    self,
    symbols: List[str],
    start_date: str,
    end_date: str,
    frequency: str = "1d"
) -> pd.DataFrame:
    """获取股票历史数据

    从数据库查询指定股票的历史行情数据。

    Args:
        symbols: 股票代码列表,如 ['000001.SZ', '600000.SH']
        start_date: 开始日期,格式 'YYYY-MM-DD'
        end_date: 结束日期,格式 'YYYY-MM-DD'
        frequency: 数据频率,'1d'=日线,'1w'=周线,'1M'=月线

    Returns:
        包含OHLCV数据的DataFrame

    Raises:
        ValueError: 参数格式错误
        DatabaseError: 数据库查询失败

    Example:
        >>> data = router.get_stock_history(
        ...     symbols=['000001.SZ'],
        ...     start_date='2024-01-01',
        ...     end_date='2024-01-31'
        ... )
    """
    pass
```
