import threading
import time
from datetime import date
from unittest.mock import Mock

import pandas as pd
import pytest

from simtradedata.web.etf_catalogue import EtfCatalogue, local_ranges, sse_etfs
from simtradedata.writers.duckdb_writer import DuckDBWriter


@pytest.fixture(autouse=True)
def offline_etf_fallback(monkeypatch):
    monkeypatch.setattr('simtradedata.web.etf_listing.fetch_etf_listing_info',lambda *args:None)
    monkeypatch.setattr('simtradedata.web.etf_history.fetch_etf_daily_tencent', lambda *args: pd.DataFrame())
    monkeypatch.setattr('simtradedata.web.etf_history.fetch_etf_daily_fallback', lambda *args: pd.DataFrame())


def item(symbol='159919.SZ'):
    return {'symbol': symbol, 'name': '沪深300ETF', 'short_name': '300ETF', 'market': symbol[-2:], 'history_pcf_supported': symbol.endswith('.SZ')}


def wait_result(catalogue):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        result = catalogue.get()
        if not result['running']:
            return result
        time.sleep(.01)
    pytest.fail('catalogue did not finish')


def test_catalogue_is_cached_in_memory_and_read_only(tmp_path):
    calls = []
    catalogue = EtfCatalogue(tmp_path, loader=lambda: (calls.append(1) or [item()], []))
    result = wait_result(catalogue)
    assert result['total'] == 1 and result['etfs'][0]['local_daily'] is None
    assert not list(tmp_path.iterdir())
    catalogue.get()
    assert calls == [1]
    catalogue.get(refresh=True)
    wait_result(catalogue)
    assert calls == [1, 1]


def test_refresh_cannot_spawn_overlapping_loaders(tmp_path):
    release, entered = threading.Event(), threading.Event()
    calls = []
    def load():
        calls.append(1); entered.set(); release.wait(2)
        return [item()], []
    catalogue = EtfCatalogue(tmp_path, loader=load)
    catalogue.get()
    assert entered.wait(1)
    catalogue.get(refresh=True)
    assert calls == [1]
    release.set()
    assert wait_result(catalogue)['total'] == 1


def test_failure_does_not_fake_single_sample_catalogue(tmp_path):
    def fail(): raise ConnectionError('source offline')
    result = wait_result(EtfCatalogue(tmp_path, loader=fail))
    assert result['total'] == 0 and result['etfs'] == []
    assert 'offline' in result['error']


def test_partial_catalogue_reports_missing_source(tmp_path):
    result = wait_result(EtfCatalogue(tmp_path, loader=lambda: ([item()], ['沪市目录不可用'])))
    assert result['warnings'] == ['沪市目录不可用']


def page(number, code, total=2):
    return {'pageHelp': {'pageNo': number, 'pageCount': 2, 'total': total}, 'result': [{'FUNDID2': code, 'ETF_FULLNAME': '测试ETF'}]}


def test_sse_reads_all_pages():
    session = Mock()
    session.get.side_effect = [Mock(json=lambda: page(1, '510300')), Mock(json=lambda: page(2, '510500'))]
    result = sse_etfs(session)
    assert [row['symbol'] for row in result] == ['510300.SS', '510500.SS']
    assert session.get.call_args.kwargs['params']['pageHelp.pageNo'] == 2


@pytest.mark.parametrize('second', [page(1, '510500'), page(2, '510300'), page(2, '510500', 3)])
def test_wrong_or_duplicate_page_is_not_reported_complete(second):
    session = Mock()
    session.get.side_effect = [Mock(json=lambda: page(1, '510300')), Mock(json=lambda: second)]
    with pytest.raises(ValueError):
        sse_etfs(session)


def test_local_dates_are_read_from_database(tmp_path):
    with DuckDBWriter(str(tmp_path / 'cn.duckdb')) as writer:
        writer.conn.execute("INSERT INTO stocks(symbol,date,close) VALUES ('159919.SZ','2020-01-02',4),('159919.SZ','2026-11-20',5)")
    result = local_ranges(tmp_path, [item()])
    assert result[0]['local_daily'] == {'records': 2, 'first_date': '2020-01-02', 'last_date': '2026-11-20'}


