English | [中文](README_zh.md) | [Deutsch](README_de.md)

# SimTradeData - Quantitative Trading Data Downloader

[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-red.svg)](LICENSE)
[![Version](https://img.shields.io/badge/Version-1.2.0-brightgreen.svg)](CHANGELOG.md)
[![DuckDB](https://img.shields.io/badge/Storage-DuckDB-FFF000?logo=duckdb&logoColor=black)](https://duckdb.org/)
[![Parquet](https://img.shields.io/badge/Export-Parquet-50ABF1)](https://parquet.apache.org/)
[![Code Style: Black](https://img.shields.io/badge/Code%20Style-Black-000000.svg)](https://github.com/psf/black)
[![uv](https://img.shields.io/badge/Packaging-uv-261230)](https://docs.astral.sh/uv/)

> **BaoStock + Mootdx + EastMoney + yfinance Multi-Source** | **China A-Shares + US Stocks** | **PTrade Compatible** | **DuckDB + Parquet Storage**

**SimTradeData** is an efficient data download tool designed for [SimTradeLab](https://github.com/kay-ou/SimTradeLab). It supports China A-shares (BaoStock, Mootdx, EastMoney) and US stocks (yfinance) from multiple data sources, automatically orchestrating each source's strengths. Data is stored in DuckDB as intermediate storage and exported to Parquet format, with efficient incremental updates and querying.

---

<div align="center">

### Recommended Combo: SimTradeData + SimTradeLab

**Fully PTrade Compatible | A-Shares + US Stocks | 20x+ Backtesting Speedup**

[![SimTradeLab](https://img.shields.io/badge/SimTradeLab-Quant_Backtesting-blue?style=for-the-badge)](https://github.com/kay-ou/SimTradeLab)

**No PTrade Strategy Code Changes Needed** | **Ultra-Fast Local Backtesting** | **Zero-Cost Solution**

</div>

---

## Key Features

### Efficient Storage Architecture
- **DuckDB Intermediate Storage**: High-performance columnar database with SQL queries and incremental updates
- **Parquet Export Format**: Highly compressed, cross-platform compatible, ideal for large-scale data analysis
- **Automatic Incremental Updates**: Intelligently detects existing data, only downloads new records

### Comprehensive Data Coverage
- **Market Data**: OHLCV daily bars with limit-up/down prices and previous close
- **Valuation Metrics**: PE/PB/PS/PCF/Turnover Rate/Total Shares/Float Shares
- **Financial Data**: 23 quarterly financial indicators + automatic TTM calculation
- **Corporate Actions**: Dividends, bonus shares, rights offerings (with forward adjustment factors)
- **Metadata**: Stock info, trading calendar, index constituents, ST/suspension status
- **US Stock Support**: 6,000+ US common stocks, S&P 500 / NASDAQ-100 index constituents

### Data Quality Assurance
- **Auto-Validation**: Data integrity validation before writes
- **Export-Time Calculation**: Limit prices, TTM metrics computed at export for consistency
- **Detailed Logging**: Comprehensive error logs and warnings

## Generated Data Structure

```
data/
├── cn.duckdb          # DuckDB database - A-shares (download source)
├── us.duckdb             # DuckDB database - US stocks (download source)
└── export/                      # Exported Parquet files (by market)
    ├── cn/                      # A-shares export
    │   ├── stocks/              # Daily bars (one file per stock)
    │   │   ├── 000001.SZ.parquet
    │   │   └── 600519.SS.parquet
    │   ├── exrights/            # Corporate action events
    │   ├── fundamentals/        # Quarterly financials (with TTM)
    │   ├── valuation/           # Valuation metrics (daily)
    │   ├── metadata/            # Metadata
    │   └── manifest.json
    └── us/                      # US stocks export
        ├── stocks/
        │   ├── AAPL.US.parquet
        │   └── MSFT.US.parquet
        ├── exrights/
        ├── fundamentals/
        ├── valuation/
        ├── metadata/
        └── manifest.json
```

## Prerequisites

- **Python**: 3.10 or higher
- **uv**: [Installation guide](https://docs.astral.sh/uv/getting-started/installation/)
- **Network**: Required for downloading data from BaoStock/Mootdx/EastMoney/yfinance (China mainland network recommended for A-share data)

## Quick Start

### Option 1: Download Pre-Built Data (Recommended)

Download the latest data from [Releases](https://github.com/kay-ou/SimTradeData/releases):

- **A-shares**: `data-cn-v*` release → extract to `data/cn/`
- **US stocks**: `data-us-v*` release → extract to `data/us/`

```bash
# A-shares
mkdir -p /path/to/SimTradeLab/data/cn
tar -xzf simtradelab-data-cn-*.tar.gz -C /path/to/SimTradeLab/data/cn/

# US stocks
mkdir -p /path/to/SimTradeLab/data/us
tar -xzf simtradelab-data-us-*.tar.gz -C /path/to/SimTradeLab/data/us/
```

### Option 2: Download Data Yourself

#### 1. Install Dependencies

```bash
# Clone the project
git clone https://github.com/kay-ou/SimTradeData.git
cd SimTradeData

# Install dependencies
uv sync
```

#### 2. Download Data

**Recommended: Unified Download Command**

A single command downloads all data, using the TDX daily package for the fast OHLCV path and then orchestrating Mootdx and BaoStock for their respective strengths:

```bash
# Daily/production CN refresh (recommended)
# TDX: OHLCV full history and daily package import
# Mootdx: corporate actions, bulk financials, trading calendar, benchmark index
# BaoStock: valuation metrics, ST/suspension status, index constituents
uv run python scripts/download.py --tdx-download --skip-mootdx-ohlcv

# Fallback full orchestration without the TDX package
uv run python scripts/download.py

# Use an already-downloaded TDX ZIP file
uv run python scripts/download.py --tdx-source data/downloads/hsjday.zip --skip-mootdx-ohlcv

# Check data status
uv run python scripts/download.py --status

# Skip financial data (faster)
uv run python scripts/download.py --tdx-download --skip-mootdx-ohlcv --skip-fundamentals

# Run Mootdx phase only
uv run python scripts/download.py --source mootdx

# Run BaoStock phase only
uv run python scripts/download.py --source baostock
```

**Data Source Division of Labor**

| Data Type | Source | Reason |
|-----------|--------|--------|
| OHLCV Market Data (daily/full history) | TDX Daily Package | Fastest, ~500MB bulk import of full history and latest daily data |
| OHLCV Market Data (fallback/backfill) | Mootdx | Per-symbol fallback when the TDX package is unavailable |
| Corporate Actions (XDXR) | Mootdx | More complete data |
| Bulk Financial Data | Mootdx | One ZIP = all stocks, far better than per-stock queries |
| Valuation PE/PB/PS/Turnover | BaoStock | Exclusive data |
| ST/Suspension Status | BaoStock | Exclusive data |
| Index Constituents | BaoStock | Exclusive data |
| Trading Calendar | Mootdx | Provides exchange calendar data |
| Benchmark Index | Mootdx | Provides benchmark series |

**Using Individual Data Sources**

```bash
# BaoStock (includes valuation data, but slower)
uv run python scripts/download_efficient.py
uv run python scripts/download_efficient.py --skip-fundamentals
uv run python scripts/download_efficient.py --valuation-only  # Valuation + status only

# Mootdx (faster, but no valuation data)
uv run python scripts/download_mootdx.py
uv run python scripts/download_mootdx.py --skip-fundamentals
```

**EastMoney Complementary Data (Money Flow, Dragon Tiger Board, Margin Trading)**

```bash
# Download last 30 days of complementary data (requires existing market data)
uv run python scripts/download_daily_extras.py

# Specify number of days (LHB API only retains ~30 days, run regularly)
uv run python scripts/download_daily_extras.py --days 7
```

**US Stock Data (yfinance)**

Free US stock data via yfinance, no API key required:

```bash
# Full download (6,000+ US stocks with OHLCV + financials + valuation + metadata)
uv run python scripts/download_us.py

# Specific symbols (small-scale testing)
uv run python scripts/download_us.py --symbols AAPL,MSFT,GOOGL

# Market data only (skip time-consuming per-stock financials and metadata)
uv run python scripts/download_us.py --skip-fundamentals --skip-metadata

# Specify start date
uv run python scripts/download_us.py --start-date 2020-01-01
```

US stock ticker format: `AAPL.US` (consistent with A-shares `600000.SS` using `{code}.{market}`), stored in a separate database `data/us.duckdb`.

**TDX Official Data Package (Fastest Way to Get Full Historical Data)**

```bash
# Auto-download official TDX Shanghai/Shenzhen/Beijing daily data package (~500MB)
uv run python scripts/download_tdx_day.py

# Force re-download
uv run python scripts/download_tdx_day.py --force-download

# Use an already-downloaded file
uv run python scripts/download_tdx_day.py --file hsjday.zip
```

#### 3. Export to Parquet

```bash
# Export A-shares → data/export/cn/
uv run python scripts/export_parquet.py

# Export US stocks → data/export/us/
uv run python scripts/export_parquet.py --market us

# Custom output directory
uv run python scripts/export_parquet.py --market cn --output /custom/path
```

#### 4. Publish Data (Maintainer)

The same DuckDB snapshot is exported to Parquet for both distribution channels, but the published artifacts differ:

- **GitHub Releases** publishes full baseline archives only.
- **Tencent COS** publishes a full baseline plus an incremental archive when the previous COS version can be advanced safely. Its `releases.json` index lets clients discover both.

```bash
# Release A-shares data
bash scripts/release_data.sh --market cn

# Release US stock data
bash scripts/release_data.sh --market us

# The release version comes from data/export/<market>/manifest.json

# Publish a baseline and, when possible, a delta to Tencent COS
COS_SECRET_ID=... COS_SECRET_KEY=... bash scripts/release_data.sh \
  --market cn --publish-targets cos --cos-bucket BUCKET --cos-region REGION
```

#### 5. Use in SimTradeLab

```bash
# Copy exported data to SimTradeLab data directory
rsync -a data/export/cn/ /path/to/SimTradeLab/data/cn/
rsync -a data/export/us/ /path/to/SimTradeLab/data/us/
```

## Project Architecture

```
SimTradeData/
├── scripts/
│   ├── download.py                # Unified download entry (recommended for A-shares)
│   ├── download_efficient.py      # BaoStock download script
│   ├── download_mootdx.py         # Mootdx (TDX API) download script
│   ├── download_daily_extras.py   # EastMoney complementary data download script
│   ├── download_tdx_day.py        # TDX official daily data package download/import
│   ├── download_us.py             # US stock download script (yfinance)
│   ├── import_tdx_day.py          # TDX .day file import script
│   ├── export_parquet.py          # Parquet export script
│   └── release_data.sh            # GitHub Release publishing script
├── simtradedata/
│   ├── router/
│   │   ├── smart_router.py      # SmartRouter - smart data source routing
│   │   ├── route_config.py      # Route table configuration
│   │   └── exceptions.py        # Router exceptions
│   ├── fetchers/
│   │   ├── base_fetcher.py      # Base Fetcher class
│   │   ├── baostock_fetcher.py  # BaoStock data fetching
│   │   ├── unified_fetcher.py   # BaoStock unified fetching (optimized)
│   │   ├── mootdx_fetcher.py    # Mootdx basic data fetching
│   │   ├── mootdx_unified_fetcher.py  # Mootdx unified data fetching
│   │   ├── mootdx_affair_fetcher.py   # Mootdx financial data fetching
│   │   ├── eastmoney_fetcher.py # EastMoney complementary data fetching
│   │   └── yfinance_fetcher.py  # yfinance US stock data fetching
│   ├── processors/
│   │   └── data_splitter.py     # Data stream splitting
│   ├── writers/
│   │   └── duckdb_writer.py     # DuckDB write and export
│   ├── validators/
│   │   └── data_validator.py    # Data quality validation
│   ├── config/
│   │   ├── field_mappings.py    # A-share field mapping config
│   │   ├── us_field_mappings.py # US stock field mapping config
│   │   └── mootdx_finvalue_map.py  # Mootdx financial field mapping
│   └── utils/
│       ├── code_utils.py        # Stock code conversion
│       └── ttm_calculator.py    # Quarterly range calculation
├── data/                        # Data directory (gitignored)
│   ├── cn.duckdb      # A-shares DuckDB source
│   ├── us.duckdb         # US stocks DuckDB source
│   └── export/                  # Parquet exports
│       ├── cn/                  # A-shares export
│       └── us/                  # US stocks export
└── docs/                        # Documentation
    ├── PTRADE_PARQUET_FORMAT.md # Parquet format specification
    └── PTrade_API_mini_Reference.md
```

### Core Modules

**1. SmartRouter** - Smart Data Source Router
- Unified data access API, automatically selects the best data source by data type and market
- Static priority + health-aware: auto fallback to backup sources when primary fails
- Integrates Phase 1 circuit breaker, skips unhealthy sources

```python
from simtradedata.router import SmartRouter

with SmartRouter() as router:
    # Auto-selects best source: mootdx → eastmoney → baostock
    df = router.get_daily_bars("600000.SS", "2024-01-01", "2024-12-31")

    # Single-source data also goes through router for unified API
    mf = router.get_money_flow("600000.SS", "2024-01-01", "2024-12-31")

    # US stocks auto-route to yfinance
    us = router.get_daily_bars("AAPL.US", "2024-01-01", "2024-12-31")
```

**2. UnifiedDataFetcher** - Unified Data Fetching
- Single API call fetches market, valuation, and status data
- Reduces API calls by 33%

**2. DuckDBWriter** - Data Storage and Export
- Efficient incremental writes (upsert)
- Computes limit prices and TTM metrics at export time
- Forward-fills quarterly data to daily frequency

**3. DataSplitter** - Data Stream Splitting
- Routes unified data to appropriate tables by type

## Data Field Reference

### stocks/ - Daily Stock Bars
| Field | Description |
|-------|-------------|
| date | Trading date |
| open/high/low/close | OHLC prices |
| high_limit/low_limit | Limit-up/down prices (computed at export) |
| preclose | Previous close price |
| volume | Trading volume (shares) |
| money | Trading amount (CNY) |

### valuation/ - Valuation Metrics (Daily)
| Field | Description |
|-------|-------------|
| pe_ttm/pb/ps_ttm/pcf | Valuation ratios |
| roe/roe_ttm/roa/roa_ttm | Profitability metrics (forward-filled from quarterly reports) |
| naps | Net asset per share (computed at export) |
| total_shares/a_floats | Total shares / float shares |
| turnover_rate | Turnover rate |

### fundamentals/ - Financial Data (Quarterly)
Contains 23 financial indicators and their TTM versions. See [PTRADE_PARQUET_FORMAT.md](docs/PTRADE_PARQUET_FORMAT.md) for details.

## Configuration

Edit `scripts/download_efficient.py`:

```python
# Date range
START_DATE = "2017-01-01"
END_DATE = None  # None = current date

# Output directory
OUTPUT_DIR = "data"

# Batch size
BATCH_SIZE = 20
```

## Documentation

| Document | Description |
|----------|-------------|
| [PTRADE_PARQUET_FORMAT.md](docs/PTRADE_PARQUET_FORMAT.md) | Parquet data format specification |
| [PTrade_API_mini_Reference.md](docs/PTrade_API_mini_Reference.md) | PTrade API reference |

## Notes

### Data Source Comparison

| Feature | BaoStock | Mootdx API | EastMoney | TDX Official Package | yfinance (US) |
|---------|----------|------------|-----------|---------------------|---------------|
| Market | A-shares | A-shares | A-shares | A-shares | US stocks |
| Speed | Slower | Fast | Fast | Fastest (bulk download) | Medium |
| Valuation Data | Yes (PE/PB/PS etc.) | No | No | No | Yes (computed) |
| Financial Data | Yes (per-stock query) | Yes (bulk ZIP, faster) | No | No | Yes (per-stock query) |
| Money Flow | No | No | Yes (exclusive) | No | No |
| Dragon Tiger Board | No | No | Yes (exclusive) | No | No |
| Margin Trading | No | No | Yes (exclusive) | No | No |
| History Start | 2015 | 2015 | 2015 | Full history | Full history |
| API Key | Not required | Not required | Not required | N/A | Not required |

> **Recommended**: Use `scripts/download.py --tdx-download --skip-mootdx-ohlcv` for daily/production CN refreshes. TDX handles OHLCV, Mootdx supplements corporate actions/financials/calendar/benchmark, and BaoStock handles valuation/status.

### Incremental Update Mechanism

- **Market Data**: Checks for new trading days; skips in seconds when no new data
- **Financial Data**: Incremental checks based on remote file hash; only downloads changed quarters
- **Index Constituents**: Tracks downloaded months; only downloads new months
- **Interrupt Recovery**: Financial data progress and data are committed in the same transaction; resumes after interruption

#### Incremental Update Workflow

```bash
# 1. Incremental download (fetches only new data, automatically skips existing)
uv run python scripts/download.py --tdx-download --skip-mootdx-ohlcv

# 2. Export a full Parquet snapshot (replaces the output directory)
uv run python scripts/export_parquet.py              # CN → data/export/cn/
uv run python scripts/export_parquet.py --market us  # US → data/export/us/

# 3. Maintainers only: export a standalone delta package
uv run python scripts/export_parquet.py --delta --base-version 2026-06-20 --target-version 2026-06-22
```

Step 1 imports the latest TDX daily package, skips Mootdx OHLCV after that fast path, and refreshes the remaining Mootdx/BaoStock datasets. When there are no new trading days, existing rows are skipped quickly.
The normal export in step 2 rebuilds the destination from DuckDB; it is not an in-place Parquet merge. Delta export writes a separate package containing changed symbol-table rows and a manifest for the requested version window.

End users do not run the delta command or choose an update mode. SimTradeDeskX asks SimTradeAPI for an automatic update: the API prefers a configured dynamic delta endpoint, then a COS delta chain, and falls back to a full baseline for first install, reconciliation, or failed delta recovery. It applies the complete chain to a snapshot and publishes it with an atomic directory swap, so a failed update keeps the previous data intact. GitHub Releases remain the full-baseline fallback; COS is the channel that supports indexed incremental delivery.

For production scheduling, run the daily script later in the trading day (for example after 22:30). It uses the same TDX-backed fast path by default. The recommended production setting is three bounded attempts, each covering download plus pre-release integrity, so transient upstream delays can recover without publishing incomplete data:

```bash
DOWNLOAD_ATTEMPTS=3 INTEGRITY_STRICT=1 bash scripts/run_daily.sh
```

The integrity gate verifies that active stocks have latest market and valuation data, validates the exported manifest, and writes JSON reports under `logs/daily/`. If all bounded attempts fail, keep the last known-good release and inspect the latest log or alert before running manual recovery.

### Data Quality
- Data sourced from BaoStock free data service
- For research and educational purposes only

## Testing

```bash
# Unit tests (no network required)
uv run pytest tests/ -v

# SmartRouter routing and fallback tests
uv run pytest tests/router/ -v

# SmartRouter live integration test (requires network)
uv run python scripts/test_smart_router_live.py
```

## Version History

See [CHANGELOG.md](CHANGELOG.md) for the full version history.

**Latest**: v1.2.0 (2026-03-13) - Smart Data Source Router

## Related Links

- **SimTradeLab**: https://github.com/kay-ou/SimTradeLab
- **BaoStock**: http://baostock.com/
- **Mootdx**: https://github.com/mootdx/mootdx
- **EastMoney**: https://www.eastmoney.com/
- **yfinance**: https://github.com/ranaroussi/yfinance

## 💖 Sponsor

If this project helps you, consider sponsoring!

| WeChat Pay | Alipay |
|:---:|:---:|
| <img src="docs/sponsor/WechatPay.png?raw=true" alt="WeChat Pay" width="200"> | <img src="docs/sponsor/AliPay.png?raw=true" alt="Alipay" width="200"> |

## License

This project is licensed under AGPL-3.0. See the [LICENSE](LICENSE) file for details.

---

**Status**: Production Ready | **Version**: v1.2.0 | **Last Updated**: 2026-03-13
