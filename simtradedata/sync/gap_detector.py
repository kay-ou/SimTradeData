"""
数据缺口检测器

负责检测数据缺口，分析缺口原因，提供自动修复建议。
"""

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, List

from ..config import Config
from ..core import BaseManager
from ..database import DatabaseManager

logger = logging.getLogger(__name__)


class GapDetector(BaseManager):
    """数据缺口检测器"""

    def __init__(self, db_manager: DatabaseManager, config: Config = None, **kwargs):
        """
        初始化缺口检测器

        Args:
            db_manager: 数据库管理器
            config: 配置对象
        """
        super().__init__(config=config, **kwargs)
        self.db_manager = db_manager

    def _init_specific_config(self):
        """初始化缺口检测器特定配置"""
        # 检测配置
        self.max_gap_days = self._get_config("gap_detection.max_gap_days", 5)
        self.min_data_quality = self._get_config("gap_detection.min_data_quality", 60)
        self.check_frequencies = self._get_config("gap_detection.frequencies", ["1d"])
        self.exclude_weekends = self._get_config("gap_detection.exclude_weekends", True)

    def _init_components(self):
        """初始化组件"""
        # 支持的表格和对应的日期字段
        self.supported_tables = {
            "market_data": {
                "date_column": "date",
                "frequency_column": "frequency",
                "description": "市场数据",
            },
            "valuations": {
                "date_column": "date",
                "frequency_column": None,
                "description": "估值数据",
            },
            "technical_indicators": {
                "date_column": "date",
                "frequency_column": "frequency",
                "description": "技术指标",
            },
            "financials": {
                "date_column": "report_date",
                "frequency_column": None,
                "description": "财务数据",
            },
            "corporate_actions": {
                "date_column": "ex_date",
                "frequency_column": None,
                "description": "除权除息数据",
            },
        }

        self.logger.info("数据缺口检测器初始化完成")

    def _get_required_attributes(self) -> List[str]:
        """必需属性列表"""
        return ["supported_tables"]

    def detect_all_tables_gaps(
        self,
        start_date: date = None,
        end_date: date = None,
        symbols: List[str] = None,
        tables: List[str] = None,
    ) -> Dict[str, Any]:
        """
        检测所有表格的数据缺口

        Args:
            start_date: 开始日期
            end_date: 结束日期
            symbols: 股票代码列表
            tables: 要检测的表格列表，默认检测所有支持的表格

        Returns:
            Dict[str, Any]: 按表格分组的缺口检测结果
        """
        if start_date is None:
            start_date = datetime.now().date() - timedelta(days=30)
        if end_date is None:
            end_date = datetime.now().date()
        if symbols is None:
            symbols = self._get_active_symbols()
        if tables is None:
            tables = list(self.supported_tables.keys())

        logger.info(f"开始检测所有表格缺口: {len(tables)}个表格, {len(symbols)}只股票")

        detection_result = {
            "start_date": str(start_date),
            "end_date": str(end_date),
            "symbols_count": len(symbols),
            "tables_checked": len(tables),
            "gaps_by_table": {},
            "summary": {
                "total_gaps": 0,
                "tables_with_gaps": 0,
                "symbols_with_gaps": set(),
                "gap_types": defaultdict(int),
            },
        }

        # 按表格检测缺口
        for table_name in tables:
            if table_name not in self.supported_tables:
                logger.warning(f"不支持的表格: {table_name}")
                continue

            try:
                table_gaps = self._detect_table_gaps(
                    table_name, symbols, start_date, end_date
                )
                detection_result["gaps_by_table"][table_name] = table_gaps

                # 更新汇总统计
                if table_gaps["gaps"]:
                    detection_result["summary"]["tables_with_gaps"] += 1
                    detection_result["summary"]["total_gaps"] += len(table_gaps["gaps"])

                    for gap in table_gaps["gaps"]:
                        detection_result["summary"]["symbols_with_gaps"].add(
                            gap["symbol"]
                        )
                        detection_result["summary"]["gap_types"][gap["gap_type"]] += 1

            except Exception as e:
                logger.error(f"检测表格 {table_name} 缺口失败: {e}")
                detection_result["gaps_by_table"][table_name] = {
                    "error": str(e),
                    "gaps": [],
                    "symbols_with_gaps": [],
                }

        # 转换set为list以便JSON序列化
        detection_result["summary"]["symbols_with_gaps"] = list(
            detection_result["summary"]["symbols_with_gaps"]
        )

        logger.info(
            f"所有表格缺口检测完成: 总缺口={detection_result['summary']['total_gaps']}, "
            f"涉及表格={detection_result['summary']['tables_with_gaps']}, "
            f"涉及股票={len(detection_result['summary']['symbols_with_gaps'])}"
        )

        return detection_result

    def detect_all_gaps(
        self,
        start_date: date = None,
        end_date: date = None,
        symbols: List[str] = None,
        frequencies: List[str] = None,
    ) -> Dict[str, Any]:
        """
        检测所有数据缺口

        Args:
            start_date: 开始日期，默认为30天前
            end_date: 结束日期，默认为今天
            symbols: 股票代码列表，默认为所有活跃股票
            frequencies: 频率列表，默认为配置中的频率

        Returns:
            Dict[str, Any]: 缺口检测结果
        """
        if start_date is None:
            start_date = datetime.now().date() - timedelta(days=30)

        if end_date is None:
            end_date = datetime.now().date()

        if frequencies is None:
            frequencies = self.check_frequencies

        try:
            logger.info(
                f"开始缺口检测: {start_date} 到 {end_date}, 频率: {frequencies}"
            )

            # 获取需要检测的股票列表
            if symbols is None:
                symbols = self._get_active_symbols()

            detection_result = {
                "start_date": str(start_date),
                "end_date": str(end_date),
                "total_symbols": len(symbols),
                "frequencies": frequencies,
                "gaps_by_frequency": {},
                "summary": {
                    "total_gaps": 0,
                    "symbols_with_gaps": 0,
                    "gap_types": defaultdict(int),
                },
            }

            # 按频率检测缺口
            for frequency in frequencies:
                freq_gaps = self._detect_frequency_gaps(
                    symbols, start_date, end_date, frequency
                )
                detection_result["gaps_by_frequency"][frequency] = freq_gaps

                # 更新汇总统计
                detection_result["summary"]["total_gaps"] += len(freq_gaps["gaps"])
                detection_result["summary"]["symbols_with_gaps"] += len(
                    freq_gaps["symbols_with_gaps"]
                )

                for gap in freq_gaps["gaps"]:
                    detection_result["summary"]["gap_types"][gap["gap_type"]] += 1

            logger.info(
                f"缺口检测完成: 总缺口={detection_result['summary']['total_gaps']}, "
                f"涉及股票={detection_result['summary']['symbols_with_gaps']}"
            )

            return detection_result

        except Exception as e:
            logger.error(f"缺口检测失败: {e}")
            raise

    def detect_symbol_gaps(
        self, symbol: str, start_date: date, end_date: date, frequency: str = "1d"
    ) -> List[Dict[str, Any]]:
        """
        检测单个股票的数据缺口

        Args:
            symbol: 股票代码
            start_date: 开始日期
            end_date: 结束日期
            frequency: 频率

        Returns:
            List[Dict[str, Any]]: 缺口列表
        """
        logger.debug(f"检测股票缺口: {symbol} {start_date} 到 {end_date} {frequency}")

        # 获取交易日历
        trading_days = self._get_trading_days(start_date, end_date)

        # 获取已有数据日期
        existing_dates = self._get_existing_dates(
            symbol, start_date, end_date, frequency
        )

        # 只检测日期缺口，删除过度的质量和异常检测
        gaps = self._detect_date_gaps(symbol, trading_days, existing_dates, frequency)

        logger.debug(f"股票缺口检测完成: {symbol}, 发现 {len(gaps)} 个缺口")
        return gaps

    def _detect_frequency_gaps(
        self, symbols: List[str], start_date: date, end_date: date, frequency: str
    ) -> Dict[str, Any]:
        """检测特定频率的缺口"""
        logger.info(f"检测频率缺口: {frequency}, 股票数量: {len(symbols)}")

        result = {
            "frequency": frequency,
            "total_symbols": len(symbols),
            "symbols_with_gaps": set(),
            "gaps": [],
        }

        for symbol in symbols:
            try:
                symbol_gaps = self.detect_symbol_gaps(
                    symbol, start_date, end_date, frequency
                )

                if symbol_gaps:
                    result["symbols_with_gaps"].add(symbol)
                    result["gaps"].extend(symbol_gaps)

            except Exception as e:
                logger.error(f"检测股票缺口失败 {symbol}: {e}")

        # 转换set为list以便JSON序列化
        result["symbols_with_gaps"] = list(result["symbols_with_gaps"])

        return result

    def _detect_table_gaps(
        self, table_name: str, symbols: List[str], start_date: date, end_date: date
    ) -> Dict[str, Any]:
        """检测特定表格的缺口"""
        logger.info(f"检测表格缺口: {table_name}, 股票数量: {len(symbols)}")

        table_config = self.supported_tables[table_name]
        result = {
            "table_name": table_name,
            "description": table_config["description"],
            "symbols_with_gaps": [],
            "gaps": [],
        }

        for symbol in symbols:
            try:
                symbol_gaps = self._detect_symbol_table_gaps(
                    table_name, symbol, start_date, end_date
                )

                if symbol_gaps:
                    result["symbols_with_gaps"].append(symbol)
                    result["gaps"].extend(symbol_gaps)

            except Exception as e:
                logger.debug(f"检测 {table_name} 表格 {symbol} 缺口失败: {e}")

        logger.debug(
            f"表格 {table_name} 缺口检测完成: 发现 {len(result['gaps'])} 个缺口"
        )
        return result

    def _detect_symbol_table_gaps(
        self, table_name: str, symbol: str, start_date: date, end_date: date
    ) -> List[Dict[str, Any]]:
        """检测单个股票在特定表格中的缺口"""
        table_config = self.supported_tables[table_name]
        date_column = table_config["date_column"]
        frequency_column = table_config["frequency_column"]

        # 获取交易日
        trading_days = self._get_trading_days(start_date, end_date)
        if not trading_days:
            return []

        # 获取已有数据日期
        existing_dates = self._get_existing_table_dates(
            table_name, symbol, start_date, end_date, date_column, frequency_column
        )

        # 检测日期缺口
        gaps = self._detect_date_gaps(
            symbol,
            trading_days,
            existing_dates,
            frequency_column or "1d",  # 如果没有频率字段，默认为日线
        )

        # 为每个缺口添加表格信息
        for gap in gaps:
            gap["table_name"] = table_name
            gap["description"] = table_config["description"]

        return gaps

    def _get_existing_table_dates(
        self,
        table_name: str,
        symbol: str,
        start_date: date,
        end_date: date,
        date_column: str,
        frequency_column: str = None,
    ) -> List[date]:
        """获取表格中已有数据日期"""
        try:
            if frequency_column:
                # 有频率字段的表格（如market_data, technical_indicators）
                sql = f"""
                SELECT DISTINCT {date_column} FROM {table_name}
                WHERE symbol = ? AND {frequency_column} = ?
                AND {date_column} >= ? AND {date_column} <= ?
                ORDER BY {date_column}
                """
                params = (symbol, "1d", str(start_date), str(end_date))
            else:
                # 没有频率字段的表格（如valuations, financials, corporate_actions）
                sql = f"""
                SELECT DISTINCT {date_column} FROM {table_name}
                WHERE symbol = ?
                AND {date_column} >= ? AND {date_column} <= ?
                ORDER BY {date_column}
                """
                params = (symbol, str(start_date), str(end_date))

            rows = self.db_manager.fetchall(sql, params)
            dates = []
            for row in rows:
                date_str = row[date_column]
                if date_str:
                    try:
                        dates.append(datetime.strptime(date_str, "%Y-%m-%d").date())
                    except ValueError:
                        continue

            return dates

        except Exception as e:
            logger.debug(f"获取 {table_name} 表格 {symbol} 已有日期失败: {e}")
            return []

    def _detect_date_gaps(
        self,
        symbol: str,
        trading_days: List[date],
        existing_dates: List[date],
        frequency: str,
    ) -> List[Dict[str, Any]]:
        """检测日期缺口"""
        gaps = []

        # 转换为集合以便快速查找
        existing_set = set(existing_dates)

        # 查找缺失的交易日
        missing_dates = []
        for trading_day in trading_days:
            if trading_day not in existing_set:
                missing_dates.append(trading_day)

        if not missing_dates:
            return gaps

        # 将连续的缺失日期合并为缺口
        current_gap_start = None
        current_gap_end = None

        for missing_date in sorted(missing_dates):
            if current_gap_start is None:
                current_gap_start = missing_date
                current_gap_end = missing_date
            elif missing_date == current_gap_end + timedelta(days=1):
                # 连续日期，扩展当前缺口
                current_gap_end = missing_date
            else:
                # 非连续日期，结束当前缺口并开始新缺口
                gaps.append(
                    {
                        "symbol": symbol,
                        "frequency": frequency,
                        "gap_type": "date_missing",
                        "start_date": str(current_gap_start),
                        "end_date": str(current_gap_end),
                        "gap_days": (current_gap_end - current_gap_start).days + 1,
                        "severity": self._calculate_gap_severity(
                            current_gap_start, current_gap_end
                        ),
                        "description": f"缺失交易日数据: {current_gap_start} 到 {current_gap_end}",
                    }
                )

                current_gap_start = missing_date
                current_gap_end = missing_date

        # 添加最后一个缺口
        if current_gap_start is not None:
            gaps.append(
                {
                    "symbol": symbol,
                    "frequency": frequency,
                    "gap_type": "date_missing",
                    "start_date": str(current_gap_start),
                    "end_date": str(current_gap_end),
                    "gap_days": (current_gap_end - current_gap_start).days + 1,
                    "severity": self._calculate_gap_severity(
                        current_gap_start, current_gap_end
                    ),
                    "description": f"缺失交易日数据: {current_gap_start} 到 {current_gap_end}",
                }
            )

        return gaps

    def _get_trading_days(self, start_date: date, end_date: date) -> List[date]:
        """获取交易日列表"""
        # 直接从数据库查询交易日历
        sql = """
        SELECT date FROM trading_calendar
        WHERE date >= ? AND date <= ?
        AND market = 'CN' AND is_trading = 1
        ORDER BY date
        """

        results = self.db_manager.fetchall(sql, (str(start_date), str(end_date)))

        if results:
            trading_days = [
                datetime.strptime(row["date"], "%Y-%m-%d").date() for row in results
            ]
            logger.debug(f"从数据库获取到 {len(trading_days)} 个交易日")
            return trading_days
        else:
            logger.warning(f"数据库中无交易日历数据: {start_date} 到 {end_date}")
            return []

    def _get_existing_dates(
        self, symbol: str, start_date: date, end_date: date, frequency: str
    ) -> List[date]:
        """获取已有数据日期"""
        try:
            sql = """
            SELECT DISTINCT date FROM market_data
            WHERE symbol = ? AND frequency = ?
            AND date >= ? AND date <= ?
            ORDER BY date
            """

            results = self.db_manager.fetchall(
                sql, (symbol, frequency, str(start_date), str(end_date))
            )

            return [
                datetime.strptime(row["date"], "%Y-%m-%d").date() for row in results
            ]

        except Exception as e:
            logger.error(f"获取已有数据日期失败 {symbol}: {e}")
            return []

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

    def _calculate_gap_severity(self, start_date: date, end_date: date) -> str:
        """计算缺口严重程度"""
        gap_days = (end_date - start_date).days + 1

        if gap_days <= 1:
            return "low"
        elif gap_days <= 3:
            return "medium"
        elif gap_days <= 7:
            return "high"
        else:
            return "critical"

    def generate_gap_report(self, detection_result: Dict[str, Any]) -> str:
        """生成缺口检测报告"""
        try:
            report_lines = []

            # 报告头部
            report_lines.append("=" * 60)
            report_lines.append("数据缺口检测报告")
            report_lines.append("=" * 60)
            report_lines.append(
                f"检测时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            report_lines.append(
                f"检测范围: {detection_result['start_date']} 到 {detection_result['end_date']}"
            )
            report_lines.append(f"检测股票: {detection_result['total_symbols']} 只")
            report_lines.append(
                f"检测频率: {', '.join(detection_result['frequencies'])}"
            )
            report_lines.append("")

            # 汇总统计
            summary = detection_result["summary"]
            report_lines.append("汇总统计:")
            report_lines.append(f"  总缺口数: {summary['total_gaps']}")
            report_lines.append(f"  涉及股票: {summary['symbols_with_gaps']}")
            report_lines.append("")

            # 缺口类型统计
            if summary["gap_types"]:
                report_lines.append("缺口类型分布:")
                for gap_type, count in summary["gap_types"].items():
                    report_lines.append(f"  {gap_type}: {count}")
                report_lines.append("")

            # 按频率详细统计
            for frequency, freq_data in detection_result["gaps_by_frequency"].items():
                report_lines.append(f"频率 {frequency} 详细信息:")
                report_lines.append(
                    f"  涉及股票: {len(freq_data['symbols_with_gaps'])}"
                )
                report_lines.append(f"  缺口数量: {len(freq_data['gaps'])}")

                # 按严重程度统计
                severity_count = defaultdict(int)
                for gap in freq_data["gaps"]:
                    severity_count[gap["severity"]] += 1

                if severity_count:
                    report_lines.append("  严重程度分布:")
                    for severity, count in severity_count.items():
                        report_lines.append(f"    {severity}: {count}")

                report_lines.append("")

            # 建议修复的缺口
            critical_gaps = []
            for freq_data in detection_result["gaps_by_frequency"].values():
                for gap in freq_data["gaps"]:
                    if gap["severity"] in ["high", "critical"]:
                        critical_gaps.append(gap)

            if critical_gaps:
                report_lines.append("建议优先修复的缺口:")
                for gap in critical_gaps[:10]:  # 只显示前10个
                    report_lines.append(
                        f"  {gap['symbol']} {gap['start_date']} "
                        f"({gap['gap_type']}, {gap['severity']})"
                    )

                if len(critical_gaps) > 10:
                    report_lines.append(f"  ... 还有 {len(critical_gaps) - 10} 个缺口")

            return "\n".join(report_lines)

        except Exception as e:
            logger.error(f"生成缺口报告失败: {e}")
            return f"报告生成失败: {e}"

    def generate_all_tables_gap_report(self, detection_result: Dict[str, Any]) -> str:
        """生成所有表格的缺口报告"""
        try:
            report_lines = []
            report_lines.append("=" * 60)
            report_lines.append("数据缺口检测报告 - 所有表格")
            report_lines.append("=" * 60)
            report_lines.append("")

            # 基本信息
            report_lines.append("基本信息:")
            report_lines.append(
                f"  检测日期范围: {detection_result['start_date']} 到 {detection_result['end_date']}"
            )
            report_lines.append(f"  检测股票数量: {detection_result['symbols_count']}")
            report_lines.append(f"  检测表格数量: {detection_result['tables_checked']}")
            report_lines.append("")

            # 汇总统计
            summary = detection_result["summary"]
            report_lines.append("汇总统计:")
            report_lines.append(f"  总缺口数量: {summary['total_gaps']}")
            report_lines.append(f"  有缺口的表格: {summary['tables_with_gaps']}")
            report_lines.append(f"  涉及股票数量: {len(summary['symbols_with_gaps'])}")

            if summary["gap_types"]:
                report_lines.append("  缺口类型分布:")
                for gap_type, count in summary["gap_types"].items():
                    report_lines.append(f"    {gap_type}: {count}")
            report_lines.append("")

            # 按表格详细统计
            for table_name, table_data in detection_result["gaps_by_table"].items():
                if "error" in table_data:
                    report_lines.append(
                        f"表格 {table_name} ({table_data.get('description', '')}):"
                    )
                    report_lines.append(f"  ❌ 检测失败: {table_data['error']}")
                    report_lines.append("")
                    continue

                report_lines.append(
                    f"表格 {table_name} ({table_data.get('description', '')}):"
                )
                report_lines.append(
                    f"  涉及股票: {len(table_data['symbols_with_gaps'])}"
                )
                report_lines.append(f"  缺口数量: {len(table_data['gaps'])}")

                if table_data["gaps"]:
                    # 按严重程度分组
                    severity_groups = defaultdict(list)
                    for gap in table_data["gaps"]:
                        severity_groups[gap["severity"]].append(gap)

                    for severity in ["critical", "high", "medium", "low"]:
                        if severity in severity_groups:
                            gaps = severity_groups[severity]
                            report_lines.append(
                                f"  {severity.upper()} 级缺口: {len(gaps)}"
                            )

                            # 显示前3个缺口示例
                            for gap in gaps[:3]:
                                gap_days = (
                                    datetime.strptime(
                                        gap["end_date"], "%Y-%m-%d"
                                    ).date()
                                    - datetime.strptime(
                                        gap["start_date"], "%Y-%m-%d"
                                    ).date()
                                ).days + 1
                                report_lines.append(
                                    f"    - {gap['symbol']}: {gap['start_date']} 到 {gap['end_date']} ({gap_days}天)"
                                )

                            if len(gaps) > 3:
                                report_lines.append(
                                    f"    ... 还有 {len(gaps) - 3} 个缺口"
                                )

                report_lines.append("")

            # 建议修复的缺口
            critical_gaps = []
            for table_data in detection_result["gaps_by_table"].values():
                if "gaps" in table_data:
                    for gap in table_data["gaps"]:
                        if gap["severity"] in ["high", "critical"]:
                            critical_gaps.append(gap)

            if critical_gaps:
                report_lines.append("建议优先修复的缺口:")
                for gap in critical_gaps[:10]:  # 只显示前10个
                    gap_days = (
                        datetime.strptime(gap["end_date"], "%Y-%m-%d").date()
                        - datetime.strptime(gap["start_date"], "%Y-%m-%d").date()
                    ).days + 1
                    report_lines.append(
                        f"  - {gap['table_name']}.{gap['symbol']}: {gap['start_date']} 到 {gap['end_date']} "
                        f"({gap_days}天, {gap['severity'].upper()})"
                    )

                if len(critical_gaps) > 10:
                    report_lines.append(
                        f"  ... 还有 {len(critical_gaps) - 10} 个高优先级缺口"
                    )

            report_lines.append("")
            report_lines.append("=" * 60)

            return "\n".join(report_lines)

        except Exception as e:
            logger.error(f"生成所有表格缺口报告失败: {e}")
            return f"报告生成失败: {e}"
