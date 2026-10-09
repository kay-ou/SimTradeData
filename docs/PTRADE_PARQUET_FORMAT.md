# PTrade Parquet 数据格式规范

本文档定义了 PTrade 数据包的 Parquet 格式规范，作为 SimTradeData 导出的目标格式。

财务字段覆盖情况见 [PTrade Financial Field Coverage](PTRADE_FINANCIAL_COVERAGE.md)。

## 目录结构

```
data/
├── stocks/                      # 股票日线行情 (每symbol一个文件)
│   ├── 000001.SZ.parquet
│   └── 600000.SS.parquet
├── exrights/                    # 除权除息事件 (每symbol一个文件)
│   ├── 000001.SZ.parquet
│   └── 600000.SS.parquet
├── fundamentals/                # 季度财务数据 (每symbol一个文件)
│   ├── 000001.SZ.parquet
│   └── 600000.SS.parquet
├── valuation/                   # 估值指标 (每symbol一个文件)
│   ├── 000001.SZ.parquet
│   └── 600000.SS.parquet
├── metadata/                    # 全局元数据
│   ├── stock_metadata.parquet   # 股票基本信息
│   ├── benchmark.parquet        # 基准指数(沪深300)
│   ├── trade_days.parquet       # 交易日历
│   ├── index_constituents.parquet  # 指数成分股
│   ├── stock_status.parquet     # 股票状态(ST/停牌/退市)
│   └── version.parquet          # 版本信息
├── ptrade_adj_pre.parquet       # 前复权因子 (全量)
├── ptrade_adj_post.parquet      # 后复权因子 (全量)
└── manifest.json                # 数据包清单
```

## 文件命名规则

- **股票代码格式**: `{6位代码}.{市场}.parquet`
- **市场标识**: `SS` = 上海, `SZ` = 深圳
- **示例**: `000001.SZ.parquet`, `600000.SS.parquet`

---

## 数据表结构

### 1. stocks/ - 股票日线行情

| 字段 | 类型 | 说明 | 备注 |
|------|------|------|------|
| `date` | timestamp[ns] | 交易日期 | **主键** |
| `open` | double | 开盘价 | |
| `close` | double | 收盘价 | |
| `high` | double | 最高价 | |
| `low` | double | 最低价 | |
| `high_limit` | double | 涨停价 | |
| `low_limit` | double | 跌停价 | |
| `preclose` | double | 前收盘价 | |
| `volume` | int64 | 成交量 | 单位: 股 |
| `money` | double | 成交金额 | 单位: 元 |

**特点**: 按日期升序排列，无分区

---

### 2. exrights/ - 除权除息事件

| 字段 | 类型 | 说明 | 备注 |
|------|------|------|------|
| `date` | timestamp[ns] | 除权除息日 | **主键** |
| `allotted_ps` | double | 配股数/股 | 可为0 |
| `rationed_ps` | double | 配售数/股 | 可为0 |
| `rationed_px` | double | 配售价格 | 可为0 |
| `bonus_ps` | double | 红股数/股 | 可为0 |
| `dividend` | double | 现金分红 | 可为NaN |

---

### 3. valuation/ - 估值指标 (日频)

| 字段 | 类型 | 说明 | 备注 |
|------|------|------|------|
| `date` | timestamp[ns] | 日期 | **主键** |
| `pe_ttm` | double | 市盈率(TTM) | |
| `pb` | double | 市净率 | |
| `ps_ttm` | double | 市销率(TTM) | |
| `pcf` | double | 市现率 | |
| `roe` | double | 净资产收益率 | |
| `roe_ttm` | double | ROE(TTM) | |
| `roa` | double | 资产回报率 | |
| `roa_ttm` | double | ROA(TTM) | |
| `naps` | double | 每股净资产 | |
| `total_shares` | double | 总股本 | 单位: 股 |
| `a_floats` | double | 流通股 | 单位: 股 |
| `a_shares` | double | A股股本 | 单位: 股, 日频值 |
| `total_value` | double | 总市值 | = total_shares × close |
| `float_value` | double | 流通市值 | 日频值优先, 否则 = a_floats × close |
| `turnover_rate` | double | 换手率 | 百分比 |

---

### 4. fundamentals/ - 季度财务数据

