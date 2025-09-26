"""
默认配置定义

定义系统的默认配置参数。
"""

from typing import Any, Dict

# 默认配置
DEFAULT_CONFIG = {
    # 数据库配置
    "database": {
        "path": "./data/simtradedata.db",
        "timeout": 30.0,
        "check_same_thread": False,
        "backup_enabled": True,
        "backup_interval_hours": 24,
        "vacuum_interval_days": 7,
    },
    # 支持的市场
    "markets": {
        "enabled": ["SZ", "SS", "HK", "US"],
        "SZ": {
            "name": "深圳证券交易所",
            "suffix": ".SZ",
            "timezone": "Asia/Shanghai",
            "currency": "CNY",
            "trading_hours": "09:30-11:30,13:00-15:00",
            "features": ["涨跌停限制", "T+1交易", "集合竞价"],
        },
        "SS": {
            "name": "上海证券交易所",
            "suffix": ".SS",
            "timezone": "Asia/Shanghai",
            "currency": "CNY",
            "trading_hours": "09:30-11:30,13:00-15:00",
            "features": ["涨跌停限制", "T+1交易", "集合竞价"],
        },
        "HK": {
            "name": "香港证券交易所",
            "suffix": ".HK",
            "timezone": "Asia/Hong_Kong",
            "currency": "HKD",
            "trading_hours": "09:30-12:00,13:00-16:00",
            "features": ["无涨跌停限制", "T+0交易"],
        },
        "US": {
            "name": "美国证券交易所",
            "suffix": ".US",
            "timezone": "America/New_York",
            "currency": "USD",
            "trading_hours": "09:30-16:00",
            "features": ["无涨跌停限制", "T+0交易", "盘前盘后交易"],
        },
    },
    # 数据源配置
    "data_sources": {
        "akshare": {
            "enabled": True,
            "timeout": 10,
            "retry_times": 3,
            "retry_delay": 1,
            "rate_limit": 100,  # 每分钟请求数限制
        },
        "baostock": {
            "enabled": True,
            "timeout": 15,
            "retry_times": 3,
            "retry_delay": 2,
            "rate_limit": 200,
        },
        "qstock": {
            "enabled": True,
            "timeout": 10,
            "retry_times": 3,
            "retry_delay": 1,
            "rate_limit": 150,
        },
    },
    # 支持的频率
    "frequencies": {
        "supported": ["1m", "5m", "15m", "30m", "60m", "120m", "1d", "1w", "1y"],
        "default": "1d",
        "minute_frequencies": ["1m", "5m", "15m", "30m", "60m", "120m"],
        "daily_frequencies": ["1d", "1w", "1y"],
    },
    # 数据同步配置
    "sync": {
        "enabled": True,
        "daily_schedule": "02:00",  # 每日凌晨2点同步
        "auto_gap_fix": True,
        "max_gap_days": 30,
        "max_concurrent_tasks": 1,  # 数据下载任务串行
        "batch_size": 10,  # 批次大小
        "minute_data_retention_days": 30,  # 分钟线数据保留30天
        "daily_data_retention_days": 1095,  # 日线数据保留3年
        "enable_parallel_download": False,  # 禁用并行下载
        "enable_parallel_processing": True,  # 允许并行处理
        "max_processing_workers": 4,  # 本地处理最大线程数
    },
    # 查询性能配置
    "query": {
        "default_limit": 1000,
        "max_limit": 10000,
        "cache_enabled": True,
        "cache_ttl_seconds": 300,  # 缓存5分钟
        "parallel_query_enabled": False,  # 禁用并行查询
        "max_parallel_queries": 1,  # 改为1
    },
    # 日志配置
    "logging": {
        "level": "INFO",
        "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        "file_enabled": True,
        "file_path": "./logs/simtradedata.log",
        "file_max_size": "10MB",
        "file_backup_count": 5,
        "console_enabled": True,
    },
    # 监控配置（简化）
    "monitoring": {
        "enabled": False,  # 禁用过度监控
        "slow_query_threshold": 1000,  # 慢查询阈值(毫秒)
    },
    # API配置
    "api": {
        "enabled_apis": [
            # 高优先级API
            "get_history",
            "get_price",
            "get_Ashares",
            "get_stock_info",
            "get_trade_days",
            "get_all_trade_days",
            "get_fundamentals",
            "get_stock_blocks",
            "get_snapshot",
        ],
        "rate_limit_enabled": True,
        "rate_limit_per_minute": 1000,
        "response_format": "pandas",  # pandas/json/dict
        "error_handling": "strict",  # strict/lenient
    },
    # 扩展功能配置
    "extensions": {
        "technical_indicators_enabled": True,
        "custom_fields_enabled": True,
        "data_export_enabled": True,
        "backup_restore_enabled": True,
    },
}


def get_default_config() -> Dict[str, Any]:
    """获取默认配置的深拷贝"""
    import copy

    return copy.deepcopy(DEFAULT_CONFIG)


def get_market_config(market: str) -> Dict[str, Any]:
    """
    获取指定市场的配置

    Args:
        market: 市场代码 (SZ/SS/HK/US)

    Returns:
        Dict[str, Any]: 市场配置
    """
    config = get_default_config()
    return config["markets"].get(market, {})


def get_data_source_config(source: str) -> Dict[str, Any]:
    """
    获取指定数据源的配置

    Args:
        source: 数据源名称 (akshare/baostock/qstock)

    Returns:
        Dict[str, Any]: 数据源配置
    """
    config = get_default_config()
    return config["data_sources"].get(source, {})


def get_supported_frequencies() -> list:
    """获取支持的频率列表"""
    config = get_default_config()
    return config["frequencies"]["supported"]


def get_supported_markets() -> list:
    """获取支持的市场列表"""
    config = get_default_config()
    return config["markets"]["enabled"]


def is_minute_frequency(frequency: str) -> bool:
    """判断是否为分钟线频率"""
    config = get_default_config()
    return frequency in config["frequencies"]["minute_frequencies"]


def is_daily_frequency(frequency: str) -> bool:
    """判断是否为日线频率"""
    config = get_default_config()
    return frequency in config["frequencies"]["daily_frequencies"]


# 注意: 数据源优先级现在完全由数据库 data_sources 表管理
# 所有优先级配置已移至数据库以确保单一数据源


# 技术指标配置
TECHNICAL_INDICATORS_CONFIG = {
    "ma": {
        "periods": [5, 10, 20, 60],
        "enabled": True,
    },
    "ema": {
        "periods": [12, 26],
        "enabled": True,  # 启用EMA
    },
    "macd": {
        "fast_period": 12,
        "slow_period": 26,
        "signal_period": 9,
        "enabled": True,  # 启用MACD
    },
    "rsi": {
        "period": 14,
        "enabled": True,  # 启用RSI
    },
    "kdj": {
        "k_period": 9,
        "d_period": 3,
        "j_period": 3,
        "enabled": True,  # 启用KDJ
    },
}


def get_technical_indicators_config() -> Dict[str, Any]:
    """获取技术指标配置"""
    return TECHNICAL_INDICATORS_CONFIG.copy()
