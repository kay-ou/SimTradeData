"""Daily display categories; ETF allocations exclude LOF and structured funds."""

import re

# Current exchange ETF allocations, including the historical codes in TDX packages.
ETF_SYMBOL_PATTERN = r'(15[89][0-9]{3}\.SZ|5(1[0-8]|2[06]|30|51|6[0-9]|8[89])[0-9]{3}\.SS)'
# The 2026-10-09 prospectus states A/B classes trade and redeem using one code.
# 159006 is the B-class reference code; the common trading code is 159005.
ETF_SHARE_CLASSES = {'159006.SZ': '159005.SZ'}
NON_TRADING_ETF_CODES = ('159900.SZ', *ETF_SHARE_CLASSES)

STOCK_SYMBOL_PATTERN = r'(6[0-9]{5}\.SS|(00|30)[0-9]{4}\.SZ|(43|83|87|92)[0-9]{4}\.BJ)'
DAILY_CATEGORIES = [('stock_daily', '股票日线'), ('etf_daily', 'ETF日线'), ('other_daily', '其他行情（LOF/指数等）')]


def is_exchange_etf(symbol):
    return symbol not in NON_TRADING_ETF_CODES and re.fullmatch(ETF_SYMBOL_PATTERN, symbol) is not None
