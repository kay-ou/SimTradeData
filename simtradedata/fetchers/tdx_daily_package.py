"""Official TDX per-session .cod/.md1 packages, independent of TCP quotes.

Endpoint reference: simonlin1212/a-stock-data, Apache-2.0.
Binary layout reference: jing2uo/tdx2db, MIT.
"""

import io
import re
import struct
import zipfile

import numpy as np
import pandas as pd
import requests


URL = 'https://www.tdx.com.cn/products/data/data/g4day/{day}.zip'


def parse_daily_package(content, day):
    stamp = pd.Timestamp(day)
    compact = stamp.strftime('%Y%m%d')
    rows = []
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for market in ['sh', 'sz', 'bj']:
            code_name, quote_name = f'{market}{compact[2:]}.cod', f'{market}{compact[2:]}.md1'
            names = set(archive.namelist())
            if market == 'bj' and compact < '20220506' and code_name not in names and quote_name not in names:
                continue
            if code_name not in names or quote_name not in names:
                raise ValueError(f'{day}: daily package missing {market} files')
            codes, quotes = archive.read(code_name), archive.read(quote_name)
            if len(codes) % 150 or len(quotes) % 512 or len(codes) // 150 != len(quotes) // 512:
                raise ValueError(f'{day}: invalid {market} record lengths')
            seen, positions = set(), set()
            before = len(rows)
            for offset in range(0, len(codes), 150):
                record = codes[offset:offset + 150]
                code = record[:6].rstrip(b'\x00 ').decode('ascii')
                position = struct.unpack_from('<H', record, 32)[0]
                if not re.fullmatch(r'\d{6}', code) or code in seen or position in positions:
                    raise ValueError(f'{day}: duplicate or invalid {market} code/position')
                seen.add(code)
                positions.add(position)
                block = quotes[position * 512:(position + 1) * 512]
                if len(block) != 512:
                    raise ValueError(f'{day}: {market}{code} quote position out of range')
                preclose, open_, high, low, close = struct.unpack_from('<5d', block, 4)
                money = struct.unpack_from('<d', block, 72)[0]
                volume = struct.unpack_from('<Q', block, 56)[0]
                if not np.isfinite([preclose, open_, high, low, close, money]).all():
                    raise ValueError(f'{day}: non-finite quote in {market}{code}')
                if close <= 0:
                    continue
                rows.append({'symbol': code + '.' + {'sh':'SS','sz':'SZ','bj':'BJ'}[market],
                             'date': stamp.normalize(), 'open': open_, 'high': high, 'low': low,
                             'close': close, 'preclose': preclose, 'volume': volume, 'money': money})
            if len(rows) == before:
                raise ValueError(f'{day}: {market} has no priced records')
    frame = pd.DataFrame(rows)
    frame.attrs['source_url'] = URL.format(day=compact)
    return frame


def fetch_daily_package(day):
    url = URL.format(day=pd.Timestamp(day).strftime('%Y%m%d'))
    response = requests.get(url, timeout=(5,30), headers={'User-Agent':'SimTradeData/1.2'})
    response.raise_for_status()
    return parse_daily_package(response.content, day)
