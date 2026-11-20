from datetime import datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from simtradedata.utils import sampling
from simtradedata.utils.ttm_calculator import (
    calculate_ttm_indicators,
    get_quarters_in_range,
)


@pytest.mark.parametrize(
    "start,end,expected",
    [
        ("2024-01-15", "2024-03-15", ["2024-02-01", "2024-03-01", "2024-03-15"]),
        ("2024-01-15", "2024-03-01", ["2024-02-01", "2024-03-01"]),
        ("2024-02-01", "2024-02-29", ["2024-02-01", "2024-02-29"]),
        ("2024-03-01", "2024-02-29", ["2024-02-29"]),
    ],
)
def test_monthly_starts_preserve_endpoint_and_return_datetimes(start, end, expected):
    dates = sampling.generate_monthly_start_dates(start, end)
    assert dates == [datetime.fromisoformat(value) for value in expected]
    assert all(type(value) is datetime for value in dates)


def test_monthly_starts_default_to_today(monkeypatch):
    monkeypatch.setattr(
        sampling, "datetime", SimpleNamespace(now=lambda: datetime(2024, 2, 29))
    )
    assert sampling.generate_monthly_start_dates("2024-01-15") == [
        datetime(2024, 2, 1),
        datetime(2024, 2, 29),
    ]


@pytest.mark.parametrize(
    "start,end,expected",
    [
        ("2024-02-15", "2024-03-31", [(2024, 1)]),
        ("2024-04-01", "2024-06-29", []),
        ("2024-06-30", "2024-06-30", [(2024, 2)]),
        ("2024-12-01", "2025-03-31", [(2024, 4), (2025, 1)]),
        ("2024-01-01", "2024-12-31", [(2024, 1), (2024, 2), (2024, 3), (2024, 4)]),
        ("2024-07-01", "2024-06-30", []),
    ],
)
def test_quarters_include_only_completed_quarters(start, end, expected):
    assert get_quarters_in_range(start, end) == expected


@pytest.mark.parametrize("date_format", ["column", "datetime_index", "named_index"])
@pytest.mark.parametrize("periods", [2, 4])
def test_ttm_preserves_sorting_window_and_source_frame(date_format, periods):
    fields = ["roe", "roa", "net_profit_ratio", "gross_income_ratio"]
    frame = pd.DataFrame(
        {
            "end_date": [
                "2024-03-31",
                "2024-06-30",
                "2024-09-30",
                "2024-12-31",
                "2025-03-31",
            ],
            **{
                field: [value + offset * 10 for value in range(1, 6)]
                for offset, field in enumerate(fields)
            },
        }
    ).iloc[[3, 0, 4, 2, 1]]
    if date_format != "column":
        frame = frame.set_index("end_date")
        if date_format == "datetime_index":
            frame.index = pd.DatetimeIndex(frame.index, name=None)
    original = frame.copy(deep=True)

    result = calculate_ttm_indicators(frame, periods)

    means = [None, 1.5, 2.5, 3.5, 4.5] if periods == 2 else [None, None, None, 2.5, 3.5]
    for offset, field in enumerate(fields):
        name = f"{field}_ttm"
        expected = pd.Series(
            [None if value is None else value + offset * 10 for value in means],
            index=result.index,
            name=name,
            dtype="float64",
        )
        pd.testing.assert_series_equal(result[name], expected)
    pd.testing.assert_frame_equal(frame, original)


def test_ttm_missing_base_fields_preserve_existing_indicators():
    frame = pd.DataFrame({"net_profit_ratio": [1.0, 2.0], "roe_ttm": [50.0, 60.0]})
    result = calculate_ttm_indicators(frame)
    assert result["roe_ttm"].tolist() == [50.0, 60.0]
    assert result["net_profit_ratio_ttm"].isna().all()
    assert "roa_ttm" not in result.columns


def test_ttm_empty_input_preserves_identity():
    frame = pd.DataFrame()
    assert calculate_ttm_indicators(frame) is frame