def test_daily_selected_etf_range_does_not_start_from_other_stocks(tmp_path, monkeypatch):
    from simtradedata.web import recommendations
    class Clock(date):
        @classmethod
        def today(cls): return cls(2026, 11, 21)
    monkeypatch.setattr(recommendations, 'date', Clock)
    with DuckDBWriter(str(tmp_path / 'cn.duckdb')) as writer:
        writer.conn.execute("INSERT INTO stocks(symbol,date,open,high,low,close,volume,money) VALUES ('600000.SS','2015-01-05',10,11,9,10,100,1000),('159919.SZ','2026-11-20',1,1,1,1,100,100)")
    result = recommendations.recommend(tmp_path, 'daily', ['159919.SZ'])
    assert result['scope_start'] == '2026-11-20'
    assert not result['work_pending']


def test_sources_include_shanghai_prices_and_exclude_shenzhen_structured_funds(monkeypatch):
    from simtradedata.web import etf_catalogue
    from simtradedata.fetchers.mootdx_fetcher import MootdxFetcher
    fetcher = Mock()
    fetcher.__enter__ = Mock(return_value=fetcher)
    fetcher.__exit__ = Mock(return_value=False)
    fetcher.fetch_stock_list.return_value = pd.DataFrame([
        {'code':'159919','name':'嘉实300','market':0,'pre_close':4.},
        {'code':'158000','name':'港股金融','market':0,'pre_close':1.},
        {'code':'150001','name':'分级基金','market':0,'pre_close':1.},
        {'code':'159900','name':'现金项','market':0,'pre_close':1.},
        {'code':'510300','name':'300ETF','market':1,'pre_close':4.},
    ])
    monkeypatch.setattr('simtradedata.fetchers.mootdx_fetcher.MootdxFetcher', lambda **kwargs: fetcher)
    monkeypatch.setattr(etf_catalogue, 'sse_etfs', lambda _: [{'symbol':'510300.SS','name':'沪深300交易型开放式指数基金','short_name':'full','market':'SS','source':'上交所官方目录'}])
    rows,warnings = etf_catalogue.load_etfs()
    assert warnings == []
    assert [row['symbol'] for row in rows] == ['158000.SZ','159919.SZ','510300.SS']
    assert all(row['daily_supported'] for row in rows)
    assert rows[-1]['history_pcf_supported'] is False
    assert rows[-1]['short_name'] == '300ETF'


@pytest.mark.parametrize('symbol', ['530000.SS','550000.SS'])
def test_new_official_etf_prefixes_are_not_excluded_from_daily_import(symbol):
    from simtradedata.utils.code_utils import is_etf_code, get_security_type
    from scripts.import_tdx_day import is_stock_code
    assert is_etf_code(symbol)
    assert get_security_type(symbol) == 'etf'
    assert is_stock_code('sh' + symbol[:6] + '.day')


@pytest.mark.parametrize('symbols', [['551000.SS'], ['600000.SS', '551000.SS']])
def test_selected_daily_missing_from_zip_uses_mootdx_without_rescaling(tmp_path, monkeypatch, symbols):
    from tests.ui_server import seed
    from tests.test_webadmin import request
    from scripts import download_tdx_day
    from simtradedata.fetchers.mootdx_fetcher import MootdxFetcher
    from simtradedata.web.operations import update_daily
    from simtradedata.web.plans import build_plan

    seed(tmp_path)
    monkeypatch.setattr(download_tdx_day, 'get_remote_file_info', lambda *a: pytest.fail('cache covers dates'))
    calls = []
    monkeypatch.setattr(MootdxFetcher, '__enter__', lambda self: self)
    monkeypatch.setattr(MootdxFetcher, '__exit__', lambda *a: None)
    def fetch(self, symbol, start, end):
        calls.append(symbol)
        return pd.DataFrame({'date': pd.to_datetime(['2026-11-19', '2026-11-20']),
                             'open': [102., 102.], 'high': [103., 103.], 'low': [101., 101.],
                             'close': [102.037, 102.059], 'volume': [100., 200.], 'amount': [10203.7, 20411.8]})
    monkeypatch.setattr(MootdxFetcher, 'fetch_daily_bars', fetch)
    plan = build_plan(tmp_path, request(mode='auto', symbols=symbols))
    assert update_daily(tmp_path, plan)['written'] >= 2
    assert calls == ['551000.SS']
    with DuckDBWriter(str(tmp_path / 'cn.duckdb')) as writer:
        assert writer.conn.execute("SELECT close,money FROM stocks WHERE symbol='551000.SS' ORDER BY date").fetchall() == [(102.037, 10203.7), (102.059, 20411.8)]
        writer.conn.execute("UPDATE stocks SET close=102.5 WHERE symbol='551000.SS'")
    update_daily(tmp_path, plan)
    assert calls == ['551000.SS']
    with DuckDBWriter(str(tmp_path / 'cn.duckdb')) as writer:
        assert writer.conn.execute("SELECT DISTINCT close FROM stocks WHERE symbol='551000.SS'").fetchall() == [(102.5,)]