| 字段 | 类型 | 说明 | 备注 |
|------|------|------|------|
| `date` | timestamp[ns] | 报告期末 | **主键**, 季末日期 |
| `publ_date` | string | 公布日期 | YYYYMMDD |
| `basic_eps` | double | 基本每股收益 | |
| `undivided_profit` | double | 每股未分配利润 | 可null |
| `naps` | double | 每股净资产 | |
| `capital_surplus_fund_ps` | double | 每股资本公积金 | 可null |
| `net_operate_cash_flow_ps` | double | 每股经营活动现金流量净额 | 可null |
| `cash_equivalents` | double | 货币资金 | 可null |
| `account_receivable` | double | 应收账款 | 可null |
| `inventories` | double | 存货 | 可null |
| `total_current_assets` | double | 流动资产合计 | 可null |
| `fixed_assets` | double | 固定资产 | 可null |
| `intangible_assets` | double | 无形资产 | 可null |
| `total_non_current_assets` | double | 非流动资产合计 | 可null |
| `total_assets` | double | 资产总计 | |
| `shortterm_loan` | double | 短期借款 | 可null |
| `accounts_payable` | double | 应付账款 | 可null |
| `total_current_liability` | double | 流动负债合计 | 可null |
| `longterm_loan` | double | 长期借款 | 可null |
| `total_non_current_liability` | double | 非流动负债合计 | 可null |
| `total_liability` | double | 负债合计 | |
| `paidin_capital` | double | 实收资本/股本 | 可null |
| `retained_profit` | double | 未分配利润 | 可null |
| `total_shareholder_equity` | double | 所有者权益合计 | |
| `operating_revenue` | double | 营业收入 | |
| `operating_cost` | double | 营业成本 | 可null |
| `financial_expense` | double | 财务费用 | 可null |
| `operating_profit` | double | 营业利润 | |
| `total_profit` | double | 利润总额 | |
| `net_profit` | double | 净利润 | |
| `np_parent_company_owners` | double | 归属于母公司所有者的净利润 | |
| `net_operate_cash_flow` | double | 经营活动产生的现金流量净额 | |
| `net_invest_cash_flow` | double | 投资活动产生的现金流量净额 | 可null |
| `net_finance_cash_flow` | double | 筹资活动产生的现金流量净额 | 可null |
| `operating_revenue_grow_rate` | double | 营收增长率 | % |
| `net_profit_grow_rate` | double | 净利润增长率 | % |
| `net_asset_grow_rate` | double | 净资产增长率 | % |
| `basic_eps_yoy` | double | EPS同比 | |
| `np_parent_company_yoy` | double | 归母净利润同比 | |
| `net_profit_ratio` | double | 净利润率 | % |
| `net_profit_ratio_ttm` | double | 净利润率(TTM) | % |
| `gross_income_ratio` | double | 毛利率 | % |
| `gross_income_ratio_ttm` | double | 毛利率(TTM) | % |
| `roa` | double | 资产回报率 | |
| `roa_ttm` | double | ROA(TTM) | |
| `roe` | double | 净资产收益率 | |
| `roe_ttm` | double | ROE(TTM) | |
| `total_asset_grow_rate` | double | 总资产增长率 | % |
| `total_asset_turnover_rate` | double | 总资产周转率 | |
| `current_assets_turnover_rate` | double | 流动资产周转率 | 可null |
| `inventory_turnover_rate` | double | 库存周转率 | 可null |
| `accounts_receivables_turnover_rate` | double | 应收账款周转率 | 可null |
| `current_ratio` | double | 流动比率 | 可null |
| `quick_ratio` | double | 速动比率 | 可null |
| `debt_equity_ratio` | double | 资产负债率 | |
| `interest_cover` | double | 利息覆盖率 | 可null |
| `roic` | double | 投入资本回报率 | 可null |
| `roa_ebit_ttm` | double | EBIT/总资产(TTM) | 可null |
| `roe_weighted` | double | 加权 ROE | 可null |
| `total_shares` | double | 总股本 | 股 |
| `a_floats` | double | A股流通股本 | 股 |

以上扩展字段使用 PTrade 财务 API 字段名；当上游 TDX/mootdx FINVALUE 批量包无法对应到 PTrade 字段时不导出。

---

### 5. metadata/stock_metadata.parquet - 股票信息

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `symbol` | string | 股票代码 | `000001.SZ` |
| `stock_name` | string | 股票名称 | `平安银行` |
| `listed_date` | string | 上市日期 | `1991-04-03` |
| `de_listed_date` | string | 退市日期 | null=未退市 |
| `blocks` | string | 板块 | `主板`/`创业板` |

---

### 6. metadata/benchmark.parquet - 基准指数

| 字段 | 类型 | 说明 |
|------|------|------|
| `date` | datetime64[ns] | 交易日期 |
| `open` | float64 | 开盘价 |
| `high` | float64 | 最高价 |
| `low` | float64 | 最低价 |
| `close` | float64 | 收盘价 |
| `volume` | float64 | 成交量 |
| `money` | float64 | 成交金额 |

---

### 7. metadata/trade_days.parquet - 交易日历

| 字段 | 类型 | 说明 |
|------|------|------|
| `date` | datetime64[ns] | 交易日期 |

---

### 8. metadata/index_constituents.parquet - 指数成分股

| 字段 | 类型 | 说明 |
|------|------|------|
| `date` | string | 调整日期 (YYYYMMDD) |
| `index_code` | string | 指数代码 |
| `symbols` | string (JSON array) | 成分股列表 |

---

### 9. metadata/stock_status.parquet - 股票状态

