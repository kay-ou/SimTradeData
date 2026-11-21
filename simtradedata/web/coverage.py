"""Read-only coverage inspection; never creates or migrates a database."""

from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from simtradedata.config.mootdx_finvalue_map import FULL_FUNDAMENTAL_FIELDS
from simtradedata.utils.ttm_calculator import get_quarters_in_range

CORE_FIELDS = ["basic_eps", "total_assets", "good_will", "administration_expense", "total_operating_revenue", "diluted_eps"]


def quarter_dates(start, end):
    return [date(year, quarter * 3, [31, 30, 30, 31][quarter - 1]).isoformat()
            for year, quarter in get_quarters_in_range(start, end)]


def trading_dates(conn, start, end):
    tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
    if "trade_days" not in tables:
        return []
    return [str(row[0]) for row in conn.execute(
        "SELECT date FROM trade_days WHERE date BETWEEN ? AND ? ORDER BY date", [start, end],
    ).fetchall()]


def inspect_coverage(data_dir: Path, start: str, end: str, symbols=None):
    symbols = symbols or []
    result = {"start_date": start, "end_date": end, "symbols": symbols, "busy": False,
              "datasets": [], "calendar_available": False}
    db_path = data_dir / "cn.duckdb"
    tables = set()
    expected = []
    conn = None
    try:
        if db_path.exists():
            conn = duckdb.connect(str(db_path), read_only=True)
            tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
            expected = trading_dates(conn, start, end)
            result["calendar_available"] = bool(expected)
        for key, table, label in [("daily", "stocks", "日线行情"), ("fundamentals", "fundamentals", "季度财务")]:
            item = {"key": key, "name": label, "kind": "historical", "records": 0, "securities": 0,
                    "first_date": None, "last_date": None, "dates": [], "missing_dates": [], "null_fields": []}
            if conn and table in tables:
                where = "date BETWEEN ? AND ?"
                params = [start, end]
                if symbols:
                    where += " AND symbol IN (" + ",".join("?" for _ in symbols) + ")"
                    params.extend(symbols)
                counts = conn.execute(
                    f"SELECT COUNT(*), COUNT(DISTINCT symbol), MIN(date), MAX(date) FROM {table} WHERE {where}", params,
                ).fetchone()
                item.update(records=counts[0], securities=counts[1],
                            first_date=str(counts[2]) if counts[2] else None,
                            last_date=str(counts[3]) if counts[3] else None)
                item["dates"] = [str(row[0]) for row in conn.execute(
                    f"SELECT DISTINCT date FROM {table} WHERE {where} ORDER BY date", params,
                ).fetchall()]
                columns = {row[0] for row in conn.execute(f"DESCRIBE {table}").fetchall()}
                fields = CORE_FIELDS if key == "fundamentals" else ["open", "high", "low", "close", "volume", "money"]
                present = [field for field in fields if field in columns]
                if present:
                    counts = conn.execute("SELECT " + ",".join(
                        f"COUNT(*) FILTER (WHERE {field} IS NULL OR NOT isfinite({field}))" for field in present
                    ) + f" FROM {table} WHERE {where}", params).fetchone()
                    item["null_fields"] = [{"field": field, "missing": count} for field, count in zip(present, counts)]
                item["absent_fields"] = [field for field in fields if field not in columns]
                if key == "fundamentals":
                    item["mapped_columns"] = len(set(FULL_FUNDAMENTAL_FIELDS) & columns)
                    item["supported_columns"] = len(set(FULL_FUNDAMENTAL_FIELDS))
            candidates = quarter_dates(start, end) if key == "fundamentals" else expected
            item["expected_dates"] = len(candidates)
            item["missing_dates"] = sorted(set(candidates) - set(item["dates"]))
            result["datasets"].append(item)
    finally:
        if conn:
            conn.close()

    item = {"key": "pcf", "name": "ETF 历史 PCF", "kind": "historical", "records": 0,
            "securities": 0, "first_date": None, "last_date": None, "dates": [], "missing_dates": [],
            "null_fields": [], "archives": [], "invalid_archives": []}
    pcf_dir = data_dir / "etf_pcf"
    dates = set()
    securities = set()
    for path in sorted(pcf_dir.glob("*/*.parquet")):
        if symbols and path.parent.name not in symbols:
            continue
        if not start <= path.stem <= end:
            continue
        try:
            data = read_pcf_archive(path, path.parent.name, path.stem)
            dates.add(path.stem)
            securities.add(path.parent.name)
            item["records"] += len(data)
            item["archives"].append({"symbol": path.parent.name, "date": path.stem, "rows": len(data)})
        except Exception as error:
            item["invalid_archives"].append({"file": path.name, "symbol": path.parent.name, "detail": str(error)})
    item.update(securities=len(securities), dates=sorted(dates),
                first_date=min(dates) if dates else None, last_date=max(dates) if dates else None)
    # Count ETF/date pairs, not just dates with at least one archived ETF.
    targets = [symbol for symbol in symbols if symbol.startswith(("15", "5"))] if symbols else sorted(securities)
    archived = {(row["symbol"], row["date"]) for row in item["archives"]}
    item["missing_dates"] = [f"{symbol} · {day}" for symbol in targets for day in expected if (symbol, day) not in archived]
    item["expected_dates"] = len(targets) * len(expected)
    result["datasets"].append(item)
    result["calendar_dates"] = expected
    return result


def read_pcf_archive(path, symbol, day):
    frame = pd.read_parquet(path)
    required = {"date", "etf_code", "code", "market_code", "code_num", "report_unit", "nav_percu", "nav_pre"}
    if frame.empty or not required <= set(frame.columns):
        raise ValueError("PCF 归档缺少必要字段")
    if set(frame["date"]) != {day} or set(frame["etf_code"]) != {symbol}:
        raise ValueError("归档日期或基金代码不匹配")
    numeric = frame[["code_num", "report_unit", "nav_percu", "nav_pre"]].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(numeric).all().all():
        raise ValueError("PCF 归档包含缺失值或非有限数值")
    if frame.duplicated(["code", "market_code"]).any() or frame["code_num"].isna().any() or (frame["code_num"] < 0).any():
        raise ValueError("PCF 归档存在重复证券或无效数量")
    if frame[["report_unit", "nav_percu", "nav_pre"]].isna().any().any() or (frame[["report_unit", "nav_percu", "nav_pre"]] <= 0).any().any():
        raise ValueError("PCF 归档缺少有效申赎单位或净值")
    return frame
