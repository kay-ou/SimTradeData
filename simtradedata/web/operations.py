"""Fixed update operations, executed in a separate process from the UI server."""

import json
import struct
import sys
import time
import zipfile
from pathlib import Path

import pandas as pd

from simtradedata.utils.process_lock import ProcessLock
from simtradedata.web.plans import build_plan
from simtradedata.web.cache_state import financial_signature
from simtradedata.web.completeness import INVALID_DAILY, calendar_covers
import logging

EVENT_PREFIX = "@@SIMTRADE_EVENT@@"


def emit(**event):
    print(EVENT_PREFIX + json.dumps(event, ensure_ascii=False), flush=True)


def download_progress(message, total_hint=None):
    started = time.monotonic()
    last = [0.0]

    def update(downloaded, total):
        total = total or total_hint
        current = time.monotonic()
        if downloaded and downloaded != total and current - last[0] < .25:
            return
        last[0] = current
        emit(stage="download", message=message, downloaded_bytes=downloaded,
             total_bytes=total or None, bytes_per_second=downloaded / max(current - started, .01))
    return update


def cache_signature(path):
    from simtradedata.web.cache_state import file_signature
    return file_signature(path)


def daily_cache_date(path):
    """Cache the actual latest date in the ZIP, not its download timestamp."""
    from scripts.import_tdx_day import is_stock_code

    latest = 0
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            normalized = name.replace("\\", "/")
            if "/lday/" not in normalized or not is_stock_code(normalized.split("/")[-1]):
                continue
            with archive.open(name) as stream:
                size = archive.getinfo(name).file_size
                if size >= 32 and size % 32 == 0:
                    stream.seek(size - 32)
                    latest = max(latest, struct.unpack("<I", stream.read(4))[0])
    if not latest:
        raise ValueError("日线缓存没有可用记录")
    day = pd.to_datetime(str(latest), format="%Y%m%d").strftime("%Y-%m-%d")
    return day


def fetch_financial_with_progress(fetcher, filename, total_hint=None):
    from mootdx.affair import Affair

    if not total_hint:
        try:
            total_hint = next((row.get("filesize") for row in fetcher.list_available_reports() if row["filename"] == filename), None)
        except Exception:
            pass
    Affair.fetch.__globals__["financial"].Financial().fetch_only(
        downdir=str(fetcher._download_dir), filename=filename,
        report_hook=download_progress(f"下载财务包 {filename}", total_hint),
    )
    return fetcher._convert_to_ptrade_format(fetcher.parse_local(filename))


