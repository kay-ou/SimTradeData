"""ETF issuance/listing evidence; never infer listing from a generic trading label."""

import re
from datetime import date
from html.parser import HTMLParser

import requests

from simtradedata.resilience.retry import RetryConfig, retry

_HTTP_RETRY = RetryConfig(max_retries=2, base_delay=.5)


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, text):
        if text.strip():
            self.parts.append(text.strip())


class EtfNotTradingInRange(ValueError):
    def __init__(self, info):
        self.info = info
        super().__init__(f"{info['symbol']}：{info['detail']}")


def listing_date(content):
    """Extract a stated listing date, never an announcement publication date."""
    text = re.sub(r'\s+', '', content)
    patterns = [
        r'(?:上市交易日期|上市日期)[：:]?(?:为)?(20\d{2})[年/-](\d{1,2})[月/-](\d{1,2})日?',
        r'自(20\d{2})[年/-](\d{1,2})[月/-](\d{1,2})日起.{0,40}?上市交易',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return date(*map(int, match.groups())).isoformat()
    return None


@retry(config=_HTTP_RETRY)
def fetch_etf_listing_info(symbol):
    code = symbol[:6]
    profile_url = f'https://fund.eastmoney.com/{code}.html'
    response = requests.get(profile_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=(10, 20))
    response.raise_for_status()
    response.encoding = 'utf-8'  # Fund pages declare UTF-8 but send no HTTP charset.
    parser = _Text()
    parser.feed(response.text)
    title = next((part for part in parser.parts if f'({code})' in part and '基金净值' in part), '')
    state = next((parser.parts[i + 1] for i, part in enumerate(parser.parts[:-1])
                  if part in {'交易状态：', '交易状态:'}), None)
    base = {'symbol': symbol, 'name': title.split('(')[0] or code, 'source_url': profile_url}
    if state == '认购期':
        return {**base, 'state': 'pre_listing', 'listed_date': None, 'detail': '处于认购期，尚无上市交易行情'}

    response = requests.get('https://api.fund.eastmoney.com/f10/JJGG',
                            params={'fundcode': code, 'pageIndex': 1, 'pageSize': 20, 'type': 0},
                            headers={'Referer': 'https://fundf10.eastmoney.com/'}, timeout=(10, 20))
    response.raise_for_status()
    notices = response.json()
    if notices.get('ErrCode') != 0 or not isinstance(notices.get('Data'), list):
        raise ValueError('ETF公告列表格式改变')
    for notice in notices['Data']:
        if str(notice.get('FUNDCODE')) != code:
            raise ValueError('ETF公告返回证券不匹配')
        heading = notice.get('TITLE', '')
        if any(term in heading for term in ['基金合同不能生效', '基金合同未能生效', '募集失败']):
            return {**base, 'state': 'not_established', 'listed_date': None,
                    'source_url': f"https://fund.eastmoney.com/gonggao/{code},{notice['ID']}.html",
                    'detail': '公告显示募集未成功，未形成上市交易历史'}
        if any(term in heading for term in ['暂停','终止','取消','退市','停牌']):
            continue
        if '上市交易的公告' not in heading and not heading.endswith('上市交易公告书'):
            continue
        response = requests.get('https://np-cnotice-fund.eastmoney.com/api/content/ann',
                                params={'client_source': 'web_fund', 'show_all': 1, 'art_code': notice['ID']},
                                headers={'Referer': 'https://fund.eastmoney.com/'}, timeout=(10, 20))
        response.raise_for_status()
        data = response.json()
        if data.get('success') != 1 or not isinstance(data.get('data'), dict):
            raise ValueError('ETF上市公告正文不可用')
        content = data['data'].get('notice_content', '')
        if code not in re.sub(r'\s+', '', content):
            raise ValueError('ETF上市公告正文未匹配证券代码')
        listed = listing_date(content)
        if listed:
            return {**base, 'state': 'pre_listing' if listed > date.today().isoformat() else 'listed',
                    'listed_date': listed,
                    'source_url': f"https://fund.eastmoney.com/gonggao/{code},{notice['ID']}.html",
                    'detail': f'公告明确上市日为 {listed}'}
    basic_url = f'https://fundf10.eastmoney.com/jbgk_{code}.html'
    response = requests.get(basic_url, headers={'User-Agent':'Mozilla/5.0'}, timeout=(10,20))
    response.raise_for_status()
    response.encoding = 'utf-8'
    parser = _Text()
    parser.feed(response.text)
    basic_title = next((part for part in parser.parts if f'({code})' in part and '基金基本概况' in part), '')
    match = re.search(r'<th[^>]*>发行日期</th>\s*<td[^>]*>(.*?)</td>', response.text, re.S)
    if basic_title and match:
        issue = re.search(r'(20\d{2})年(\d{1,2})月(\d{1,2})日', match.group(1))
        if issue:
            issued = date(*map(int, issue.groups())).isoformat()
            if issued > date.today().isoformat():
                return {**base, 'name':basic_title.split('(')[0], 'state':'pre_listing',
                        'listed_date':None, 'issuance_date':issued, 'source_url':basic_url,
                        'detail':f'发行开始日为 {issued}，尚未开始上市交易'}
    return None
