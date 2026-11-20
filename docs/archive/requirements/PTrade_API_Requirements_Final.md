# PTrade API需求分析与数据源映射 - 最终版本

> 本文是历史规划，接口数量与覆盖百分比不是实测验收结果。尤其普通分笔行情不能等同于 PTrade L2 逐笔委托/成交。最新核查见 [PTrade 数据覆盖](../../PTRADE_DATA_COVERAGE.md)。

## 🎯 项目目标

为PTrade量化交易平台设计SQLite数据缓存系统，实现：
- **离线数据访问**: 支持无网络环境下的量化分析
- **高性能查询**: 毫秒级响应，支持高频策略
- **多数据源融合**: Mootdx、BaoStock、QStock智能组合
- **完全兼容**: PTrade API调用方式完全不变

## 📊 PTrade API完整清单 (64个)

### 🔥 高优先级API (20个) - 核心功能

#### 基础交易日历 (3个)
- `get_trade_days()` - 获取交易日列表
- `get_all_trade_days()` - 获取所有交易日
- `get_previous_trade_day()` - 获取前一交易日

#### 历史行情数据 (2个)
- `get_history()` - 获取历史数据 ⭐ **最重要**
- `get_price()` - 获取价格数据 ⭐ **最重要**

#### 实时行情数据 (5个)
- `get_snapshot()` - 获取快照数据
- `get_tick()` - 获取逐笔数据
- `get_current_tick()` - 获取当前tick
- `get_ticks()` - 获取tick数据
- `get_bars()` - 获取K线数据

#### 股票基础信息 (5个)
- `get_Ashares()` - 获取A股列表 ⭐ **重要**
- `get_stock_info()` - 获取股票信息 ⭐ **重要**
- `get_all_securities()` - 获取所有证券
- `get_security_info()` - 获取证券信息
- `get_stock_list()` - 获取股票列表

#### 行业板块 (4个)
- `get_stock_blocks()` - 获取股票板块 ⭐ **重要**
- `get_index_stocks()` - 获取指数成分股
- `get_industry()` - 获取行业分类
- `get_concept()` - 获取概念板块

#### 财务数据 (1个)
- `get_fundamentals()` - 获取财务数据 ⭐ **重要**

### 🔶 中优先级API (15个) - 扩展功能

#### ETF相关 (4个)
- `get_etf_info()` - 获取ETF信息
- `get_etf_stocks()` - 获取ETF成分股
- `get_etf_list()` - 获取ETF列表
- `get_etf_nav()` - 获取ETF净值

#### 可转债 (2个)
- `get_bond_info()` - 获取债券信息
- `get_convertible_bonds()` - 获取可转债

#### 市场信息 (2个)
- `get_market_info()` - 获取市场信息
- `get_trading_status()` - 获取交易状态

#### IPO相关 (1个)
- `get_ipo_info()` - 获取IPO信息

#### 技术指标计算 (4个)
- `get_macd()` - MACD指标
- `get_kdj()` - KDJ指标
- `get_rsi()` - RSI指标
- `get_cci()` - CCI指标

#### 期权相关 (2个)
- `get_option_info()` - 获取期权信息
- `get_option_contracts()` - 获取期权合约

### 🔷 低优先级API (29个) - 专业功能

#### 融资融券 (12个)
- `get_margin_info()` - 融资融券信息
- `get_margin_stocks()` - 融资融券标的
- `get_margin_details()` - 融资融券明细
- `get_short_info()` - 融券信息
- `get_margin_ratio()` - 保证金比例
- `get_margin_balance()` - 融资融券余额
- `get_margin_trading()` - 融资融券交易
- `get_short_balance()` - 融券余额
- `get_margin_rate()` - 融资利率
- `get_short_rate()` - 融券费率
- `get_margin_requirement()` - 保证金要求
- `get_short_requirement()` - 融券要求

#### 期货相关 (2个)
- `get_future_info()` - 期货信息
- `get_future_margin()` - 期货保证金

#### 期权相关 (4个)
- `get_option_underlying()` - 期权标的
- `get_option_expiry()` - 期权到期日
- `get_option_strike()` - 期权行权价
- `get_option_greeks()` - 期权希腊字母

#### 交易相关查询 (7个)
- `get_positions()` - 获取持仓
- `get_orders()` - 获取订单
- `get_trades()` - 获取成交
- `get_account()` - 获取账户
- `get_balance()` - 获取资金
- `get_portfolio()` - 获取组合
- `get_performance()` - 获取绩效

#### 系统信息 (4个)
- `get_system_info()` - 系统信息
- `get_version()` - 版本信息
- `get_status()` - 状态信息
- `get_config()` - 配置信息

## 🗂️ 数据源能力分析

### Mootdx (30个API支持，85%)
**优势**: 本地数据、高性能、深度行情、财务数据
```python
MOOTDX_SUPPORT = {
    '历史行情': ['get_history', 'get_price', '分钟线'],
    '实时数据': ['get_snapshot', '逐笔委托', '逐笔成交'],
    '股票信息': ['get_Ashares', 'get_stock_info'],
    '财务数据': ['FINVALUE 322个字段'],
    '深度行情': ['逐笔委托', '逐笔成交'],
    '性能': ['本地读取10-100倍提升'],
}
```