def update_daily(data_dir, plan):
    from scripts.download_tdx_day import DOWNLOAD_URL, download_file, get_remote_file_info, needs_update
    from scripts.import_tdx_day import TdxDayImporter, filename_to_ptrade_code, is_stock_code, iter_day_files_from_zip, parse_tdx_day_file
    from simtradedata.utils.code_utils import is_etf_code
    from simtradedata.validators.data_validator import MarketDataValidator

    request = plan["request"]
    cache = data_dir / "downloads" / "hsjday.zip"
    cache.parent.mkdir(parents=True, exist_ok=True)
    emit(stage="download", message="检查日线历史包", completed=0, total=None)
    if request["mode"] == "auto" and not calendar_covers(data_dir, request["start_date"], request["end_date"]):
        try:
            update_calendar(data_dir, plan)
            plan = build_plan(data_dir, request)
        except Exception as error:
            print(f"交易日历暂无法同步：{error}", flush=True)
    reuse = request["mode"] == "reparse"
    if request["mode"] == "auto" and cache.exists():
        try:
            days = plan["before"].get("calendar_dates", [])
            target = max(days) if days else request["end_date"]
            reuse = daily_cache_date(cache) >= target
            if reuse:
                emit(stage="write", message="本地日线缓存已覆盖所选日期，直接补齐缺失记录", completed=0, total=1)
        except (ValueError, zipfile.BadZipFile):
            reuse = False
    if not reuse:
        remote = get_remote_file_info(DOWNLOAD_URL)
        if request["mode"] in {"refresh", "auto"} or needs_update(cache, remote):
            if not download_file(DOWNLOAD_URL, cache, show_progress=False,
                                 progress_callback=download_progress("下载沪深日线历史包")):
                raise ConnectionError("日线历史包下载失败")
    if not cache.exists():
        raise ValueError("没有可用日线缓存")
    wanted = set(request["symbols"]) or None
    with zipfile.ZipFile(cache) as archive:
        total = sum(1 for name in archive.namelist()
                    if "/lday/" in name.replace("\\", "/") and name.endswith(".day")
                    and is_stock_code(name.replace("\\", "/").split("/")[-1])
                    and (wanted is None or filename_to_ptrade_code(name.replace("\\", "/").split("/")[-1]) in wanted))
    if not total:
        raise ValueError("历史包中没有所选证券")
    importer = TdxDayImporter(str(data_dir / "cn.duckdb"), full_import=request["mode"] not in {"auto", "incremental"})
    processed, source_rows = 0, 0
    found = set()
    try:
        for filename, content in iter_day_files_from_zip(cache, wanted):
            if not is_stock_code(filename):
                continue
            symbol = filename_to_ptrade_code(filename)
            found.add(symbol)
            frame = parse_tdx_day_file(content, price_divisor=1000 if is_etf_code(symbol) else 100)
            if not frame.empty:
                frame["date"] = pd.to_datetime(frame["date"])
                frame = frame[frame["date"].between(request["start_date"], request["end_date"])]
                if not frame.empty:
                    values = frame[["open", "high", "low", "close", "volume", "money"]]
                    if values.isna().any().any() or (values[["open", "high", "low", "close"]] <= 0).any().any():
                        raise ValueError(f"{symbol} 日线包含缺失值或无效价格")
                    MarketDataValidator.validate(frame.set_index("date"), symbol, strict=True)
                    if (values["high"] < values[["open", "close", "low"]].max(axis=1)).any() or (values["low"] > values[["open", "close", "high"]].min(axis=1)).any():
                        raise ValueError(f"{symbol} 日线高低价不一致")
                    source_rows += len(frame)
                    importer.import_stock(symbol, frame)
                    if request["mode"] == "auto":
                        invalid = importer.writer.conn.execute(
                            f"SELECT s.date FROM stocks s WHERE s.symbol=? AND s.date BETWEEN ? AND ? AND ({INVALID_DAILY})",
                            [symbol, request["start_date"], request["end_date"]],
                        ).fetchall()
                        repair = frame[frame["date"].isin([pd.Timestamp(row[0]) for row in invalid])]
                        if not repair.empty:
                            importer.stats["records_imported"] += importer.writer.write_market_data(symbol, repair.set_index("date"))
            processed += 1
            if processed == total or processed % 50 == 0 or wanted:
                emit(stage="write", message=f"处理 {symbol}", completed=processed, total=total,
                     written=importer.stats["records_imported"])
        if wanted and wanted - found:
            raise ValueError("历史包未包含这些证券：" + ", ".join(sorted(wanted - found)))
        if not source_rows:
            raise ValueError("所选日期没有源记录，未填造停牌或缺失行情")
        return {"written": importer.stats["records_imported"], "source_rows": source_rows, "failed_units": []}
    finally:
        importer.writer.close()


