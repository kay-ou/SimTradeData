"""
数据聚合器

负责多维度数据聚合、统计分析和数据挖掘功能。
"""

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, List

from ..config import Config
from ..database import DatabaseManager

logger = logging.getLogger(__name__)


class DataAggregator:
    """数据聚合器"""

    def __init__(self, db_manager: DatabaseManager, config: Config = None):
        """
        初始化数据聚合器

        Args:
            db_manager: 数据库管理器
            config: 配置对象
        """
        self.db_manager = db_manager
        self.config = config or Config()

        # 聚合维度
        self.aggregation_dimensions = {
            "time": ["daily", "weekly", "monthly", "quarterly", "yearly"],
            "market": ["SZ", "SS", "HK", "US"],
            "sector": ["industry", "concept", "region"],
            "size": ["large_cap", "mid_cap", "small_cap"],
            "style": ["growth", "value", "momentum"],
        }

        # 聚合指标
        self.aggregation_metrics = {
            "price": ["avg_price", "price_change", "price_volatility"],
            "volume": ["total_volume", "avg_volume", "volume_change"],
            "market_cap": ["total_market_cap", "avg_market_cap"],
            "valuation": ["avg_pe", "avg_pb", "avg_ps"],
            "performance": ["return_rate", "sharpe_ratio", "max_drawdown"],
        }

        logger.info("数据聚合器初始化完成")

    def aggregate_market_data(
        self, aggregation_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        聚合市场数据

        Args:
            aggregation_config: 聚合配置

        Returns:
            Dict[str, Any]: 聚合结果
        """
        try:
            dimension = aggregation_config.get("dimension", "time")
            granularity = aggregation_config.get("granularity", "daily")
            metrics = aggregation_config.get("metrics", ["price", "volume"])
            filters = aggregation_config.get("filters", {})
            start_date = aggregation_config.get("start_date")
            end_date = aggregation_config.get("end_date")

            if end_date is None:
                end_date = datetime.now().date()

            if start_date is None:
                start_date = end_date - timedelta(days=30)

            # 根据维度选择聚合方法
            if dimension == "time":
                result = self._aggregate_by_time(
                    granularity, metrics, filters, start_date, end_date
                )
            elif dimension == "market":
                result = self._aggregate_by_market(
                    metrics, filters, start_date, end_date
                )
            elif dimension == "sector":
                result = self._aggregate_by_sector(
                    granularity, metrics, filters, start_date, end_date
                )
            else:
                logger.error(f"不支持的聚合维度: {dimension}")
                return {}

            # 添加元数据
            result["metadata"] = {
                "aggregation_config": aggregation_config,
                "data_range": {
                    "start_date": str(start_date),
                    "end_date": str(end_date),
                },
                "generated_time": datetime.now().isoformat(),
            }

            logger.info(f"市场数据聚合完成: {dimension} - {granularity}")
            return result

        except Exception as e:
            logger.error(f"聚合市场数据失败: {e}")
            return {}

    def calculate_market_statistics(
        self, market: str = None, date_range: int = 30
    ) -> Dict[str, Any]:
        """
        计算市场统计数据

        Args:
            market: 市场代码，None表示所有市场
            date_range: 统计天数

        Returns:
            Dict[str, Any]: 市场统计数据
        """
        try:
            end_date = datetime.now().date()
            start_date = end_date - timedelta(days=date_range)

            # 构建查询条件
            conditions = ["date >= ?", "date <= ?", "frequency = '1d'"]
            params = [str(start_date), str(end_date)]

            if market:
                conditions.append("market = ?")
                params.append(market)

            where_clause = " AND ".join(conditions)

            # 基础统计
            basic_stats_sql = f"""
            SELECT
                COUNT(DISTINCT symbol) as total_stocks,
                COUNT(*) as total_records,
                AVG(close) as avg_price,
                SUM(volume) as total_volume,
                SUM(amount) as total_turnover,
                AVG(change_percent) as avg_change_pct,
                STDDEV(change_percent) as volatility
            FROM market_data
            WHERE {where_clause}
            """

            basic_stats = self.db_manager.fetchone(basic_stats_sql, params)

            # 涨跌统计
            change_stats_sql = f"""
            SELECT 
                SUM(CASE WHEN change_percent > 0 THEN 1 ELSE 0 END) as rising_count,
                SUM(CASE WHEN change_percent < 0 THEN 1 ELSE 0 END) as falling_count,
                SUM(CASE WHEN change_percent = 0 THEN 1 ELSE 0 END) as flat_count,
                MAX(change_percent) as max_gain,
                MIN(change_percent) as max_loss
            FROM market_data 
            WHERE {where_clause}
            """

            change_stats = self.db_manager.fetchone(change_stats_sql, params)

            # 市值统计（如果有市值数据）
            market_cap_sql = f"""
            SELECT
                SUM(close * total_shares) as total_market_cap,
                AVG(close * total_shares) as avg_market_cap
            FROM market_data h
            JOIN stocks s ON h.symbol = s.symbol
            WHERE {where_clause} AND s.total_shares IS NOT NULL
            """

            market_cap_stats = self.db_manager.fetchone(market_cap_sql, params)

            # 组合结果
            statistics = {
                "market": market or "ALL",
                "date_range": date_range,
                "period": {"start_date": str(start_date), "end_date": str(end_date)},
                "basic_stats": dict(basic_stats) if basic_stats else {},
                "change_stats": dict(change_stats) if change_stats else {},
                "market_cap_stats": dict(market_cap_stats) if market_cap_stats else {},
            }

            # 计算衍生指标
            if basic_stats and change_stats:
                total_records = basic_stats["total_records"]
                if total_records > 0:
                    statistics["derived_metrics"] = {
                        "rising_ratio": (change_stats["rising_count"] / total_records)
                        * 100,
                        "falling_ratio": (change_stats["falling_count"] / total_records)
                        * 100,
                        "flat_ratio": (change_stats["flat_count"] / total_records)
                        * 100,
                        "avg_daily_volume": (
                            basic_stats["total_volume"] / date_range
                            if basic_stats["total_volume"]
                            else 0
                        ),
                        "avg_daily_turnover": (
                            basic_stats["total_turnover"] / date_range
                            if basic_stats["total_turnover"]
                            else 0
                        ),
                    }

            logger.debug(f"市场统计计算完成: {market}, {date_range}天")
            return statistics

        except Exception as e:
            logger.error(f"计算市场统计失败: {e}")
            return {}

    def analyze_sector_performance(
        self, sector_type: str = "industry", date_range: int = 30
    ) -> List[Dict[str, Any]]:
        """
        分析板块表现

        Args:
            sector_type: 板块类型
            date_range: 分析天数

        Returns:
            List[Dict[str, Any]]: 板块表现数据
        """
        try:
            end_date = datetime.now().date()
            start_date = end_date - timedelta(days=date_range)

            # 获取板块列表
            if sector_type == "industry":
                sectors_sql = """
                SELECT DISTINCT level1_code as sector_code, level1_name as sector_name
                FROM ptrade_industry_classification 
                WHERE standard = 'sw'
                """
            else:
                sectors_sql = """
                SELECT sector_code, sector_name 
                FROM ptrade_concept_sectors 
                WHERE sector_type = ?
                """

            if sector_type == "industry":
                sectors = self.db_manager.fetchall(sectors_sql)
            else:
                sectors = self.db_manager.fetchall(sectors_sql, (sector_type,))

            sector_performance = []

            for sector in sectors:
                sector_code = sector["sector_code"]
                sector_name = sector["sector_name"]

                # 获取板块成分股
                if sector_type == "industry":
                    constituents_sql = """
                    SELECT symbol FROM ptrade_industry_classification 
                    WHERE level1_code = ? AND standard = 'sw'
                    """
                    constituents = self.db_manager.fetchall(
                        constituents_sql, (sector_code,)
                    )
                else:
                    constituents_sql = """
                    SELECT symbol FROM ptrade_sector_constituents 
                    WHERE sector_code = ?
                    """
                    constituents = self.db_manager.fetchall(
                        constituents_sql, (sector_code,)
                    )

                if not constituents:
                    continue

                symbols = [c["symbol"] for c in constituents]

                # 计算板块表现
                performance = self._calculate_portfolio_performance(
                    symbols, start_date, end_date
                )

                if performance:
                    performance.update(
                        {
                            "sector_code": sector_code,
                            "sector_name": sector_name,
                            "sector_type": sector_type,
                            "constituent_count": len(symbols),
                        }
                    )

                    sector_performance.append(performance)

            # 按收益率排序
            sector_performance.sort(key=lambda x: x.get("return_rate", 0), reverse=True)

            logger.info(
                f"板块表现分析完成: {sector_type}, {len(sector_performance)} 个板块"
            )
            return sector_performance

        except Exception as e:
            logger.error(f"分析板块表现失败: {e}")
            return []

    def generate_market_report(
        self, report_type: str = "daily", target_date: date = None
    ) -> Dict[str, Any]:
        """
        生成市场报告

        Args:
            report_type: 报告类型 (daily, weekly, monthly)
            target_date: 目标日期

        Returns:
            Dict[str, Any]: 市场报告
        """
        try:
            if target_date is None:
                target_date = datetime.now().date()

            # 确定报告周期
            if report_type == "daily":
                start_date = target_date
                period_days = 1
            elif report_type == "weekly":
                start_date = target_date - timedelta(days=6)
                period_days = 7
            elif report_type == "monthly":
                start_date = target_date.replace(day=1)
                period_days = (target_date - start_date).days + 1
            else:
                logger.error(f"不支持的报告类型: {report_type}")
                return {}

            report = {
                "report_type": report_type,
                "target_date": str(target_date),
                "period": {"start_date": str(start_date), "end_date": str(target_date)},
                "generated_time": datetime.now().isoformat(),
            }

            # 市场概览
            report["market_overview"] = {}
            for market in ["SZ", "SS", "HK", "US"]:
                market_stats = self.calculate_market_statistics(market, period_days)
                if market_stats:
                    report["market_overview"][market] = market_stats

            # 板块表现
            report["sector_performance"] = {
                "industry": self.analyze_sector_performance("industry", period_days)[
                    :10
                ],  # 前10个行业
                "concept": self.analyze_sector_performance("concept", period_days)[
                    :10
                ],  # 前10个概念
            }

            # 市场热点
            report["market_highlights"] = self._identify_market_highlights(
                start_date, target_date
            )

            # 技术指标概览
            report["technical_overview"] = self._generate_technical_overview(
                target_date
            )

            logger.info(f"市场报告生成完成: {report_type} - {target_date}")
            return report

        except Exception as e:
            logger.error(f"生成市场报告失败: {e}")
            return {}

    def _aggregate_by_time(
        self,
        granularity: str,
        metrics: List[str],
        filters: Dict[str, Any],
        start_date: date,
        end_date: date,
    ) -> Dict[str, Any]:
        """按时间维度聚合"""
        try:
            # 根据粒度确定时间分组
            if granularity == "daily":
                date_trunc = "trade_date"
            elif granularity == "weekly":
                date_trunc = "strftime('%Y-W%W', trade_date)"
            elif granularity == "monthly":
                date_trunc = "strftime('%Y-%m', trade_date)"
            else:
                date_trunc = "trade_date"

            # 构建查询
            conditions = ["trade_date >= ?", "trade_date <= ?", "frequency = '1d'"]
            params = [str(start_date), str(end_date)]

            # 添加过滤条件
            for key, value in filters.items():
                if key == "market":
                    conditions.append("market = ?")
                    params.append(value)
                elif key == "symbols":
                    placeholders = ",".join(["?" for _ in value])
                    conditions.append(f"symbol IN ({placeholders})")
                    params.extend(value)

            where_clause = " AND ".join(conditions)

            # 构建聚合查询
            select_fields = [f"{date_trunc} as period"]

            if "price" in metrics:
                select_fields.extend(
                    [
                        "AVG(close) as avg_price",
                        "AVG(change_percent) as avg_change_pct",
                        "STDDEV(change_percent) as price_volatility",
                    ]
                )

            if "volume" in metrics:
                select_fields.extend(
                    ["SUM(volume) as total_volume", "AVG(volume) as avg_volume"]
                )

            sql = f"""
            SELECT {', '.join(select_fields)}
            FROM market_data 
            WHERE {where_clause}
            GROUP BY {date_trunc}
            ORDER BY period
            """

            results = self.db_manager.fetchall(sql, params)

            return {
                "dimension": "time",
                "granularity": granularity,
                "data": [dict(row) for row in results],
            }

        except Exception as e:
            logger.error(f"按时间聚合失败: {e}")
            return {}

    def _aggregate_by_market(
        self,
        metrics: List[str],
        filters: Dict[str, Any],
        start_date: date,
        end_date: date,
    ) -> Dict[str, Any]:
        """按市场维度聚合"""
        try:
            conditions = ["trade_date >= ?", "trade_date <= ?", "frequency = '1d'"]
            params = [str(start_date), str(end_date)]

            where_clause = " AND ".join(conditions)

            # 构建聚合查询
            select_fields = ["market"]

            if "price" in metrics:
                select_fields.extend(
                    ["AVG(close) as avg_price", "AVG(change_percent) as avg_change_pct"]
                )

            if "volume" in metrics:
                select_fields.extend(
                    [
                        "SUM(volume) as total_volume",
                        "COUNT(DISTINCT symbol) as stock_count",
                    ]
                )

            sql = f"""
            SELECT {', '.join(select_fields)}
            FROM market_data 
            WHERE {where_clause}
            GROUP BY market
            ORDER BY market
            """

            results = self.db_manager.fetchall(sql, params)

            return {"dimension": "market", "data": [dict(row) for row in results]}

        except Exception as e:
            logger.error(f"按市场聚合失败: {e}")
            return {}

    def _aggregate_by_sector(
        self,
        granularity: str,
        metrics: List[str],
        filters: Dict[str, Any],
        start_date: date,
        end_date: date,
    ) -> Dict[str, Any]:
        """按板块维度聚合"""
        try:
            # 获取行业分类数据
            sector_sql = """
            SELECT h.symbol, h.trade_date, h.close, h.change_percent, h.volume,
                   ic.level1_code as sector_code, ic.level1_name as sector_name
            FROM market_data h
            JOIN ptrade_industry_classification ic ON h.symbol = ic.symbol
            WHERE h.trade_date >= ? AND h.trade_date <= ? 
            AND h.frequency = '1d' AND ic.standard = 'sw'
            """

            results = self.db_manager.fetchall(
                sector_sql, [str(start_date), str(end_date)]
            )

            # 按板块聚合
            sector_data = defaultdict(
                lambda: {"total_volume": 0, "price_changes": [], "stock_count": set()}
            )

            for row in results:
                sector_code = row["sector_code"]
                sector_data[sector_code]["total_volume"] += row["volume"] or 0
                sector_data[sector_code]["price_changes"].append(
                    row["change_percent"] or 0
                )
                sector_data[sector_code]["stock_count"].add(row["symbol"])
                sector_data[sector_code]["sector_name"] = row["sector_name"]

            # 计算聚合指标
            aggregated_data = []
            for sector_code, data in sector_data.items():
                if data["price_changes"]:
                    avg_change = sum(data["price_changes"]) / len(data["price_changes"])
                else:
                    avg_change = 0

                aggregated_data.append(
                    {
                        "sector_code": sector_code,
                        "sector_name": data["sector_name"],
                        "avg_change_pct": round(avg_change, 4),
                        "total_volume": data["total_volume"],
                        "stock_count": len(data["stock_count"]),
                    }
                )

            # 按平均涨跌幅排序
            aggregated_data.sort(key=lambda x: x["avg_change_pct"], reverse=True)

            return {
                "dimension": "sector",
                "granularity": granularity,
                "data": aggregated_data,
            }

        except Exception as e:
            logger.error(f"按板块聚合失败: {e}")
            return {}

    def _calculate_portfolio_performance(
        self, symbols: List[str], start_date: date, end_date: date
    ) -> Dict[str, Any]:
        """计算投资组合表现"""
        try:
            if not symbols:
                return {}

            # 获取价格数据
            placeholders = ",".join(["?" for _ in symbols])
            sql = f"""
            SELECT symbol, trade_date, close, change_percent
            FROM market_data 
            WHERE symbol IN ({placeholders}) 
            AND trade_date >= ? AND trade_date <= ?
            AND frequency = '1d'
            ORDER BY symbol, trade_date
            """

            params = symbols + [str(start_date), str(end_date)]
            results = self.db_manager.fetchall(sql, params)

            if not results:
                return {}

            # 按股票分组
            stock_data = defaultdict(list)
            for row in results:
                stock_data[row["symbol"]].append(
                    {
                        "date": row["trade_date"],
                        "close": row["close"],
                        "change_percent": row["change_percent"] or 0,
                    }
                )

            # 计算等权重组合收益
            daily_returns = defaultdict(list)

            for symbol, data in stock_data.items():
                for record in data:
                    daily_returns[record["date"]].append(record["change_percent"])

            # 计算组合日收益率
            portfolio_returns = []
            for date, returns in daily_returns.items():
                if returns:
                    avg_return = sum(returns) / len(returns)
                    portfolio_returns.append(avg_return)

            if not portfolio_returns:
                return {}

            # 计算性能指标
            total_return = sum(portfolio_returns)
            avg_return = (
                total_return / len(portfolio_returns) if portfolio_returns else 0
            )

            # 计算波动率
            if len(portfolio_returns) > 1:
                variance = sum((r - avg_return) ** 2 for r in portfolio_returns) / (
                    len(portfolio_returns) - 1
                )
                volatility = variance**0.5
            else:
                volatility = 0

            return {
                "return_rate": round(total_return, 4),
                "avg_daily_return": round(avg_return, 4),
                "volatility": round(volatility, 4),
                "trading_days": len(portfolio_returns),
                "valid_stocks": len(stock_data),
            }

        except Exception as e:
            logger.error(f"计算投资组合表现失败: {e}")
            return {}

    def _identify_market_highlights(
        self, start_date: date, end_date: date
    ) -> Dict[str, Any]:
        """识别市场热点"""
        try:
            # 涨幅榜
            gainers_sql = """
            SELECT symbol, name as stock_name, close, change_percent, volume
            FROM market_data h
            LEFT JOIN stocks s ON h.symbol = s.symbol
            WHERE trade_date = ? AND frequency = '1d'
            ORDER BY change_percent DESC LIMIT 10
            """

            gainers = self.db_manager.fetchall(gainers_sql, [str(end_date)])

            # 跌幅榜
            losers_sql = """
            SELECT symbol, name as stock_name, close, change_percent, volume
            FROM market_data h
            LEFT JOIN stocks s ON h.symbol = s.symbol
            WHERE trade_date = ? AND frequency = '1d'
            ORDER BY change_percent ASC LIMIT 10
            """

            losers = self.db_manager.fetchall(losers_sql, [str(end_date)])

            # 成交量榜
            volume_leaders_sql = """
            SELECT symbol, name as stock_name, close, change_percent, volume
            FROM market_data h
            LEFT JOIN stocks s ON h.symbol = s.symbol
            WHERE trade_date = ? AND frequency = '1d'
            ORDER BY volume DESC LIMIT 10
            """

            volume_leaders = self.db_manager.fetchall(
                volume_leaders_sql, [str(end_date)]
            )

            return {
                "top_gainers": [dict(row) for row in gainers],
                "top_losers": [dict(row) for row in losers],
                "volume_leaders": [dict(row) for row in volume_leaders],
            }

        except Exception as e:
            logger.error(f"识别市场热点失败: {e}")
            return {}

    def _generate_technical_overview(self, target_date: date) -> Dict[str, Any]:
        """生成技术指标概览"""
        try:
            # 获取市场主要指数的数据来分析整体趋势
            major_indices = [
                "000001.SZ",
                "000002.SZ",
                "600000.SS",
                "600036.SS",
            ]  # 代表性股票

            # 获取最近30天的数据用于分析
            start_date = target_date - timedelta(days=30)

            price_changes = []
            volumes = []

            for symbol in major_indices:
                try:
                    # 获取最近的市场数据
                    sql = """
                    SELECT close, volume, change_percent 
                    FROM market_data 
                    WHERE symbol = ? AND date >= ? AND date <= ?
                    ORDER BY date DESC 
                    LIMIT 20
                    """

                    data = self.db_manager.fetchall(
                        sql, (symbol, str(start_date), str(target_date))
                    )

                    if data:
                        # 收集价格变化和成交量数据
                        for row in data:
                            if row["change_percent"] is not None:
                                price_changes.append(float(row["change_percent"]))
                            if row["volume"] is not None:
                                volumes.append(float(row["volume"]))

                except Exception as e:
                    logger.warning(f"获取 {symbol} 技术数据失败: {e}")

            # 分析市场情感
            market_sentiment = "neutral"
            trend_direction = "sideways"
            volatility_level = "normal"

            if price_changes:
                avg_change = sum(price_changes) / len(price_changes)
                sum(1 for x in price_changes if x > 0)
                sum(1 for x in price_changes if x < 0)

                # 判断市场情感
                if avg_change > 1.0:
                    market_sentiment = "bullish"
                    trend_direction = "upward"
                elif avg_change < -1.0:
                    market_sentiment = "bearish"
                    trend_direction = "downward"
                else:
                    market_sentiment = "neutral"
                    trend_direction = "sideways"

                # 判断波动性
                if price_changes:
                    price_std = (
                        sum((x - avg_change) ** 2 for x in price_changes)
                        / len(price_changes)
                    ) ** 0.5
                    if price_std > 3.0:
                        volatility_level = "high"
                    elif price_std < 1.0:
                        volatility_level = "low"
                    else:
                        volatility_level = "normal"

            # 简单的支撑阻力位计算（基于最近价格）
            support_resistance = {"support": None, "resistance": None}

            if price_changes:
                # 获取最近的收盘价数据用于支撑阻力分析
                recent_closes = []
                for symbol in major_indices[:2]:  # 只用前两个代表性股票
                    try:
                        sql = "SELECT close FROM market_data WHERE symbol = ? AND date <= ? ORDER BY date DESC LIMIT 10"
                        data = self.db_manager.fetchall(sql, (symbol, str(target_date)))
                        for row in data:
                            if row["close"]:
                                recent_closes.append(float(row["close"]))
                    except:
                        pass

                if recent_closes:
                    support_resistance = {
                        "support": round(
                            min(recent_closes) * 0.98, 2
                        ),  # 支撑位略低于最低价
                        "resistance": round(
                            max(recent_closes) * 1.02, 2
                        ),  # 阻力位略高于最高价
                    }

            return {
                "market_sentiment": market_sentiment,
                "trend_direction": trend_direction,
                "volatility_level": volatility_level,
                "support_resistance": support_resistance,
                "analysis_date": str(target_date),
                "data_points": len(price_changes),
                "avg_change_percent": (
                    round(sum(price_changes) / len(price_changes), 2)
                    if price_changes
                    else 0
                ),
            }

        except Exception as e:
            logger.error(f"生成技术指标概览失败: {e}")
            return {
                "market_sentiment": "unknown",
                "trend_direction": "unknown",
                "volatility_level": "unknown",
                "support_resistance": {"support": None, "resistance": None},
                "error": str(e),
            }

    def get_aggregator_stats(self) -> Dict[str, Any]:
        """获取聚合器统计信息"""
        return {
            "aggregation_dimensions": self.aggregation_dimensions,
            "aggregation_metrics": self.aggregation_metrics,
            "supported_features": [
                "多维度数据聚合",
                "市场统计分析",
                "板块表现分析",
                "市场报告生成",
                "投资组合分析",
                "市场热点识别",
            ],
        }
