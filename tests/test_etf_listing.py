from datetime import date
from unittest.mock import Mock

import pytest
import requests

from simtradedata.web.etf_listing import fetch_etf_listing_info, listing_date


class Clock(date):
    @classmethod
    def today(cls):
        return cls(2026, 11, 22)


def profile(state='场内交易'):
    response = requests.Response()
    response.status_code = 200
    response.encoding = 'ISO-8859-1'
    response._content = f'<title>均衡ETF(158900)基金净值_估值_行情走势</title><span>交易状态：</span><span>{state}</span>'.encode('utf-8')
    return response


def response(data):
    return Mock(json=lambda:data)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr('simtradedata.web.etf_listing.date',Clock)


def test_listing_date_is_not_the_publication_date():
    content='时间：2026-11-18 证券代码：158900，自 2026 年 11 月 23 日起在本所上市交易。'
    assert listing_date(content)=='2026-11-23'


def test_profile_charset_is_respected_and_subscription_has_no_price_request(monkeypatch):
    get=Mock(return_value=profile('认购期'))
    monkeypatch.setattr('simtradedata.web.etf_listing.requests.get',get)
    info=fetch_etf_listing_info('158900.SZ')
    assert info['state']=='pre_listing' and info['listed_date'] is None
    assert get.call_count==1


def test_generic_trading_label_does_not_prove_already_listed(monkeypatch):
    get=Mock(side_effect=[profile(),response({'ErrCode':0,'Data':[{'FUNDCODE':'158900','TITLE':'关于ETF上市交易的公告','ID':'AN1'}]}),
                         response({'success':1,'data':{'notice_content':'证券代码：158900，自2026年11月23日起在本所上市交易。'}})])
    monkeypatch.setattr('simtradedata.web.etf_listing.requests.get',get)
    info=fetch_etf_listing_info('158900.SZ')
    assert info['state']=='pre_listing' and info['listed_date']=='2026-11-23'


def test_future_issuance_is_not_saved_as_a_listing_date(monkeypatch):
    basic=profile('')
    basic._content='<title>均衡ETF(158900)基金基本概况</title><th>发行日期</th><td>2026年11月30日</td>'.encode()
    get=Mock(side_effect=[profile(''),response({'ErrCode':0,'Data':[]}),basic])
    monkeypatch.setattr('simtradedata.web.etf_listing.requests.get',get)
    info=fetch_etf_listing_info('158900.SZ')
    assert info['state']=='pre_listing' and info['listed_date'] is None
    assert info['issuance_date']=='2026-11-30'


def test_failed_issuance_is_not_confused_with_delisting(monkeypatch):
    get=Mock(side_effect=[profile('封闭期'),response({'ErrCode':0,'Data':[{'FUNDCODE':'158900','TITLE':'基金合同不能生效的公告','ID':'AN2'}]})])
    monkeypatch.setattr('simtradedata.web.etf_listing.requests.get',get)
    assert fetch_etf_listing_info('158900.SZ')['state']=='not_established'


def test_delisting_uses_explicit_date_not_record_or_publication_date():
    from simtradedata.web.etf_listing import delisting_dates
    content='基金代码：159503。终止上市日：2024年7月19日。权益登记日：2024年7月18日。本基金已自2024年7月12日起停牌，停牌直至终止上市。'
    assert delisting_dates(content,'159503.SZ')=={'de_listed_date':'2024-07-19','halt_start':'2024-07-12'}
    assert delisting_dates('基金代码：159503。公告发布2024年7月18日，将终止上市。','159503.SZ') is None
    with pytest.raises(ValueError):
        delisting_dates(content,'159001.SZ')


def test_temporary_suspension_is_not_extended_to_delisting():
    from simtradedata.web.etf_listing import delisting_dates
    content='基金代码：159503。终止上市日：2024年7月19日。自2024年7月12日起停牌一天，次日复牌。'
    assert delisting_dates(content,'159503.SZ')['halt_start'] is None


