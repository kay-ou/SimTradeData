# 命令行下载与导出

[返回文档导航](README.md)。日常股票与ETF更新推荐使用[网页管理台](WEB_DATA_ADMIN.md)；本页用于独立数据源、美股与Parquet导出。

先在项目根目录运行 `uv sync`。命令行下载和网页更新不要同时写入同一个数据库。

## 下载数据

**推荐方式：统一下载命令**

统一命令运行主要A股下载阶段，使用 TDX 日线包作为快速 OHLCV 路径，再自动编排 Mootdx 和 BaoStock 各自擅长的数据：

```bash
# 每日/生产 CN 刷新（推荐）
# TDX: OHLCV 全历史和日线包导入
# Mootdx: 除权除息、批量财务
# BaoStock: 估值指标、ST/停牌状态、指数成分股、日历和指数查询
uv run python scripts/download.py

# 旧参数仍兼容；TDX 包失败不会启用 Mootdx 日线兜底
uv run python scripts/download.py --tdx-download --skip-mootdx-ohlcv

# 使用已下载的 TDX ZIP 文件
uv run python scripts/download.py --tdx-source data/downloads/hsjday.zip --skip-mootdx-ohlcv

# 查看数据状态
uv run python scripts/download.py --status

# 跳过财务数据（更快）
uv run python scripts/download.py --tdx-download --skip-mootdx-ohlcv --skip-fundamentals

# 仅运行 Mootdx 补充数据阶段（不下载 OHLCV）
uv run python scripts/download.py --source mootdx

# 仅运行 BaoStock 阶段
uv run python scripts/download.py --source baostock
```

**数据源分工说明**

| 数据类型 | 负责数据源 | 原因 |
|---------|-----------|------|
| 行情 OHLCV（每日/全历史） | TDX 日线包 | 最快，批量导入源包所含历史与最新日线，大小随来源变化 |
| 行情 OHLCV（逐证券查询） | Mootdx，失败后使用 EastMoney、BaoStock | 已恢复匿名初始化，支持股票与ETF历史分页 |
| 除权除息 (XDXR) | Mootdx | 数据更完整 |
| 批量财务数据 | Mootdx | 一个ZIP=所有股票，远优于逐股查询 |
| 估值 PE/PB/PS/换手率 | BaoStock | 独有数据 |
| ST/停牌状态 | BaoStock | 独有数据 |
| 指数成分股 | BaoStock | 独有数据 |
| 交易日历 | BaoStock | 独立交易所日历 |
| 基准指数 | TDX 日线包 / BaoStock | 批量导入 / 逐指数查询 |

**单独使用某个数据源**

```bash
# BaoStock（包含估值数据，但速度较慢）
uv run python scripts/download_efficient.py
uv run python scripts/download_efficient.py --skip-fundamentals
uv run python scripts/download_efficient.py --valuation-only  # 仅估值+状态

# Mootdx 补充数据（除权除息和批量财务，不下载 OHLCV）
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
# 下载美股证券池（OHLCV、财务、估值和元数据按来源可用范围）
uv run python scripts/download_us.py

# 指定股票（小规模测试）
uv run python scripts/download_us.py --symbols AAPL,MSFT,GOOGL

# 仅下载行情数据（跳过耗时的逐股财务和元数据）
uv run python scripts/download_us.py --skip-fundamentals --skip-metadata

# 指定起始日期
uv run python scripts/download_us.py --start-date 2020-01-01
```

美股代码格式：`AAPL.US`（与 A 股 `600000.SS` 保持 `{code}.{market}` 一致），数据存入独立数据库 `data/us.duckdb`。

**TDX 官方历史数据包**

```bash
# 下载并导入通达信官方日线包
uv run python scripts/download_tdx_day.py

# 强制重新下载
uv run python scripts/download_tdx_day.py --force-download

# 使用已下载的文件
uv run python scripts/download_tdx_day.py --file hsjday.zip
```

## 导出为 Parquet

```bash
# 导出 A股 → data/export/cn/
uv run python scripts/export_parquet.py

# 导出美股 → data/export/us/
uv run python scripts/export_parquet.py --market us

# 自定义输出目录
uv run python scripts/export_parquet.py --market cn --output /custom/path
```

## 在 SimTradeLab 中使用

```bash
# 复制导出数据到 SimTradeLab 数据目录
rsync -a data/export/cn/ /path/to/SimTradeLab/data/cn/
rsync -a data/export/us/ /path/to/SimTradeLab/data/us/
```

## 完整快照与增量包

普通导出从DuckDB重建目标目录，不在原Parquet文件上合并。维护者可生成独立增量包：

```bash
uv run python scripts/export_parquet.py --delta --base-version 2026-06-20 --target-version 2026-06-22
```

GitHub Releases提供完整基线；COS提供基线和带索引的增量链。发布、生产调度与失败恢复见[运维说明](OPERATIONS.md)。字段和目录结构见[Parquet格式](PTRADE_PARQUET_FORMAT.md)。
