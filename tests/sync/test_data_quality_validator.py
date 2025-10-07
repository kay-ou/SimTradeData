"""
测试数据质量验证器

测试 DataQualityValidator 类的所有验证方法
"""

from datetime import date
from typing import Any, Dict

import pytest

from simtradedata.sync.manager import DataQualityValidator, SyncConstants


class TestDataQualityValidator:
    """测试数据质量验证器"""

    # ==================== 财务数据验证测试 ====================

    def test_valid_financial_data_with_positive_revenue(self):
        """测试有效财务数据 - 正营收"""
        data = {
            "revenue": 1000000.0,
            "net_profit": 0,
            "total_assets": 0,
        }
        assert DataQualityValidator.is_valid_financial_data(data) is True

    def test_valid_financial_data_with_positive_assets(self):
        """测试有效财务数据 - 正总资产"""
        data = {
            "revenue": 0,
            "net_profit": 0,
            "total_assets": 5000000.0,
        }
        assert DataQualityValidator.is_valid_financial_data(data) is True

    def test_valid_financial_data_with_negative_profit(self):
        """测试有效财务数据 - 负净利润（亏损公司）"""
        data = {
            "revenue": 0,
            "net_profit": -100000.0,  # 净利润可以为负
            "total_assets": 0,
        }
        assert DataQualityValidator.is_valid_financial_data(data) is True

    def test_valid_financial_data_with_positive_profit(self):
        """测试有效财务数据 - 正净利润"""
        data = {
            "revenue": 0,
            "net_profit": 100000.0,
            "total_assets": 0,
        }
        assert DataQualityValidator.is_valid_financial_data(data) is True

    def test_valid_financial_data_with_multiple_indicators(self):
        """测试有效财务数据 - 多个非零指标"""
        data = {
            "revenue": 1000000.0,
            "net_profit": 50000.0,
            "total_assets": 5000000.0,
        }
        assert DataQualityValidator.is_valid_financial_data(data) is True

    def test_invalid_financial_data_all_zero(self):
        """测试无效财务数据 - 所有指标为零"""
        data = {
            "revenue": 0,
            "net_profit": 0,
            "total_assets": 0,
        }
        assert DataQualityValidator.is_valid_financial_data(data) is False

    def test_invalid_financial_data_none_values(self):
        """测试无效财务数据 - None 值"""
        data = {
            "revenue": None,
            "net_profit": None,
            "total_assets": None,
        }
        assert DataQualityValidator.is_valid_financial_data(data) is False

    def test_invalid_financial_data_empty_dict(self):
        """测试无效财务数据 - 空字典"""
        data = {}
        assert DataQualityValidator.is_valid_financial_data(data) is False

    def test_invalid_financial_data_none_input(self):
        """测试无效财务数据 - None 输入"""
        assert DataQualityValidator.is_valid_financial_data(None) is False

    def test_invalid_financial_data_not_dict(self):
        """测试无效财务数据 - 非字典类型"""
        assert DataQualityValidator.is_valid_financial_data("not a dict") is False
        assert DataQualityValidator.is_valid_financial_data([1, 2, 3]) is False
        assert DataQualityValidator.is_valid_financial_data(12345) is False

    # ==================== 估值数据验证测试 ====================

    def test_valid_valuation_data_with_pe(self):
        """测试有效估值数据 - 有 PE 比率"""
        data = {
            "pe_ratio": 15.5,
            "pb_ratio": 0,
            "ps_ratio": 0,
            "pcf_ratio": 0,
        }
        assert DataQualityValidator.is_valid_valuation_data(data) is True

    def test_valid_valuation_data_with_negative_pe(self):
        """测试有效估值数据 - 负 PE 比率（亏损公司）"""
        data = {
            "pe_ratio": -10.5,  # 亏损公司可能有负 PE
            "pb_ratio": 0,
            "ps_ratio": 0,
            "pcf_ratio": 0,
        }
        assert DataQualityValidator.is_valid_valuation_data(data) is True

    def test_valid_valuation_data_with_zero_pe(self):
        """测试有效估值数据 - 零 PE 比率"""
        data = {
            "pe_ratio": 0,
            "pb_ratio": 0,
            "ps_ratio": 0,
            "pcf_ratio": 0,
        }
        assert DataQualityValidator.is_valid_valuation_data(data) is True

    def test_valid_valuation_data_with_pb(self):
        """测试有效估值数据 - 有 PB 比率"""
        data = {
            "pe_ratio": None,
            "pb_ratio": 2.5,
            "ps_ratio": 0,
            "pcf_ratio": 0,
        }
        assert DataQualityValidator.is_valid_valuation_data(data) is True

    def test_valid_valuation_data_with_ps(self):
        """测试有效估值数据 - 有 PS 比率"""
        data = {
            "pe_ratio": None,
            "pb_ratio": 0,
            "ps_ratio": 3.2,
            "pcf_ratio": 0,
        }
        assert DataQualityValidator.is_valid_valuation_data(data) is True

    def test_valid_valuation_data_with_pcf(self):
        """测试有效估值数据 - 有 PCF 比率"""
        data = {
            "pe_ratio": None,
            "pb_ratio": 0,
            "ps_ratio": 0,
            "pcf_ratio": 8.5,
        }
        assert DataQualityValidator.is_valid_valuation_data(data) is True

    def test_valid_valuation_data_with_multiple_ratios(self):
        """测试有效估值数据 - 多个估值指标"""
        data = {
            "pe_ratio": 15.5,
            "pb_ratio": 2.5,
            "ps_ratio": 3.2,
            "pcf_ratio": 8.5,
        }
        assert DataQualityValidator.is_valid_valuation_data(data) is True

    def test_invalid_valuation_data_all_none_or_zero(self):
        """测试无效估值数据 - 所有指标为 None 或零"""
        data = {
            "pe_ratio": None,
            "pb_ratio": 0,
            "ps_ratio": 0,
            "pcf_ratio": 0,
        }
        assert DataQualityValidator.is_valid_valuation_data(data) is False

    def test_invalid_valuation_data_empty_dict(self):
        """测试无效估值数据 - 空字典"""
        data = {}
        assert DataQualityValidator.is_valid_valuation_data(data) is False

    def test_invalid_valuation_data_none_input(self):
        """测试无效估值数据 - None 输入"""
        assert DataQualityValidator.is_valid_valuation_data(None) is False

    def test_invalid_valuation_data_not_dict(self):
        """测试无效估值数据 - 非字典类型"""
        assert DataQualityValidator.is_valid_valuation_data("not a dict") is False
        assert DataQualityValidator.is_valid_valuation_data([1, 2, 3]) is False
        assert DataQualityValidator.is_valid_valuation_data(12345) is False

    # ==================== 报告期有效性验证测试 ====================

    def test_valid_report_date_recent(self):
        """测试有效报告期 - 最近的日期"""
        report_date = "2024-12-31"
        assert DataQualityValidator.is_valid_report_date(report_date) is True

    def test_valid_report_date_min_year(self):
        """测试有效报告期 - MIN_REPORT_YEAR（1990）"""
        report_date = f"{SyncConstants.MIN_REPORT_YEAR}-01-01"
        assert DataQualityValidator.is_valid_report_date(report_date) is True

    def test_valid_report_date_middle_range(self):
        """测试有效报告期 - 中间范围的日期"""
        report_date = "2010-06-30"
        assert DataQualityValidator.is_valid_report_date(report_date) is True

    def test_invalid_report_date_before_min_year(self):
        """测试无效报告期 - MIN_REPORT_YEAR 之前"""
        report_date = f"{SyncConstants.MIN_REPORT_YEAR - 1}-12-31"
        assert DataQualityValidator.is_valid_report_date(report_date) is False

    def test_invalid_report_date_future(self):
        """测试无效报告期 - 未来日期"""
        future_date = date.today().replace(year=date.today().year + 2)
        report_date = future_date.strftime("%Y-%m-%d")
        assert DataQualityValidator.is_valid_report_date(report_date) is False

    def test_invalid_report_date_empty_string(self):
        """测试无效报告期 - 空字符串"""
        assert DataQualityValidator.is_valid_report_date("") is False

    def test_invalid_report_date_none(self):
        """测试无效报告期 - None"""
        assert DataQualityValidator.is_valid_report_date(None) is False

    def test_invalid_report_date_invalid_format(self):
        """测试无效报告期 - 无效格式"""
        # 错误的日期格式
        assert DataQualityValidator.is_valid_report_date("2024/12/31") is False
        assert DataQualityValidator.is_valid_report_date("31-12-2024") is False
        assert (
            DataQualityValidator.is_valid_report_date("2024-13-01") is False
        )  # 月份无效
        assert (
            DataQualityValidator.is_valid_report_date("2024-12-32") is False
        )  # 日期无效
        assert DataQualityValidator.is_valid_report_date("not a date") is False

    def test_invalid_report_date_not_string(self):
        """测试无效报告期 - 非字符串类型"""
        assert DataQualityValidator.is_valid_report_date(20241231) is False
        assert DataQualityValidator.is_valid_report_date(date(2024, 12, 31)) is False