def test_offline_reparse_missing_etf_never_calls_network(tmp_path, monkeypatch):
    from tests.ui_server import seed
    from tests.test_webadmin import request
    from simtradedata.fetchers.mootdx_fetcher import MootdxFetcher
    from simtradedata.web.operations import update_daily
    from simtradedata.web.plans import build_plan
    seed(tmp_path)
    monkeypatch.setattr(MootdxFetcher, '__enter__', lambda *a: pytest.fail('offline mode'))
    with pytest.raises(ValueError, match='离线重解析'):
        update_daily(tmp_path, build_plan(tmp_path, request(mode='reparse', symbols=['551000.SS'])))


def test_missing_etf_empty_response_is_not_reported_as_success(tmp_path, monkeypatch):
    from tests.ui_server import seed
    from tests.test_webadmin import request
    from scripts import download_tdx_day
    from simtradedata.fetchers.mootdx_fetcher import MootdxFetcher
    from simtradedata.web.operations import update_daily
    from simtradedata.web.plans import build_plan
    seed(tmp_path)
    monkeypatch.setattr(download_tdx_day, 'get_remote_file_info', lambda *a: pytest.fail('cache covers dates'))
    monkeypatch.setattr(MootdxFetcher, '__enter__', lambda self: self)
    monkeypatch.setattr(MootdxFetcher, '__exit__', lambda *a: None)
    monkeypatch.setattr(MootdxFetcher, 'fetch_daily_bars', lambda *a: pd.DataFrame())
    result = update_daily(tmp_path, build_plan(tmp_path, request(mode='auto', symbols=['551000.SS'])))
    assert result['written'] == 0 and result['failed_units'][0]['unit'] == '551000.SS'
    with DuckDBWriter(str(tmp_path / 'cn.duckdb')) as writer:
        assert writer.conn.execute("SELECT COUNT(*) FROM stocks WHERE symbol='551000.SS'").fetchone()[0] == 0


def test_all_market_automatically_saves_missing_etfs_and_continues_failures(tmp_path, monkeypatch):
    from tests.ui_server import seed
    from tests.test_webadmin import request
    from simtradedata.web.plans import build_plan
    from simtradedata.web.operations import update_daily
    from simtradedata.fetchers.mootdx_fetcher import MootdxFetcher
    seed(tmp_path)
    monkeypatch.setattr('simtradedata.web.etf_catalogue.load_etfs', lambda: ([item('551000.SS'), item('551001.SS')], []))
    monkeypatch.setattr(MootdxFetcher, '__enter__', lambda self: self)
    monkeypatch.setattr(MootdxFetcher, '__exit__', lambda *a: None)
    calls = []
    def fetch(self, symbol, start, end):
        calls.append(symbol)
        if symbol == '551001.SS':
            return pd.DataFrame()
        return pd.DataFrame({'date': pd.to_datetime(['2026-11-18', '2026-11-19', '2026-11-20']),
                             'open': 1., 'high': 1.1, 'low': .9, 'close': 1., 'volume': 100., 'amount': 100.})
    monkeypatch.setattr(MootdxFetcher, 'fetch_daily_bars', fetch)
    plan = build_plan(tmp_path, request(mode='auto'))
    result = update_daily(tmp_path, plan)
    assert calls == ['551000.SS', '551001.SS']
    assert result['failed_units'][0]['unit'] == '551001.SS'
    with DuckDBWriter(str(tmp_path / 'cn.duckdb')) as writer:
        assert writer.conn.execute("SELECT COUNT(*) FROM stocks WHERE symbol='551000.SS'").fetchone()[0] == 3
    calls.clear()
    result = update_daily(tmp_path, plan)
    assert calls == ['551001.SS']
    # Both the complete stock in the archive and the saved ETF are skipped.
    assert result['skipped'] == 2


def test_all_market_offline_does_not_discover_or_fetch_etfs(tmp_path, monkeypatch):
    from tests.ui_server import seed
    from tests.test_webadmin import request
    from simtradedata.web.plans import build_plan
    from simtradedata.web.operations import update_daily
    seed(tmp_path)
    monkeypatch.setattr('simtradedata.web.etf_catalogue.load_etfs', lambda: pytest.fail('offline mode'))
    assert update_daily(tmp_path, build_plan(tmp_path, request(mode='reparse')))['failed_units'] == []


