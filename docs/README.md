# 文档导航

[项目首页与启动命令](../README_zh.md) · [English](../README.md) · [Deutsch](../README_de.md)

日常使用从网页指南开始；无需阅读接口研究资料。

| 文档 | 内容 |
| --- | --- |
| [网页使用指南](WEB_DATA_ADMIN.md) | 更新股票与ETF、进度、继续补齐、修补异常记录 |
| [命令行下载与导出](CLI_GUIDE.md) | 各数据源、美股、Parquet及SimTradeLab接入 |
| [数据覆盖与获取示例](PTRADE_DATA_COVERAGE.md) | PTrade对照、ETF历史边界、通达信按需接口 |
| [财务字段覆盖](PTRADE_FINANCIAL_COVERAGE.md) | 223个映射字段、单位、缺失项及历史刷新 |
| [ETF申赎清单](ETF_PCF_DATA.md) | 深市历史PCF、沪市最新PCF、字段和日期限制 |
| [Parquet格式](PTRADE_PARQUET_FORMAT.md) | 输出目录、表结构与字段规范 |
| [运维与开发](OPERATIONS.md) | CI、来源监控、生产发布、DuckDB恢复与性能复测 |

## 参考资料与历史记录

`reference/` 保存接口规范和研究资料，不代表项目全部实现；`archive/` 保存原始需求和一次性核验记录，不作为当前使用说明。

- [PTrade API参考](reference/PTrade_API_mini_Reference.md)
- [通达信扩展财务字段](reference/tdx_finvalue_extended.md)
- [Mootdx接口参考](reference/mootdx_api/MOOTDX_API_Reference.md)
- [BaoStock接口参考](reference/baostock_api/BaoStock_API_Reference.md)
- [QStock接口参考](reference/qstock_api/QStock_API_Reference.md)
- [原始PTrade需求](archive/requirements/PTrade_API_Requirements_Final.md)
- [ETF历史核验记录（2026-11-22）](archive/reports/ETF_HISTORY_20261122.md)
- [systemd部署模板](../ops/systemd/README.md)

同一主题维护一份详细说明；三种语言的首页只保留上手入口，避免重复命令和覆盖承诺。
