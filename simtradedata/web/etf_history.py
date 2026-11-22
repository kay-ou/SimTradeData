"""Unadjusted ETF daily fallback, using Eastmoney's documented AkShare adapter format."""

import pandas as pd
import requests

from simtradedata.web.daily_categories import is_exchange_etf
from simtradedata.resilience.retry import RetryConfig, retry


@retry(config=RetryConfig(max_retries=2, base_delay=.5))
def fetch_etf_daily_fallback(symbol, start, end):
    if not is_exchange_etf(symbol):
        raise ValueError('备用ETF日线源只接受ETF交易代码')
    code, market = symbol.split('.')
    market_id = 0 if market == 'SZ' else 1
    response = requests.get('https://push2his.eastmoney.com/api/qt/stock/kline/get', params={
        'fields1': 'f1,f2,f3,f4,f5,f6',
        'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
        'ut': '7eea3edcaed734bea9cbfc24409ed989', 'klt': '101', 'fqt': '0',
        'beg': start.replace('-', ''), 'end': end.replace('-', ''), 'secid': f'{market_id}.{code}',
    }, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/'}, timeout=(10, 20))
    response.raise_for_status()
    data = response.json().get('data')
    if not data or not data.get('klines'):
        return pd.DataFrame()
    if str(data.get('code')) != code or data.get('market') != market_id:
        raise ValueError('备用ETF源返回的证券或市场不匹配')
    rows = [row.split(',') for row in data['klines']]
    frame = pd.DataFrame(rows, columns=['date','open','close','high','low','volume','money',
                                       'amplitude','change_pct','change','turnover'])
    frame['date'] = pd.to_datetime(frame['date'], errors='raise').dt.normalize()
    if frame['date'].duplicated().any():
        raise ValueError('备用ETF日线存在重复日期')
    for field in ['open','close','high','low','volume','money']:
        frame[field] = pd.to_numeric(frame[field], errors='raise')
    frame['volume'] *= 100  # Provider volume is 100-unit lots; amount is already yuan.
    return frame[frame['date'].between(start, end)].sort_values('date')[
        ['date','open','high','low','close','volume','money']].reset_index(drop=True)


@retry(config=RetryConfig(max_retries=2, base_delay=.5))
def fetch_etf_daily_tencent(symbol, start, end):
    """Unadjusted Tencent newfqkline data, with actual volume and amount.

    Format and units: AKShare stock/quote/stock_hist_tx.py and
    index/index_stock_zh.py. Two-year requests stay below the 640-bar limit.
    """
    if not is_exchange_etf(symbol):
        raise ValueError('Tencent ETF history requires an ETF trading code')
    code, market = symbol.split('.')
    ticker = ('sz' if market == 'SZ' else 'sh') + code
    begin, finish = pd.Timestamp(start), pd.Timestamp(end)
    rows = []
    for year in range(begin.year, finish.year + 1, 2):
        chunk_start = max(begin, pd.Timestamp(year=year, month=1, day=1))
        stop = min(finish, pd.Timestamp(year=year + 1, month=12, day=31))
        response = requests.get('https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get',
                                params={'param': f'{ticker},day,{year}-01-01,{stop:%Y-%m-%d},640,'},
                                timeout=(10, 20))
        response.raise_for_status()
        payload = response.json()
        if payload.get('code') != 0 or not isinstance(payload.get('data'), dict):
            raise ValueError('Invalid Tencent ETF history response')
        data = payload['data']
        if data and ticker not in data:
            raise ValueError('Tencent ETF history returned a different security')
        bars = data.get(ticker, {}).get('day', [])
        for bar in bars:
            if len(bar) < 9:
                raise ValueError('Tencent ETF history lacks traded amount')
            if chunk_start <= pd.Timestamp(bar[0]) <= stop:
                rows.append([bar[i] for i in [0, 1, 3, 4, 2, 5, 8]])
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows, columns=['date','open','high','low','close','volume','money'])
    frame['date'] = pd.to_datetime(frame['date'], errors='raise').dt.normalize()
    frame = frame[frame['date'].between(begin, finish)].copy()
    if frame['date'].duplicated().any():
        raise ValueError('Tencent ETF history contains duplicate dates')
    for field in ['open','high','low','close','volume','money']:
        frame[field] = pd.to_numeric(frame[field], errors='raise')
    frame['volume'] *= 100  # ETF volume is in lots.
    frame['money'] *= 10000  # Tencent amount is in ten-thousand yuan.
    return frame.sort_values('date').reset_index(drop=True)
