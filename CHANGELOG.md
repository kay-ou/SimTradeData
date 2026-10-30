# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed
- exrights 导出在 `dividend` 为空时用 `bonus_ps` 回填（部分权威数据源只提供 `bonus_ps`，两者在现有数据上完全一致）；覆盖全量 per-symbol、batch、delta 和 API delta 四条导出路径，避免 SimTradeLab 分红缓存读不到现金分红
- 统一 exrights 事件局部字段契约：`allotted_ps`=送股/转增、`rationed_ps`=配股、`rationed_px`=配股价、`bonus_ps`=税前现金分红、`dividend` 为 `bonus_ps` 别名。修正 TDX 字段映射（`songzhuangu→allotted_ps`、`fenhong→bonus_ps`）、BaoStock 把转增并入 `allotted_ps`、东财补齐配股/分红列；`write_exrights` 对缺失列补 0 并回填 `dividend`

### Changed
- Valuation export prefers daily fields (`roe`, `roa`, `naps`, `total_shares`, `float_value`) over quarterly fundamentals enrichment when present
- `valuation` table gains `a_shares` (A股股本) and `float_value` (流通市值) daily columns; `write_valuation` only overwrites the columns a source provides, preserving daily fields
- Valuation export derives `a_floats` as the customer snapshot's market cap over the trading day's close (`float_value / close`) instead of carrying the quarterly fundamentals share count forward. The engine computes 流通市值 as `close × a_floats` at runtime and ignores the stored `float_value` column, so a quarterly share count made the returned value differ from the platform's own figure and step on report days (observed: 002226.SZ 2026-06-30, +51% overnight). With this, the engine's value equals the stored `float_value` — i.e. redis — on every row that has a snapshot; rows without one leave `a_floats` NULL rather than inventing a figure from quarterly data

## [1.2.1] - 2026-03-26

### Fixed
- Valuation export: replace `ASOF JOIN fundamentals` with `LEFT JOIN LATERAL` to preserve early-period valuation rows when no fundamentals match exists
- Affects both single-symbol and batch export paths in `DuckDBWriter`

## [1.2.0] - 2026-03-13 - Smart Data Source Router / 智能数据源路由

### Added
- SmartRouter unified data access layer / SmartRouter 统一数据访问层
- Auto-selects best data source by data type and market / 自动根据数据类型和市场选择最佳数据源
- Static priority + circuit breaker health-aware, auto fallback on failure / 静态优先级 + 熔断器健康感知，主源失败自动 fallback
- 13 data types: daily bars, XDXR, fundamentals, valuation, money flow, LHB, margin trading, etc. / 支持 13 种数据类型
- EastMoney as A-share daily bars fallback source / EastMoney 作为 A 股日线 fallback
- Output column normalization: consistent column structure regardless of source / 输出列标准化

## [1.1.0] - 2026-03-10 - TDX Fast Import Integration / TDX 快速导入集成

### Added
- `--tdx-download` flag: auto-download TDX official daily data package and import / 自动下载 TDX 官方日线包并导入
- `--tdx-source` flag: import TDX daily data from local ZIP file or directory / 从本地 ZIP 文件导入
- First-time 6,000+ stocks OHLCV reduced from hours to minutes / 首次下载 6000+ 只股票从数小时缩短到几分钟
- TDX import runs as Phase 0 before Mootdx phase / TDX 导入作为 Phase 0 在 Mootdx 之前执行

### Fixed
- Corporate actions not downloading after TDX bulk import / TDX 导入后除权除息无法补充下载
- Corporate actions now check per-symbol independently of OHLCV incremental logic / 除权除息按个股检查

## [0.6.0] - 2026-02-08 - US Stock Support / 美股数据支持

### Added
- yfinance data source: 6,000+ US common stocks / yfinance 数据源，6000+ 只美股
- US stock ticker format `AAPL.US`, consistent with A-shares `{code}.{market}` / 美股代码格式
- Separate database `data/us.duckdb` / 独立数据库
- 5-phase download: stock list -> bulk OHLCV -> financials+valuation -> metadata+corporate actions -> global data
- `yf.download()` batch market data (50 per batch) / 批量获取行情
- S&P 500 / NASDAQ-100 index constituents (Wikipedia) / 指数成分股
- Incremental updates via `get_max_date()` / 增量更新

## [0.5.0] - 2026-02-01 - Unified Download Command / 统一下载命令

### Added
- `scripts/download.py` unified download entry point / 统一下载入口
- Automatic orchestration of Mootdx and BaoStock sources / 自动编排数据源
- Optimized incremental detection: skips all stocks in seconds when no new trading days / 优化增量检测
- Financial data incremental via remote file hash / 财务数据增量检测
- Index constituents incremental: tracks downloaded months / 指数成分股增量

### Fixed
- Mootdx Affair API return value handling / Mootdx Affair API 返回值处理
- DuckDB `changes()` function compatibility / DuckDB changes() 兼容性
- Auto-filters empty rows for suspended stocks / 停牌股票空行过滤

## [0.4.0] - 2026-01-30 - DuckDB + Parquet Architecture / DuckDB + Parquet 架构

### Changed
- Storage migrated from HDF5 to DuckDB + Parquet / 存储从 HDF5 迁移到 DuckDB + Parquet

### Added
- Limit-up/down price calculation (computed at export from preclose) / 涨跌停价计算
- TTM metric calculation (SQL window functions at export) / TTM 指标计算
- Corporate action data download / 除权除息数据下载
- Share capital data (total_shares/a_floats) / 股本数据
- Optimized incremental update logic / 优化增量更新逻辑

## [0.3.0] - 2025-11-24 - Quality & Architecture Optimization / 质量与架构优化

### Added
- Market cap field calculation / 市值字段计算
- Data validator / 数据验证器
- Extracted BaseFetcher base class / 提取 BaseFetcher 基类

### Fixed
- TTM metric calculation / TTM 指标计算

## [0.2.0] - 2025-11-22 - Performance Optimization / 性能优化

### Added
- Unified data fetching, reducing API calls by 33% / 统一数据获取
- Optimized HDF5 write logic / 优化 HDF5 写入逻辑

## [0.1.0] - 2024-11-14 - Initial Release / 初始版本

### Added
- Basic data download functionality / 基础数据下载功能
- BaoStock data source integration / BaoStock 数据源集成
