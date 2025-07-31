"""
多市场交易时间配置

支持不同市场的交易时间配置，无需存储到数据库中
"""

from datetime import time
from typing import Dict, List, Optional, Tuple

# 中国A股交易时间 (SZ深交所 / SS上交所)
CHINA_TRADING_HOURS = {
    "morning_open": time(9, 30, 0),  # 09:30:00
    "morning_close": time(11, 30, 0),  # 11:30:00
    "afternoon_open": time(13, 0, 0),  # 13:00:00
    "afternoon_close": time(15, 0, 0),  # 15:00:00
}

# 港股交易时间
HONG_KONG_TRADING_HOURS = {
    "morning_open": time(9, 30, 0),  # 09:30:00
    "morning_close": time(12, 0, 0),  # 12:00:00
    "afternoon_open": time(13, 0, 0),  # 13:00:00
    "afternoon_close": time(16, 0, 0),  # 16:00:00
}

# 美股交易时间 (NYSE/NASDAQ - EST时间)
US_TRADING_HOURS = {
    "morning_open": time(9, 30, 0),  # 09:30:00 EST
    "morning_close": time(16, 0, 0),  # 16:00:00 EST
    "afternoon_open": None,  # 美股无午休
    "afternoon_close": None,
}

# 多市场交易时间映射
MARKET_TRADING_HOURS = {
    # 中国市场
    "CN": CHINA_TRADING_HOURS,
    "SZ": CHINA_TRADING_HOURS,  # 深交所
    "SS": CHINA_TRADING_HOURS,  # 上交所
    "SH": CHINA_TRADING_HOURS,  # 上交所别名
    # 港股
    "HK": HONG_KONG_TRADING_HOURS,
    # 美股
    "US": US_TRADING_HOURS,
    "NYSE": US_TRADING_HOURS,
    "NASDAQ": US_TRADING_HOURS,
}

# 市场显示名称
MARKET_DISPLAY_NAMES = {
    "CN": "中国A股",
    "SZ": "深交所",
    "SS": "上交所",
    "SH": "上交所",
    "HK": "港股",
    "US": "美股",
    "NYSE": "纽交所",
    "NASDAQ": "纳斯达克",
}


def get_trading_hours(market: str = "CN") -> Dict[str, Optional[time]]:
    """
    获取指定市场的交易时间

    Args:
        market: 市场代码 (CN/SZ/SS/HK/US等)

    Returns:
        Dict[str, time]: 交易时间配置
    """
    return MARKET_TRADING_HOURS.get(market.upper(), CHINA_TRADING_HOURS)


def is_trading_time(current_time: time, market: str = "CN") -> bool:
    """
    判断指定时间是否在交易时间内

    Args:
        current_time: 当前时间
        market: 市场代码

    Returns:
        bool: 是否在交易时间内
    """
    hours = get_trading_hours(market)

    # 上午交易时间
    if hours["morning_open"] <= current_time <= hours["morning_close"]:
        return True

    # 下午交易时间 (如果存在)
    if hours.get("afternoon_open") and hours.get("afternoon_close"):
        if hours["afternoon_open"] <= current_time <= hours["afternoon_close"]:
            return True

    return False


def get_trading_sessions(market: str = "CN") -> List[Tuple[time, time]]:
    """
    获取交易时段列表

    Args:
        market: 市场代码

    Returns:
        List[Tuple[time, time]]: [(开始时间, 结束时间), ...]
    """
    hours = get_trading_hours(market)
    sessions = []

    # 上午时段
    sessions.append((hours["morning_open"], hours["morning_close"]))

    # 下午时段 (如果存在)
    if hours.get("afternoon_open") and hours.get("afternoon_close"):
        sessions.append((hours["afternoon_open"], hours["afternoon_close"]))

    return sessions


def format_trading_hours_display(market: str = "CN") -> str:
    """
    格式化交易时间用于显示

    Args:
        market: 市场代码

    Returns:
        str: 格式化的交易时间字符串
    """
    hours = get_trading_hours(market)
    market_name = MARKET_DISPLAY_NAMES.get(market.upper(), market.upper())

    morning = f"{hours['morning_open'].strftime('%H:%M')}-{hours['morning_close'].strftime('%H:%M')}"

    if hours.get("afternoon_open") and hours.get("afternoon_close"):
        afternoon = f"{hours['afternoon_open'].strftime('%H:%M')}-{hours['afternoon_close'].strftime('%H:%M')}"
        return f"{market_name} {morning}, {afternoon}"
    else:
        return f"{market_name} {morning}"


def get_supported_markets() -> List[str]:
    """
    获取支持的市场列表

    Returns:
        List[str]: 支持的市场代码列表
    """
    return list(MARKET_TRADING_HOURS.keys())


def is_market_supported(market: str) -> bool:
    """
    检查市场是否支持

    Args:
        market: 市场代码

    Returns:
        bool: 是否支持该市场
    """
    return market.upper() in MARKET_TRADING_HOURS


def get_market_timezone(market: str) -> str:
    """
    获取市场时区

    Args:
        market: 市场代码

    Returns:
        str: 时区标识
    """
    timezone_mapping = {
        "CN": "Asia/Shanghai",
        "SZ": "Asia/Shanghai",
        "SS": "Asia/Shanghai",
        "SH": "Asia/Shanghai",
        "HK": "Asia/Hong_Kong",
        "US": "America/New_York",
        "NYSE": "America/New_York",
        "NASDAQ": "America/New_York",
    }

    return timezone_mapping.get(market.upper(), "Asia/Shanghai")
