"""
全新数据库架构设计

基于PTrade API需求和数据源特点，从零设计的高效数据库结构。
消除冗余，优化性能，完善功能。
"""

import logging
from typing import Dict, List

logger = logging.getLogger(__name__)

# 全新数据库表结构定义
DATABASE_SCHEMA = {
    # 1. 股票基础信息表 (已优化：保留必要的行业和概念数据字段)
    "stocks": """
        CREATE TABLE stocks (
            symbol TEXT PRIMARY KEY,              -- 股票代码 (000001.SZ)
            name TEXT NOT NULL,                   -- 股票名称
            market TEXT NOT NULL,                 -- 市场 (SZ/SS/HK/US) - exchange可从此推导

            -- 分类信息
            industry_l1 TEXT,                     -- 一级行业 (从BaoStock获取)
            industry_l2 TEXT,                     -- 二级行业/行业分类 (从BaoStock industryClassification获取)
            concepts TEXT,                        -- 概念标签 (JSON数组，从QStock/AKShare获取)

            -- 基础属性
            list_date DATE,                       -- 上市日期
            delist_date DATE,                     -- 退市日期
            lot_size INTEGER DEFAULT 100,         -- 最小交易单位 (CNY默认值在应用层处理)

            -- 股本信息
            total_shares REAL,                    -- 总股本
            float_shares REAL,                    -- 流通股本

            -- 状态
            status TEXT DEFAULT 'active',         -- active/suspended/delisted
            is_st BOOLEAN DEFAULT FALSE,          -- 是否ST

            -- 元数据
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    # 2. 交易日历表 (多市场版本)
    "trading_calendar": """
        CREATE TABLE trading_calendar (
            date TEXT NOT NULL,                   -- 日期 YYYY-MM-DD
            market TEXT NOT NULL,                 -- 市场代码 CN/HK/US
            is_trading INTEGER NOT NULL DEFAULT 1,-- 是否交易日 (1=是, 0=否)
            
            PRIMARY KEY (date, market)
        )
    """,
    # 3. 历史行情数据表 (核心表)
    "market_data": """
        CREATE TABLE market_data (
            symbol TEXT NOT NULL,
            date DATE NOT NULL,
            time TIME,                            -- 分钟线时间
            frequency TEXT NOT NULL,              -- 1d/5m/15m/30m/60m
            
            -- OHLCV数据
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            amount REAL,                          -- 成交金额
            
            -- 价格变动
            prev_close REAL,                      -- 昨收价
            change_amount REAL,                   -- 涨跌额
            change_percent REAL,                  -- 涨跌幅
            amplitude REAL,                       -- 振幅
            
            -- A股特有
            high_limit REAL,                      -- 涨停价
            low_limit REAL,                       -- 跌停价
            is_limit_up BOOLEAN DEFAULT FALSE,    -- 是否涨停
            is_limit_down BOOLEAN DEFAULT FALSE,  -- 是否跌停
            
            -- 交易指标
            turnover_rate REAL,                   -- 换手率
            
            -- 数据质量
            source TEXT NOT NULL,                 -- 数据源
            quality_score INTEGER DEFAULT 100,   -- 质量评分
            is_adjusted BOOLEAN DEFAULT FALSE,    -- 是否复权
            
            -- 时间戳
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            
            PRIMARY KEY (symbol, date, time, frequency)
        )
    """,
    # 4. 估值指标表
    "valuations": """
        CREATE TABLE valuations (
            symbol TEXT NOT NULL,
            date DATE NOT NULL,
            
            -- 估值比率
            pe_ratio REAL,                        -- 市盈率
            pb_ratio REAL,                        -- 市净率
            ps_ratio REAL,                        -- 市销率
            pcf_ratio REAL,                       -- 市现率
            peg_ratio REAL,                       -- PEG比率
            
            -- 市值
            market_cap REAL,                      -- 总市值
            circulating_cap REAL,                -- 流通市值
            
            -- 数据源
            source TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            
            PRIMARY KEY (symbol, date)
        )
    """,
    # 5. 技术指标表
    "technical_indicators": """
        CREATE TABLE technical_indicators (
            symbol TEXT NOT NULL,
            date DATE NOT NULL,
            frequency TEXT NOT NULL DEFAULT '1d',
            
            -- 移动平均线
            ma5 REAL, ma10 REAL, ma20 REAL, ma60 REAL,
            ma120 REAL, ma250 REAL,
            
            -- 指数移动平均
            ema12 REAL, ema26 REAL,
            
            -- MACD
            macd_dif REAL,
            macd_dea REAL,
            macd_histogram REAL,
            
            -- KDJ
            kdj_k REAL,
            kdj_d REAL,
            kdj_j REAL,
            
            -- RSI
            rsi_6 REAL,
            rsi_12 REAL,
            rsi_24 REAL,
            
            -- 布林带
            boll_upper REAL,
            boll_middle REAL,
            boll_lower REAL,
            
            -- 其他指标
            cci REAL,
            williams_r REAL,
            
            calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            
            PRIMARY KEY (symbol, date, frequency)
        )
    """,
    # 6. 财务数据表
    "financials": """
        CREATE TABLE financials (
            symbol TEXT NOT NULL,
            report_date DATE NOT NULL,
            report_type TEXT NOT NULL,            -- Q1/Q2/Q3/Q4/annual
            
            -- 损益表
            revenue REAL,                         -- 营业收入
            operating_profit REAL,               -- 营业利润
            net_profit REAL,                     -- 净利润
            gross_margin REAL,                   -- 毛利率
            net_margin REAL,                     -- 净利率
            
            -- 资产负债表
            total_assets REAL,                   -- 总资产
            total_liabilities REAL,             -- 总负债
            shareholders_equity REAL,            -- 股东权益
            
            -- 现金流量表
            operating_cash_flow REAL,            -- 经营现金流
            investing_cash_flow REAL,            -- 投资现金流
            financing_cash_flow REAL,            -- 筹资现金流
            
            -- 每股指标
            eps REAL,                            -- 每股收益
            bps REAL,                            -- 每股净资产
            
            -- 财务比率
            roe REAL,                            -- 净资产收益率
            roa REAL,                            -- 总资产收益率
            debt_ratio REAL,                     -- 资产负债率
            
            -- 数据源
            source TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            
            PRIMARY KEY (symbol, report_date, report_type)
        )
    """,
    # 7. 分红除权表
    "corporate_actions": """
        CREATE TABLE corporate_actions (
            symbol TEXT NOT NULL,
            ex_date DATE NOT NULL,               -- 除权除息日
            record_date DATE,                    -- 股权登记日
            
            -- 分红
            cash_dividend REAL DEFAULT 0,        -- 现金分红(每股)
            stock_dividend REAL DEFAULT 0,       -- 股票分红(每股)
            
            -- 配股
            rights_ratio REAL DEFAULT 0,         -- 配股比例
            rights_price REAL DEFAULT 0,         -- 配股价格
            
            -- 拆股合股
            split_ratio REAL DEFAULT 1,          -- 拆股比例
            
            -- 复权因子
            adj_factor REAL DEFAULT 1,           -- 复权因子
            
            source TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            
            PRIMARY KEY (symbol, ex_date)
        )
    """,
    # 8. 数据源配置表
    "data_sources": """
        CREATE TABLE data_sources (
            name TEXT PRIMARY KEY,               -- 数据源名称
            type TEXT NOT NULL,                  -- akshare/baostock/qstock
            
            -- 配置
            enabled BOOLEAN DEFAULT TRUE,
            priority INTEGER DEFAULT 1,          -- 优先级(1最高)
            rate_limit INTEGER DEFAULT 60,       -- 每分钟请求限制
            
            -- 支持的功能
            supports_realtime BOOLEAN DEFAULT FALSE,
            supports_history BOOLEAN DEFAULT TRUE,
            supports_financials BOOLEAN DEFAULT FALSE,
            
            -- 市场支持
            markets TEXT,                        -- JSON数组: ["SZ","SS","HK","US"]
            frequencies TEXT,                    -- JSON数组: ["1d","5m","15m"]
            
            -- 状态
            status TEXT DEFAULT 'active',        -- active/disabled/error
            last_check TIMESTAMP,
            
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    # 9. 数据源质量监控表
    "data_source_quality": """
        CREATE TABLE data_source_quality (
            source_name TEXT NOT NULL,
            symbol TEXT,                         -- NULL表示全局统计
            data_type TEXT NOT NULL,             -- ohlcv/financials/valuations
            date DATE NOT NULL,

            -- 质量指标
            success_rate REAL DEFAULT 100,
            completeness_rate REAL DEFAULT 100,
            accuracy_score REAL DEFAULT 100,
            timeliness_score REAL DEFAULT 100,

            -- 统计信息
            total_requests INTEGER DEFAULT 0,
            successful_requests INTEGER DEFAULT 0,
            failed_requests INTEGER DEFAULT 0,
            avg_response_time REAL,

            -- 错误信息
            last_error TEXT,
            error_count INTEGER DEFAULT 0,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            PRIMARY KEY (source_name, symbol, data_type, date)
        )
    """,
    # 10. 同步状态表
    "sync_status": """
        CREATE TABLE sync_status (
            symbol TEXT NOT NULL,
            frequency TEXT NOT NULL,
            
            -- 同步进度
            last_sync_date DATE,
            last_data_date DATE,
            next_sync_date DATE,
            
            -- 状态
            status TEXT DEFAULT 'pending',        -- pending/running/completed/failed
            progress REAL DEFAULT 0,             -- 进度百分比
            
            -- 错误信息
            error_message TEXT,
            retry_count INTEGER DEFAULT 0,
            
            -- 统计
            total_records INTEGER DEFAULT 0,
            sync_duration REAL,                  -- 同步耗时(秒)
            
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            
            PRIMARY KEY (symbol, frequency)
        )
    """,
    # 11. 扩展数据同步状态表
    "extended_sync_status": """
        CREATE TABLE extended_sync_status (
            symbol TEXT NOT NULL,
            sync_type TEXT NOT NULL,                 -- 'financials'/'valuations'/'indicators'
            target_date TEXT NOT NULL,               -- 目标日期
            
            -- 同步状态
            status TEXT DEFAULT 'pending',           -- pending/processing/completed/failed/skipped
            last_updated TIMESTAMP,
            
            -- 进度信息
            phase TEXT DEFAULT 'extended_data',      -- 同步阶段标识
            session_id TEXT,                         -- 同步会话ID
            
            -- 数据统计
            records_count INTEGER DEFAULT 0,
            
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            
            PRIMARY KEY (symbol, sync_type, target_date)
        )
    """,
    # 12. 系统配置表
    "system_config": """
        CREATE TABLE system_config (
            key TEXT PRIMARY KEY,
            value TEXT,
            description TEXT,
            category TEXT DEFAULT 'general',
            is_encrypted BOOLEAN DEFAULT FALSE,
            
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
}

# 索引定义
DATABASE_INDEXES = {
    # 股票表索引
    "idx_stocks_market": "CREATE INDEX idx_stocks_market ON stocks(market, status)",
    "idx_stocks_industry": "CREATE INDEX idx_stocks_industry ON stocks(industry_l1, industry_l2)",
    "idx_stocks_list_date": "CREATE INDEX idx_stocks_list_date ON stocks(list_date)",
    "idx_stocks_status": "CREATE INDEX idx_stocks_status ON stocks(status)",  # 新增：活跃股票查询优化
    # 行情数据索引
    "idx_market_data_symbol_date": "CREATE INDEX idx_market_data_symbol_date ON market_data(symbol, date DESC)",
    "idx_market_data_date_freq": "CREATE INDEX idx_market_data_date_freq ON market_data(date DESC, frequency)",
    "idx_market_data_symbol_freq": "CREATE INDEX idx_market_data_symbol_freq ON market_data(symbol, frequency, date DESC)",
    "idx_market_data_source": "CREATE INDEX idx_market_data_source ON market_data(source, quality_score)",
    "idx_market_data_created_at": "CREATE INDEX idx_market_data_created_at ON market_data(created_at DESC)",  # 新增：最近数据查询优化
    # 估值指标索引
    "idx_valuations_symbol_date": "CREATE INDEX idx_valuations_symbol_date ON valuations(symbol, date DESC)",
    "idx_valuations_date": "CREATE INDEX idx_valuations_date ON valuations(date DESC)",
    "idx_valuations_created_at": "CREATE INDEX idx_valuations_created_at ON valuations(created_at DESC)",  # 新增：最近数据查询优化
    # 技术指标索引
    "idx_technical_symbol_freq_date": "CREATE INDEX idx_technical_symbol_freq_date ON technical_indicators(symbol, frequency, date DESC)",
    "idx_technical_created_at": "CREATE INDEX idx_technical_created_at ON technical_indicators(calculated_at DESC)",  # 新增：最近计算指标查询
    # 财务数据索引
    "idx_financials_symbol_date": "CREATE INDEX idx_financials_symbol_date ON financials(symbol, report_date DESC)",
    "idx_financials_report_date": "CREATE INDEX idx_financials_report_date ON financials(report_date DESC, report_type)",
    "idx_financials_created_at": "CREATE INDEX idx_financials_created_at ON financials(created_at DESC)",  # 新增：最近财务数据查询优化
    "idx_financials_symbol_report": "CREATE INDEX idx_financials_symbol_report ON financials(symbol, report_date, report_type)",  # 新增：复合查询优化
    # 数据质量索引
    "idx_data_quality_source": "CREATE INDEX idx_data_quality_source ON data_source_quality(source_name, data_type, date DESC)",
    "idx_data_quality_symbol": "CREATE INDEX idx_data_quality_symbol ON data_source_quality(symbol, source_name)",
    # 同步状态索引
    "idx_sync_status_date": "CREATE INDEX idx_sync_status_date ON sync_status(last_sync_date DESC)",
    "idx_sync_status_status": "CREATE INDEX idx_sync_status_status ON sync_status(status, next_sync_date)",
    # 扩展数据同步状态索引 - 关键性能优化
    "idx_extended_sync_symbol_date": "CREATE INDEX idx_extended_sync_symbol_date ON extended_sync_status(symbol, target_date DESC)",
    "idx_extended_sync_status": "CREATE INDEX idx_extended_sync_status ON extended_sync_status(status, phase)",
    "idx_extended_sync_session": "CREATE INDEX idx_extended_sync_session ON extended_sync_status(session_id, updated_at DESC)",
    "idx_extended_sync_target_status": "CREATE INDEX idx_extended_sync_target_status ON extended_sync_status(target_date, status)",  # 新增：关键优化
    "idx_extended_sync_symbol_status": "CREATE INDEX idx_extended_sync_symbol_status ON extended_sync_status(symbol, status, target_date)",  # 新增：复合查询优化
    # 交易日历索引优化
    "idx_trading_calendar_date": "CREATE INDEX idx_trading_calendar_date ON trading_calendar(date DESC)",  # 新增：日期查询优化
    "idx_trading_calendar_market_date": "CREATE INDEX idx_trading_calendar_market_date ON trading_calendar(market, date DESC)",  # 新增：市场日期复合查询
}


def create_database_schema(db_manager) -> bool:
    """
    创建完整的数据库架构

    Args:
        db_manager: 数据库管理器

    Returns:
        bool: 创建是否成功
    """
    try:
        logger.info("开始创建全新数据库架构...")

        with db_manager.transaction() as conn:
            # 1. 创建所有表
            for table_name, schema_sql in DATABASE_SCHEMA.items():
                logger.info(f"创建表: {table_name}")
                conn.execute(schema_sql)

            # 2. 创建所有索引
            for index_name, index_sql in DATABASE_INDEXES.items():
                logger.info(f"创建索引: {index_name}")
                conn.execute(index_sql)

            # 3. 初始化系统配置
            _initialize_system_config(conn)

            # 4. 初始化数据源配置
            _initialize_data_sources(conn)

        logger.info("数据库架构创建完成")
        return True

    except Exception as e:
        logger.error(f"创建数据库架构失败: {e}")
        return False


def _initialize_system_config(conn):
    """初始化系统配置"""
    configs = [
        ("db_version", "3.0.0", "数据库版本", "system"),
        ("schema_created_at", "2025-01-26", "架构创建时间", "system"),
        ("auto_sync_enabled", "true", "是否启用自动同步", "sync"),
        ("default_frequency", "1d", "默认数据频率", "sync"),
        ("quality_threshold", "80", "数据质量阈值", "quality"),
        ("max_retry_count", "3", "最大重试次数", "sync"),
    ]

    sql = """
    INSERT OR REPLACE INTO system_config (key, value, description, category)
    VALUES (?, ?, ?, ?)
    """

    for config in configs:
        conn.execute(sql, config)


def _initialize_data_sources(conn):
    """初始化数据源配置"""
    sources = [
        (
            "baostock",  # 优先级1: 最稳定，数据质量高
            "baostock",
            True,
            1,  # 最高优先级
            120,
            False,
            True,
            True,
            '["SZ","SS"]',
            '["1d"]',  # BaoStock只支持日线
        ),
        (
            "akshare",  # 优先级2: 功能全面但不稳定
            "akshare",
            True,
            2,
            60,
            True,
            True,
            True,
            '["SZ","SS","HK","US"]',
            '["1d","5m","15m","30m","60m"]',
        ),
        (
            "qstock",  # 优先级3: 备用数据源
            "qstock",
            True,
            3,
            100,
            False,
            True,
            False,
            '["SZ","SS"]',
            '["1d","5m","15m","30m","60m"]',
        ),
    ]

    sql = """
    INSERT OR REPLACE INTO data_sources 
    (name, type, enabled, priority, rate_limit, supports_realtime, 
     supports_history, supports_financials, markets, frequencies)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    for source in sources:
        conn.execute(sql, source)


def get_table_list() -> List[str]:
    """获取所有表名列表"""
    return list(DATABASE_SCHEMA.keys())


def get_table_schema(table_name: str) -> str:
    """获取指定表的架构"""
    return DATABASE_SCHEMA.get(table_name, "")


def validate_schema(db_manager) -> Dict[str, bool]:
    """
    验证数据库架构完整性

    Args:
        db_manager: 数据库管理器

    Returns:
        Dict[str, bool]: 验证结果
    """
    results = {}

    try:
        # 检查所有表是否存在
        for table_name in DATABASE_SCHEMA.keys():
            results[f"table_{table_name}"] = db_manager.table_exists(table_name)

        # 检查关键索引
        for index_name in DATABASE_INDEXES.keys():
            try:
                sql = f"SELECT name FROM sqlite_master WHERE type='index' AND name='{index_name}'"
                result = db_manager.fetchone(sql)
                results[f"index_{index_name}"] = result is not None
            except Exception:
                results[f"index_{index_name}"] = False

        # 检查系统配置
        try:
            sql = "SELECT COUNT(*) as count FROM system_config"
            result = db_manager.fetchone(sql)
            results["system_config_initialized"] = result["count"] > 0
        except Exception:
            results["system_config_initialized"] = False

    except Exception as e:
        logger.error(f"验证架构失败: {e}")
        results["validation_error"] = str(e)

    return results