def test_etf_db_gaps_include_internal_holes_but_not_pre_listing_days(tmp_path):
    from simtradedata.web.etf_catalogue import daily_etf_gaps
    with DuckDBWriter(str(tmp_path / 'cn.duckdb')) as writer:
        writer.conn.execute("INSERT INTO trade_days VALUES ('2026-11-18'),('2026-11-19'),('2026-11-20')")
        writer.conn.execute("INSERT INTO stocks(symbol,date,close) VALUES ('551000.SS','2026-11-18',1),('551000.SS','2026-11-20',1),('551001.SS','2026-11-20',1)")
        gaps = daily_etf_gaps(writer.conn, ['551000.SS','551001.SS','551002.SS'], '2026-11-18','2026-11-20')
    assert gaps == {'551000.SS': ('2026-11-19','2026-11-19'), '551002.SS': ('2026-11-18','2026-11-20')}


def test_manager_does_not_claim_all_market_complete_when_etf_absent(tmp_path):
    from tests.ui_server import seed
    from tests.test_webadmin import request
    from simtradedata.web.jobs import JobManager
    seed(tmp_path)
    manager = JobManager(tmp_path)
    manager.etf_catalogue = Mock(get=lambda: {'running': False, 'error': None, 'warnings': [], 'etfs': [item('551000.SS')]})
    result = manager._include_etfs(request(mode='auto'), {'work_pending': False})
    assert result['work_pending'] and result['update_summary']['pending_etfs'] == 1


def test_daily_statistics_separate_etfs_stocks_and_lofs(tmp_path):
    from simtradedata.web.coverage import inspect_coverage
    with DuckDBWriter(str(tmp_path / 'cn.duckdb')) as writer:
        writer.conn.execute("INSERT INTO trade_days VALUES ('2026-11-19'),('2026-11-20')")
        for symbol in ['600000.SS','159919.SZ','551000.SS','161725.SZ','000001.SS']:
            writer.conn.execute('INSERT INTO stocks(symbol,date,open,high,low,close,volume,money) VALUES (?,\'2026-11-20\',1,1,1,1,100,100)',[symbol])
    daily = inspect_coverage(tmp_path,'2026-11-19','2026-11-20')['datasets'][0]
    groups = {row['key']:row for row in daily['categories']}
    assert daily['records'] == 5
    assert groups['stock_daily']['records'] == 1
    assert groups['etf_daily']['records'] == groups['etf_daily']['securities'] == 2
    assert groups['other_daily']['records'] == 2
    assert groups['etf_daily']['first_date'] == '2026-11-20'
    assert groups['etf_daily']['missing_dates'] == ['2026-11-19']
    filtered = inspect_coverage(tmp_path,'2026-11-19','2026-11-20',['159919.SZ'])['datasets'][0]['categories']
    assert [r['records'] for r in filtered] == [0,1,0]


@pytest.mark.parametrize('primary_failure', ['empty', 'connect', 'fetch'])
def test_missing_etf_can_use_secondary_history_source(tmp_path, monkeypatch, primary_failure):
    from tests.ui_server import seed
    from tests.test_webadmin import request
    from simtradedata.web.operations import update_daily
    from simtradedata.web.plans import build_plan
    from simtradedata.fetchers.mootdx_fetcher import MootdxFetcher
    seed(tmp_path)
    monkeypatch.setattr(MootdxFetcher,'__enter__',lambda self:self)
    monkeypatch.setattr(MootdxFetcher,'__exit__',lambda *args:None)
    monkeypatch.setattr(MootdxFetcher,'fetch_daily_bars',lambda *args:pd.DataFrame())
    def unavailable(*args):
        raise ConnectionError('TDX unavailable')
    if primary_failure == 'connect':
        monkeypatch.setattr(MootdxFetcher,'__enter__',unavailable)
    elif primary_failure == 'fetch':
        monkeypatch.setattr(MootdxFetcher,'fetch_daily_bars',unavailable)
    monkeypatch.setattr('simtradedata.web.etf_history.fetch_etf_daily_fallback',lambda *args:pd.DataFrame({'date':pd.to_datetime(['2026-11-20']),'open':[1.0],'high':[1.1],'low':[.9],'close':[1.0],'volume':[100],'money':[100.0]}))
    assert update_daily(tmp_path,build_plan(tmp_path,request(mode='auto',symbols=['551000.SS'])))['written'] == 1


