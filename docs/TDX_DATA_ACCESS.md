# 通达信补充数据使用说明

以下接口通过 `SmartRouter` 按需读取，不依赖账户登录。核验日期：2026-11-20。
本页区分 PTrade 的已确认字段子集与通达信原始数据接口，完整能力边界见
[PTrade 数据对照](PTRADE_DATA_COVERAGE.md)。

## 快照与五档

```python
from simtradedata.router import SmartRouter

with SmartRouter() as router:
    snapshots = router.get_snapshot(["600519.SS", "000001.SZ", "510300.SS"])
    book = router.get_gear_price("510300.SS")
    books = router.get_gear_price(["600519.SS", "510300.SS"])
```

`get_snapshot` 对字符串和列表都返回按证券代码索引的字典。
`get_gear_price` 对字符串返回单证券的 `bid_grp` / `offer_grp`，对列表返回按证券索引的字典。
两次调用会分别请求实时数据，不保证快照与随后获取的五档来自同一时刻。

| 快照字段 | 来源与单位 |
| --- | --- |
| `last_px`、`preclose_px`、`open_px`、`high_px`、`low_px` | 源报价，元 |
| `business_balance` | 源成交额，元 |
| `business_amount`、`business_amount_in`、`business_amount_out` | 源总量、内盘、外盘，由手 × 100 换算为股/份 |
| `bid_grp`、`offer_grp` | 1–5 档；每档为 `[价格, 股/份数量, 0]`，快照第一档另附空队列 `{}` |
| `px_change_rate` | `(最新价 / 昨收 - 1) × 100`，百分数；昨收大于零时提供 |
| `wavg_px` | 成交额 / 股或份成交量，元；成交量大于零时提供 |

以上单位适用于已核验的沪深股票和 ETF，不作为债券、期货等品种的单位承诺。
原始 `get_realtime_quotes` 保留源数量单位（手），不能把它的 `volume` 与适配快照的股/份数量混用。
证券匹配使用返回的市场和代码，支持漏返、乱序和沪深同代码；每批最多请求 80 个证券。

未提供的字段包括交易状态、涨跌停价、带日期的交易所时间戳、动态市盈率、
完整盘口委买委卖总量及成交笔数等。缺字段不填零；无 L2 委托笔数依照 PTrade 文档为零，队列为空。
零价格的空档保留。没有返回记录时快照为空字典，单证券档位为两个空档位字典；
连接异常仍沿用路由的 `DataSourceError`，调用方应自行处理，不能把通信失败视为合法空行情。

## 当前板块、F10 与历史数据

```python
from simtradedata.router import SmartRouter

with SmartRouter() as router:
    blocks = router.get_blocks("block_zs.dat")
    blocks.to_parquet("tdx-index-blocks.parquet", index=False)

    catalog = router.get_f10_catalog("600000.SS")
    text = router.get_f10_detail("600000.SS", "最新提示")

    trades = router.get_history_transactions("600000.SS", "2026-11-20", start=0, offset=20)
    curve = router.get_history_minutes("600000.SS", "2026-11-20")
    trades.to_parquet("tdx-trades.parquet", index=False)
    curve.to_parquet("tdx-intraday-curve.parquet", index=False)
```

| 数据 | 返回与限制 |
| --- | --- |
| 板块 | DataFrame：`blockname/block_type/code_index/code`；支持 `block.dat`、`block_zs.dat`、`block_fg.dat` |
| F10 目录 | DataFrame；使用实际目录名称获取正文 |
| F10 正文 | 原始字符串，无正文时为 `None`；未自动结构化为财务报表 |
| 历史分笔 | DataFrame：`time/price/vol/buyorsell`；保留通达信原始数量与方向值，分页 `start` 为记录偏移 |
| 历史分时曲线 | DataFrame：`price/vol`；保留源顺序和原始数量，不补造时间戳或分钟 OHLC |

板块下载按服务器实际返回长度推进偏移，避免 SDK 固定步长造成重叠；
解析验证文件长度、记录数、每条六位数字代码和每块最多 400 个代码，异常时拒绝返回。
实测 `block.dat` 为 16,862 条，`block_zs.dat` 为 11,554 条，`block_fg.dat` 为 20,548 条，
全部代码格式有效，同文件同板块内无重复成员。这不证明其与交易所官方成分一致。
板块名称与 `block_type` 为通达信分类，不是 PTrade XBHS 分类；原始文件缺逐成员市场标识，
不要单靠数字代码将所有成员盲目转换成 PTrade 后缀，也不要将当前成员用于任意历史日期。

历史分笔只有 `HH:MM`，方向含通达信值 5；不提供毫秒、成交编号和委托关联编号。
历史分时曲线实测为 240 条。两者都未承诺多年历史覆盖，不替代 PTrade L2 接口或分钟 K 线。
保存样本时应同时记录请求日期、获取时间和板块文件名，便于追溯。

## 自动健康检查

```bash
uv run python -m scripts.check_mootdx_interfaces \
  --interfaces snapshot blocks f10 history_transactions history_minutes \
  --timeout 90 --json-output logs/validation/tdx-extra-data.json
```

这些接口已纳入默认检查列表，每项使用独立进程和超时，单项失败不会隐藏其他结果。
检查验证快照价格及五档、板块代码与重复成员、F10 正文、历史价格和数量。
历史接口请求最近应完成交易日；源返回没有完整交易日期，因此该检查不能证明返回内容的实际日期或历史完整性。
这些检查不会修改生产数据库，也不会自动归档实时行情。
