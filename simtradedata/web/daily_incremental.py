"""Use small official day packages only beyond an existing historical cache."""

import time

import pandas as pd
import numpy as np
import duckdb
import requests
import zipfile
import zlib

from simtradedata.fetchers.tdx_daily_package import fetch_daily_package
from simtradedata.web.daily_categories import STOCK_SYMBOL_PATTERN, is_exchange_etf
from simtradedata.writers.duckdb_writer import DuckDBWriter


def prefill_sessions(data_dir, plan, cache_end, timings):
    from simtradedata.web.operations import emit
    request = plan['request']
    daily = next((row for row in plan['before'].get('datasets', []) if row['key'] == 'daily'), {})
    stocks = next((row for row in daily.get('categories', []) if row['key'] == 'stock_daily'), {})
    if request['symbols'] or not stocks.get('records'):
        return {'days': [], 'written': 0, 'source_rows': 0}
    lower = max(cache_end, (pd.Timestamp(request['end_date']) - pd.Timedelta(days=31)).strftime('%Y-%m-%d'))
    days = [day for day in stocks.get('missing_dates', []) if lower < day <= request['end_date']]
    if not days or len(days) > 10:
        return {'days': [], 'written': 0, 'source_rows': 0}
    result = {'days': [], 'requested_days': days, 'written': 0, 'source_rows': 0}
    with DuckDBWriter(str(data_dir / 'cn.duckdb')) as writer:
        for index, day in enumerate(days):
            emit(stage='download', message=f'下载通达信每日增量包 {day}', completed=index, total=len(days))
            try:
                started = time.perf_counter()
                try:
                    frame = fetch_daily_package(day)
                finally:
                    timings['incremental_request'] += time.perf_counter() - started
                selected = frame['symbol'].str.endswith(('.SS','.SZ')) & (
                    frame['symbol'].str.fullmatch(STOCK_SYMBOL_PATTERN) | frame['symbol'].map(is_exchange_etf) | (frame['symbol'] == '000300.SS'))
                frame = frame[selected & (frame['volume'] > 0) & (frame['money'] > 0)].copy()
                values = frame[['open','high','low','close','preclose','volume','money']]
                if frame.empty or not np.isfinite(values).all().all() or (values[['open','high','low','close']] <= 0).any().any():
                    raise ValueError('每日增量包缺少有效交易行情')
                if (values['high'] < values[['open','close','low']].max(axis=1)).any() or (values['low'] > values[['open','close','high']].min(axis=1)).any():
                    raise ValueError('每日增量包高低价不一致')
                securities = frame['symbol'] != '000300.SS'
                average = frame.loc[securities,'money'] / frame.loc[securities,'volume']
                if not average.between(frame.loc[securities,'low'] * .99, frame.loc[securities,'high'] * 1.01).all():
                    raise ValueError('每日增量包成交量、成交额与价格单位不一致')
                if not frame['date'].eq(pd.Timestamp(day)).all():
                    raise ValueError('每日增量包日期不匹配')
                started = time.perf_counter()
                writer.begin()
                try:
                    count = writer.insert_market_batch(frame)
                    writer.commit()
                except duckdb.FatalException:
                    raise
                except Exception:
                    writer.rollback()
                    raise
                finally:
                    timings['write'] += time.perf_counter() - started
                result['days'].append(day)
                result['written'] += count
                result['source_rows'] += len(frame)
            except (requests.RequestException, ValueError, zipfile.BadZipFile, zlib.error, EOFError) as error:
                print(f'TDX daily package {day}: {error}; retaining historical-package fallback', flush=True)
    return result