def test_etf_before_listing_is_skipped_without_failed_download(tmp_path,monkeypatch):
    from tests.ui_server import seed
    from tests.test_webadmin import request
    from simtradedata.web.operations import update_daily
    from simtradedata.web.plans import build_plan
    from simtradedata.fetchers.mootdx_fetcher import MootdxFetcher
    from simtradedata.web.etf_listing import EtfNotTradingInRange
    seed(tmp_path)
    monkeypatch.setattr(MootdxFetcher,'__enter__',lambda self:self)
    monkeypatch.setattr(MootdxFetcher,'__exit__',lambda *args:None)
    monkeypatch.setattr(MootdxFetcher,'fetch_daily_bars',lambda *args:pd.DataFrame())
    info={'symbol':'551000.SS','name':'测试ETF','state':'pre_listing','listed_date':'2026-11-23','detail':'上市日为2026-11-23','source_url':'https://example.com'}
    def not_listed(*args):raise EtfNotTradingInRange(info)
    monkeypatch.setattr('simtradedata.web.etf_history.fetch_etf_daily_fallback',not_listed)
    result=update_daily(tmp_path,build_plan(tmp_path,request(mode='auto',symbols=['551000.SS'])))
    assert result['failed_units']==[] and result['skipped']==1 and result['written']==0
    assert result['not_trading']==[info]
    with DuckDBWriter(str(tmp_path/'cn.duckdb')) as writer:
        assert writer.conn.execute("SELECT listed_date FROM stock_metadata WHERE symbol='551000.SS'").fetchone()==('2026-11-23',)
        assert writer.conn.execute("SELECT COUNT(*) FROM stocks WHERE symbol='551000.SS'").fetchone()[0]==0


def test_sse_directory_retries_transient_timeout(monkeypatch):
    session=Mock()
    session.get.side_effect=[__import__('requests').exceptions.ReadTimeout('timed out'),Mock(json=lambda:{'pageHelp':{'pageNo':1,'pageCount':1,'total':1},'result':[{'FUNDID2':'510300','ETF_FULLNAME':'沪深300ETF'}]})]
    assert sse_etfs(session)[0]['symbol']=='510300.SS'
    assert session.get.call_count==2


def test_pre_listing_guard_runs_before_accepting_tdx_placeholder(tmp_path,monkeypatch):
    from tests.ui_server import seed
    from tests.test_webadmin import request
    from simtradedata.web.operations import update_daily
    from simtradedata.web.plans import build_plan
    from simtradedata.fetchers.mootdx_fetcher import MootdxFetcher
    seed(tmp_path)
    info={'symbol':'551000.SS','name':'未上市ETF','state':'pre_listing','listed_date':'2026-11-23','detail':'尚未上市','source_url':'https://example.com'}
    monkeypatch.setattr('simtradedata.web.etf_listing.fetch_etf_listing_info',lambda *args:info)
    monkeypatch.setattr(MootdxFetcher,'__enter__',lambda *args:pytest.fail('No price connection is needed before listing'))
    result=update_daily(tmp_path,build_plan(tmp_path,request(mode='auto',symbols=['551000.SS'])))
    assert result['written']==0 and result['failed_units']==[] and result['not_trading']==[info]


def test_fund_class_code_is_not_an_independent_trading_etf(tmp_path,monkeypatch):
    from tests.ui_server import seed
    from tests.test_webadmin import request
    from simtradedata.web.operations import update_daily
    from simtradedata.web.plans import build_plan
    from simtradedata.fetchers.mootdx_fetcher import MootdxFetcher
    from simtradedata.web.daily_categories import is_exchange_etf
    seed(tmp_path)
    assert not is_exchange_etf('159006.SZ')
    assert is_exchange_etf('159005.SZ')
    monkeypatch.setattr(MootdxFetcher,'__enter__',lambda *args:pytest.fail('Use the actual common trading code'))
    result=update_daily(tmp_path,build_plan(tmp_path,request(mode='auto',symbols=['159006.SZ'])))
    assert result['failed_units']==[] and result['written']==0
    assert result['not_trading'][0]['trading_symbol']=='159005.SZ'