class TestFinancialDataRelaxedValidation:
    """测试宽松的财务数据验证（如果存在 _is_valid_financial_data_relaxed 方法）"""

    def test_relaxed_validation_exists(self):
        """测试 _is_valid_financial_data_relaxed 方法是否存在"""
        # 检查 SyncManager 中是否有这个方法
        from simtradedata.sync.manager import SyncManager

        # 如果方法存在，进行测试
        if hasattr(SyncManager, "_is_valid_financial_data_relaxed"):
            # 注意：这是私有方法，需要通过实例访问
            # 这里只是检查方法存在性
            assert callable(getattr(SyncManager, "_is_valid_financial_data_relaxed"))
        else:
            pytest.skip("_is_valid_financial_data_relaxed 方法不存在")


# 参数化测试 - 边界情况组合测试
@pytest.mark.parametrize(
    "data,expected",
    [
        # 财务数据边界情况
        ({"revenue": 0.01, "net_profit": 0, "total_assets": 0}, True),  # 极小正值
        ({"revenue": 0, "net_profit": -0.01, "total_assets": 0}, True),  # 极小负值
        ({"revenue": 1e10, "net_profit": 0, "total_assets": 0}, True),  # 极大值
        ({"revenue": 0, "net_profit": 0, "total_assets": 0.01}, True),  # 极小资产
        # 缺少字段的情况
        ({"revenue": 1000}, True),  # 只有营收字段
        ({"net_profit": -100}, True),  # 只有净利润字段
        ({"total_assets": 5000}, True),  # 只有总资产字段
        ({}, False),  # 完全空字典
    ],
)
def test_financial_data_edge_cases(data: Dict[str, Any], expected: bool):
    """参数化测试 - 财务数据边界情况"""
    assert DataQualityValidator.is_valid_financial_data(data) == expected


