"""ETF fallback parsing and job-local availability, without external connections."""
from unittest.mock import Mock

import pandas as pd
import pytest
import requests

from simtradedata.web.etf_history import fetch_etf_daily_tencent


def test_tencent_amount_units_and_date_filter(monkeypatch):
    get = Mock(return_value=Mock(json=lambda: {'code': 0, 'data': {'sz159192': {'day': [
        ['2026-04-30','1.015','1.022','1.025','1.015','886585',{},'36.94','9036.83'],
        ['2026-05-06','1.022','1.025','1.025','1.011','314766',{},'16.48','3215.10'],
    ]}}}))
    monkeypatch.setattr('simtradedata.web.etf_history.requests.get', get)
    frame = fetch_etf_daily_tencent('159192.SZ','2026-04-30','2026-04-30')
    assert len(frame) == 1
    row = frame.iloc[0]
    assert row['volume'] == 88658500
    assert row['money'] == pytest.approx(90368300)
    assert row['low'] <= row['money'] / row['volume'] <= row['high']
    assert get.call_args.kwargs['params']['param'].endswith(',640,')


@pytest.mark.parametrize('bars', [
    [['2026-04-30','1','1','1','1','100']],
    [['bad-date','1','1','1','1','100',{},'1','1']],
    [['2026-04-30','1','1','1','1','100',{},'1','1']] * 2,
])
def test_tencent_rejects_missing_amount_bad_dates_and_duplicates(monkeypatch, bars):
    monkeypatch.setattr('simtradedata.web.etf_history.requests.get', lambda *a,**k: Mock(json=lambda:{'code':0,'data':{'sz159192':{'day':bars}}}))
    with pytest.raises(ValueError):
        fetch_etf_daily_tencent('159192.SZ','2026-04-30','2026-05-01')


def test_tencent_chunks_long_history_and_checks_security(monkeypatch):
    get=Mock(return_value=Mock(json=lambda:{'code':0,'data':{}}))
    monkeypatch.setattr('simtradedata.web.etf_history.requests.get',get)
    assert fetch_etf_daily_tencent('159192.SZ','2020-01-01','2026-11-20').empty
    assert len(get.call_args_list)==4
    get.return_value.json=lambda:{'code':0,'data':{'sz159919':{'day':[]}}}
    with pytest.raises(ValueError,match='different security'):
        fetch_etf_daily_tencent('159192.SZ','2026-11-18','2026-11-20')


def test_empty_primary_uses_working_fallback_and_limits_dead_source_per_job(tmp_path,monkeypatch):
    from tests.ui_server import seed
    from simtradedata.web.operations import update_daily
    from simtradedata.web.plans import build_plan
    seed(tmp_path)
    symbols=['159203.SZ','159210.SZ','159211.SZ','159216.SZ']
    class Fetcher:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def fetch_daily_bars(self,*args): return pd.DataFrame()
    monkeypatch.setattr('simtradedata.fetchers.mootdx_fetcher.MootdxFetcher',Fetcher)
    monkeypatch.setattr('simtradedata.web.etf_listing.fetch_etf_listing_info',lambda *args:None)
    monkeypatch.setattr('simtradedata.web.etf_history.fetch_etf_daily_tencent',lambda *args:pd.DataFrame())
    dead=Mock(side_effect=requests.ConnectionError('RemoteDisconnected'))
    monkeypatch.setattr('simtradedata.web.etf_history.fetch_etf_daily_fallback',dead)
    result=update_daily(tmp_path,build_plan(tmp_path,{'dataset':'daily','mode':'auto','start_date':'2026-11-18','end_date':'2026-11-20','symbols':symbols}))
    assert dead.call_count==3
    assert [row['unit'] for row in result['failed_units']]==symbols
    assert all(row['missing_dates']==['2026-11-18','2026-11-19','2026-11-20'] for row in result['failed_units'])
    assert 'Skipped' in result['failed_units'][-1]['source_errors']['eastmoney']


