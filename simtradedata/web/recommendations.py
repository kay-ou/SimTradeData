"""Discover the next work range from stored data, instead of fixed UI dates."""

from datetime import date, timedelta
import zipfile
import duckdb

from simtradedata.web.coverage import inspect_coverage
from simtradedata.web.completeness import daily_pending
from simtradedata.web.plans import build_plan, validate_request


def recommend(data_dir, dataset, symbols=None):
    end = date.today()
    while end.weekday() >= 5:
        end -= timedelta(days=1)
    start = "2015-01-01"
    validate_request({"dataset": dataset, "mode": "auto", "start_date": start,
                      "end_date": end.isoformat(), "symbols": symbols or []})
    database = data_dir / "cn.duckdb"
    initialized = False
    table = {"daily": "stocks", "fundamentals": "fundamentals"}.get(dataset)
    if database.exists() and table:
        with duckdb.connect(str(database), read_only=True) as conn:
            if table in {row[0] for row in conn.execute("SHOW TABLES").fetchall()}:
                first = conn.execute(f"SELECT MIN(date) FROM {table}").fetchone()[0]
                if first:
                    start, initialized = str(first), True
    if dataset == "pcf":
        files = [path for symbol in symbols or [] for path in (data_dir / "etf_pcf" / symbol).glob("*.parquet")]
        if files:
            start, initialized = min(path.stem for path in files), True
    request = validate_request({"dataset": dataset, "mode": "auto", "start_date": start,
                                "end_date": end.isoformat(), "symbols": symbols or []})
    gap = {}
    if dataset == "daily":
        coverage = inspect_coverage(data_dir, start, request["end_date"], symbols)
        try:
            pending = daily_pending(data_dir, request, coverage, gap)
        except (ValueError, zipfile.BadZipFile):
            pending = True
            gap.clear()
        if gap:
            request.update(gap)
        plan = build_plan(data_dir, request)
        plan["work_pending"] = pending
    else:
        plan = build_plan(data_dir, request)
        if dataset in {"fundamentals", "pcf"} and plan["units"]:
            dates = [unit.split('|')[-1] for unit in plan["units"]]
            request.update(start_date=min(dates), end_date=max(dates))
            plan = build_plan(data_dir, request)
    return {"request": request, "work_pending": plan["work_pending"], "cached_files": plan.get("cached_files", 0),
            "existing_records": plan["existing_records"], "needs_calendar": plan.get("needs_calendar", False),
            "initialized": initialized, "scope_start": start, "scope_end": end.isoformat(),
            "explanation": "自动检测当前覆盖区间内的缺口和尾部更新；已有有效记录跳过。" if initialized else "首次获取使用项目默认范围（2015 年起），可在高级选项修改。"}
