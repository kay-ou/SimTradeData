English | [Chinese](README_zh.md) | [German](README_de.md)

# SimTradeData

[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-red.svg)](LICENSE)
[![Version](https://img.shields.io/badge/Version-1.2.0-brightgreen.svg)](CHANGELOG.md)
[![DuckDB](https://img.shields.io/badge/Storage-DuckDB-FFF000?logo=duckdb&logoColor=black)](https://duckdb.org/)
[![Parquet](https://img.shields.io/badge/Export-Parquet-50ABF1)](https://parquet.apache.org/)
[![Code Style: Black](https://img.shields.io/badge/Code%20Style-Black-000000.svg)](https://github.com/psf/black)
[![uv](https://img.shields.io/badge/Packaging-uv-261230)](https://docs.astral.sh/uv/)

Prepare local backtesting data for [SimTradeLab](https://github.com/kay-ou/SimTradeLab) and SimTradeDesk: China stocks, ETFs and US stocks. Store data in DuckDB and export shared Parquet data packages.

## Start the web UI (recommended)

Install Python 3.10+, [uv](https://docs.astral.sh/uv/getting-started/installation/) and Node.js 20+ with npm. From the project root:

```bash
npm run ui
```

Open the URL printed in the terminal (starting from **http://127.0.0.1:8765**, automatically skipping occupied ports). Optionally choose a fixed port:

```bash
npm run ui -- --port 8767
```

Click **Update backtesting data** to update stocks and ETFs together. Valid existing records are skipped; the UI shows live progress, cancellation and retries. Stock and ETF statistics are separate. Quarterly financials and ETF PCF appear under **Other data**.

The default database is `data/cn.duckdb`; use `--data-dir /path/to/data` for another directory. Completion is checked against stored data; download task history is not persisted. Startup installs frontend dependencies and builds only when needed; uv synchronizes Python dependencies. The first available port from 8765 is selected automatically; use the URL printed in the terminal. Switch Chinese, English or German in the header; the browser language is used initially and your manual choice is remembered.

After updating, use **Export backtest data** to generate and download CN or US Parquet packages for SimTradeLab / SimTradeDesk. Complete securities are skipped during smart updates; recent sessions can use small official daily packages instead of the historical ZIP. US downloads still use the CLI. See the [UI guide](docs/WEB_DATA_ADMIN.md) (Chinese).

## Use prebuilt data

Download `data-cn-v*` or `data-us-v*` from [Releases](https://github.com/kay-ou/SimTradeData/releases). Extract Parquet data into SimTradeLab’s `data/cn/` or `data/us/`. These packages are not editable DuckDB source databases.

## CLI download and export

For CLI-only use, install Python and uv, run `uv sync`, then:

```bash
uv run python scripts/download.py
uv run python scripts/export_parquet.py
```

Exports default to `data/export/cn/`. See the [CLI guide](docs/CLI_GUIDE.md) for US stocks, individual sources and export options. Avoid concurrent UI and CLI writes to the same database.

## Coverage and documentation

TDX supplies bulk daily history; Mootdx supports stock/ETF history queries, BaoStock supplies valuation/status/calendars, EastMoney supports selected fallback endpoints, and yfinance supplies US data. There are 223 mapped public financial fields; historical universes, publication-time semantics, L2 and some PCF data remain incomplete. Full PTrade equivalence is not guaranteed. Ordinary ETF price backtests do not require PCF.

| Need | Guide |
| --- | --- |
| Update, inspect and repair local data | [Web UI](docs/WEB_DATA_ADMIN.md) |
| US downloads, CLI and exports | [CLI guide](docs/CLI_GUIDE.md) |
| Available data and limitations | [Data coverage](docs/PTRADE_DATA_COVERAGE.md) |
| CI, monitoring, publishing and recovery | [Operations](docs/OPERATIONS.md) |
| Schemas, financials and PCF | [Documentation index](docs/README.md) |

Detailed user and coverage guides are maintained in Chinese; operations documentation includes English. Provider health samples do not certify complete historical coverage.

See [CHANGELOG](CHANGELOG.md) for version history and [LICENSE](LICENSE) for AGPL-3.0 terms.

## Sponsor


If this project helps you, consider sponsoring!

| WeChat Pay | Alipay |
|:---:|:---:|
| <img src="docs/sponsor/WechatPay.png?raw=true" alt="WeChat Pay" width="200"> | <img src="docs/sponsor/AliPay.png?raw=true" alt="Alipay" width="200"> |

