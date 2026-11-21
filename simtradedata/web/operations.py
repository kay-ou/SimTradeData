"""Fixed update operations, executed in a separate process from the UI server."""

import json
import sys
import time
import zipfile
from pathlib import Path

import pandas as pd

from simtradedata.utils.process_lock import ProcessLock
from simtradedata.web.plans import build_plan

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


def update_daily(data_dir, plan):
    from scripts.download_tdx_day import DOWNLOAD_URL, download_file, get_remote_file_info, needs_update
    from scripts.import_tdx_day import TdxDayImporter, filename_to_ptrade_code, is_stock_code, iter_day_files_from_zip, parse_tdx_day_file
    from simtradedata.utils.code_utils import is_etf_code
    from simtradedata.validators.data_validator import MarketDataValidator

    request = plan["request"]
    cache = data_dir / "downloads" / "hsjday.zip"
    cache.parent.mkdir(parents=True, exist_ok=True)
    emit(stage="download", message="检查日线历史包", completed=0, total=None)
    if request["mode"] != "reparse":
        remote = get_remote_file_info(DOWNLOAD_URL)
        if request["mode"] == "refresh" or needs_update(cache, remote):
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
    importer = TdxDayImporter(str(data_dir / "cn.duckdb"), full_import=request["mode"] != "incremental")
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
    sizes = {}
    if request["mode"] != "reparse":
        try:
            sizes = {row["filename"]: int(row.get("filesize") or 0) for row in fetcher.list_available_reports()}
        except Exception:
            pass  # Download can proceed when catalog size metadata is unavailable.
    writer = DuckDBWriter(str(data_dir / "cn.duckdb"))
    completed = writer.get_completed_fundamental_quarters()
    failures, written, skipped = [], 0, 0
    try:
        for index, day in enumerate(plan["units"], 1):
            stamp = pd.Timestamp(day)
            year, quarter = stamp.year, stamp.month // 3
            filename = f"gpcw{day.replace('-', '')}.zip"
            if request["mode"] == "incremental" and (year, quarter) in completed and index <= len(plan["units"]) - 4:
                skipped += 1
                emit(stage="write", message=f"跳过已完成季度 {day}", completed=index, total=plan["unit_count"])
                continue
            try:
                emit(stage="download", message=f"读取财务季度 {day}", completed=index - 1, total=plan["unit_count"])
                if request["mode"] == "reparse":
                    frame = fetcher._convert_to_ptrade_format(fetcher.parse_local(filename))
                else:
                    from mootdx.affair import Affair
                    crawler = Affair.fetch.__globals__["financial"].Financial()
                    crawler.fetch_only(downdir=str(data_dir / "mootdx_financial"), filename=filename,
                                       report_hook=download_progress(f"下载财务季度 {day}", sizes.get(filename)))
                    frame = fetcher._convert_to_ptrade_format(fetcher.parse_local(filename))
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
                    writer.mark_fundamental_quarter_completed(year, quarter, count, filename=filename)
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
    with EtfPcfFetcher() as fetcher:
        for index, unit in enumerate(plan["units"], 1):
            symbol, day = unit.split("|")
            emit(stage="download", message=f"获取 {symbol} · {day} 历史清单", completed=index - 1, total=plan["unit_count"])
            path = data_dir / "etf_pcf" / symbol / f"{day}.parquet"
            try:
                reusable = False
                if plan["request"]["mode"] == "incremental" and path.exists():
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
    frame = frame[frame["is_trading_day"].astype(str).isin(["1", "True"])].rename(columns={"calendar_date": "date"})
    frame = frame[pd.to_datetime(frame["date"]).between(request["start_date"], request["end_date"])]
    writer = DuckDBWriter(str(data_dir / "cn.duckdb"))
    try:
        written = writer.write_trade_days(frame)
    finally:
        writer.close()
    emit(stage="write", message="交易日历已同步", completed=1, total=1)
    return {"written": written, "failed_units": []}


def main(argv=None):
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
