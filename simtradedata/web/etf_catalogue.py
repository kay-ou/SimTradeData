"""Current ETF selection catalogue; capabilities are not historical coverage claims."""

import re
import threading
import time
from datetime import datetime, timezone
from simtradedata.web.daily_categories import NON_TRADING_ETF_CODES
from pathlib import Path

import duckdb
import requests
from datetime import date
from simtradedata.resilience.retry import RetryConfig, retry

SSE_LIST_URL = 'https://query.sse.com.cn/commonQuery.do'


@retry(config=RetryConfig(max_retries=2, base_delay=.5))
def sse_etfs(session):
    items, seen = [], set()
    page, total = 1, None
    while True:
        response = session.get(SSE_LIST_URL, params={
            'sqlId': 'COMMON_SSE_PL_ETFGGSGSHQD_L', 'isPagination': 'true',
            'pageHelp.pageSize': 1000, 'pageHelp.pageNo': page,
            'pageHelp.beginPage': page, 'pageHelp.endPage': page, 'pageHelp.cacheSize': 1,
        }, headers={'Referer': 'https://www.sse.com.cn/', 'User-Agent': 'Mozilla/5.0'}, timeout=(15, 20))
        response.raise_for_status()
        data = response.json()
        metadata, rows = data.get('pageHelp', {}), data.get('result')
        if not isinstance(rows, list) or not rows or int(metadata.get('pageNo', 0)) != page:
            raise ValueError('上交所ETF目录为空或分页格式改变')
        pages, count = int(metadata.get('pageCount', 0)), int(metadata.get('total', 0))
        if not 1 <= pages <= 100 or count <= 0 or (total is not None and total != count):
            raise ValueError('上交所ETF目录分页统计不一致')
        total = count
        for row in rows:
            code, name = str(row.get('FUNDID2', '')), row.get('ETF_FULLNAME')
            if not re.fullmatch(r'5\d{5}', code) or not isinstance(name, str) or not name or code in seen:
                raise ValueError('上交所ETF目录代码、名称或分页重复')
            seen.add(code)
            items.append({'symbol': code + '.SS', 'name': name, 'short_name': name, 'market': 'SS', 'source': '上交所官方目录'})
        if page == pages:
            if len(items) != total:
                raise ValueError('上交所ETF目录未完整读取')
            return items
        page += 1


def load_etfs():
    from simtradedata.fetchers.mootdx_fetcher import MootdxFetcher
    rows, warnings, names = [], [], {}
    try:
        with MootdxFetcher(timeout=10) as fetcher:
            securities = fetcher.fetch_stock_list()
        if securities.empty or not {'code', 'name', 'market', 'pre_close'} <= set(securities.columns):
            raise ValueError('证券目录为空或缺少字段')
        for security in securities.to_dict('records'):
            code = str(security['code']).strip('\x00 ')
            market = 'SZ' if security['market'] == 0 else 'SS'
            symbol = code + '.' + market
            names[symbol] = str(security['name']).strip('\x00 ')
            # Current Shenzhen ETF allocations; 150-series structured funds are not ETFs.
            if market == 'SZ' and re.fullmatch(r'15[89]\d{3}', code) and symbol not in NON_TRADING_ETF_CODES and security['pre_close'] > 0:
                rows.append({'symbol': symbol, 'name': names[symbol], 'short_name': names[symbol], 'market': market, 'source': '通达信深市ETF行情目录'})
        if not rows:
            raise ValueError('深市ETF行情目录未返回有效证券')
    except Exception as error:
        warnings.append(f'深市ETF目录暂不可用：{str(error)[:160]}')
    try:
        with requests.Session() as session:
            shanghai = sse_etfs(session)
        for row in shanghai:
            row['short_name'] = names.get(row['symbol'], row['name'])
        rows.extend(shanghai)
    except Exception as error:
        warnings.append(f'沪市ETF目录暂不可用：{str(error)[:160]}')
    if not rows:
        raise RuntimeError('；'.join(warnings))
    for row in rows:
        row['daily_supported'] = True
        row['history_pcf_supported'] = row['market'] == 'SZ'
        row['pcf_support'] = '历史可按日期查询，连续覆盖待核验' if row['market'] == 'SZ' else '目前仅最新PCF，历史尚未接入'
    return sorted(rows, key=lambda row: row['symbol']), warnings


def local_ranges(data_dir, rows):
    database = Path(data_dir) / 'cn.duckdb'
    ranges, busy = {}, False
    if database.exists() and rows:
        try:
            with duckdb.connect(str(database), read_only=True) as conn:
                if 'stocks' in {row[0] for row in conn.execute('SHOW TABLES').fetchall()}:
                    symbols = [row['symbol'] for row in rows]
                    ranges = {symbol: {'records': count, 'first_date': str(first), 'last_date': str(last)}
                              for symbol, count, first, last in conn.execute('SELECT symbol,COUNT(*),MIN(date),MAX(date) FROM stocks WHERE symbol IN (' + ','.join('?' for _ in symbols) + ') GROUP BY symbol', symbols).fetchall()}
        except duckdb.Error:
            busy = True
    return [{**row, 'local_daily': ranges.get(row['symbol']), 'local_busy': busy} for row in rows]


