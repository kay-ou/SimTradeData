"""
Utility functions for stock code conversion
"""


def convert_to_ptrade_code(code: str, source: str = "baostock") -> str:
    """
    Convert stock code from various sources to PTrade format

    Args:
        code: Stock code in source format
        source: Data source name ('baostock', 'qstock', 'yahoo', 'yfinance')

    Returns:
        Stock code in PTrade format (e.g., '600000.SS', '000001.SZ', 'AAPL.US')

    Note:
        PTrade/SimTradeLab uses:
        - Shanghai stocks: .SS (not .SH)
        - Shenzhen stocks: .SZ
        - US stocks: .US

    Examples:
        >>> convert_to_ptrade_code('sh.600000', 'baostock')
        '600000.SS'
        >>> convert_to_ptrade_code('000001', 'qstock')
        '000001.SZ'
        >>> convert_to_ptrade_code('AAPL', 'yfinance')
        'AAPL.US'
    """
    if source == "baostock":
        # BaoStock format: sh.600000, sz.000001
        if "." in code:
            market, symbol = code.split(".")
            # Map to SimTradeLab format: SS for Shanghai, SZ for Shenzhen
            market_map = {"sh": "SS", "sz": "SZ"}
            return f"{symbol}.{market_map[market.lower()]}"
        return code

    elif source == "qstock":
        # QStock format: 600000, 000001
        # Determine market by code prefix
        if code.startswith("6") or code.startswith("5"):
            return f"{code}.SS"  # Shanghai uses .SS
        elif code[0] in ("0", "1", "3"):
            return f"{code}.SZ"
        return code

    elif source == "yahoo":
        # Yahoo format: 600000.SS (Shanghai), 000001.SZ (Shenzhen)
        # Yahoo already uses .SS, so no conversion needed
        return code

    elif source == "yfinance":
        # yfinance raw ticker: AAPL, MSFT, GOOGL -> AAPL.US, MSFT.US
        if "." not in code:
            return f"{code}.US"
        return code

    return code


def convert_from_ptrade_code(code: str, target_source: str) -> str:
    """
    Convert PTrade format code to target source format

    Args:
        code: Stock code in PTrade format (e.g., '600000.SS', 'AAPL.US')
        target_source: Target source name ('baostock', 'qstock', 'yahoo',
                       'mootdx', 'yfinance')

    Returns:
        Stock code in target source format

    Examples:
        >>> convert_from_ptrade_code('600000.SS', 'baostock')
        'sh.600000'
        >>> convert_from_ptrade_code('000001.SZ', 'qstock')
        '000001'
        >>> convert_from_ptrade_code('000001.SZ', 'mootdx')
        '000001'
        >>> convert_from_ptrade_code('AAPL.US', 'yfinance')
        'AAPL'
    """
    if "." not in code:
        return code

    symbol, market = code.split(".")

    if target_source == "baostock":
        # Map SS back to sh for BaoStock
        market_map = {"SS": "sh", "SZ": "sz", "SH": "sh"}  # Support both SS and SH
        return f"{market_map.get(market, market.lower())}.{symbol}"

    elif target_source in ("qstock", "mootdx"):
        # Both qstock and mootdx use simple code format (e.g., '000001')
        return symbol

    elif target_source == "yahoo":
        # Yahoo uses .SS for Shanghai (same as PTrade)
        return code

    elif target_source == "yfinance":
        # yfinance uses raw tickers: AAPL.US -> AAPL
        if market == "US":
            return symbol
        return code

    return code


def get_mootdx_market(symbol: str) -> int:
    """
    Convert PTrade code to mootdx market code.

    Args:
        symbol: Stock code in PTrade format (e.g., '000001.SZ', '600000.SS')

    Returns:
        0 for Shenzhen, 1 for Shanghai, 2 for Beijing.
        Prefer an explicit PTrade suffix; infer by prefix for bare codes.

    Examples:
        >>> get_mootdx_market('000001.SZ')
        0
        >>> get_mootdx_market('600000.SS')
        1
    """
    if "." in symbol:
        code, suffix = symbol.rsplit(".", 1)
        if suffix in {"SS", "SZ", "BJ"}:
            return {"SS": 1, "SZ": 0, "BJ": 2}[suffix]
    else:
        code = symbol
    return 0 if code[0] in "0123" else 1


_ETF_PREFIXES = ("15", "16", "50", "51", "52", "56", "58", "59")


def is_etf_code(symbol: str) -> bool:
    """
    Check whether *symbol* is an ETF, LOF, or other exchange-traded fund.

    Accepts both bare codes (``"159919"``) and PTrade-style codes
    (``"159919.SZ"``).  The decision is based on the first two digits of
    the numeric part.

    Returns:
        True if the code matches a known ETF/LOF prefix, False otherwise.
    """
    code = symbol.split(".")[0] if "." in symbol else symbol
    return code[:2] in _ETF_PREFIXES


def get_security_type(symbol: str) -> str:
    """
    Classify *symbol* as ``"stock"``, ``"etf"``, or ``"index"``.

    Rules (applied in order):
    1. ETF/LOF — first two digits match ``_ETF_PREFIXES``.
    2. Index   — ``399xxx`` is always an index; ``000xxx`` on the ``.SS``
       market (Shanghai) is an index (e.g. 000001.SS = SSE Composite).
    3. Everything else is treated as a stock.
    """
    if is_etf_code(symbol):
        return "etf"

    code = symbol.split(".")[0] if "." in symbol else symbol
    market = symbol.split(".")[1] if "." in symbol else ""

    if code.startswith("399"):
        return "index"
    if code.startswith("000") and market == "SS":
        return "index"

    return "stock"


def get_price_divisor(symbol: str) -> float:
    """
    Return the price divisor needed to correct raw TDX API prices.

    The TDX protocol returns ETF/LOF prices multiplied by 10.  This
    helper returns ``10.0`` for fund codes and ``1.0`` for everything else
    so callers can simply divide: ``price / get_price_divisor(code)``.
    """
    return 10.0 if is_etf_code(symbol) else 1.0
