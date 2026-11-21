"""Determine missing data from DuckDB rows and the local source, not task receipts."""

import zipfile
from datetime import date, timedelta

import duckdb
import numpy as np
import pandas as pd

INVALID_DAILY = (
    "s.open IS NULL OR s.high IS NULL OR s.low IS NULL OR s.close IS NULL OR s.volume IS NULL OR s.money IS NULL "
    "OR NOT isfinite(s.open) OR NOT isfinite(s.high) OR NOT isfinite(s.low) OR NOT isfinite(s.close) OR NOT isfinite(s.money) "
    "OR s.open<=0 OR s.high<=0 OR s.low<=0 OR s.close<=0 OR s.volume<0 OR s.money<0 OR s.high<s.low"
)


def calendar_covers(data_dir, start, end):
    database = data_dir / "cn.duckdb"
    if not database.exists():
        return False
    last = date.fromisoformat(end)
    while last.weekday() >= 5:
        last -= timedelta(days=1)
    first_day = date.fromisoformat(start)
    while first_day.weekday() >= 5:
        first_day += timedelta(days=1)
    if first_day > last:
        return True
    with duckdb.connect(str(database), read_only=True) as conn:
        if "exchange_calendar" not in {row[0] for row in conn.execute("SHOW TABLES").fetchall()}:
            return False
        count = conn.execute("SELECT COUNT(*) FROM exchange_calendar WHERE date BETWEEN ? AND ? AND is_trading_day IS NOT NULL", [start, end]).fetchone()[0]
        return count == (date.fromisoformat(end) - date.fromisoformat(start)).days + 1


def daily_pending(data_dir, request, coverage, gap=None):
    from scripts.import_tdx_day import filename_to_ptrade_code, is_stock_code

    package = data_dir / "downloads" / "hsjday.zip"
    database = data_dir / "cn.duckdb"
    if calendar_covers(data_dir, request["start_date"], request["end_date"]) and not coverage["calendar_dates"]:
        return False
    if not package.exists() or not database.exists():
        return True
    wanted = set(request["symbols"])
    start = int(request["start_date"].replace("-", ""))
    end = int(request["end_date"].replace("-", ""))
    target = date.fromisoformat(request["end_date"])
    while target.weekday() >= 5:
        target -= timedelta(days=1)
    if calendar_covers(data_dir, request["start_date"], request["end_date"]) and coverage["calendar_dates"]:
        target = date.fromisoformat(max(coverage["calendar_dates"]))
    target_int = int(target.strftime("%Y%m%d"))
    latest, found, expected_count = 0, set(), 0
    with duckdb.connect(str(database), read_only=True) as conn:
        if "stocks" not in {row[0] for row in conn.execute("SHOW TABLES").fetchall()}:
            return True
        with zipfile.ZipFile(package) as archive:
            for name in archive.namelist():
                normalized = name.replace("\\", "/")
                filename = normalized.split("/")[-1]
                if "/lday/" not in normalized or not filename.endswith(".day") or not is_stock_code(filename):
                    continue
                symbol = filename_to_ptrade_code(filename)
                content = archive.read(name)
                if not content or len(content) % 32:
                    return True
                dates = np.frombuffer(content, dtype='<u4').reshape(-1, 8)[:, 0]
                latest = max(latest, int(dates.max()))
                if wanted and symbol not in wanted:
                    continue
                found.add(symbol)
                dates = dates[(dates >= start) & (dates <= end)]
                if not len(dates):
                    continue
                expected = pd.DataFrame({"date": pd.to_datetime(dates.astype(str), format='%Y%m%d', errors='coerce')}).dropna()
                expected_count += len(expected)
                conn.register("expected_days", expected)
                missing = conn.execute(
                    ("SELECT MIN(e.date), MAX(e.date)" if gap is not None else "SELECT 1") +
                    " FROM expected_days e LEFT JOIN stocks s ON s.symbol=? AND s.date=e.date::DATE " +
                    f"WHERE s.symbol IS NULL OR {INVALID_DAILY}" + ("" if gap is not None else " LIMIT 1"), [symbol],
                ).fetchone()
                conn.unregister("expected_days")
                if missing and (gap is None or missing[0] is not None):
                    if gap is None:
                        return True
                    first, last = [pd.Timestamp(value).strftime('%Y-%m-%d') for value in missing]
                    gap["start_date"] = min(gap.get("start_date", first), first)
                    gap["end_date"] = max(gap.get("end_date", last), last)
    stale = latest < target_int
    if gap is not None and stale and latest:
        first = (pd.Timestamp(str(latest)) + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
        gap["start_date"] = min(gap.get("start_date", first), first)
        gap["end_date"] = request["end_date"]
    return bool(gap) or stale or bool(wanted - found) or (expected_count == 0 and bool(coverage["calendar_dates"]))


def daily_database_pending(data_dir, request, coverage, gap=None):
    """Fast DB-only check; does not assert source-level per-security completeness."""
    item = next(row for row in coverage['datasets'] if row['key'] == 'daily')
    database = data_dir / 'cn.duckdb'
    if not item['records'] or not database.exists():
        return bool(coverage['calendar_dates']) or not calendar_covers(data_dir, request['start_date'], request['end_date'])
    missing = list(item['missing_dates'])
    target = date.fromisoformat(request['end_date'])
    while target.weekday() >= 5:
        target -= timedelta(days=1)
    with duckdb.connect(str(database), read_only=True) as conn:
        tables = {row[0] for row in conn.execute('SHOW TABLES').fetchall()}
        if 'exchange_calendar' in tables:
            flag = conn.execute('SELECT is_trading_day FROM exchange_calendar WHERE date=?', [request['end_date']]).fetchone()
            if flag and flag[0] is False:
                last = conn.execute('SELECT MAX(date) FROM exchange_calendar WHERE date<=? AND is_trading_day', [request['end_date']]).fetchone()[0]
                if last:
                    target = last
        where = 's.date BETWEEN ? AND ?'
        params = [request['start_date'], request['end_date']]
        if request['symbols']:
            where += ' AND s.symbol IN (' + ','.join('?' for _ in request['symbols']) + ')'
            params.extend(request['symbols'])
        invalid = conn.execute(f'SELECT COUNT(*), MIN(s.date), MAX(s.date) FROM stocks s WHERE {where} AND ({INVALID_DAILY})', params).fetchone()
    item['invalid_records'] = invalid[0]
    item['invalid_start_date'] = str(invalid[1]) if invalid[1] else None
    item['invalid_end_date'] = str(invalid[2]) if invalid[2] else None
    # Field quality is a separate diagnostic; the current source may omit these rows.
    # Do not turn it into an endless automatic download request.
    if item['last_date'] < target.isoformat():
        missing.extend([(date.fromisoformat(item['last_date']) + timedelta(days=1)).isoformat(), target.isoformat()])
    if missing and gap is not None:
        gap.update(start_date=min(missing), end_date=max(missing))
    return bool(missing)
