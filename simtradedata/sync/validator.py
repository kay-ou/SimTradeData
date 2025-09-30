"""
数据验证器

负责数据完整性检查、数据质量评估和异常数据报告。
"""

import logging
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, List

from ..config import Config
from ..database import DatabaseManager

logger = logging.getLogger(__name__)


class DataValidator:
    """数据验证器"""

    def __init__(self, db_manager: DatabaseManager, config: Config = None):
        """
        初始化数据验证器

        Args:
            db_manager: 数据库管理器
            config: 配置对象
        """
        self.db_manager = db_manager
        self.config = config or Config()

        # 验证配置
        self.min_data_quality = self.config.get("validation.min_data_quality", 60)
        self.max_price_change_pct = self.config.get(
            "validation.max_price_change_pct", 20.0
        )
        self.min_volume_threshold = self.config.get(
            "validation.min_volume_threshold", 100
        )
        self.check_frequencies = self.config.get("validation.frequencies", ["1d"])

        logger.info("数据验证器初始化完成")

    def validate_all_data(
        self,
        start_date: date = None,
        end_date: date = None,
        symbols: List[str] = None,
        frequencies: List[str] = None,
    ) -> Dict[str, Any]:
        """
        验证所有数据

        Args:
            start_date: 开始日期，默认为7天前
            end_date: 结束日期，默认为今天
            symbols: 股票代码列表，默认为所有活跃股票
            frequencies: 频率列表，默认为配置中的频率

        Returns:
            Dict[str, Any]: 验证结果
        """
        if start_date is None:
            start_date = datetime.now().date() - timedelta(days=7)

        if end_date is None:
            end_date = datetime.now().date()

        if frequencies is None:
            frequencies = self.check_frequencies

        try:
            logger.info(
                f"开始数据验证: {start_date} 到 {end_date}, 频率: {frequencies}"
            )

            # 获取需要验证的股票列表
            if symbols is None:
                symbols = self._get_active_symbols()

            validation_result = {
                "start_date": str(start_date),
                "end_date": str(end_date),
                "total_symbols": len(symbols),
                "frequencies": frequencies,
                "validation_by_frequency": {},
                "summary": {
                    "total_records": 0,
                    "valid_records": 0,
                    "invalid_records": 0,
                    "validation_rate": 0.0,
                    "issue_types": defaultdict(int),
                },
            }

            # 按频率验证
            for frequency in frequencies:
                freq_result = self._validate_frequency_data(
                    symbols, start_date, end_date, frequency
                )
                validation_result["validation_by_frequency"][frequency] = freq_result

                # 更新汇总统计
                validation_result["summary"]["total_records"] += freq_result[
                    "total_records"
                ]
                validation_result["summary"]["valid_records"] += freq_result[
                    "valid_records"
                ]
                validation_result["summary"]["invalid_records"] += freq_result[
                    "invalid_records"
                ]

                for issue_type, count in freq_result["issue_types"].items():
                    validation_result["summary"]["issue_types"][issue_type] += count

            # 计算验证率
            total = validation_result["summary"]["total_records"]
            if total > 0:
                validation_result["summary"]["validation_rate"] = (
                    validation_result["summary"]["valid_records"] / total * 100
                )

            logger.info(
                f"数据验证完成: 总记录={total}, "
                f"有效={validation_result['summary']['valid_records']}, "
                f"验证率={validation_result['summary']['validation_rate']:.2f}%"
            )

            return validation_result

        except Exception as e:
            logger.error(f"数据验证失败: {e}")
            raise

    def validate_symbol_data(
        self, symbol: str, start_date: date, end_date: date, frequency: str = "1d"
    ) -> Dict[str, Any]:
        """
        验证单个股票的数据

        Args:
            symbol: 股票代码
            start_date: 开始日期
            end_date: 结束日期
            frequency: 频率

        Returns:
            Dict[str, Any]: 验证结果
        """
        try:
            logger.debug(
                f"验证股票数据: {symbol} {start_date} 到 {end_date} {frequency}"
            )

            result = {
                "symbol": symbol,
                "frequency": frequency,
                "start_date": str(start_date),
                "end_date": str(end_date),
                "total_records": 0,
                "valid_records": 0,
                "invalid_records": 0,
                "issues": [],
                "data_quality_stats": {},
            }

            # 获取数据
            data_records = self._get_symbol_data(
                symbol, start_date, end_date, frequency
            )
            result["total_records"] = len(data_records)

            if not data_records:
                return result

            # 逐条验证数据
            for record in data_records:
                issues = self._validate_record(record, frequency)

                if issues:
                    result["invalid_records"] += 1
                    result["issues"].extend(issues)
                else:
                    result["valid_records"] += 1

            # 计算数据质量统计
            result["data_quality_stats"] = self._calculate_quality_stats(data_records)

            logger.debug(
                f"股票数据验证完成: {symbol}, 有效={result['valid_records']}, "
                f"无效={result['invalid_records']}"
            )

            return result

        except Exception as e:
            logger.error(f"股票数据验证失败 {symbol}: {e}")
            return {
                "symbol": symbol,
                "frequency": frequency,
                "error": str(e),
                "total_records": 0,
                "valid_records": 0,
                "invalid_records": 0,
                "issues": [],
            }

    def _validate_frequency_data(
        self, symbols: List[str], start_date: date, end_date: date, frequency: str
    ) -> Dict[str, Any]:
        """验证特定频率的数据"""
        logger.info(f"验证频率数据: {frequency}, 股票数量: {len(symbols)}")

        result = {
            "frequency": frequency,
            "total_symbols": len(symbols),
            "total_records": 0,
            "valid_records": 0,
            "invalid_records": 0,
            "issue_types": defaultdict(int),
            "symbols_with_issues": [],
            "quality_distribution": defaultdict(int),
        }

        for symbol in symbols:
            try:
                symbol_result = self.validate_symbol_data(
                    symbol, start_date, end_date, frequency
                )

                result["total_records"] += symbol_result["total_records"]
                result["valid_records"] += symbol_result["valid_records"]
                result["invalid_records"] += symbol_result["invalid_records"]

                # 统计问题类型
                if symbol_result["issues"]:
                    result["symbols_with_issues"].append(symbol)

                    for issue in symbol_result["issues"]:
                        result["issue_types"][issue["issue_type"]] += 1

                # 统计质量分布
                if "data_quality_stats" in symbol_result:
                    avg_quality = symbol_result["data_quality_stats"].get(
                        "avg_quality", 0
                    )
                    quality_range = self._get_quality_range(avg_quality)
                    result["quality_distribution"][quality_range] += 1

            except Exception as e:
                logger.error(f"验证股票数据失败 {symbol}: {e}")
                result["invalid_records"] += 1

        return result

    def _validate_record(
        self, record: Dict[str, Any], frequency: str
    ) -> List[Dict[str, Any]]:
        """验证单条记录"""
        issues = []

        try:
            # 1. 基础字段检查
            required_fields = [
                "symbol",
                "trade_date",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]
            for field in required_fields:
                if field not in record or record[field] is None:
                    issues.append(
                        {
                            "issue_type": "missing_field",
                            "field": field,
                            "trade_date": record.get("trade_date", ""),
                            "description": f"缺少必需字段: {field}",
                        }
                    )

            # 2. 价格逻辑检查
            if all(
                field in record and record[field] is not None
                for field in ["open", "high", "low", "close"]
            ):

                open_price = float(record["open"])
                high_price = float(record["high"])
                low_price = float(record["low"])
                close_price = float(record["close"])

                # 检查价格是否为正数
                if any(
                    price <= 0
                    for price in [open_price, high_price, low_price, close_price]
                ):
                    issues.append(
                        {
                            "issue_type": "invalid_price",
                            "trade_date": record.get("trade_date", ""),
                            "description": "价格不能为0或负数",
                        }
                    )

                # 检查高低价关系
                if high_price < low_price:
                    issues.append(
                        {
                            "issue_type": "price_logic_error",
                            "trade_date": record.get("trade_date", ""),
                            "description": f"最高价({high_price})低于最低价({low_price})",
                        }
                    )

                # 检查开盘价和收盘价是否在高低价范围内
                if not (low_price <= open_price <= high_price):
                    issues.append(
                        {
                            "issue_type": "price_logic_error",
                            "trade_date": record.get("trade_date", ""),
                            "description": f"开盘价({open_price})超出高低价范围",
                        }
                    )

                if not (low_price <= close_price <= high_price):
                    issues.append(
                        {
                            "issue_type": "price_logic_error",
                            "trade_date": record.get("trade_date", ""),
                            "description": f"收盘价({close_price})超出高低价范围",
                        }
                    )

            # 3. 成交量检查
            if "volume" in record and record["volume"] is not None:
                volume = float(record["volume"])
                if volume < 0:
                    issues.append(
                        {
                            "issue_type": "invalid_volume",
                            "trade_date": record.get("trade_date", ""),
                            "description": f"成交量不能为负数: {volume}",
                        }
                    )
                elif volume < self.min_volume_threshold:
                    issues.append(
                        {
                            "issue_type": "low_volume",
                            "trade_date": record.get("trade_date", ""),
                            "description": f"成交量过低: {volume}",
                        }
                    )

            # 4. 数据质量检查
            if "quality_score" in record and record["quality_score"] is not None:
                quality = float(record["quality_score"])
                if quality < self.min_data_quality:
                    issues.append(
                        {
                            "issue_type": "low_quality",
                            "trade_date": record.get("trade_date", ""),
                            "description": f"数据质量过低: {quality}",
                        }
                    )

            # 5. 涨跌幅检查
            if all(
                field in record and record[field] is not None
                for field in ["close", "preclose"]
            ):

                close_price = float(record["close"])
                preclose = float(record["preclose"])

                if preclose > 0:
                    change_percent = abs((close_price - preclose) / preclose * 100)
                    if change_percent > self.max_price_change_pct:
                        issues.append(
                            {
                                "issue_type": "extreme_change",
                                "trade_date": record.get("trade_date", ""),
                                "description": f"涨跌幅异常: {change_percent:.2f}%",
                            }
                        )

        except Exception as e:
            issues.append(
                {
                    "issue_type": "validation_error",
                    "trade_date": record.get("trade_date", ""),
                    "description": f"验证过程出错: {e}",
                }
            )

        return issues

    def _get_symbol_data(
        self, symbol: str, start_date: date, end_date: date, frequency: str
    ) -> List[Dict[str, Any]]:
        """获取股票数据"""
        try:
            sql = """
            SELECT symbol, date as trade_date, frequency, open, high, low, close,
                   volume, amount, prev_close, change_percent, turnover_rate, quality_score
            FROM market_data
            WHERE symbol = ? AND frequency = ?
            AND date >= ? AND date <= ?
            ORDER BY date
            """

            results = self.db_manager.fetchall(
                sql, (symbol, frequency, str(start_date), str(end_date))
            )

            return [dict(row) for row in results]

        except Exception as e:
            logger.error(f"获取股票数据失败 {symbol}: {e}")
            return []

    def _calculate_quality_stats(
        self, data_records: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """计算数据质量统计"""
        try:
            if not data_records:
                return {}

            # 提取数据质量分数
            quality_scores = []
            for record in data_records:
                if "quality_score" in record and record["quality_score"] is not None:
                    quality_scores.append(float(record["quality_score"]))

            if not quality_scores:
                return {}

            return {
                "avg_quality": statistics.mean(quality_scores),
                "min_quality": min(quality_scores),
                "max_quality": max(quality_scores),
                "median_quality": statistics.median(quality_scores),
                "quality_count": len(quality_scores),
            }

        except Exception as e:
            logger.error(f"计算质量统计失败: {e}")
            return {}

    def _get_active_symbols(self) -> List[str]:
        """获取活跃股票列表"""
        try:
            sql = """
            SELECT symbol FROM stocks
            WHERE status = 'active'
            ORDER BY symbol
            """
            results = self.db_manager.fetchall(sql)

            if results:
                return [row["symbol"] for row in results]
            else:
                logger.warning("数据库中无活跃股票")
                return []

        except Exception as e:
            logger.error(f"获取活跃股票列表失败: {e}")
            return []

    def _get_quality_range(self, quality_score: float) -> str:
        """获取质量分数范围"""
        if quality_score >= 90:
            return "excellent"
        elif quality_score >= 80:
            return "good"
        elif quality_score >= 70:
            return "fair"
        elif quality_score >= 60:
            return "poor"
        else:
            return "very_poor"

    def generate_validation_report(self, validation_result: Dict[str, Any]) -> str:
        """生成验证报告"""
        try:
            report_lines = []

            # 报告头部
            report_lines.append("=" * 60)
            report_lines.append("数据验证报告")
            report_lines.append("=" * 60)
            report_lines.append(
                f"验证时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            report_lines.append(
                f"验证范围: {validation_result['start_date']} 到 {validation_result['end_date']}"
            )
            report_lines.append(f"验证股票: {validation_result['total_symbols']} 只")
            report_lines.append(
                f"验证频率: {', '.join(validation_result['frequencies'])}"
            )
            report_lines.append("")

            # 汇总统计
            summary = validation_result["summary"]
            report_lines.append("汇总统计:")
            report_lines.append(f"  总记录数: {summary['total_records']:,}")
            report_lines.append(f"  有效记录: {summary['valid_records']:,}")
            report_lines.append(f"  无效记录: {summary['invalid_records']:,}")
            report_lines.append(f"  验证率: {summary['validation_rate']:.2f}%")
            report_lines.append("")

            # 问题类型统计
            if summary["issue_types"]:
                report_lines.append("问题类型分布:")
                for issue_type, count in summary["issue_types"].items():
                    report_lines.append(f"  {issue_type}: {count}")
                report_lines.append("")

            # 按频率详细统计
            for frequency, freq_data in validation_result[
                "validation_by_frequency"
            ].items():
                report_lines.append(f"频率 {frequency} 详细信息:")
                report_lines.append(f"  总记录数: {freq_data['total_records']:,}")
                report_lines.append(f"  有效记录: {freq_data['valid_records']:,}")
                report_lines.append(f"  无效记录: {freq_data['invalid_records']:,}")

                if freq_data["total_records"] > 0:
                    validation_rate = (
                        freq_data["valid_records"] / freq_data["total_records"] * 100
                    )
                    report_lines.append(f"  验证率: {validation_rate:.2f}%")

                report_lines.append(
                    f"  问题股票数: {len(freq_data['symbols_with_issues'])}"
                )

                # 质量分布
                if freq_data["quality_distribution"]:
                    report_lines.append("  质量分布:")
                    for quality_range, count in freq_data[
                        "quality_distribution"
                    ].items():
                        report_lines.append(f"    {quality_range}: {count}")

                report_lines.append("")

            return "\n".join(report_lines)

        except Exception as e:
            logger.error(f"生成验证报告失败: {e}")
            return f"报告生成失败: {e}"
