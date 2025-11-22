# SimTradeData - 高效量化交易数据下载工具

> 🚀 **优化的BaoStock数据下载** | 📊 **PTrade格式兼容** | ⚡ **API调用减少33%**

**SimTradeData** 是为 [SimTradeLab](https://github.com/kay_ou/SimTradeLab) 设计的高效数据下载工具。通过智能的API调用优化，在单次请求中获取多种数据类型，显著提升下载效率。

---

<div align="center">

### 💎 推荐组合：SimTradeData + SimTradeLab

** 完全兼容PTrade | 回测速度提升10倍以上**

[![SimTradeLab](https://img.shields.io/badge/SimTradeLab-量化回测框架-blue?style=for-the-badge)](https://github.com/kay_ou/SimTradeLab)

🎯 **无需修改PTrade策略代码** | 🚀 **极速本地回测** | 💰 **零成本解决方案**

</div>

---


**快速迁移**：
```bash
# 1. 下载数据（本工具）
poetry run python scripts/download_efficient.py

# 2. 复制到SimTradeLab
cp data/*.h5 /path/to/SimTradeLab/data/

# 3. 运行策略（享受10倍速度提升）
# 无需修改任何PTrade策略代码！
```

**适用场景**：
- ✅ 策略研发：快速迭代测试
- ✅ 参数优化：大规模参数扫描
- ✅ 因子挖掘：高频因子回测
- ✅ 学习研究：免费学习量化

## ✨ 核心特性

### 🎯 性能优化
- **统一数据获取**: 一次API调用同时获取行情、估值、状态数据
- **API调用优化**: 相比传统方法减少 **33%** 的API调用次数
- **增量更新支持**: 智能识别已下载数据，仅更新增量部分
- **断点续传**: 中断后自动跳过已完成的股票

### 📦 数据完整性
- **市场数据**: OHLCV日线数据
- **估值指标**: PE/PB/PS/PCF/换手率
- **复权因子**: 前复权/后复权因子
- **股票元数据**: 上市日期、退市日期、行业分类
- **指数成分股**: 上证50、沪深300、中证500等
- **交易日历**: 完整的A股交易日历

## 📦 生成的数据文件

| 文件名 | 说明 | 数据内容 |
|--------|------|----------|
| `ptrade_data.h5` | 主数据文件 | 股票行情(OHLCV)、基准指数、除权除息、股票元数据、交易日历 |
| `ptrade_fundamentals.h5` | 估值数据 | 每日估值指标(PE/PB/PS/PCF/换手率) |
| `ptrade_adj_pre.h5` | 复权因子 | 每只股票的历史复权因子序列 |

## 🚀 快速开始

### 1. 安装依赖

```bash
# 克隆项目
git clone https://github.com/kay-ou/SimTradeData.git
cd SimTradeData

# 安装依赖(使用 Poetry)
poetry install

# 激活虚拟环境
poetry shell
```

### 2. 下载数据

```bash
# 【首次下载】下载全部数据（2017至今）
poetry run python scripts/download_efficient.py

# 【增量更新】更新最近7天数据
poetry run python scripts/download_efficient.py --incremental 7

# 【增量更新】更新最近30天数据
poetry run python scripts/download_efficient.py --incremental 30
```

### 3. 在 SimTradeLab 中使用

生成的 HDF5 文件可直接放入 [SimTradeLab](https://github.com/kay_ou/SimTradeLab) 的数据目录使用：

```bash
# 复制生成的文件到 SimTradeLab 数据目录
cp data/*.h5 /path/to/SimTradeLab/data/
```

**性能提升**：配合SimTradeLab使用，相比PTrade平台可获得：
- 🚀 **10倍以上回测速度提升**
- 💰 **零数据成本**（完全免费）
- 🔧 **完全兼容**（无需修改PTrade策略代码）
- 🔒 **本地运行**（数据隐私安全）

## ⚡ 性能优化详解

### 传统方法 vs 优化方法

**传统方法**（每股6次API调用）:
```
1. 获取市场数据（OHLCV）
2. 获取估值数据（PE/PB/PS）
3. 获取ST状态
4. 获取复权因子
5. 获取基本信息
6. 获取行业分类
```

**优化方法**（每股4次API调用）:
```
1. 统一获取（市场+估值+状态）← 三合一！
2. 获取复权因子
3. 获取基本信息
4. 获取行业分类
```

**性能对比**:
| 指标 | 传统方法 | 优化方法 | 提升 |
|------|---------|---------|------|
| API调用/股 | 6次 | 4次 | **-33%** |
| 5000股总调用 | 30,000次 | 20,000次 | 节省10,000次 |

### 下载时间估算

以5000只股票为例：

| 模式 | 时间范围 | 预计耗时 | 说明 |
|------|---------|---------|------|
| 首次完整下载 | 2017-01-01 至今 | ~8-10小时 | 包含所有历史数据 |
| 增量更新(7天) | 最近7天 | ~30-40分钟 | 仅更新最新数据 |
| 增量更新(30天) | 最近30天 | ~2-3小时 | 适合月度更新 |

## 🏗️ 项目架构

```
SimTradeData/
├── scripts/
│   └── download_efficient.py      # 主下载脚本（优化版）
├── simtradedata/
│   ├── fetchers/
│   │   ├── baostock_fetcher.py   # BaoStock基础封装
│   │   └── unified_fetcher.py    # 统一数据获取（核心优化）
│   ├── processors/
│   │   └── data_splitter.py      # 数据分流处理
│   ├── writers/
│   │   └── h5_writer.py          # HDF5写入（优化版）
│   └── utils/
│       └── code_utils.py         # 工具函数
├── data/                          # 生成的H5文件
└── docs/                          # 文档
```

### 核心模块说明

**1. UnifiedDataFetcher** - 统一数据获取
- 一次API调用获取多种数据类型
- 减少网络请求次数
- 自动处理数据类型转换

**2. DataSplitter** - 智能数据分流
- 将统一数据分流到不同目标
- 市场数据 → `ptrade_data.h5/stock_data`
- 估值数据 → `ptrade_fundamentals.h5/valuation`
- 状态数据 → 内存缓存（用于构建历史）

**3. HDF5Writer** - 高效数据写入
- 支持增量追加写入
- 自动去重和合并
- 压缩存储（blosc压缩算法）

## 📊 数据结构

### ptrade_data.h5
```
/stock_data/{symbol}     - 股票OHLCV数据
/exrights/{symbol}       - 除权除息数据
/stock_metadata          - 股票元数据（名称、上市日期等）
/trade_days              - 交易日历
/benchmark               - 基准指数数据
/metadata                - 全局元数据（包含指数成分股历史）
```

### ptrade_fundamentals.h5
```
/valuation/{symbol}      - 估值指标（PE/PB/PS/PCF/换手率）
```

### ptrade_adj_pre.h5
```
/{symbol}                - 后复权因子序列
```

详细数据结构请参考: [H5_DATA_STRUCTURE.md](docs/H5_DATA_STRUCTURE.md)

## 🔧 配置说明

编辑 `scripts/download_efficient.py` 中的配置参数:

```python
# 输出目录配置
OUTPUT_DIR = "data"            # 输出目录（HDF5文件保存位置）
LOG_FILE = "data/download_efficient.log"  # 日志文件

# 日期范围配置
START_DATE = "2017-01-01"      # 起始日期
END_DATE = None                # 结束日期（None表示当前日期）
INCREMENTAL_DAYS = None        # 增量天数（None表示完整下载）

# 批次配置
BATCH_SIZE = 20                # 每批处理股票数
```

## 💡 使用示例

### Python API 使用

```python
from simtradedata.fetchers.unified_fetcher import UnifiedDataFetcher
from simtradedata.processors.data_splitter import DataSplitter
from simtradedata.writers.h5_writer import HDF5Writer

# 初始化组件
fetcher = UnifiedDataFetcher()
splitter = DataSplitter()
writer = HDF5Writer(output_dir="data")  # 输出到data目录

fetcher.login()

try:
    # 1. 统一获取数据（一次API调用）
    unified_data = fetcher.fetch_unified_daily_data(
        symbol="600000.SS",
        start_date="2024-01-01",
        end_date="2024-11-22"
    )

    # 2. 分流数据
    split_data = splitter.split_data(unified_data)

    # 3. 写入不同文件
    if 'market' in split_data:
        writer.write_market_data("600000.SS", split_data['market'])

    if 'valuation' in split_data:
        writer.write_valuation("600000.SS", split_data['valuation'])

finally:
    fetcher.logout()
```

### 批量下载

```python
from scripts.download_efficient import download_all_data

# 完整下载
download_all_data(incremental_days=None)

# 增量更新最近7天
download_all_data(incremental_days=7)
```

## 📚 文档

| 文档 | 说明 | 状态 |
|------|------|------|
| [H5_DATA_STRUCTURE.md](docs/H5_DATA_STRUCTURE.md) | HDF5文件详细数据结构 | ✅ 完成 |
| [BaoStock_Data_Mapping.md](docs/BaoStock_Data_Mapping.md) | BaoStock数据映射方案 | ✅ 完成 |
| [BaoStock API Reference](docs/reference/baostock_api/) | BaoStock API文档 | ✅ 完成 |

## ⚠️ 注意事项

### 推荐配置：SimTradeLab
- **强烈推荐**: 配合 [SimTradeLab](https://github.com/kay_ou/SimTradeLab) 使用
- **性能优势**: 相比PTrade平台回测速度提升 **10倍以上**
- **完全免费**: 数据下载和策略回测均零成本
- **无缝兼容**: PTrade策略代码无需修改直接运行
- **本地运行**: 数据和策略完全本地化，保护隐私

### BaoStock限制
- **不支持并发**: BaoStock不支持多线程/多进程，所有下载均为顺序执行
- **每日限额**: 建议控制在合理范围内，避免频繁大量请求
- **网络稳定性**: 建议在网络稳定的环境下运行

### 数据质量
- **数据来源**: 所有数据来自BaoStock免费数据源
- **免责声明**: 数据仅供学习研究使用，请勿用于实盘交易
- **质量检查**: 建议下载完成后使用验证工具检查数据完整性

### 增量更新建议
- **首次下载**: 完整下载2017年至今的所有数据
- **日常更新**: 使用 `--incremental 7` 更新最近一周
- **月度更新**: 使用 `--incremental 30` 更新最近一月
- **数据合并**: 程序会自动合并新旧数据，去除重复

## 🔄 版本历史

### v0.2.0 (2025-11-22) - 性能优化版
- ✅ 实现统一数据获取，API调用减少33%
- ✅ 优化HDF5写入逻辑，移除不必要的线程锁
- ✅ 增强增量更新机制，支持数据合并去重
- ✅ 代码精简54%（7054行 → 3250行）
- ✅ 删除未使用的方法和模块

### v0.1.0 (2024-11-14) - 初始版本
- ✅ 基础数据下载功能
- ✅ BaoStock数据源集成
- ✅ PTrade格式兼容

## 🤝 贡献

欢迎贡献代码、报告问题或提出建议！

## 📄 许可证

本项目采用 MIT 许可证。详见 [LICENSE](LICENSE) 文件。

## 🔗 相关链接

### 推荐使用
- **SimTradeLab** ⭐: https://github.com/kay_ou/SimTradeLab
  - 开源Python量化回测框架
  - 完全兼容PTrade数据格式
  - **回测速度提升10倍以上**
  - 免费开源，功能强大

### 数据源
- **BaoStock**: http://baostock.com/
  - 免费A股数据平台
  - 本项目主要数据来源

---

💡 **最佳实践**: SimTradeData (数据下载) + SimTradeLab (策略回测) = 高效免费的量化研究完整解决方案

## 📮 联系方式

- **Issues**: https://github.com/kay_ou/SimTradeData/issues
- **合作QQ**: 3185289532

---

**项目状态**: ✅ 稳定版 | **当前版本**: v0.2.0 | **最后更新**: 2025-11-22