def test_partial_primary_and_secondary_only_save_missing_dates(tmp_path,monkeypatch):
    import duckdb
    from tests.ui_server import seed
    from simtradedata.web.operations import update_daily
    from simtradedata.web.plans import build_plan
    seed(tmp_path)
    def bars(days):
        return pd.DataFrame({'date':pd.to_datetime(days),'open':1.,'high':1.1,'low':.9,'close':1.05,'volume':1000,'money':1050.})
    class Fetcher:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def fetch_daily_bars(self,*args): return bars(['2026-11-18'])
    monkeypatch.setattr('simtradedata.fetchers.mootdx_fetcher.MootdxFetcher',Fetcher)
    monkeypatch.setattr('simtradedata.web.etf_listing.fetch_etf_listing_info',lambda *args:None)
    secondary=Mock(return_value=bars(['2026-11-18','2026-11-19','2026-11-20']))
    monkeypatch.setattr('simtradedata.web.etf_history.fetch_etf_daily_tencent',secondary)
    monkeypatch.setattr('simtradedata.web.etf_history.fetch_etf_daily_fallback',lambda *args:pytest.fail('working secondary must avoid Eastmoney'))
    request={'dataset':'daily','mode':'auto','start_date':'2026-11-18','end_date':'2026-11-20','symbols':['159919.SZ']}
    result=update_daily(tmp_path,build_plan(tmp_path,request))
    assert result['failed_units']==[] and result['written']==3
    assert secondary.call_args.args==('159919.SZ','2026-11-19','2026-11-20')
    with duckdb.connect(str(tmp_path/'cn.duckdb'),read_only=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM stocks WHERE symbol='159919.SZ'").fetchone()[0]==3


def test_adjacent_dates_do_not_count_as_repaired_gaps(tmp_path,monkeypatch):
    from tests.ui_server import seed
    from simtradedata.web.operations import update_daily
    from simtradedata.web.plans import build_plan
    seed(tmp_path)
    def unrelated(*args):
        return pd.DataFrame({'date':pd.to_datetime(['2026-11-17']),'open':[1.],'high':[1.1],'low':[.9],'close':[1.05],'volume':[1000],'money':[1050.]})
    class Fetcher:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        fetch_daily_bars=unrelated
    monkeypatch.setattr('simtradedata.fetchers.mootdx_fetcher.MootdxFetcher',Fetcher)
    monkeypatch.setattr('simtradedata.web.etf_listing.fetch_etf_listing_info',lambda *args:None)
    monkeypatch.setattr('simtradedata.web.etf_history.fetch_etf_daily_tencent',unrelated)
    monkeypatch.setattr('simtradedata.web.etf_history.fetch_etf_daily_fallback',unrelated)
    r={'dataset':'daily','mode':'auto','start_date':'2026-11-18','end_date':'2026-11-20','symbols':['159919.SZ']}
    result=update_daily(tmp_path,build_plan(tmp_path,r))
    assert result['written']==0
    assert result['failed_units'][0]['missing_dates']==['2026-11-18','2026-11-19','2026-11-20']
    assert all('No records matched' in value for value in result['failed_units'][0]['source_errors'].values())


def test_tencent_ignores_out_of_chunk_rows_returned_by_server(monkeypatch):
    bars=[['2021-12-31','1','1','1','1','100',{},'1','1'],
          ['2022-01-04','1','1','1','1','100',{},'1','1']]
    get=Mock(return_value=Mock(json=lambda:{'code':0,'data':{'sz159192':{'day':bars}}}))
    monkeypatch.setattr('simtradedata.web.etf_history.requests.get',get)
    result=fetch_etf_daily_tencent('159192.SZ','2020-01-01','2022-01-04')
    assert result['date'].dt.strftime('%Y-%m-%d').tolist()==['2021-12-31','2022-01-04']
    assert get.call_count==2
