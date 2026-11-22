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
