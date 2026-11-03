[English](README.md) | 中文 | [Deutsch](README_de.md)

# SimTradeData - 高效量化交易数据下载工具

[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-red.svg)](LICENSE)
[![Version](https://img.shields.io/badge/Version-1.2.0-brightgreen.svg)](CHANGELOG.md)
[![DuckDB](https://img.shields.io/badge/Storage-DuckDB-FFF000?logo=duckdb&logoColor=black)](https://duckdb.org/)
[![Parquet](https://img.shields.io/badge/Export-Parquet-50ABF1)](https://parquet.apache.org/)
[![Code Style: Black](https://img.shields.io/badge/Code%20Style-Black-000000.svg)](https://github.com/psf/black)
[![uv](https://img.shields.io/badge/Packaging-uv-261230)](https://docs.astral.sh/uv/)

> **BaoStock + Mootdx + EastMoney + yfinance 多数据源** | **A股 + 美股** | **PTrade格式兼容** | **DuckDB + Parquet存储**

**SimTradeData** 是为 [SimTradeLab](https://github.com/kay-ou/SimTradeLab) 设计的高效数据下载工具。支持 A 股（BaoStock、Mootdx、EastMoney）和美股（yfinance）多数据源，各取所长自动编排，采用 DuckDB 作为中间存储，导出为 Parquet 格式，支持高效的增量更新和数据查询。

---

<div align="center">

### 推荐组合：SimTradeData + SimTradeLab

**完全兼容PTrade | A股+美股 | 回测速度提升20倍以上**

[![SimTradeLab](https://img.shields.io/badge/SimTradeLab-量化回测框架-blue?style=for-the-badge)](https://github.com/kay-ou/SimTradeLab)

**无需修改PTrade策略代码** | **极速本地回测** | **零成本解决方案**

</div>

---

## 核心特性

### 高效存储架构
- **DuckDB 中间存储**: 高性能列式数据库，支持 SQL 查询和增量更新
- **Parquet 导出格式**: 压缩高效，跨平台兼容，适合大规模数据分析
- **自动增量更新**: 智能识别已下载数据，仅更新增量部分

### 数据完整性
- **市场数据**: OHLCV 日线数据，含涨跌停价、前收盘价
- **估值指标**: PE/PB/PS/PCF/换手率/总股本/流通股
- **财务数据**: 23个季度财务指标 + TTM指标自动计算
- **除权除息**: 分红、送股、配股数据（含前复权因子）
- **元数据**: 股票信息、交易日历、指数成分股、ST/停牌状态
- **美股支持**: 6000+ 美股普通股，S&P 500 / NASDAQ-100 指数成分股

### 数据质量保障
- **自动验证**: 写入前自动验证数据完整性
- **导出时计算**: 涨跌停价、TTM指标等在导出时计算，确保数据一致性
- **详细日志**: 完整的错误日志和警告信息

## 生成的数据结构

```
data/
├── cn.duckdb          # DuckDB 数据库 - A股（下载源）
├── us.duckdb             # DuckDB 数据库 - 美股（下载源）
└── export/                      # 导出的 Parquet 文件（按市场分目录）
    ├── cn/                      # A股导出
    │   ├── stocks/              # 日线行情（每只股票一个文件）
    │   │   ├── 000001.SZ.parquet
    │   │   └── 600519.SS.parquet
    │   ├── exrights/            # 除权除息事件
    │   ├── fundamentals/        # 季度财务数据（含TTM）
    │   ├── valuation/           # 估值指标（日频）
    │   ├── metadata/            # 元数据
    │   └── manifest.json
    └── us/                      # 美股导出
        ├── stocks/
        │   ├── AAPL.US.parquet
        │   └── MSFT.US.parquet
        ├── exrights/
        ├── fundamentals/
        ├── valuation/
        ├── metadata/
        └── manifest.json
```

## 前置条件

- **Python**: 3.10 或更高版本
- **uv**: [安装指南](https://docs.astral.sh/uv/getting-started/installation/)
- **网络**: 下载数据需要网络（A 股数据建议使用中国大陆网络）

## 快速开始

### 方式一：直接下载现成数据（推荐）

从 [Releases](https://github.com/kay-ou/SimTradeData/releases) 下载最新数据：

- **A股**: `data-cn-v*` release → 解压到 `data/cn/`
- **美股**: `data-us-v*` release → 解压到 `data/us/`

```bash
# A股
mkdir -p /path/to/SimTradeLab/data/cn
tar -xzf simtradelab-data-cn-*.tar.gz -C /path/to/SimTradeLab/data/cn/

# 美股
mkdir -p /path/to/SimTradeLab/data/us
tar -xzf simtradelab-data-us-*.tar.gz -C /path/to/SimTradeLab/data/us/
```

### 方式二：自行下载数据

#### 1. 安装依赖

```bash
# 克隆项目
git clone https://github.com/kay-ou/SimTradeData.git
cd SimTradeData

# 安装依赖
uv sync
```

#### 2. 下载数据

**推荐方式：统一下载命令**

一条命令完成所有数据下载，使用 TDX 日线包作为快速 OHLCV 路径，再自动编排 Mootdx 和 BaoStock 各自擅长的数据：

```bash
# 每日/生产 CN 刷新（推荐）
# TDX: OHLCV 全历史和日线包导入
# Mootdx: 除权除息、批量财务、交易日历、基准指数
# BaoStock: 估值指标、ST/停牌状态、指数成分股
uv run python scripts/download.py --tdx-download --skip-mootdx-ohlcv

# 不使用 TDX 日线包的完整兜底编排
uv run python scripts/download.py

# 使用已下载的 TDX ZIP 文件
uv run python scripts/download.py --tdx-source data/downloads/hsjday.zip --skip-mootdx-ohlcv

# 查看数据状态
uv run python scripts/download.py --status

# 跳过财务数据（更快）
uv run python scripts/download.py --tdx-download --skip-mootdx-ohlcv --skip-fundamentals

# 仅运行 Mootdx 阶段
uv run python scripts/download.py --source mootdx

# 仅运行 BaoStock 阶段
uv run python scripts/download.py --source baostock
```

**数据源分工说明**

| 数据类型 | 负责数据源 | 原因 |
|---------|-----------|------|
| 行情 OHLCV（每日/全历史） | TDX 日线包 | 最快，~500MB 一次性导入全部历史和最新日线 |
| 行情 OHLCV（兜底/回补） | Mootdx | TDX 日线包不可用时逐股兜底 |
| 除权除息 (XDXR) | Mootdx | 数据更完整 |
| 批量财务数据 | Mootdx | 一个ZIP=所有股票，远优于逐股查询 |
| 估值 PE/PB/PS/换手率 | BaoStock | 独有数据 |
| ST/停牌状态 | BaoStock | 独有数据 |
| 指数成分股 | BaoStock | 独有数据 |
| 交易日历 | Mootdx | 提供交易所日历 |
| 基准指数 | Mootdx | 提供基准指数序列 |

**单独使用某个数据源**

```bash
# BaoStock（包含估值数据，但速度较慢）
uv run python scripts/download_efficient.py
uv run python scripts/download_efficient.py --skip-fundamentals
uv run python scripts/download_efficient.py --valuation-only  # 仅估值+状态

# Mootdx（速度快，但无估值数据）
uv run python scripts/download_mootdx.py
uv run python scripts/download_mootdx.py --skip-fundamentals
```

**EastMoney 补充数据（资金流向、龙虎榜、融资融券）**

```bash
# 下载最近30天的补充数据（需先有行情数据）
uv run python scripts/download_daily_extras.py

# 指定天数（龙虎榜 API 仅保留~30天数据，建议定期运行）
uv run python scripts/download_daily_extras.py --days 7
```

**美股数据下载（yfinance）**

使用 yfinance 免费获取美股数据，无需 API Key：

```bash
# 完整下载（6000+ 只美股，含 OHLCV + 财务 + 估值 + 元数据）
uv run python scripts/download_us.py

# 指定股票（小规模测试）
uv run python scripts/download_us.py --symbols AAPL,MSFT,GOOGL

# 仅下载行情数据（跳过耗时的逐股财务和元数据）
uv run python scripts/download_us.py --skip-fundamentals --skip-metadata

# 指定起始日期
uv run python scripts/download_us.py --start-date 2020-01-01
```

美股代码格式：`AAPL.US`（与 A 股 `600000.SS` 保持 `{code}.{market}` 一致），数据存入独立数据库 `data/us.duckdb`。

**TDX 官方数据包（最快获取完整历史行情）**

```bash
# 自动下载通达信官方沪深京日线完整包（~500MB）
uv run python scripts/download_tdx_day.py

# 强制重新下载
uv run python scripts/download_tdx_day.py --force-download

# 使用已下载的文件
uv run python scripts/download_tdx_day.py --file hsjday.zip
```

#### 3. 导出为 Parquet

```bash
# 导出 A股 → data/export/cn/
uv run python scripts/export_parquet.py

# 导出美股 → data/export/us/
uv run python scripts/export_parquet.py --market us

# 自定义输出目录
uv run python scripts/export_parquet.py --market cn --output /custom/path
```

#### 4. 发布数据（维护者）

两个分发渠道都来自同一份 DuckDB/Parquet 快照，但发布内容不同：

- **GitHub Releases** 只发布完整基线包。
- **腾讯云 COS** 发布完整基线；当 COS 上一版本可以安全推进时，同时发布增量包，并通过 `releases.json` 索引供客户端发现。

```bash
# 发布 A股数据
bash scripts/release_data.sh --market cn

# 发布美股数据
bash scripts/release_data.sh --market us

# 发布版本来自 data/export/<market>/manifest.json

# 发布完整基线，并在可用时同时发布 COS 增量包
COS_SECRET_ID=... COS_SECRET_KEY=... bash scripts/release_data.sh \
  --market cn --publish-targets cos --cos-bucket BUCKET --cos-region REGION
```

#### 5. 在 SimTradeLab 中使用

```bash
# 复制导出数据到 SimTradeLab 数据目录
rsync -a data/export/cn/ /path/to/SimTradeLab/data/cn/
rsync -a data/export/us/ /path/to/SimTradeLab/data/us/
```

## 项目架构

```
SimTradeData/
├── scripts/
│   ├── download.py                # 统一下载入口（A股推荐）
│   ├── download_efficient.py      # BaoStock 下载脚本
│   ├── download_mootdx.py         # Mootdx（通达信API）下载脚本
│   ├── download_daily_extras.py   # EastMoney 补充数据下载脚本
│   ├── download_tdx_day.py        # TDX 官方日线数据包下载导入脚本
│   ├── download_us.py             # 美股下载脚本（yfinance）
│   ├── import_tdx_day.py          # TDX .day 文件导入脚本
│   ├── export_parquet.py          # Parquet 导出脚本
│   └── release_data.sh            # GitHub Release 发布脚本
├── simtradedata/
│   ├── router/
│   │   ├── smart_router.py      # SmartRouter 智能数据源路由
│   │   ├── route_config.py      # 路由表配置
│   │   └── exceptions.py        # 路由异常
│   ├── fetchers/
│   │   ├── base_fetcher.py      # 基础 Fetcher 类
│   │   ├── baostock_fetcher.py  # BaoStock 数据获取
│   │   ├── unified_fetcher.py   # BaoStock 统一数据获取（优化版）
│   │   ├── mootdx_fetcher.py    # Mootdx 基础数据获取
│   │   ├── mootdx_unified_fetcher.py  # Mootdx 统一数据获取
│   │   ├── mootdx_affair_fetcher.py   # Mootdx 财务数据获取
│   │   ├── eastmoney_fetcher.py # EastMoney 补充数据获取
│   │   └── yfinance_fetcher.py  # yfinance 美股数据获取
│   ├── processors/
│   │   └── data_splitter.py     # 数据分流处理
│   ├── writers/
│   │   └── duckdb_writer.py     # DuckDB 写入和导出
│   ├── validators/
│   │   └── data_validator.py    # 数据质量验证
│   ├── config/
│   │   ├── field_mappings.py    # A股字段映射配置
│   │   ├── us_field_mappings.py # 美股字段映射配置
│   │   └── mootdx_finvalue_map.py  # Mootdx 财务字段映射
│   └── utils/
│       ├── code_utils.py        # 股票代码转换
│       └── ttm_calculator.py    # 季度范围计算
├── data/                        # 数据目录
└── docs/                        # 文档
    ├── PTRADE_PARQUET_FORMAT.md # Parquet 格式规范
    └── PTrade_API_mini_Reference.md
```

### 核心模块

**1. SmartRouter** - 智能数据源路由
- 统一数据访问接口，自动根据数据类型和市场选择最佳数据源
- 静态优先级 + 健康感知：主源失败时自动 fallback 到备用源
- 集成 Phase 1 熔断器，跳过不健康的数据源

```python
from simtradedata.router import SmartRouter

with SmartRouter() as router:
    # 自动选择最佳源：mootdx → eastmoney → baostock
    df = router.get_daily_bars("600000.SS", "2024-01-01", "2024-12-31")

    # 单源数据也走 router，接口统一
    mf = router.get_money_flow("600000.SS", "2024-01-01", "2024-12-31")

    # 美股自动路由到 yfinance
    us = router.get_daily_bars("AAPL.US", "2024-01-01", "2024-12-31")
```

**2. UnifiedDataFetcher** - 统一数据获取
- 一次 API 调用获取行情、估值、状态数据
- 减少 API 调用次数 33%

**2. DuckDBWriter** - 数据存储和导出
- 高效的增量写入（upsert）
- 导出时计算涨跌停价、TTM指标
- Forward fill 季度数据到日频

**3. DataSplitter** - 数据分流
- 将统一数据按类型分流到不同表

## 数据字段说明

### stocks/ - 股票日线
| 字段 | 说明 |
|------|------|
| date | 交易日期 |
| open/high/low/close | OHLC价格 |
| high_limit/low_limit | 涨跌停价（导出时计算） |
| preclose | 前收盘价 |
| volume | 成交量（股） |
| money | 成交金额（元） |

### valuation/ - 估值指标（日频）
| 字段 | 说明 |
|------|------|
| pe_ttm/pb/ps_ttm/pcf | 估值比率 |
| roe/roe_ttm/roa/roa_ttm | 盈利指标（季报forward fill） |
| naps | 每股净资产（导出时计算） |
| total_shares/a_floats | 总股本/流通股 |
| turnover_rate | 换手率 |

### fundamentals/ - 财务数据（季频）
包含23个财务指标及其TTM版本，详见 [PTRADE_PARQUET_FORMAT.md](docs/PTRADE_PARQUET_FORMAT.md)

## 配置说明

编辑 `scripts/download_efficient.py`:

```python
# 日期范围
START_DATE = "2017-01-01"
END_DATE = None  # None = 当前日期

# 输出目录
OUTPUT_DIR = "data"

# 批次大小
BATCH_SIZE = 20
```

## 文档

| 文档 | 说明 |
|------|------|
| [PTRADE_PARQUET_FORMAT.md](docs/PTRADE_PARQUET_FORMAT.md) | Parquet 数据格式规范 |
| [PTrade_API_mini_Reference.md](docs/PTrade_API_mini_Reference.md) | PTrade API 参考 |

## 注意事项

### 数据源对比

| 特性 | BaoStock | Mootdx API | EastMoney | TDX 官方数据包 | yfinance (美股) |
|------|----------|------------|-----------|---------------|----------------|
| 市场 | A股 | A股 | A股 | A股 | 美股 |
| 速度 | 较慢 | 快 | 快 | 最快（一次性下载） | 中等 |
| 估值数据 | 有 (PE/PB/PS等) | 无 | 无 | 无 | 有（计算得出） |
| 财务数据 | 有（逐股查询） | 有（批量ZIP，更快） | 无 | 无 | 有（逐股查询） |
| 资金流向 | 无 | 无 | 有（独有） | 无 | 无 |
| 龙虎榜 | 无 | 无 | 有（独有） | 无 | 无 |
| 融资融券 | 无 | 无 | 有（独有） | 无 | 无 |
| 历史起始 | 2015年 | 2015年 | 2015年 | 完整历史 | 完整历史 |
| API Key | 不需要 | 不需要 | 不需要 | N/A | 不需要 |

> **推荐**：每日/生产 CN 刷新使用 `scripts/download.py --tdx-download --skip-mootdx-ohlcv`。TDX 负责 OHLCV，Mootdx 补充除权除息/财务/日历/基准指数，BaoStock 负责估值/状态。

### 增量更新机制

- **行情数据**：检查是否有新交易日，无新数据时秒级跳过
- **财务数据**：基于远程文件 hash 增量检查，仅下载有变更的季度
- **指数成分股**：记录已下载月份，仅下载新月份
- **中断恢复**：财务数据进度与数据在同一事务中提交，中断后可续传

#### 增量更新流程

```bash
# 1. 增量下载（仅获取新数据，已有数据自动跳过）
uv run python scripts/download.py --tdx-download --skip-mootdx-ohlcv

# 2. 导出完整 Parquet 快照（替换输出目录）
uv run python scripts/export_parquet.py

# 3. 仅维护者：导出独立增量包
uv run python scripts/export_parquet.py --delta --base-version 2026-06-20 --target-version 2026-06-22
```

第 1 步会导入最新 TDX 日线包，然后跳过 Mootdx OHLCV，并刷新剩余 Mootdx/BaoStock 数据。
无新交易日时已有行会快速跳过。
第 2 步的普通导出会从 DuckDB 重建目标目录，并不是在原 Parquet 文件上原地合并。delta 导出会生成独立数据包，只包含指定版本窗口内变化的 symbol 表行和 manifest。

终端用户不需要运行 delta 命令，也不需要选择更新模式。SimTradeDeskX 会让 SimTradeAPI 自动更新：优先使用已配置的动态增量端点，其次使用 COS 的增量链；首次安装、校准或增量失败时自动回退到完整基线。API 会先在快照上应用整条增量链，再通过目录原子替换发布，因此更新失败时旧数据仍然可用。GitHub Releases 保持为完整基线回退渠道；COS 才提供带索引的增量分发。

生产环境建议使用每日脚本，并把执行时间放到交易日较晚时段（例如 22:30 以后）。该脚本默认使用同一条 TDX 快路径。推荐生产配置是三次有界尝试，每次都覆盖下载和发布前完整性门禁，让短暂的数据源延迟有机会自动恢复，但仍不会发布不完整数据：

```bash
DOWNLOAD_ATTEMPTS=3 INTEGRITY_STRICT=1 bash scripts/run_daily.sh
```

完整性门禁会检查活跃股票的行情和估值是否到最新日期、导出 manifest 是否匹配，并在 `logs/daily/` 下写入 JSON 报告。如果三次有界尝试仍失败，保留最后一个已知可用 release，再根据日志或告警做人工恢复。

### 数据质量
- 数据来自 BaoStock 免费数据源
- 仅供学习研究使用

## 测试

```bash
# 单元测试（无需网络）
uv run pytest tests/ -v

# SmartRouter 路由和 fallback 测试
uv run pytest tests/router/ -v

# SmartRouter 真实数据源集成测试（需要网络）
uv run python scripts/test_smart_router_live.py
```

## 版本历史

完整版本历史请查看 [CHANGELOG.md](CHANGELOG.md)。

**最新版本**: v1.2.0 (2026-03-13) - 智能数据源路由

## 相关链接

- **SimTradeLab**: https://github.com/kay-ou/SimTradeLab
- **BaoStock**: http://baostock.com/
- **Mootdx**: https://github.com/mootdx/mootdx
- **EastMoney**: https://www.eastmoney.com/
- **yfinance**: https://github.com/ranaroussi/yfinance

## 💖 赞助支持

如果这个项目对您有帮助，欢迎赞助支持开发！

| 微信赞助 | 支付宝赞助 |
|:---:|:---:|
| <img src="docs/sponsor/WechatPay.png?raw=true" alt="微信赞助" width="200"> | <img src="docs/sponsor/AliPay.png?raw=true" alt="支付宝赞助" width="200"> |

**您的支持是我们持续改进的动力！**

## 许可证

本项目采用 AGPL-3.0 许可证。详见 [LICENSE](LICENSE) 文件。

---

**项目状态**: 生产就绪 | **当前版本**: v1.2.0 | **最后更新**: 2026-03-13