class EtfCatalogue:
    def __init__(self, data_dir, loader=None):
        self.data_dir = data_dir
        self.loader = loader or load_etfs
        self.lock = threading.RLock()
        self.rows = []
        self.not_trading = {}
        self.warnings = []
        self.error = None
        self.running = False
        self.checked_at = None
        self.loaded = 0.0
        self.retry_after = 0.0

    def get(self, refresh=False):
        with self.lock:
            self.not_trading = {symbol: info for symbol, info in self.not_trading.items()
                                if not (info.get('listed_date') or info.get('issuance_date')) or (info.get('listed_date') or info['issuance_date']) > date.today().isoformat()}
            if not self.running and (refresh or (not self.loaded and not self.error) or (self.loaded and time.monotonic() - self.loaded > 3600 and time.monotonic() >= self.retry_after)):
                if refresh or self.loaded:
                    self.not_trading.clear()  # Recheck old evidence, preserving facts learned before the first load.
                self.running = True
                threading.Thread(target=self._load, daemon=True).start()
            running = self.running
            result = {'running': running, 'checked_at': self.checked_at, 'warnings': list(self.warnings), 'error': self.error}
            rows = [{**row, **({'listing_info': self.not_trading[row['symbol']]}
                    if row['symbol'] in self.not_trading else {})} for row in self.rows]
        result['etfs'] = local_ranges(self.data_dir, rows)
        result['total'] = len(rows)
        return result

    def mark_not_trading(self, items):
        with self.lock:
            self.not_trading.update({item['symbol']: item for item in items
                                     if item['state'] in {'pre_listing', 'not_established'}})

    def _load(self):
        try:
            rows, warnings = self.loader()
            if not rows:
                raise ValueError("ETF目录为空，无法提供可选基金")
            with self.lock:
                self.rows, self.warnings, self.error = rows, warnings, None
                self.loaded = time.monotonic()
                self.retry_after = 0.0
                self.checked_at = datetime.now(timezone.utc).isoformat()
        except Exception as error:
            with self.lock:
                self.error = str(error)
                self.retry_after = time.monotonic() + 60
        finally:
            with self.lock:
                self.running = False


def daily_etf_gaps(conn, symbols, start, end, include_dates=False):
    """Actual missing dates, excluding known holidays, HALT and delisting."""
    if not symbols:
        return {}
    tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
    from simtradedata.web.coverage import trading_dates
    days = trading_dates(conn, start, end)
    metadata_join, metadata_filter = '', ''
    lower_bound = 'NULL::DATE'
    if 'stock_metadata' in tables:
        columns = {row[0] for row in conn.execute('DESCRIBE stock_metadata').fetchall()}
        if {'listed_date', 'de_listed_date'} & columns:
            metadata_join = "LEFT JOIN stock_metadata m ON m.symbol=w.symbol"
        if 'listed_date' in columns:
            lower_bound = 'TRY_CAST(m.listed_date AS DATE)'
            metadata_filter += "AND (TRY_CAST(m.listed_date AS DATE) IS NULL OR t.date>=TRY_CAST(m.listed_date AS DATE)) "
        if 'de_listed_date' in columns:
            metadata_filter += "AND (TRY_CAST(m.de_listed_date AS DATE) IS NULL OR t.date<TRY_CAST(m.de_listed_date AS DATE))"
    halt_filter = """AND NOT EXISTS (SELECT 1 FROM stock_status h
        WHERE h.status_type='HALT'
        AND coalesce(TRY_CAST(h.date AS DATE),try_strptime(h.date,'%Y%m%d')::DATE)=e.date
        AND json_contains(TRY_CAST(h.symbols AS JSON),to_json(e.symbol)))""" if 'stock_status' in tables else ''
    selection = "list(e.date ORDER BY e.date)" if include_dates else "MIN(e.date),MAX(e.date)"
    rows = conn.execute(f"""WITH wanted AS (SELECT unnest(?::VARCHAR[]) AS symbol),
        expected AS (SELECT w.symbol,t.date FROM wanted w
                     {metadata_join}
                     JOIN (SELECT unnest(?::DATE[]) AS date) t ON t.date BETWEEN ? AND ?
                     AND ({lower_bound} IS NULL OR t.date>={lower_bound})
                     WHERE TRUE {metadata_filter})
        SELECT e.symbol,{selection} FROM expected e
        LEFT JOIN stocks s ON s.symbol=e.symbol AND s.date=e.date
        WHERE s.symbol IS NULL {halt_filter} GROUP BY e.symbol""", [symbols, days, start, end]).fetchall()
    if include_dates:
        return {symbol: [str(day) for day in days] for symbol, days in rows}
    return {symbol: (str(first), str(last)) for symbol, first, last in rows}
