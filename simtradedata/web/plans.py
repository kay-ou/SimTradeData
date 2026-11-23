"""Validate update intents and build reviewable, deterministic update plans."""

import re
from datetime import date
from pathlib import Path

from simtradedata.web.coverage import inspect_coverage, quarter_dates
from simtradedata.web.cache_state import file_signature, financial_signature
from simtradedata.web.completeness import calendar_covers, daily_database_pending
import duckdb

MODES = {"auto", "incremental", "refresh", "reparse", "repair"}
DATASETS = {"daily", "fundamentals", "pcf", "calendar"}


def validate_request(body):
    if not isinstance(body, dict) or set(body) - {"dataset", "mode", "start_date", "end_date", "symbols"}:
        raise ValueError("更新参数不合法")
    dataset, mode = body.get("dataset"), body.get("mode")
    if dataset not in DATASETS or mode not in MODES:
        raise ValueError("请选择有效的数据类别和更新方式")
    if mode == "repair" and dataset != "daily":
        raise ValueError("定向修补目前仅支持日线")
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
    if dataset == "calendar" and (symbols or mode not in {"auto", "incremental"}):
        raise ValueError("交易日历只支持按日期范围同步，不接受证券筛选")
    if dataset == "pcf":
        if not symbols or any(not re.fullmatch(r"15\d{4}\.SZ", symbol) for symbol in symbols):
            raise ValueError("历史 PCF 目前仅支持明确指定的深市 ETF（15 开头，.SZ）")
        if mode == "reparse":
            raise ValueError("PCF 尚未缓存原始 XML，不能重新解析源文件")
    return {"dataset": dataset, "mode": mode, "start_date": start, "end_date": end, "symbols": symbols}