@pytest.mark.parametrize(
    "data,expected",
    [
        # 估值数据边界情况
        ({"pe_ratio": 0.01}, True),  # 极小 PE
        ({"pe_ratio": -1000}, True),  # 极大负 PE
        ({"pe_ratio": 1000}, True),  # 极大正 PE
        ({"pb_ratio": 0.01}, True),  # 极小 PB
        ({"ps_ratio": 0.01}, True),  # 极小 PS
        ({"pcf_ratio": 0.01}, True),  # 极小 PCF
        # 混合 None 和零值
        ({"pe_ratio": None, "pb_ratio": None, "ps_ratio": 0, "pcf_ratio": 0}, False),
        ({"pe_ratio": 0, "pb_ratio": None, "ps_ratio": None, "pcf_ratio": None}, True),
    ],
)
def test_valuation_data_edge_cases(data: Dict[str, Any], expected: bool):
    """参数化测试 - 估值数据边界情况"""
    assert DataQualityValidator.is_valid_valuation_data(data) == expected


@pytest.mark.parametrize(
    "report_date,expected",
    [
        # 报告期边界情况
        ("1990-01-01", True),  # MIN_REPORT_YEAR 的第一天
        ("1989-12-31", False),  # MIN_REPORT_YEAR 前一天
        ("2024-02-29", True),  # 闰年日期
        ("2023-02-29", False),  # 非闰年的 2月29日（无效）
        ("2024-12-31", True),  # 年末
        ("2024-01-01", True),  # 年初
    ],
)
def test_report_date_edge_cases(report_date: str, expected: bool):
    """参数化测试 - 报告期边界情况"""
    assert DataQualityValidator.is_valid_report_date(report_date) == expected
