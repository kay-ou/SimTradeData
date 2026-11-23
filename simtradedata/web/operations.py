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


def fetch_financial_with_progress(fetcher, filename, total_hint=None, timings=None):
    from mootdx.affair import Affair

    if not total_hint:
        try:
            total_hint = next((row.get("filesize") for row in fetcher.list_available_reports() if row["filename"] == filename), None)
        except Exception:
            pass
    started = time.perf_counter()
    Affair.fetch.__globals__["financial"].Financial().fetch_only(
        downdir=str(fetcher._download_dir), filename=filename,
        report_hook=download_progress(f"下载财务包 {filename}", total_hint),
    )
    if timings is not None:
        timings["download"] += time.perf_counter() - started
    started = time.perf_counter()
    frame = fetcher._convert_to_ptrade_format(fetcher.parse_local(filename))
    if timings is not None:
        timings["parse"] += time.perf_counter() - started
    return frame


def update_daily(data_dir, plan):
    if plan['request']['mode'] == 'repair':
        from simtradedata.web.daily_repair import repair_daily
        return repair_daily(data_dir, plan)
    from scripts.download_tdx_day import DOWNLOAD_URL, download_file, get_remote_file_info, needs_update
    from scripts.import_tdx_day import TdxDayImporter, filename_to_ptrade_code, is_stock_code, iter_day_files_from_zip, parse_tdx_day_file
    from simtradedata.utils.code_utils import is_etf_code
    from simtradedata.web.daily_categories import is_exchange_etf
    from simtradedata.validators.data_validator import MarketDataValidator

    started = time.perf_counter()
    timings = {"archive_parse": 0.0, "etf_check": 0.0, "write": 0.0,
               "incremental_request": 0.0, "cache_check": 0.0, "validation": 0.0,
               "lifecycle_request": 0.0, "mootdx_request": 0.0, "tencent_request": 0.0, "eastmoney_request": 0.0,
               "archive_read": 0.0, "package_request": 0.0, "catalogue_request": 0.0, "calendar_request": 0.0}
    def timed_call(stage, call, *args, **kwargs):
        tick = time.perf_counter()
        try:
            return call(*args, **kwargs)
        finally:
            timings[stage] += time.perf_counter() - tick

    request = plan["request"]
    cache = data_dir / "downloads" / "hsjday.zip"
    cache.parent.mkdir(parents=True, exist_ok=True)
    emit(stage="download", message="检查日线历史包", completed=0, total=None)
    if request["mode"] == "auto" and not calendar_covers(data_dir, request["start_date"], request["end_date"]):
        try:
            timed_call("calendar_request", update_calendar, data_dir, plan)
            plan = build_plan(data_dir, request)
        except Exception as error:
            print(f"交易日历暂无法同步：{error}", flush=True)
    reuse = request["mode"] == "reparse"
    usable_cache = True
    prefilled = {'days': [], 'written': 0, 'source_rows': 0}
    if request["mode"] == "auto" and cache.exists():
        try:
            days = plan["before"].get("calendar_dates", [])
            target = max(days) if days else request["end_date"]
            cache_end = timed_call("cache_check", daily_cache_date, cache)
            reuse = cache_end >= target
            if not reuse and calendar_covers(data_dir, request['start_date'], request['end_date']):
                from simtradedata.web.daily_incremental import prefill_sessions
                prefilled = prefill_sessions(data_dir, plan, cache_end, timings)
                reuse = target in prefilled['days'] and set(prefilled['days']) == set(prefilled.get('requested_days', []))
            if reuse:
                emit(stage="write", message="本地日线缓存已覆盖所选日期，直接补齐缺失记录", completed=0, total=1)
        except (ValueError, zipfile.BadZipFile):
            reuse = False
            usable_cache = False
    if not reuse:
        remote = timed_call("package_request", get_remote_file_info, DOWNLOAD_URL)
        if request["mode"] == "refresh" or not usable_cache or needs_update(cache, remote):
            if not timed_call("package_request", download_file, DOWNLOAD_URL, cache, show_progress=False,
                                 progress_callback=download_progress("下载沪深日线历史包")):
                raise ConnectionError("日线历史包下载失败")
    if not cache.exists():
        raise ValueError("没有可用日线缓存")
    calendar_complete = calendar_covers(data_dir, request["start_date"], request["end_date"])
    wanted = set(request["symbols"]) or None
    with zipfile.ZipFile(cache) as archive:
        available = {filename_to_ptrade_code(name.replace("\\", "/").split("/")[-1])
                     for name in archive.namelist()
                     if "/lday/" in name.replace("\\", "/") and name.endswith(".day")
                     and is_stock_code(name.replace("\\", "/").split("/")[-1])}
    failures, not_trading = [], []
    etf_candidates = {symbol for symbol in (wanted or available) if is_exchange_etf(symbol)}
    if wanted is None and request["mode"] != "reparse":
        from simtradedata.web.etf_catalogue import load_etfs
        emit(stage="download", message="自动发现沪深ETF，检查历史包遗漏", completed=0, total=None)
        try:
            etfs, warnings = timed_call("catalogue_request", load_etfs)
            etf_candidates.update(row["symbol"] for row in etfs)
            missing = etf_candidates - available
            failures.extend({"unit": "ETF目录", "detail": warning.split("：")[0] + "；未核验新增ETF，已有历史保留。", "technical_detail": warning} for warning in warnings)
        except Exception as error:
            missing = set()
            failures.append({"unit": "ETF目录", "detail": str(error)})
    else:
        missing = wanted - available if wanted else set()
    if missing and request["mode"] == "reparse":
        raise ValueError("本地历史包未包含这些证券，离线重解析无法获取：" + ", ".join(sorted(missing)))
    total = len(wanted if wanted else available | missing)
    if not total:
        raise ValueError("历史包中没有所选证券")

    def validate_frame(symbol, frame):
        frame["date"] = pd.to_datetime(frame["date"])
        frame = frame[frame["date"].between(request["start_date"], request["end_date"])]
        if not frame.empty:
            values = frame[["open", "high", "low", "close", "volume", "money"]]
            if values.isna().any().any() or (values[["open", "high", "low", "close"]] <= 0).any().any():
                raise ValueError(f"{symbol} 日线包含缺失值或无效价格")
            if ((values["volume"] == 0) & (values["money"] > 0)).any():
                raise ValueError(f"{symbol} zero volume with positive traded amount")
            MarketDataValidator.validate(frame.set_index("date"), symbol, strict=True)
            if (values["high"] < values[["open", "close", "low"]].max(axis=1)).any() or (values["low"] > values[["open", "close", "high"]].min(axis=1)).any():
                raise ValueError(f"{symbol} 日线高低价不一致")
        return frame

    def source_frames():
        files = iter(iter_day_files_from_zip(cache, archive_wanted))
        while True:
            try:
                filename, content = timed_call('archive_read', next, files)
            except StopIteration:
                break
            if is_stock_code(filename):
                symbol = filename_to_ptrade_code(filename)
                parsing = time.perf_counter()
                frame = parse_tdx_day_file(content, price_divisor=1000 if is_etf_code(symbol) else 100,
                                           dates=archive_dates.get(symbol))
                timings["archive_parse"] += time.perf_counter() - parsing
                yield symbol, frame
        supplement = missing | etf_candidates if request["mode"] != "reparse" else set()
        if supplement:
            from simtradedata.fetchers.mootdx_fetcher import MootdxFetcher
            from simtradedata.web.etf_catalogue import daily_etf_gaps
            def check_gaps(symbols, start, end):
                checking = time.perf_counter()
                try:
                    gaps = daily_etf_gaps(importer.writer.conn, symbols, start, end, include_dates=True)
                    for symbol in symbols:
                        invalid = [str(day) for day in invalid_dates.get(symbol, []) if start <= str(day) <= end]
                        if invalid:
                            gaps[symbol] = sorted(set(gaps.get(symbol, [])) | set(invalid))
                    return gaps
                finally:
                    timings["etf_check"] += time.perf_counter() - checking
            from simtradedata.web.etf_listing import EtfNotTradingInRange
            import requests
            ranges = {symbol: (request["start_date"], request["end_date"]) for symbol in missing}
            gap_sets = {}
            unverified = set()
            if request["mode"] in {"auto", "incremental"}:
                if calendar_complete:
                    gap_sets = check_gaps(sorted(missing), request["start_date"], request["end_date"])
                    ranges = {symbol: (days[0], days[-1]) for symbol, days in gap_sets.items()}
                else:
                    ranges = {}
                    unverified = missing
                    failures.extend({"unit": symbol, "detail": "交易日历覆盖不完整，无法确认ETF历史缺口；请先同步交易日历。"}
                                    for symbol in sorted(unverified))
            archived_etfs = etf_candidates & available
            if calendar_complete:
                gap_sets.update(check_gaps(sorted(archived_etfs), request["start_date"], request["end_date"]))
                ranges.update({symbol: (days[0], days[-1]) for symbol, days in gap_sets.items()})
                skipped.update(archived_etfs - ranges.keys())
            elif archived_etfs:
                failures.append({"unit": "交易日历", "detail": "交易日历覆盖不完整，无法确认ETF历史缺口；请先同步交易日历。"})
            skipped.update(missing - ranges.keys() - unverified)
            for symbol in sorted((skipped | unverified) - found):
                yield symbol, pd.DataFrame()
            if ranges:
                from contextlib import ExitStack
                from simtradedata.web.etf_listing import fetch_etf_listing_info, store_delisting
                # Archive rows have already been imported; snapshot the evidence
                # once for the remaining securities, rather than scan per ETF.
                trade_bounds = {symbol: (first, last) for symbol, first, last in importer.writer.conn.execute("""
                    SELECT symbol,MIN(date),MAX(date) FROM stocks
                    WHERE symbol IN (SELECT unnest(?::VARCHAR[]))
                    AND open>0 AND high>0 AND low>0 AND close>0 AND high>=low
                    AND volume>0 AND money>0 AND isfinite(close) AND isfinite(money)
                    GROUP BY symbol""", [sorted(ranges)]).fetchall()}
                lifecycle = {symbol: (listed, exited) for symbol, listed, exited in importer.writer.conn.execute("""
                    SELECT symbol,TRY_CAST(listed_date AS DATE),TRY_CAST(de_listed_date AS DATE)
                    FROM stock_metadata WHERE symbol IN (SELECT unnest(?::VARCHAR[]))""", [sorted(ranges)]).fetchall()}
                with ExitStack() as connections:
                    fetcher = None
                    fallback_failures = {"tencent": 0, "eastmoney": 0}
                    lifecycle_failures = 0
                    for symbol, (start, end) in sorted(ranges.items()):
                        emit(stage="download", message=f"历史包缺少 {symbol}，通过通达信补齐 {start} 至 {end}",
                             completed=processed, total=total)
                        try:
                            from simtradedata.web.daily_categories import is_exchange_etf, ETF_SHARE_CLASSES
                            from simtradedata.web.etf_history import fetch_etf_daily_fallback, fetch_etf_daily_tencent
                            if symbol in ETF_SHARE_CLASSES:
                                raise EtfNotTradingInRange({
                                    'symbol':symbol, 'name':'汇添富收益快钱货币B', 'state':'share_class',
                                    'listed_date':None, 'trading_symbol':ETF_SHARE_CLASSES[symbol],
                                    'source_url':'https://fund.eastmoney.com/gonggao/159005,AN202610081830215637.html',
                                    'detail':f"B类份额不使用独立交易代码；交易行情请使用 {ETF_SHARE_CLASSES[symbol]}",
                                })
                            info, lifecycle_error = None, None
                            first_trade, last_trade = None, None
                            known_listing = True
                            if is_exchange_etf(symbol):
                                first_trade, last_trade = trade_bounds.get(symbol, (None, None))
                                metadata = lifecycle.get(symbol)
                                known_listing = bool(metadata and metadata[0] and str(metadata[0]) <= end)
                                known_exit = bool(metadata and metadata[1])
                                needs_lifecycle = not known_exit and (not last_trade or str(last_trade) < end)
                                if needs_lifecycle:
                                    if lifecycle_failures >= 3:
                                        lifecycle_error = "Lifecycle source skipped after repeated connection failures in this task"
                                    else:
                                        try:
                                            info = timed_call("lifecycle_request", fetch_etf_listing_info, symbol)
                                            lifecycle_failures = 0
                                        except requests.RequestException as error:
                                            lifecycle_failures += 1
                                            lifecycle_error = str(error)
                                            if lifecycle_failures == 3:
                                                print("ETF lifecycle source: repeated connection failures; skipping queries for this task", flush=True)
                                        except ValueError as error:
                                            lifecycle_error = str(error)
                                if info and (info['state'] in {'pre_listing', 'not_established'} or
                                             (info.get('listed_date') and end < info['listed_date'])):
                                    raise EtfNotTradingInRange(info)
                            if info and info.get('de_listed_date'):
                                store_delisting(importer.writer, info)
                            if info and info.get('listed_date'):
                                start = max(start, info['listed_date'])
                            if info and info.get('de_listed_date'):
                                # New lifecycle evidence can remove dates from
                                # the original snapshot, including HALT intervals.
                                gap_dates = check_gaps([symbol], start, end).get(symbol, [])
                            elif symbol in gap_sets:
                                gap_dates = [day for day in gap_sets[symbol] if start <= day <= end]
                            else:
                                gap_dates = check_gaps([symbol], start, end).get(symbol, [])
                            if not gap_dates and request['mode'] in {'auto','incremental'}:
                                skipped.add(symbol)
                                if info and info.get('de_listed_date'):
                                    not_trading.append(info)
                                yield symbol, pd.DataFrame()
                                continue
                            # Refresh of an ETF absent from the archive still fetches the requested range.
                            missing_only = request["mode"] in {"auto", "incremental"} or symbol in archived_etfs
                            source_errors = {"lifecycle": lifecycle_error} if lifecycle_error else {}

                            def matching_rows(data):
                                if data is None or data.empty:
                                    return pd.DataFrame()
                                data = timed_call("validation", validate_frame, symbol, data.rename(columns={"amount": "money"}))
                                if lifecycle_error and not known_listing and not data.empty:
                                    trades = data[(data['volume'] > 0) & (data['money'] > 0)]
                                    bounds = [pd.Timestamp(day) for day in [first_trade, last_trade] if day]
                                    if not trades.empty:
                                        bounds.extend([trades['date'].min(), trades['date'].max()])
                                    if not bounds:
                                        raise ValueError('Zero-volume reference prices cannot verify an ETF lifecycle')
                                    data = data[data['date'].between(min(bounds), max(bounds))]
                                return data[data["date"].isin(pd.to_datetime(gap_dates))] if missing_only else data

                            try:
                                if fetcher is None:
                                    fetcher = timed_call("mootdx_request", connections.enter_context, MootdxFetcher())
                                frame = matching_rows(timed_call("mootdx_request", fetcher.fetch_daily_bars, symbol, start, end))
                            except Exception as error:
                                if not is_exchange_etf(symbol):
                                    raise
                                source_errors["mootdx"] = str(error)
                                frame = pd.DataFrame()
                            if frame.empty and "mootdx" not in source_errors:
                                source_errors["mootdx"] = "No records matched the missing dates"
                            providers = [("tencent", fetch_etf_daily_tencent), ("eastmoney", fetch_etf_daily_fallback)]
                            outstanding = set(gap_dates) - set(frame['date'].dt.strftime('%Y-%m-%d')) if not frame.empty else set(gap_dates)
                            if (frame.empty or (missing_only and outstanding)) and is_exchange_etf(symbol):
                                for provider, load in providers:
                                    if fallback_failures[provider] >= 3:
                                        source_errors[provider] = "Skipped after repeated connection failures in this task"
                                        continue
                                    emit(stage="download", message=f"通过 {provider} 补齐 {symbol} 的缺失日期",
                                         completed=processed, total=total)
                                    try:
                                        first, last = (min(outstanding), max(outstanding)) if missing_only and outstanding else (start, end)
                                        extra = matching_rows(timed_call(provider + "_request", load, symbol, first, last))
                                        fallback_failures[provider] = 0
                                        if extra.empty:
                                            source_errors[provider] = "No records matched the missing dates"
                                        if not extra.empty:
                                            if missing_only:
                                                extra = extra[extra['date'].dt.strftime('%Y-%m-%d').isin(outstanding)]
                                            frame = pd.concat([frame, extra], ignore_index=True).sort_values('date')
                                            outstanding -= set(extra['date'].dt.strftime('%Y-%m-%d'))
                                            if not missing_only or not outstanding:
                                                break
                                    except (requests.RequestException, ConnectionError, TimeoutError) as error:
                                        fallback_failures[provider] += 1
                                        source_errors[provider] = str(error)
                                        if fallback_failures[provider] == 3:
                                            print(f"{provider}: repeated connection failures; skipping this source for the rest of this task", flush=True)
                                    except EtfNotTradingInRange:
                                        raise
                                    except ValueError as error:
                                        source_errors[provider] = str(error)
                            if frame.empty:
                                failures.append({"unit": symbol,
                                    "detail": "缺口日期尚未取得有效数据，请核验交易状态或稍后重试来源。",
                                    "missing_dates": gap_dates, "source_errors": source_errors})
                                yield symbol, frame
                                continue
                            if missing_only:
                                remaining = sorted(set(gap_dates) - set(frame['date'].dt.strftime('%Y-%m-%d')))
                                if remaining:
                                    failures.append({"unit": symbol,
                                        "detail": "部分缺口日期尚未取得有效数据；已获取并通过校验的数据已保存。",
                                        "missing_dates": remaining, "source_errors": source_errors})
                            yield symbol, frame
                        except EtfNotTradingInRange as error:
                            not_trading.append(error.info)
                            skipped.add(symbol)
                            if error.info.get('listed_date'):
                                importer.writer.conn.execute(
                                    "INSERT INTO stock_metadata(symbol,stock_name,listed_date) VALUES (?,?,?) "
                                    "ON CONFLICT(symbol) DO UPDATE SET stock_name=coalesce(stock_metadata.stock_name,excluded.stock_name),"
                                    "listed_date=coalesce(stock_metadata.listed_date,excluded.listed_date)",
                                    [symbol,error.info['name'],error.info['listed_date']],
                                )
                            emit(stage="write", message=f"{symbol} · {error.info['detail']}，无需下载上市前行情",
                                 completed=processed, total=total)
                            yield symbol, pd.DataFrame()
                        except Exception as error:
                            detail = "行情接口暂不可用，未能核验历史；可稍后只重试此证券。" if isinstance(error, requests.RequestException) else str(error)
                            failures.append({"unit": symbol, "detail": detail, "technical_detail": str(error)})
                            print(f"{symbol} ETF补齐失败：{error}", flush=True)
                            yield symbol, pd.DataFrame()

    importer = TdxDayImporter(str(data_dir / "cn.duckdb"), full_import=request["mode"] not in {"auto", "incremental"})
    processed, source_rows = 0, prefilled['source_rows']
    importer.stats['records_imported'] = prefilled['written']
    found, skipped = set(), set()
    try:
        archive_wanted, archive_dates = wanted, {}
        existing_ranges, invalid_dates = {}, {}
        if not importer.full_import:
            existing_ranges = {symbol: (first, last) for symbol, first, last in importer.writer.conn.execute("""
                SELECT symbol,MIN(date),MAX(date) FROM stocks
                WHERE symbol IN (SELECT unnest(?::VARCHAR[])) GROUP BY symbol""",
                [sorted((wanted or available) | missing)]).fetchall()}
        if request["mode"] == "auto":
            invalid_dates = {symbol: days for symbol, days in importer.writer.conn.execute(f"""
                SELECT s.symbol,list(s.date) FROM stocks s
                WHERE s.symbol IN (SELECT unnest(?::VARCHAR[])) AND s.date BETWEEN ? AND ?
                AND ({INVALID_DAILY}) GROUP BY s.symbol""",
                [sorted((wanted or available) | missing), request["start_date"], request["end_date"]]).fetchall()}
        if request["mode"] == "auto" and calendar_complete:
            from simtradedata.web.etf_catalogue import daily_etf_gaps
            archive_symbols = (wanted or available) & available
            checking = time.perf_counter()
            archive_gaps = daily_etf_gaps(importer.writer.conn, sorted(archive_symbols),
                                         request['start_date'], request['end_date'], include_dates=True)
            listings = dict(importer.writer.conn.execute("""SELECT symbol,TRY_CAST(listed_date AS DATE)
                FROM stock_metadata WHERE symbol IN (SELECT unnest(?::VARCHAR[]))""",
                [sorted(archive_symbols)]).fetchall())
            archive_wanted = set(archive_symbols)
            for symbol in sorted(archive_symbols):
                first = existing_ranges.get(symbol, (None, None))[0]
                # Without a verified listing bound, earlier requested history
                # still requires inspecting the source; MIN(date) proves no completeness.
                if not listings.get(symbol) and (not first or request['start_date'] < str(first)):
                    continue
                days = set(archive_gaps.get(symbol, [])) | {str(day) for day in invalid_dates.get(symbol, [])}
                if not days:
                    archive_wanted.remove(symbol)
                    found.add(symbol)
                    skipped.add(symbol)
                else:
                    archive_dates[symbol] = {int(day.replace('-', '')) for day in days}
            timings['etf_check'] += time.perf_counter() - checking
            total -= len(skipped)
            emit(stage='write', message='已跳过完整证券，仅处理缺口和异常日期',
                 completed=0, total=total, skipped=len(skipped))
        for symbol, frame in source_frames():
            first_source = symbol not in found
            found.add(symbol)
            if symbol in archive_dates and not is_exchange_etf(symbol):
                source_dates = set(pd.to_datetime(frame['date']).dt.strftime('%Y%m%d').astype(int)) if not frame.empty else set()
                unresolved = sorted(archive_dates[symbol] - source_dates)
                if unresolved:
                    failures.append({'unit': symbol, 'detail': '历史包尚未包含待补齐日期，已有数据保留。',
                                     'missing_dates': [f'{day // 10000:04d}-{day // 100 % 100:02d}-{day % 100:02d}' for day in unresolved]})
            if not frame.empty:
                frame = timed_call("validation", validate_frame, symbol, frame)
                if not frame.empty:
                    source_rows += len(frame)
                    writing = time.perf_counter()
                    importer.import_stock(symbol, frame, existing_range=existing_ranges.get(symbol, (None, None)))
                    if request["mode"] == "auto":
                        repair = frame[frame["date"].isin(pd.to_datetime(invalid_dates.get(symbol, [])))]
                        if not repair.empty:
                            importer.stats["records_imported"] += importer.writer.write_market_data(symbol, repair.set_index("date"))
                            invalid_dates[symbol] = sorted(set(invalid_dates[symbol]) - set(repair['date'].dt.date))
                    timings["write"] += time.perf_counter() - writing
            processed += int(first_source)
            if processed == total or processed % 50 == 0 or wanted:
                emit(stage="write", message=f"处理 {symbol}", completed=processed, total=total,
                     written=importer.stats["records_imported"])
        if wanted and wanted - found:
            raise ValueError("历史包未包含这些证券：" + ", ".join(sorted(wanted - found)))
        if not source_rows and not skipped and not failures:
            raise ValueError("所选日期没有源记录，未填造停牌或缺失行情")
        total_time = time.perf_counter() - started
        timings['other'] = max(0, total_time - sum(timings.values()))
        timings["total"] = total_time
        print(f"Daily timings (seconds): {json.dumps(timings)}", flush=True)
        return {"written": importer.stats["records_imported"], "source_rows": source_rows, "skipped": len(skipped), "not_trading": not_trading, "failed_units": failures, "timings": timings}
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
    timings = {"download": 0.0, "parse": 0.0, "write": 0.0}
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
                emit(stage="write", message=f"{day} 已导入且缓存未变化，跳过", completed=index, total=plan["unit_count"], current_unit=None, overall_completed=index, overall_total=plan["unit_count"])
                continue
            if request["mode"] == "incremental" and (year, quarter) in completed and index <= len(plan["units"]) - 4:
                skipped += 1
                emit(stage="write", message=f"跳过已完成季度 {day}", completed=index, total=plan["unit_count"], current_unit=None, overall_completed=index, overall_total=plan["unit_count"])
                continue
            try:
                emit(stage="download", message=f"读取财务季度 {day}", completed=0, total=None, current_unit=day, overall_completed=index - 1, overall_total=plan["unit_count"])
                if request["mode"] == "reparse":
                    started = time.perf_counter()
                    frame = fetcher._convert_to_ptrade_format(fetcher.parse_local(filename))
                    timings["parse"] += time.perf_counter() - started
                elif request["mode"] == "auto" and cached.exists():
                    started = time.perf_counter()
                    try:
                        frame = fetcher._convert_to_ptrade_format(fetcher.parse_local(filename))
                        if frame.empty or "end_date" not in frame or set(pd.to_datetime(frame["end_date"]).dt.strftime("%Y-%m-%d")) != {day}:
                            raise ValueError("缓存报告期不匹配")
                    except Exception:
                        timings["parse"] += time.perf_counter() - started
                        frame = fetch_financial_with_progress(fetcher, filename, sizes.get(filename), timings)
                    else:
                        timings["parse"] += time.perf_counter() - started
                else:
                    frame = fetch_financial_with_progress(fetcher, filename, sizes.get(filename), timings)
                if frame.empty or "code" not in frame or set(pd.to_datetime(frame["end_date"]).dt.strftime("%Y-%m-%d")) != {day}:
                    raise ValueError("季度源为空或报告期不匹配")
                if "total_assets" not in frame or frame["total_assets"].isna().all():
                    raise ValueError("季度源缺少可用资产总额")
                started = time.perf_counter()
                writer.begin()
                count = 0
                try:
                    total_companies = frame["code"].nunique()
                    batch = {}
                    for company, (code, group) in enumerate(frame.groupby("code"), 1):
                        symbol = convert_to_ptrade_code(str(code), "qstock")
                        batch[symbol] = group.drop(columns=["code"]).set_index("end_date")
                        if len(batch) == 500 or company == total_companies:
                            count += writer.write_fundamentals_batch(batch)
                            batch.clear()
                            emit(stage="write", message=f"{day} 写入 {symbol}", completed=company, total=total_companies)
                    writer.mark_fundamental_quarter_completed(year, quarter, count, filename=filename,
                                                            file_hash="local:" + cache_signature(cached) + mapping)
                    writer.commit()
                except Exception:
                    writer.rollback()
                    raise
                finally:
                    timings["write"] += time.perf_counter() - started
                written += count
            except Exception as error:
                failures.append({"unit": day, "detail": str(error)})
                print(f"{day} 失败：{error}", flush=True)
            emit(stage="write", message=f"财务季度 {day} 已处理", completed=index, total=plan["unit_count"], written=written, current_unit=None, overall_completed=index, overall_total=plan["unit_count"])
        print(f"财务阶段耗时：下载 {timings['download']:.2f} 秒，解析 {timings['parse']:.2f} 秒，入库 {timings['write']:.2f} 秒", flush=True)
        return {"written": written, "skipped": skipped, "failed_units": failures, "timings": timings}
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
        for index, unit in enumerate(reversed(plan["units"]), 1):
            symbol, day = unit.split("|")
            emit(stage="download", message=f"获取 {symbol} · {day} 历史清单", completed=index - 1, total=plan["unit_count"], overall_completed=index - 1, overall_total=plan["unit_count"])
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
            emit(stage="write", message=f"处理 {symbol} · {day}", completed=index, total=plan["unit_count"], written=written, overall_completed=index, overall_total=plan["unit_count"])
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
        request = json.loads(request_file.read_text())
        if request.get('dataset') == 'export':
            from simtradedata.web.exports import export_bundle
            if set(request) != {'dataset','mode','market','start_date','end_date','symbols'} or request['mode'] != 'full':
                raise ValueError('导出参数不合法')
            result = export_bundle(data_dir, request['market'])
            emit(stage='export', message='回测数据包已生成', completed=7, total=7, result=result)
            return 0
        plan = build_plan(data_dir, request)
        action = {"daily": update_daily, "fundamentals": update_fundamentals, "pcf": update_pcf,
                  "calendar": update_calendar}[plan["request"]["dataset"]]
        result = action(data_dir, plan)
        emit(stage="verify", message="数据处理结束，正在核验结果；任务尚未完成", result=result)
        return 1 if result["failed_units"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
