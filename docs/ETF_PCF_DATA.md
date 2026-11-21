# ETF 申购赎回清单与历史覆盖

核验日期：2026-11-20。数据直接来自沪深交易所公开清单，不用 ETF K 线、指数成分或季度持仓代替 PCF。

## 对历史回测的实际价值

只有最新清单不能用于过去的回测日期。历史查询必须匹配清单中的实际 `TradingDay`；
清单的 `PreTradingDay`、净值与现金项也要保留各自口径，不能将最新值前填到历史日期。

| 来源 | 当前实现 | 历史实测与限制 |
| --- | --- | --- |
| 深交所 | 按公告索引查找日期，再下载官方 XML | `159919.SZ` 的 2020-11-20、2026-11-19、2026-11-20 均成功，各 301 条记录；2015-11-20 未找到公告。尚未证明连续覆盖或最早可用日期 |
| 上交所 | 官方下载端点返回最新清单 | `510300.SS` 的 2026-11-20 成功，300 条记录；指定其他日期时拒绝用最新清单代替。尚未接入沪市历史源 |

深市公告查询每次检索目标日期之前一年的索引，不表示历史只能回溯一年：
查询的起止时间随目标日期变化。遇到休市日、无公告或文件日期不符时会报错，不静默返回前一日。
日期样本通过也不表示历史已全量入库；目前只验证并归档了样本。

## 获取与保存

```python
from simtradedata.router import SmartRouter

with SmartRouter() as router:
    history = router.get_etf_pcf("159919.SZ", date="2020-11-20")
    history.to_parquet("159919-2020-11-20.parquet", index=False)
    info = router.get_etf_info("159919.SZ", date="2020-11-20")
    stocks = router.get_etf_stock_info(
        "159919.SZ", ["000001.SZ", "600000.SS"], date="2020-11-20"
    )
```

不传 `date` 时获取最新可用清单。当前入口接受沪市 `5` 开头的六位 ETF 代码和
深市 `15` 开头的六位 ETF 代码；其他 ETF 品种及境外篮子尚未逐项验收。
历史文件必须来自对应日期的公告，不从代码推测申赎代码。

PCF 表同时保留摘要与明细：`etf_code/date/pre_date`、可确认的摘要字段、
`code/market_code/symbol/security_name/code_num/is_cash_component/substitution_flag`，
以及独立的申购溢价、赎回折价、申购现金替代金额和赎回现金替代金额。
只有已确认的市场 `101` / `102` 转成 `.SS` / `.SZ`；其他市场保留原始代码，`symbol` 为空。

## PTrade 字段适配边界

| 字段 | 官方源字段与口径 |
| --- | --- |
| `report_unit` | `CreationRedemptionUnit`，基金份额数 |
| `nav_percu` / `nav_pre` | `NAVperCU` / `NAV`，前一交易日申赎单位净值（元）/ 基金单位净值（元/份） |
| `cash_balance` | 沪市 `PreCashComponent`、深市 `CashComponent`，清单中的前一交易日现金差额（元） |
| `max_cash_ratio` | `MaxCashRatio`，小数比例，不乘 100 |
| `allot_max` / `redeem_max` | `CreationLimit` / `RedemptionLimit`，按源值保留；没有字段时不填零，也不解释源零值为禁止或无限 |
| `publish` | 沪市 `PublishIOPVFlag`、深市 `Publish`，转换为 0/1 |
| `code_num` | 沪市 `Quantity`、深市 `ComponentShare`，每个最小申赎单位中的证券数量，不按行情“手”换算 |
| `cash_replace_flag` | 仅适配已确认的源标志 `0/1/2`；其他标志保留在原始 PCF 表，不臆猜 PTrade 枚举 |
| `replace_ratio` | 申购溢价比例；赎回折价比例另行保留，未合并 |
| `replace_balance` | 申购和赎回现金替代金额均存在且相同时才提供；不同则保留两个原始金额，不强行取其中一个 |

`get_etf_info` 和 `get_etf_stock_info` 是字段子集，尚不是完整 PTrade 返回。
没有确认的 `etf_redemption_code`、`pre_cash_component` 和成分券 `is_open` 不伪造。
预估现金差额单独存为 `estimated_cash_component`，不直接当作含义未确认的 `pre_cash_component`。

深市 `159900` 是申赎现金项，不是成分证券。它保留在原始 PCF 表，标记
`is_cash_component=True`，但不出现在 `get_etf_stock_info` 的证券字典中。
本次 `159919.SZ` 样本包含 300 只证券和一个现金项；现金项的申购、赎回金额不同。

## 校验与归档

```bash
# 最新清单健康检查，并按证券/实际日期保存 Parquet
uv run python -m scripts.check_etf_pcf \
  --archive-dir data/etf_pcf --json-output logs/validation/etf-pcf.json

# 指定真实历史日期
uv run python -m scripts.check_etf_pcf --symbols 159919.SZ --date 2020-11-20 \
  --archive-dir data/etf_pcf --json-output logs/validation/etf-pcf-history.json
```

归档路径为 `<archive-dir>/<ETF代码>/<清单日期>.parquet`。最新检查对照独立交易日历判断是否陈旧；
指定日期时严格核对该日期。校验基金代码、日期顺序、净值、申赎单位、证券数量、
声明的完整记录数、重复证券及非有限数值。深市用 `TotalRecordNum`，不能把较小的 `RecordNum` 当作全部篮子数量。

定时数据源工作流新增独立 `etf-pcf` 作业，保存报告及两只代表 ETF 的每日样本，保留 30 天。
这是接口监控与样本归档，不是全市场、长期历史仓库。
已有历史回测数据仓库仍需做逐交易日下载、缺口统计和持续存储；沪市历史源仍待补。

## 来源

- [上交所 ETF 清单入口](https://www.sse.com.cn/disclosure/fund/etflist/)
- [深交所基金披露入口](https://www.szse.cn/disclosure/fund/currency/index.html)
- 接口发现参考 [AKShare PCF 实现](https://github.com/akfamily/akshare/blob/main/akshare/fund/fund_etf_pcf.py)，
  本项目未引入 AKShare 依赖；字段和格式用实际官方 XML 验证。
- 固定回归样本：`tests/fixtures/pcf/510300-20261120.xml` 和 `159919-20261120.xml`，均来自对应官方清单。
