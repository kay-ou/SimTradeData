"""Repair only invalid existing daily rows, retaining source gaps as unresolved."""

import math
import zipfile
from pathlib import Path

import duckdb
import pandas as pd

from simtradedata.web.completeness import INVALID_DAILY

FIELDS = ['open', 'high', 'low', 'close', 'volume', 'money']
PRICES = FIELDS[:4]


def daily_issue_rows(data_dir, request, conn=None):
    database = Path(data_dir) / 'cn.duckdb'
    if conn is None and not database.exists():
        return pd.DataFrame()
    owned = conn is None
    conn = conn or duckdb.connect(str(database), read_only=True)
    try:
        if 'stocks' not in {row[0] for row in conn.execute('SHOW TABLES').fetchall()}:
            return pd.DataFrame()
        where = 's.date BETWEEN ? AND ?'
        params = [request['start_date'], request['end_date']]
        if request['symbols']:
            where += ' AND s.symbol IN (' + ','.join('?' for _ in request['symbols']) + ')'
            params.extend(request['symbols'])
        return conn.execute(f"SELECT s.symbol,s.date,{','.join('s.' + field for field in FIELDS)} FROM stocks s WHERE {where} AND ({INVALID_DAILY}) ORDER BY s.symbol,s.date", params).fetchdf()
    finally:
        if owned:
            conn.close()


def invalid_fields(row):
    invalid = set()
    for field in FIELDS:
        value = row.get(field)
        if pd.isna(value) or not math.isfinite(float(value)) or (value <= 0 if field in PRICES else value < 0) or (field == 'volume' and not float(value).is_integer()):
            invalid.add(field)
    if not invalid.intersection(PRICES):
        if row['high'] < max(row[field] for field in ['open', 'close', 'low']) or row['low'] > min(row[field] for field in ['open', 'close', 'high']):
            invalid.update(['high', 'low'])
    return invalid


def source_rows(frame):
    if frame.empty or 'date' not in frame:
        return {}
    frame = frame.copy().rename(columns={'amount': 'money'})
    frame['date'] = pd.to_datetime(frame['date']).dt.date
    # Never choose an arbitrary duplicate source row.
    frame = frame[~frame['date'].duplicated(keep=False)]
    rows = {}
    for row in frame.to_dict('records'):
        # Zero trades are justified only by the source's explicit suspension flag.
        if row.get('tradestatus') == 0:
            for field in ['volume', 'money']:
                if pd.isna(row.get(field)):
                    row[field] = 0
        elif any(row.get(field) == 0 for field in ['volume', 'money']):
            continue
        if all(field in row for field in FIELDS) and not invalid_fields(row):
            rows[row['date']] = row
    return rows


def merge_repair(existing, candidate):
    invalid = invalid_fields(existing)
    for field in FIELDS:
        if field not in invalid and not math.isclose(float(existing[field]), float(candidate[field]), rel_tol=1e-4, abs_tol=1e-6):
            return None  # Do not mix differently adjusted prices and trade quantities.
    repaired = dict(existing)
    for field in invalid:
        repaired[field] = candidate[field]
    return repaired if not invalid_fields(repaired) else None


def repair_daily(data_dir, plan):
    from scripts.import_tdx_day import filename_to_ptrade_code, parse_tdx_day_file
    from simtradedata.fetchers.unified_fetcher import UnifiedDataFetcher
    from simtradedata.utils.code_utils import is_etf_code
    from simtradedata.writers.duckdb_writer import DuckDBWriter
    from simtradedata.web.operations import emit

    writer = DuckDBWriter(str(Path(data_dir) / 'cn.duckdb'))
    archive = None
    remote = None
    remote_error = None
    written = 0
    source_counts = {'cache': 0, 'baostock': 0}
    failures = []
    try:
        issues = daily_issue_rows(data_dir, plan['request'], writer.conn)
        groups = list(issues.groupby('symbol')) if not issues.empty else []
        package = Path(data_dir) / 'downloads' / 'hsjday.zip'
        filenames = {}
        if package.exists():
            try:
                archive = zipfile.ZipFile(package)
                for name in archive.namelist():
                    filename = name.replace('\\', '/').split('/')[-1]
                    if filename.endswith('.day'):
                        filenames[filename_to_ptrade_code(filename)] = name
            except (ValueError, zipfile.BadZipFile):
                if archive:
                    archive.close()
                archive = None
        for index, (symbol, group) in enumerate(groups, 1):
            emit(stage='write', message=f'定向修补 {symbol} 的 {len(group)} 条异常记录', completed=index - 1,
                 total=len(groups), overall_completed=index - 1, overall_total=len(groups))
            remaining = {row['date'].date(): row for row in group.to_dict('records')}
            patches = []
            def apply(candidates, source):
                for day in list(remaining):
                    if day not in candidates:
                        continue
                    merged = merge_repair(remaining[day], candidates[day])
                    if merged is not None:
                        patches.append(merged)
                        remaining.pop(day)
                        source_counts[source] += 1
            if archive and symbol in filenames:
                try:
                    frame = parse_tdx_day_file(archive.read(filenames[symbol]), price_divisor=1000 if is_etf_code(symbol) else 100)
                    apply(source_rows(frame), 'cache')
                except (ValueError, zipfile.BadZipFile):
                    pass  # Other existing source may still repair this symbol.
            detail = '现有来源没有匹配日期的有效数据，保留原记录待核验'
            if remaining:
                try:
                    if remote_error:
                        raise ConnectionError(remote_error)
                    if remote is None:
                        remote = UnifiedDataFetcher()
                        try:
                            remote.login()
                        except Exception as error:
                            remote_error = str(error)
                            raise
                    emit(stage='download', message=f'查询备用源 {symbol} 的异常日期范围', completed=index - 1, total=len(groups))
                    frame = remote.fetch_unified_daily_data(symbol, min(remaining).isoformat(), max(remaining).isoformat(), adjustflag='3')
                    apply(source_rows(frame), 'baostock')
                except Exception as error:
                    detail = f'备用源暂无法修补：{str(error)[:200]}'
            if patches:
                frame = pd.DataFrame(patches)
                frame['date'] = pd.to_datetime(frame['date']).dt.date
                writer.begin()
                try:
                    # Partial upsert preserves high/low limits, preclose and other stored fields.
                    writer._upsert_symbol_rows('stocks', frame[['symbol', 'date', *FIELDS]])
                    writer.commit()
                except duckdb.FatalException:
                    # A fatal engine error invalidates the connection. ROLLBACK
                    # would fail too and replace the useful original exception.
                    raise
                except Exception:
                    writer.rollback()
                    raise
                written += len(frame)
            if remaining:
                failures.append({'unit': symbol, 'detail': f'{len(remaining)} 条未修复；{detail}'})
            emit(stage='write', message=f'{symbol} 已修复 {len(patches)} 条，剩余 {len(remaining)} 条',
                 completed=index, total=len(groups), overall_completed=index, overall_total=len(groups))
        unresolved = len(daily_issue_rows(data_dir, plan['request'], writer.conn))
        return {'written': written, 'unresolved': unresolved, 'source_counts': source_counts, 'failed_units': failures}
    finally:
        if archive:
            archive.close()
        try:
            if remote:
                remote.logout()
        finally:
            writer.close()
