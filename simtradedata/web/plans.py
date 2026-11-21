"""Validate update intents and build reviewable, deterministic update plans."""

import re
from datetime import date
from pathlib import Path

from simtradedata.web.coverage import inspect_coverage, quarter_dates

MODES = {"incremental", "refresh", "reparse"}
DATASETS = {"daily", "fundamentals", "pcf", "calendar"}


def validate_request(body):
    if not isinstance(body, dict) or set(body) - {"dataset", "mode", "start_date", "end_date", "symbols"}:
        raise ValueError("更新参数不合法")
    dataset, mode = body.get("dataset"), body.get("mode")
    if dataset not in DATASETS or mode not in MODES:
        raise ValueError("请选择有效的数据类别和更新方式")
    start, end = body.get("start_date", ""), body.get("end_date", "")
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except (ValueError, TypeError):
        raise ValueError("日期格式应为 YYYY-MM-DD") from None
    if start != first.isoformat() or end != last.isoformat() or first > last or first.year < 1990:
        raise ValueError("日期范围不合法")
    if last > date.today():
        raise ValueError("结束日期不能在未来")
    symbols = body.get("symbols", [])
    if not isinstance(symbols, list) or len(symbols) > 100 or any(
        not isinstance(symbol, str) or not re.fullmatch(r"\d{6}\.(SS|SZ|BJ)", symbol) for symbol in symbols
    ):
        raise ValueError("证券代码应为六位数字和 .SS/.SZ/.BJ 后缀，最多 100 个")
    symbols = list(dict.fromkeys(symbols))
    if dataset == "fundamentals" and symbols:
        raise ValueError("季度财务包按全市场更新，不接受单证券筛选")
    if dataset == "calendar" and (symbols or mode != "incremental"):
        raise ValueError("交易日历只支持按日期范围同步，不接受证券筛选")
    if dataset == "pcf":
        if not symbols or any(not re.fullmatch(r"15\d{4}\.SZ", symbol) for symbol in symbols):
            raise ValueError("历史 PCF 目前仅支持明确指定的深市 ETF（15 开头，.SZ）")
        if mode == "reparse":
            raise ValueError("PCF 尚未缓存原始 XML，不能重新解析源文件")
    return {"dataset": dataset, "mode": mode, "start_date": start, "end_date": end, "symbols": symbols}


def build_plan(data_dir: Path, body):
    request = validate_request(body)
    coverage = inspect_coverage(data_dir, request["start_date"], request["end_date"], request["symbols"])
    dataset = request["dataset"]
    if dataset == "calendar":
        return {"request": request, "units": ["交易日历"], "unit_count": 1,
                "existing_records": len(coverage["calendar_dates"]), "missing_dates": 0,
                "note": "从现有 BaoStock 来源同步选定范围内的真实交易日，作为历史日期缺口的检查基准。",
                "scope": "只补充交易日历，不下载行情或财务。", "before": coverage}
    item = next(row for row in coverage["datasets"] if row["key"] == dataset)
    cache = data_dir / "downloads"
    if dataset == "daily":
        units = ["沪深日线 ZIP"]
        if request["mode"] == "reparse" and not (cache / "hsjday.zip").exists():
            raise ValueError("尚无日线 ZIP 缓存，请先下载日线数据")
        note = "使用通达信批量历史包，仅导入选定日期和证券。增量只插入缺失记录；刷新和重新解析更新范围内已有值。"
    elif dataset == "fundamentals":
        units = quarter_dates(request["start_date"], request["end_date"])
        if not units:
            raise ValueError("所选日期内没有已完成季度的期末日")
        if request["mode"] == "reparse":
            missing = [day for day in units if not (data_dir / "mootdx_financial" / f"gpcw{day.replace('-', '')}.zip").exists()]
            if missing:
                raise ValueError(f"缺少 {len(missing)} 个季度缓存，请先下载或缩小范围")
        note = "按季度下载全市场财务包。增量补缺失季度并刷新最近四个季度；强制刷新下载范围内所有季度；重新解析只使用缓存。"
    else:
        days = coverage["calendar_dates"]
        if not days:
            raise ValueError("本地没有所选范围的交易日历，请先同步交易日历")
        units = [f"{symbol}|{day}" for symbol in request["symbols"] for day in days]
        note = "逐 ETF、逐交易日获取真实历史清单；增量跳过已有且通过校验的归档。未找到清单的日期记为失败，不以前一日或最新值代替。"
    return {"request": request, "units": units, "unit_count": len(units), "existing_records": item["records"],
            "missing_dates": len(item["missing_dates"]), "note": note,
            "scope": "仅写入所选类别；保留其他数据和日期。", "before": coverage}