def build_plan(data_dir: Path, body):
    request = validate_request(body)
    coverage = inspect_coverage(data_dir, request["start_date"], request["end_date"], request["symbols"], include_pcf=request["dataset"] == "pcf")
    dataset = request["dataset"]
    if dataset == "calendar":
        done = request["mode"] == "auto" and calendar_covers(data_dir, request["start_date"], request["end_date"])
        return {"request": request, "units": ["交易日历"], "unit_count": 1,
                "work_pending": not done, "status": "up_to_date" if done else "ready",
                "existing_records": len(coverage["calendar_dates"]), "missing_dates": 0,
                "note": "从现有 BaoStock 来源同步选定范围内的真实交易日，作为历史日期缺口的检查基准。",
                "scope": "只补充交易日历，不下载行情或财务。", "before": coverage}
    item = next(row for row in coverage["datasets"] if row["key"] == dataset)
    cache = data_dir / "downloads"
    done = False
    if dataset == "daily":
        units = ["沪深日线 ZIP"]
        if request["mode"] == "reparse" and not (cache / "hsjday.zip").exists():
            raise ValueError("尚无日线 ZIP 缓存，请先下载日线数据")
        note = "使用通达信批量历史包，仅导入选定日期和证券。增量只插入缺失记录；刷新和重新解析更新范围内已有值。"
        if request["mode"] == "auto":
            done = not daily_database_pending(data_dir, request, coverage)
        if request["mode"] == "repair":
            from simtradedata.web.daily_repair import daily_issue_rows
            issues = daily_issue_rows(data_dir, request)
            units = sorted(issues['symbol'].unique()) if not issues.empty else []
            item['invalid_records'] = len(issues)
            done = not units
            note = "仅修补已有异常记录；先用本地通达信包，再查询未复权备用源，无法确认的保留并报告。"
    elif dataset == "fundamentals":
        units = quarter_dates(request["start_date"], request["end_date"])
        if not units:
            raise ValueError("所选日期内没有已完成季度的期末日")
        if request["mode"] == "reparse":
            missing = [day for day in units if not (data_dir / "mootdx_financial" / f"gpcw{day.replace('-', '')}.zip").exists()]
            if missing:
                raise ValueError(f"缺少 {len(missing)} 个季度缓存，请先下载或缩小范围")
        note = "按季度下载全市场财务包。增量补缺失季度并刷新最近四个季度；强制刷新下载范围内所有季度；重新解析只使用缓存。"
        if request["mode"] == "auto":
            receipts = {}
            completed = set()
            if (data_dir / "cn.duckdb").exists():
                with duckdb.connect(str(data_dir / "cn.duckdb"), read_only=True) as conn:
                    if "fundamentals_progress" in {row[0] for row in conn.execute("SHOW TABLES").fetchall()}:
                        rows = conn.execute("SELECT year, quarter, file_hash, stock_count FROM fundamentals_progress WHERE stock_count>0").fetchall()
                        counts = {str(day): count for day, count in conn.execute("SELECT date, COUNT(*) FROM fundamentals GROUP BY date").fetchall()}
                        completed = {(year, quarter) for year, quarter, _, count in rows if counts.get(f"{year}-{quarter * 3:02d}-{[31,30,30,31][quarter-1]}", 0) >= count}
                        receipts = {(year, quarter): hash_value for year, quarter, hash_value, _ in rows}
            mapping = financial_signature()
            pending = []
            for day in units:
                file = data_dir / "mootdx_financial" / f"gpcw{day.replace('-', '')}.zip"
                quarter = (int(day[:4]), int(day[5:7]) // 3)
                signature = "local:" + file_signature(file) + mapping if file.exists() else None
                recorded = receipts.get(quarter) or ""
                valid = recorded == signature if signature else recorded.startswith("local:") and recorded.endswith(mapping)
                if not valid or quarter not in completed:
                    pending.append(day)
            done = not pending
            units = pending
    else:
        days = coverage["calendar_dates"]
        needs_calendar = not calendar_covers(data_dir, request["start_date"], request["end_date"])
        if needs_calendar and request["mode"] != "auto":
            raise ValueError("本地没有所选范围的交易日历，请先同步交易日历")
        units = [] if needs_calendar else [f"{symbol}|{day}" for symbol in request["symbols"] for day in days]
        if request["mode"] == "auto" and not needs_calendar:
            existing = {f"{row['symbol']}|{row['date']}" for row in item["archives"]}
            units = [unit for unit in units if unit not in existing]
            done = not units
        note = "逐 ETF、逐交易日获取真实历史清单；增量跳过已有且通过校验的归档。未找到清单的日期记为失败，不以前一日或最新值代替。"
    if request["mode"] == "auto":
        note = "先检查本地缓存与已有记录，只获取缺少的数据；已有财务包直接用于补齐字段。"
    local_files = sum((data_dir / "mootdx_financial" / f"gpcw{day.replace('-', '')}.zip").exists() for day in units) if dataset == "fundamentals" else int((cache / "hsjday.zip").exists()) if dataset == "daily" else len(item["archives"])
    reuse_units = local_files if dataset == "fundamentals" and request["mode"] in {"auto", "reparse"} else 0
    update_summary = {"quality_issues": item.get("invalid_records", 0), "pending_units": len(units) if dataset != "pcf" or not needs_calendar else None,
                      "reuse_units": reuse_units, "download_units": None if request["mode"] == "repair" else len(units) - reuse_units,
                      "unit_label": "个证券" if request["mode"] == "repair" else "季度" if dataset == "fundamentals" else "份清单" if dataset == "pcf" else "历史包"}
    if dataset == "daily":
        update_summary["not_applicable_records"] = item.get("not_applicable_records", 0)
    return {"update_summary": update_summary, "request": request, "units": units, "unit_count": len(units), "existing_records": item["records"],
            "work_pending": not done, "status": "up_to_date" if done else "ready",
            "cached_files": local_files, "needs_calendar": dataset == "pcf" and needs_calendar,
            "missing_dates": len(item["missing_dates"]), "note": note,
            "scope": "仅写入所选类别；保留其他数据和日期。", "before": coverage}