def test_etf_gaps_exclude_calendar_holidays_confirmed_halts_and_delisting(tmp_path):
    from simtradedata.web.etf_catalogue import daily_etf_gaps
    with DuckDBWriter(str(tmp_path / 'cn.duckdb')) as writer:
        conn = writer.conn
        conn.execute("CREATE TABLE exchange_calendar(date DATE PRIMARY KEY,is_trading_day BOOLEAN)")
        conn.execute("INSERT INTO trade_days VALUES ('2026-11-18'),('2026-11-19'),('2026-11-20')")
        conn.execute("INSERT INTO exchange_calendar VALUES ('2026-11-19',false)")
        conn.execute("INSERT INTO stocks(symbol,date,close) VALUES ('159919.SZ','2026-11-18',1)")
        conn.execute("INSERT INTO stock_status VALUES ('20261120','HALT','[\"159919.SZ\"]')")
        conn.execute("INSERT INTO stock_metadata(symbol,de_listed_date) VALUES ('159920.SZ','2026-11-20')")
        assert daily_etf_gaps(conn, ['159919.SZ','159920.SZ'], '2026-11-18','2026-11-20') == {
            '159920.SZ': ('2026-11-18','2026-11-18')}
        assert daily_etf_gaps(conn, ['159920.SZ'], '2026-11-18','2026-11-20', include_dates=True) == {
            '159920.SZ': ['2026-11-18']}


def test_verified_listing_date_recovers_history_before_first_local_bar(tmp_path):
    from simtradedata.web.etf_catalogue import daily_etf_gaps
    with DuckDBWriter(str(tmp_path/'cn.duckdb')) as writer:
        conn = writer.conn
        conn.execute("INSERT INTO trade_days VALUES ('2026-11-18'),('2026-11-19'),('2026-11-20')")
        conn.execute("INSERT INTO stocks(symbol,date,close) VALUES ('159919.SZ','2026-11-20',1)")
        conn.execute("INSERT INTO stock_metadata(symbol,listed_date) VALUES ('159919.SZ','2026-11-18')")
        assert daily_etf_gaps(conn,['159919.SZ'],'2026-11-18','2026-11-20',include_dates=True) == {
            '159919.SZ':['2026-11-18','2026-11-19']}


def test_authoritative_calendar_detects_dates_absent_from_trade_days(tmp_path):
    from simtradedata.web.etf_catalogue import daily_etf_gaps
    from simtradedata.web.coverage import trading_dates
    with DuckDBWriter(str(tmp_path/'cn.duckdb')) as writer:
        conn=writer.conn
        conn.execute('CREATE TABLE exchange_calendar(date DATE PRIMARY KEY,is_trading_day BOOLEAN)')
        conn.execute("INSERT INTO exchange_calendar VALUES ('2026-11-18',true),('2026-11-19',true),('2026-11-20',true)")
        assert trading_dates(conn,'2026-11-18','2026-11-20')==['2026-11-18','2026-11-19','2026-11-20']
        assert daily_etf_gaps(conn,['159919.SZ'],'2026-11-18','2026-11-20',include_dates=True)=={
            '159919.SZ':['2026-11-18','2026-11-19','2026-11-20']}


def test_confirmed_delisting_and_halt_skip_tail_without_quote_requests(tmp_path,monkeypatch):
    import struct,zipfile
    from tests.ui_server import seed
    from simtradedata.web.operations import update_daily
    from simtradedata.web.plans import build_plan
    seed(tmp_path)
    with zipfile.ZipFile(tmp_path/'downloads'/'hsjday.zip','a') as archive:
        archive.writestr('vipdoc/sz/lday/sz159503.day',struct.pack('<IIIIIfII',20261118,1000,1100,900,1000,100.,100,0))
    info={'symbol':'159503.SZ','name':'closed ETF','state':'delisted','listed_date':None,
          'de_listed_date':'2026-11-20','halt_start':'2026-11-19','detail':'confirmed closure'}
    monkeypatch.setattr('simtradedata.web.etf_listing.fetch_etf_listing_info',lambda *args:info)
    monkeypatch.setattr('simtradedata.fetchers.mootdx_fetcher.MootdxFetcher',lambda *a,**k:pytest.fail('verified non-trading dates must not request prices'))
    request={'dataset':'daily','mode':'auto','start_date':'2026-11-18','end_date':'2026-11-20','symbols':['159503.SZ']}
    result=update_daily(tmp_path,build_plan(tmp_path,request))
    assert result['failed_units']==[] and result['skipped']==1
    assert result['not_trading'][0]['state']=='delisted'