def test_delisting_stores_metadata_and_merges_halt_without_market_rows(tmp_path):
    from simtradedata.web.etf_listing import store_delisting
    from simtradedata.web.etf_catalogue import daily_etf_gaps
    from simtradedata.writers.duckdb_writer import DuckDBWriter
    with DuckDBWriter(str(tmp_path/'cn.duckdb')) as writer:
        writer.conn.execute("INSERT INTO trade_days VALUES ('2024-07-11'),('2024-07-12'),('2024-07-18'),('2024-07-19')")
        writer.conn.execute("INSERT INTO stocks(symbol,date,close) VALUES ('159503.SZ','2024-07-11',1)")
        writer.conn.execute("INSERT INTO stock_status VALUES ('20240712','HALT','[\"000001.SZ\"]')")
        info={'symbol':'159503.SZ','name':'test ETF','de_listed_date':'2024-07-19','halt_start':'2024-07-12'}
        store_delisting(writer,info)
        assert writer.conn.execute('SELECT COUNT(*) FROM stocks').fetchone()[0]==1
        assert daily_etf_gaps(writer.conn,['159503.SZ'],'2024-07-11','2024-07-19')=={}
        assert writer.conn.execute("SELECT symbols FROM stock_status WHERE date='20240712'").fetchone()[0]=='["000001.SZ", "159503.SZ"]'
        store_delisting(writer,info)
        assert writer.conn.execute('SELECT COUNT(*) FROM stocks').fetchone()[0]==1


def test_delisting_conflicting_with_existing_market_data_is_not_applied(tmp_path):
    from simtradedata.web.etf_listing import store_delisting
    from simtradedata.writers.duckdb_writer import DuckDBWriter
    with DuckDBWriter(str(tmp_path/'cn.duckdb')) as writer:
        writer.conn.execute("INSERT INTO stocks(symbol,date,close) VALUES ('159503.SZ','2024-07-20',1)")
        with pytest.raises(ValueError,match='market bars'):
            store_delisting(writer,{'symbol':'159503.SZ','name':'test','de_listed_date':'2024-07-19','halt_start':None})
        assert writer.conn.execute('SELECT COUNT(*) FROM stock_metadata').fetchone()[0]==0


def test_indefinite_suspension_date_belongs_to_its_clause(tmp_path):
    from simtradedata.web.etf_listing import delisting_dates, store_delisting
    from simtradedata.writers.duckdb_writer import DuckDBWriter
    content = ('基金代码：159503。终止上市日：2026年11月20日。'
               '自2026年10月15日起停牌一天，次日复牌。'
               '自2026年11月16日起停牌，停牌直至终止上市。')
    info = delisting_dates(content, '159503.SZ')
    assert info['halt_start'] == '2026-11-16'
    with DuckDBWriter(str(tmp_path / 'cn.duckdb')) as writer:
        writer.conn.execute("INSERT INTO trade_days VALUES ('2026-10-15'),('2026-11-13'),('2026-11-16')")
        store_delisting(writer, {'symbol':'159503.SZ', 'name':'test', **info})
        assert writer.conn.execute('SELECT date FROM stock_status ORDER BY date').fetchall() == [('20261116',)]


@pytest.mark.parametrize("prefix", ["拟", "计划", "预计", "申请"])
def test_proposed_delisting_is_not_confirmed(prefix):
    from simtradedata.web.etf_listing import delisting_dates
    assert delisting_dates(f"基金代码：159503。{prefix}终止上市日期为2026年11月20日。", "159503.SZ") is None


def test_halt_conflicting_with_trades_is_rejected_without_writes(tmp_path):
    from simtradedata.web.etf_listing import store_delisting
    from simtradedata.writers.duckdb_writer import DuckDBWriter
    with DuckDBWriter(str(tmp_path / "cn.duckdb")) as writer:
        writer.conn.execute("INSERT INTO stocks(symbol,date,close) VALUES ('159503.SZ','2026-11-19',1)")
        with pytest.raises(ValueError, match="suspension interval"):
            store_delisting(writer, {"symbol":"159503.SZ", "name":"test", "de_listed_date":"2026-11-20", "halt_start":"2026-11-18"})
        assert writer.conn.execute("SELECT COUNT(*) FROM stock_metadata").fetchone()[0] == 0
        assert writer.conn.execute("SELECT COUNT(*) FROM stock_status").fetchone()[0] == 0