| 字段 | 类型 | 说明 |
|------|------|------|
| `date` | string | 日期 (YYYYMMDD) |
| `status_type` | string | 状态类型: `HALT`/`ST`/`DELISTING` |
| `symbols` | string (JSON array) | 股票列表 |

---

### 10. metadata/version.parquet - 版本信息

| 字段 | 类型 | 示例值 |
|------|------|--------|
| `version` | string | `2.0.0` |
| `num_stocks` | int | 5427 |
| `export_date` | string | `2026-01-26` |
| `start_date` | string | `2015-01-01` |

---

### 11. ptrade_adj_pre.parquet / ptrade_adj_post.parquet - 复权因子

| 字段 | 类型 | 说明 |
|------|------|------|
| `date` | timestamp[ns] | 交易日期 |
| `symbol` | string | 股票代码 |
| `adj_a` | double | 复权因子A |
| `adj_b` | double | 复权因子B |

**复权公式**: `复权价 = 原价 * adj_a + adj_b`

**行数**: 约 1450万行 (所有symbol × 所有交易日)

---

## 数据统计参考

| 目录 | 文件数 | 大小 | 说明 |
|------|--------|------|------|
| stocks | ~5,400 | ~436 MB | 日线行情 |
| exrights | ~5,300 | ~22 MB | 除权除息 |
| fundamentals | ~5,400 | ~123 MB | 季度财务 |
| valuation | ~5,400 | ~44 MB | 估值指标 |
| metadata | 6 | ~8 MB | 元数据 |
| 复权因子 | 2 | ~25 MB | 前/后复权 |
| **合计** | ~21,500 | **~658 MB** | |

---

## BaoStock 字段映射

### 市场数据映射

| BaoStock 字段 | PTrade 字段 |
|---------------|-------------|
| `date` | `date` |
| `open` | `open` |
| `high` | `high` |
| `low` | `low` |
| `close` | `close` |
| `preclose` | `preclose` |
| `volume` | `volume` |
| `amount` | `money` |

> `high_limit` 和 `low_limit` 在导出时根据 `preclose` 计算

### 估值数据映射

| BaoStock 字段 | PTrade 字段 | 来源 |
|---------------|-------------|------|
| `peTTM` | `pe_ttm` | 日线API |
| `pbMRQ` | `pb` | 日线API |
| `psTTM` | `ps_ttm` | 日线API |
| `pcfNcfTTM` | `pcf` | 日线API |
| `turn` | `turnover_rate` | 日线API |
| `roeAvg` | `roe` | 季报, forward fill到日频 |
| - | `roe_ttm` | 季报TTM计算, forward fill到日频 |
| `roa` | `roa` | 季报, forward fill到日频 |
| - | `roa_ttm` | 季报TTM计算, forward fill到日频 |
| - | `naps` | 导出时计算: `close / pb` |
| `totalShare` | `total_shares` | 季报, forward fill到日频 |
| `liqaShare` | `a_floats` | 季报, forward fill到日频 |

### 财务数据映射

| BaoStock 字段 | PTrade 字段 |
|---------------|-------------|
| `roeAvg` | `roe` |
| `npMargin` | `net_profit_ratio` |
| `gpMargin` | `gross_income_ratio` |
| `currentRatio` | `current_ratio` |
| `quickRatio` | `quick_ratio` |
| `liabilityToAsset` | `debt_equity_ratio` |
| `YOYORev` | `operating_revenue_grow_rate` |
| `YOYNI` | `net_profit_grow_rate` |
| `YOYAsset` | `total_asset_grow_rate` |
| `YOYEPSBasic` | `basic_eps_yoy` |
| `YOYPNI` | `np_parent_company_yoy` |
| `NRTurnRatio` | `accounts_receivables_turnover_rate` |
| `INVTurnRatio` | `inventory_turnover_rate` |
| `CATurnRatio` | `current_assets_turnover_rate` |
| `AssetTurnRatio` | `total_asset_turnover_rate` |
| `ebitToInterest` | `interest_cover` |
| `totalShare` | `total_shares` |
| `liqaShare` | `a_floats` |

> TTM 字段 (`roe_ttm`, `roa_ttm`, `net_profit_ratio_ttm`, `gross_income_ratio_ttm`) 在导出时通过4季度滚动平均计算

---

## 代码示例

### 读取数据

```python
import pyarrow.parquet as pq

# 读取单个股票行情
df = pq.read_table('data/stocks/000001.SZ.parquet').to_pandas()

# 读取元数据
meta = pq.read_table('data/metadata/stock_metadata.parquet').to_pandas()

# 读取复权因子
adj = pq.read_table('data/ptrade_adj_pre.parquet').to_pandas()
```

### 使用 DuckDB 查询

```python
import duckdb

# 查询单个股票
df = duckdb.query("""
    SELECT * FROM 'data/stocks/000001.SZ.parquet'
    WHERE date >= '2024-01-01'
""").df()

# 跨文件查询
df = duckdb.query("""
    SELECT * FROM 'data/stocks/*.parquet'
    WHERE date = '2024-01-15'
""").df()
```