def update_fundamentals(data_dir, plan):
    from simtradedata.fetchers.mootdx_affair_fetcher import MootdxAffairFetcher
    from simtradedata.utils.code_utils import convert_to_ptrade_code
    from simtradedata.writers.duckdb_writer import DuckDBWriter

    request = plan["request"]
    fetcher = MootdxAffairFetcher(download_dir=str(data_dir / "mootdx_financial"))
    mapping = financial_signature()
    sizes = {}
    if request["mode"] not in {"reparse", "auto"}:
        try:
            sizes = {row["filename"]: int(row.get("filesize") or 0) for row in fetcher.list_available_reports()}
        except Exception:
            pass  # Download can proceed when catalog size metadata is unavailable.
    writer = DuckDBWriter(str(data_dir / "cn.duckdb"))
    receipts = {(year, quarter): value for year, quarter, value in writer.conn.execute("SELECT year, quarter, file_hash FROM fundamentals_progress").fetchall()}
    completed = writer.get_completed_fundamental_quarters()
    expected_counts = {(year, quarter): count for year, quarter, count in writer.conn.execute("SELECT year, quarter, stock_count FROM fundamentals_progress").fetchall()}
    actual_counts = {str(day): count for day, count in writer.conn.execute("SELECT date, COUNT(*) FROM fundamentals GROUP BY date").fetchall()}
    failures, written, skipped = [], 0, 0
    try:
        for index, day in enumerate(plan["units"], 1):
            stamp = pd.Timestamp(day)
            year, quarter = stamp.year, stamp.month // 3
            filename = f"gpcw{day.replace('-', '')}.zip"
            cached = data_dir / "mootdx_financial" / filename
            signature = "local:" + cache_signature(cached) + mapping if cached.exists() else None
            recorded = receipts.get((year, quarter)) or ""
            matching = recorded == signature if signature else recorded.startswith("local:") and recorded.endswith(mapping)
            if request["mode"] == "auto" and matching and (year, quarter) in completed and actual_counts.get(day, 0) >= expected_counts.get((year, quarter), 1):
                skipped += 1
                emit(stage="write", message=f"{day} 已导入且缓存未变化，跳过", completed=index, total=plan["unit_count"])
                continue
            if request["mode"] == "incremental" and (year, quarter) in completed and index <= len(plan["units"]) - 4:
                skipped += 1
                emit(stage="write", message=f"跳过已完成季度 {day}", completed=index, total=plan["unit_count"])
                continue
            try:
                emit(stage="download", message=f"读取财务季度 {day}", completed=index - 1, total=plan["unit_count"])
                if request["mode"] == "reparse":
                    frame = fetcher._convert_to_ptrade_format(fetcher.parse_local(filename))
                elif request["mode"] == "auto" and cached.exists():
                    try:
                        frame = fetcher._convert_to_ptrade_format(fetcher.parse_local(filename))
                        if frame.empty or "end_date" not in frame or set(pd.to_datetime(frame["end_date"]).dt.strftime("%Y-%m-%d")) != {day}:
                            raise ValueError("缓存报告期不匹配")
                    except Exception:
                        frame = fetch_financial_with_progress(fetcher, filename, sizes.get(filename))
                else:
                    frame = fetch_financial_with_progress(fetcher, filename, sizes.get(filename))
                if frame.empty or "code" not in frame or set(pd.to_datetime(frame["end_date"]).dt.strftime("%Y-%m-%d")) != {day}:
                    raise ValueError("季度源为空或报告期不匹配")
                if "total_assets" not in frame or frame["total_assets"].isna().all():
                    raise ValueError("季度源缺少可用资产总额")
                writer.begin()
                count = 0
                try:
                    total_companies = frame["code"].nunique()
                    for company, (code, group) in enumerate(frame.groupby("code"), 1):
                        symbol = convert_to_ptrade_code(str(code), "qstock")
                        writer.write_fundamentals(symbol, group.drop(columns=["code"]).set_index("end_date"))
                        count += len(group)
                        if company % 100 == 0 or company == total_companies:
                            emit(stage="write", message=f"{day} 写入 {symbol}", completed=company, total=total_companies)
                    writer.mark_fundamental_quarter_completed(year, quarter, count, filename=filename,
                                                            file_hash="local:" + cache_signature(cached) + mapping)
                    writer.commit()
                except Exception:
                    writer.rollback()
                    raise
                written += count
            except Exception as error:
                failures.append({"unit": day, "detail": str(error)})
                print(f"{day} 失败：{error}", flush=True)
            emit(stage="write", message=f"财务季度 {day} 已处理", completed=index, total=plan["unit_count"], written=written)
        return {"written": written, "skipped": skipped, "failed_units": failures}
    finally:
        writer.close()


