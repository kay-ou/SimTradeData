中文 | [English](README.md) | [Deutsch](README_de.md)

# SimTradeData

[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-red.svg)](LICENSE)
[![Version](https://img.shields.io/badge/Version-1.2.0-brightgreen.svg)](CHANGELOG.md)
[![DuckDB](https://img.shields.io/badge/Storage-DuckDB-FFF000?logo=duckdb&logoColor=black)](https://duckdb.org/)
[![Parquet](https://img.shields.io/badge/Export-Parquet-50ABF1)](https://parquet.apache.org/)
[![Code Style: Black](https://img.shields.io/badge/Code%20Style-Black-000000.svg)](https://github.com/psf/black)
[![uv](https://img.shields.io/badge/Packaging-uv-261230)](https://docs.astral.sh/uv/)

为[SimTradeLab](https://github.com/kay-ou/SimTradeLab)和SimTradeDesk准备本地回测数据：支持A股、ETF和美股，多源下载到DuckDB，再导出为共享Parquet数据包。

## 用网页管理数据（推荐）

需要Python 3.10+、[uv](https://docs.astral.sh/uv/getting-started/installation/)和Node.js 20+（含npm）。在项目根目录执行：

```bash
npm run ui
```

默认从 **http://127.0.0.1:8765** 启动，端口占用时自动选择下一个；以终端显示的地址为准。需要固定端口时可选：

```bash
npm run ui -- --port 8767
```

1. 点击“更新回测数据”：股票和ETF统一补齐，无需逐只选择。
2. 已有有效数据自动跳过；下载时显示实时进度，可取消，失败后可继续补齐。
3. 股票和ETF分别显示已保存数量及日期范围；季度财务和ETF申赎清单位于“其他数据”。
4. 在“导出回测数据包”生成并下载CN或US包，解压到对应应用的数据目录；不需要在页面输入导出命令或路径。

默认使用 `data/cn.duckdb`，可通过 `--data-dir /path/to/data` 指定其他目录。更新判断来自实际数据，不保存下载任务历史。启动时自动安装前端依赖、按需构建，并由uv同步Python依赖。端口从8765开始自动选择可用值，无需手填；以终端显示的地址为准。界面右上角可切换中文、English和Deutsch，首次跟随浏览器语言，之后记住手动选择。

完整操作、日期范围、质量修补、数据包导出与数据源健康检查见[网页使用指南](docs/WEB_DATA_ADMIN.md)。网页管理沪深股票、ETF日线、季度财务、深市历史PCF和交易日历，并可导出CN/US数据包；美股下载仍使用命令行。

## 直接使用现成数据

在[Releases](https://github.com/kay-ou/SimTradeData/releases)下载 `data-cn-v*` 或 `data-us-v*`，将Parquet数据分别解压到SimTradeLab的 `data/cn/` 或 `data/us/`。发布包不是可直接更新的DuckDB数据库。

## 命令行下载与导出

仅使用命令行时安装Python和uv，执行 `uv sync` 后：

```bash
uv run python scripts/download.py
uv run python scripts/export_parquet.py
```

默认导出到 `data/export/cn/`。美股、独立来源和导出参数见[命令行指南](docs/CLI_GUIDE.md)。不要同时通过网页和命令行更新同一个数据库。

## 数据覆盖与文档

日线批量来源为通达信官方历史包；Mootdx支持股票与ETF按需历史查询，BaoStock提供估值、状态和日历等补充，东方财富为部分接口备用，美股使用yfinance。财务已有223个公开字段映射，但历史证券池、公告时点、L2和部分PCF仍有边界，不能视为完整PTrade等价。

| 想做什么 | 去哪里 |
| --- | --- |
| 下载、查看和修补本地数据 | [网页指南](docs/WEB_DATA_ADMIN.md) |
| 美股、命令行和Parquet导出 | [命令行指南](docs/CLI_GUIDE.md) |
| 确认可回测范围和缺失能力 | [数据覆盖](docs/PTRADE_DATA_COVERAGE.md) |
| CI、自动检查、发布和故障恢复 | [运维说明](docs/OPERATIONS.md) |
| 字段规范、财务和PCF详情 | [完整文档导航](docs/README.md) |

第三方接口会延迟、修订或不可用；健康样本通过不等于全市场历史完整。ETF普通价格回测不需要申赎清单。

版本变更见[CHANGELOG](CHANGELOG.md)。项目采用[AGPL-3.0](LICENSE)。

## 支持项目


如果项目对你有帮助，欢迎支持。

| WeChat Pay | Alipay |
|:---:|:---:|
| <img src="docs/sponsor/WechatPay.png?raw=true" alt="WeChat Pay" width="200"> | <img src="docs/sponsor/AliPay.png?raw=true" alt="Alipay" width="200"> |