### BaoStock (22个API支持，63%)
**优势**: 历史数据完整性、财务数据、复权数据、交易日历
```python
BAOSTOCK_SUPPORT = {
    '历史行情': ['get_history', 'get_price'],
    '交易日历': ['get_trade_days', 'get_all_trade_days'],
    '股票信息': ['get_Ashares', 'get_stock_info'],
    '财务数据': ['get_fundamentals', '6个季频指标'],
    '复权数据': ['除权除息信息'],
    '板块数据': ['get_stock_blocks', 'get_industry'],
    '数据质量': ['高质量历史数据', '数据完整性好'],
}
```

### QStock (20个API支持，57%)
**优势**: 多市场支持、资金流数据、同花顺概念板块
```python
QSTOCK_SUPPORT = {
    '历史行情': ['get_history', 'get_price'],
    '股票信息': ['get_Ashares', 'get_stock_info'],
    '板块数据': ['get_stock_blocks', 'get_concept'],
    '资金流向': ['主力资金', '资金净流入'],
    '概念板块': ['同花顺概念', '热点概念'],
    '多市场': ['A股', '港股', '美股'],
}
```

## 📋 API优先级实施策略

### 第一阶段 (高优先级 - 20个API)
**目标**: 满足基本量化分析需求
**数据源覆盖**: 90%以上
**实施重点**:
- 历史行情数据 (get_history, get_price)
- 股票基础信息 (get_Ashares, get_stock_info)
- 交易日历 (get_trade_days)
- 财务数据 (get_fundamentals)
- 板块数据 (get_stock_blocks)

### 第二阶段 (中优先级 - 15个API)
**目标**: 扩展投资品种和分析工具
**数据源覆盖**: 60%左右
**实施重点**:
- ETF相关数据
- 可转债数据
- 技术指标计算
- 实时数据支持

### 第三阶段 (低优先级 - 29个API)
**目标**: 完善功能覆盖度
**数据源覆盖**: 20%左右 (主要是交易和系统功能)
**实施策略**: 部分实现或标注为"需要专业接口"

## 🎯 数据源选择策略

### 按API类型的数据源优先级

```python
DATA_SOURCE_PRIORITY = {
    # 历史数据 - BaoStock数据质量最好
    'get_history': ['BaoStock', 'Mootdx', 'QStock'],
    'get_price': ['BaoStock', 'Mootdx', 'QStock'],

    # 实时数据 - Mootdx本地数据最快
    'get_snapshot': ['Mootdx', 'QStock'],
    'get_tick': ['Mootdx', 'QStock'],

    # 基础信息 - BaoStock最完整
    'get_Ashares': ['BaoStock', 'Mootdx', 'QStock'],
    'get_stock_info': ['BaoStock', 'Mootdx', 'QStock'],

    # 财务数据 - BaoStock和Mootdx互补
    'get_fundamentals': ['BaoStock', 'Mootdx', 'QStock'],

    # 板块数据 - QStock概念板块丰富
    'get_stock_blocks': ['QStock', 'BaoStock', 'Mootdx'],
    'get_concept': ['QStock', 'Mootdx'],

    # ETF数据 - QStock支持
    'get_etf_info': ['QStock', 'BaoStock'],
    'get_etf_stocks': ['QStock', 'BaoStock'],

    # 交易日历 - BaoStock最准确
    'get_trade_days': ['BaoStock', 'Mootdx'],
}
```

### 市场特定的数据源策略

```python
MARKET_DATA_SOURCE = {
    'A股 (SZ/SS)': {
        '主要': ['BaoStock', 'Mootdx', 'QStock'],
        '特色': 'BaoStock历史数据质量最高，Mootdx本地性能最优'
    },
    '港股 (HK)': {
        '主要': ['QStock'],
        '特色': 'QStock支持港股市场数据'
    },
    '美股 (US)': {
        '主要': ['QStock'],
        '特色': 'QStock支持美股市场数据'
    }
}
```

## 📊 实施成果预期

### 数据覆盖度
- **数据获取API (35个)**: 80%覆盖度
- **交易系统API (29个)**: 20%覆盖度 (需要券商接口)
- **总体覆盖度**: 约55%的完整实现

### 性能提升
- **查询速度**: 从秒级提升到毫秒级 (10-150倍提升)
- **离线能力**: 100%支持离线量化分析
- **并发支持**: 支持50-200个并发查询

### 功能完整性
- **历史回测**: 100%满足需求
- **实时分析**: 80%满足需求
- **多市场支持**: A股100%，港股美股60%

## 🚀 关键成功因素

1. **预处理架构**: 离线预处理确保查询性能
2. **多数据源融合**: 三个数据源互为备份
3. **智能路由**: 根据API类型选择最优数据源
4. **增量更新**: 只同步缺失数据，提高效率
5. **多市场支持**: 统一接口支持全球市场

## 📋 开发里程碑

### 里程碑1 (4周): 核心功能
- 完成高优先级20个API
- 实现A股市场完整支持
- 基础数据同步功能

### 里程碑2 (8周): 扩展功能
- 完成中优先级15个API
- 实现港股美股基础支持
- 完善数据同步和缺口修复

### 里程碑3 (12周): 完整系统
- 完成所有可实现的API
- 性能优化和监控
- 完整的文档和测试

这个需求分析为PTrade SQLite数据缓存系统提供了清晰的实施路径和优先级指导。
