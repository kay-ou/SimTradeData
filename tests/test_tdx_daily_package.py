import io
import struct
import zipfile
import zlib

import duckdb
import pandas as pd
import pytest
import requests

from simtradedata.fetchers.tdx_daily_package import parse_daily_package
from simtradedata.web.operations import update_daily
from simtradedata.web.plans import build_plan
from simtradedata.writers.duckdb_writer import DuckDBWriter
from tests.ui_server import seed


def package(missing=None, invalid=False):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for market, code in [('sh','600000'),('sz','159919'),('bj','920001')]:
            if market == missing:
                continue
            record = bytearray(150)
            record[:6] = code.encode()
            struct.pack_into('<H', record, 32, 5 if invalid and market == 'sh' else 0)
            quote = bytearray(512)
            struct.pack_into('<5d', quote, 4, 9.8,10.,11.,9.,10.5)
            struct.pack_into('<Q', quote, 56, 1000)
            struct.pack_into('<d', quote, 72, 10500.)
            archive.writestr(f'{market}261120.cod', record)
            archive.writestr(f'{market}261120.md1', quote)
    return stream.getvalue()


def test_package_preserves_price_and_actual_volume_amount():
    frame = parse_daily_package(package(), '2026-11-20')
    assert frame['symbol'].tolist() == ['600000.SS','159919.SZ','920001.BJ']
    assert frame['volume'].tolist() == [1000,1000,1000]
    assert frame['money'].tolist() == [10500.,10500.,10500.]


@pytest.mark.parametrize('content', [package(missing='bj'), package(invalid=True), b'not a zip'])
def test_bad_package_fails_closed(content):
    with pytest.raises((ValueError, zipfile.BadZipFile)):
        parse_daily_package(content, '2026-11-20')


def prepare(tmp_path, monkeypatch):
    seed(tmp_path)
    with DuckDBWriter(str(tmp_path / 'cn.duckdb')) as writer:
        writer.conn.execute("INSERT INTO stocks(symbol,date,open,high,low,close,volume,money) VALUES ('600000.SS','2026-11-18',10,11,9,10.5,1000,10500)")
    with zipfile.ZipFile(tmp_path / 'downloads/hsjday.zip', 'w') as archive:
        archive.writestr('vipdoc/sh/lday/sh600000.day', b''.join(struct.pack('<IIIIIfII', day,1000,1100,900,1050,10500.,1000,0) for day in [20261117,20261118,20261119]))
    monkeypatch.setattr('simtradedata.web.etf_catalogue.load_etfs', lambda: ([], []))
    monkeypatch.setattr('scripts.download_tdx_day.get_remote_file_info', lambda *args: pytest.fail('A published daily package must avoid full-package checks'))
    request = {'dataset':'daily','mode':'auto','start_date':'2026-11-18','end_date':'2026-11-20','symbols':[]}
    return build_plan(tmp_path, request)


def test_incremental_day_updates_stock_and_etf_without_parsing_old_archive(tmp_path, monkeypatch):
    plan = prepare(tmp_path, monkeypatch)
    frame = parse_daily_package(package(), '2026-11-20')
    monkeypatch.setattr('simtradedata.web.daily_incremental.fetch_daily_package', lambda day: frame)
    monkeypatch.setattr('scripts.import_tdx_day.parse_tdx_day_file', lambda *args, **kwargs: pytest.fail('Complete old history must not be reparsed'))
    result = update_daily(tmp_path, plan)
    assert result['written'] == 2 and not result['failed_units']
    assert result['timings']['archive_parse'] == 0
    with duckdb.connect(str(tmp_path / 'cn.duckdb'), read_only=True) as conn:
        assert conn.execute("SELECT close,volume,money FROM stocks WHERE symbol='159919.SZ'").fetchone() == (10.5,1000,10500.)
        assert conn.execute("SELECT COUNT(*) FROM data_change_log WHERE date='2026-11-20'").fetchone()[0] == 2


@pytest.mark.parametrize("error", [requests.HTTPError("404"), zlib.error("corrupt member"), EOFError("truncated member")])
def test_unavailable_daily_package_retains_missing_day_and_cache(tmp_path, monkeypatch, error):
    plan = prepare(tmp_path, monkeypatch)
    monkeypatch.setattr('simtradedata.web.daily_incremental.fetch_daily_package', lambda day: (_ for _ in ()).throw(error))
    cache = tmp_path / 'downloads/hsjday.zip'
    monkeypatch.setattr('scripts.download_tdx_day.get_remote_file_info', lambda *args: {'size':cache.stat().st_size})
    monkeypatch.setattr('scripts.download_tdx_day.download_file', lambda *args, **kwargs: pytest.fail('Unchanged archive must not be downloaded'))
    result = update_daily(tmp_path, plan)
    assert result['written'] == 0
    assert result['failed_units'][0]['missing_dates'] == ['2026-11-20']