def update_pcf(data_dir, plan):
    from simtradedata.fetchers.etf_pcf_fetcher import EtfPcfFetcher
    from simtradedata.web.coverage import read_pcf_archive

    failures, written, skipped = [], 0, 0
    if plan.get("needs_calendar"):
        update_calendar(data_dir, plan)
        plan = build_plan(data_dir, plan["request"])
    with EtfPcfFetcher() as fetcher:
        for index, unit in enumerate(plan["units"], 1):
            symbol, day = unit.split("|")
            emit(stage="download", message=f"获取 {symbol} · {day} 历史清单", completed=index - 1, total=plan["unit_count"])
            path = data_dir / "etf_pcf" / symbol / f"{day}.parquet"
            try:
                reusable = False
                if plan["request"]["mode"] in {"auto", "incremental"} and path.exists():
                    try:
                        read_pcf_archive(path, symbol, day)
                        reusable = True
                    except ValueError:
                        pass
                if reusable:
                    skipped += 1
                else:
                    data = fetcher.fetch_pcf(symbol, day)
                    if data.empty:
                        raise ValueError("清单为空")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = path.with_suffix(".parquet.tmp")
                    data.to_parquet(temporary, index=False)
                    temporary.replace(path)
                    written += len(data)
            except Exception as error:
                failures.append({"unit": unit, "detail": str(error)})
                print(f"{unit} 失败：{error}", flush=True)
            emit(stage="write", message=f"处理 {symbol} · {day}", completed=index, total=plan["unit_count"], written=written)
    return {"written": written, "skipped": skipped, "failed_units": failures}


def update_calendar(data_dir, plan):
    from simtradedata.router import SmartRouter
    from simtradedata.writers.duckdb_writer import DuckDBWriter

    request = plan["request"]
    emit(stage="download", message="同步交易日历", completed=0, total=1)
    with SmartRouter() as router:
        frame = router.get_trade_calendar(request["start_date"], request["end_date"])
    if frame.empty or not {"calendar_date", "is_trading_day"} <= set(frame.columns):
        raise ValueError("交易日历源为空或字段不匹配")
    calendar = frame.rename(columns={"calendar_date": "date"}).copy()
    calendar["date"] = pd.to_datetime(calendar["date"])
    flags = calendar["is_trading_day"].astype(str)
    if not flags.isin(["0", "1", "True", "False"]).all():
        raise ValueError("交易日历包含未知状态")
    calendar["is_trading_day"] = flags.isin(["1", "True"])
    calendar = calendar[calendar["date"].between(request["start_date"], request["end_date"])][["date", "is_trading_day"]]
    if calendar["date"].nunique() != (pd.Timestamp(request["end_date"]) - pd.Timestamp(request["start_date"])).days + 1:
        raise ValueError("交易日历没有覆盖完整日期范围")
    frame = calendar[calendar["is_trading_day"]]
    writer = DuckDBWriter(str(data_dir / "cn.duckdb"))
    try:
        writer.begin()
        writer.conn.execute("CREATE TABLE IF NOT EXISTS exchange_calendar(date DATE PRIMARY KEY, is_trading_day BOOLEAN NOT NULL)")
        writer.conn.register("calendar_data", calendar)
        writer.conn.execute("INSERT OR REPLACE INTO exchange_calendar SELECT date::DATE, is_trading_day FROM calendar_data")
        writer.conn.execute("DELETE FROM trade_days USING calendar_data WHERE trade_days.date=calendar_data.date::DATE AND NOT calendar_data.is_trading_day")
        written = writer.write_trade_days(frame)
        writer.commit()
    except Exception:
        writer.rollback()
        raise
    finally:
        writer.close()
    emit(stage="write", message="交易日历已同步", completed=1, total=1)
    return {"written": written, "failed_units": []}


def main(argv=None):
    logging.basicConfig(stream=sys.stdout, level=logging.WARNING)
    args = argv or sys.argv[1:]
    data_dir, request_file = Path(args[0]).resolve(), Path(args[1])
    with ProcessLock(str(data_dir / ".ui-update.lock")):
        plan = build_plan(data_dir, json.loads(request_file.read_text()))
        action = {"daily": update_daily, "fundamentals": update_fundamentals, "pcf": update_pcf,
                  "calendar": update_calendar}[plan["request"]["dataset"]]
        result = action(data_dir, plan)
        emit(stage="verify", message="更新结果已保存", result=result)
        return 1 if result["failed_units"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
