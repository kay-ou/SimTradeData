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


def delisting_dates(content, symbol):
    """Only explicit termination dates and indefinite trading suspensions."""
    text = re.sub(r'\s+', '', content)
    code = symbol[:6]
    if not re.search(r'(?:基金(?:主|交易)?代码|证券代码|交易代码)[：:]?' + code + r'(?!\d)', text):
        raise ValueError('ETF delisting announcement does not identify the requested security')
    if re.search(r'(?:拟|计划|预计|可能|申请|申请获准)终止上市', text):
        return None
    pattern = r'(20\d{2})[年/-](\d{1,2})[月/-](\d{1,2})日?'
    dates = {date(*map(int, match)).isoformat() for match in re.findall(
        r'终止上市(?:日期|日)[：:]?(?:为)?' + pattern, text)}
    if len(dates) != 1:
        return None
    out = dates.pop()
    halt = None
    matches = re.findall(
        r'自' + pattern + r'(?:日起|起|开市起)停牌[^。；;]*?'
        r'(?:直至终止上市|至终止上市|不再复牌)', text)
    halts = {date(*map(int, match)).isoformat() for match in matches}
    if len(halts) > 1:
        raise ValueError('ETF announcement contains conflicting indefinite suspension dates')
    if halts:
        halt = halts.pop()
        if halt >= out:
            raise ValueError('ETF suspension date is not before delisting')
    return {'de_listed_date': out, 'halt_start': halt}


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
        if re.search(r'(?:拟|计划|预计|可能|申请)终止上市', heading):
            continue
        terminal = '终止上市' in heading
        if not terminal and any(term in heading for term in ['暂停','终止','取消','退市','停牌']):
            continue
        if not terminal and '上市交易的公告' not in heading and not heading.endswith('上市交易公告书'):
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
        if terminal:
            dates = delisting_dates(content, symbol)
            if dates:
                return {**base, **dates, 'listed_date': None,
                        'state': 'delisted' if dates['de_listed_date'] <= date.today().isoformat() else 'listed',
                        'source_url': f"https://fund.eastmoney.com/gonggao/{code},{notice['ID']}.html",
                        'detail': f"公告确认终止上市日为 {dates['de_listed_date']}"}
            continue
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


def store_delisting(writer, info):
    """Store source-confirmed lifecycle facts, without inventing market bars."""
    import json
    import duckdb
    from simtradedata.web.coverage import trading_dates
    out = date.fromisoformat(info['de_listed_date'])
    halt = date.fromisoformat(info['halt_start']) if info.get('halt_start') else None
    if halt and halt >= out:
        raise ValueError('Invalid ETF suspension interval')
    existing = writer.conn.execute('SELECT de_listed_date FROM stock_metadata WHERE symbol=?', [info['symbol']]).fetchone()
    if existing and existing[0] and existing[0] != out.isoformat():
        raise ValueError('ETF delisting date conflicts with stored metadata')
    latest = writer.conn.execute('SELECT MAX(date) FROM stocks WHERE symbol=? AND close>0', [info['symbol']]).fetchone()[0]
    if latest and latest >= out:
        raise ValueError('ETF delisting date conflicts with stored market bars')
    if halt and latest and latest >= halt:
        raise ValueError('ETF suspension interval conflicts with stored market bars')
    days = trading_dates(writer.conn, halt.isoformat(), out.isoformat()) if halt else []
    writer.begin()
    try:
        writer.conn.execute("""INSERT INTO stock_metadata(symbol,stock_name,de_listed_date,security_type,listing_status)
            VALUES (?,?,?,'etf',?) ON CONFLICT(symbol) DO UPDATE SET
            stock_name=coalesce(nullif(stock_metadata.stock_name,''),excluded.stock_name),
            de_listed_date=excluded.de_listed_date,security_type='etf',listing_status=excluded.listing_status""",
            [info['symbol'], info['name'] if info['name'] != info['symbol'][:6] else None,
             out.isoformat(), '0' if out <= date.today() else '1'])
        for day in days:
            if day >= out.isoformat():
                continue
            key = day.replace('-', '')
            row = writer.conn.execute("SELECT symbols FROM stock_status WHERE date=? AND status_type='HALT'", [key]).fetchone()
            members = json.loads(row[0]) if row else []
            if not isinstance(members, list):
                raise ValueError('Invalid existing HALT members')
            members = sorted(set(members) | {info['symbol']})
            writer.conn.execute("""INSERT INTO stock_status VALUES (?,'HALT',?)
                ON CONFLICT(date,status_type) DO UPDATE SET symbols=excluded.symbols""", [key,json.dumps(members)])
        writer._record_stock_metadata_changes([info['symbol']])
        writer.commit()
    except duckdb.FatalException:
        raise
    except Exception:
        writer.rollback()
        raise
