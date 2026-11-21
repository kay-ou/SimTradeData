import pandas as pd
import pytest

from simtradedata.validators.data_validator import (
    DataQualityError,
    FundamentalDataValidator,
    MarketDataValidator,
)


def test_fundamental_validator_coerces_string_fields_for_range_checks():
    df = pd.DataFrame(
        {
            "roe": ["600", "not-a-number"],
            "current_ratio": ["1.2", "-0.1"],
            "debt_equity_ratio": ["0.5", "-2"],
        },
        index=pd.to_datetime(["2026-03-31", "2026-06-30"]),
    )

    assert FundamentalDataValidator.validate(df, "000001.SZ") is True


@pytest.mark.parametrize(
    "field,value,valid",
    [
        ("open", 10.0, True),
        ("open", 0, False),
        ("high", 8, False),
        ("close", 13, False),
        ("volume", -1, False),
    ],
)
def test_market_range_checks_preserve_strict_and_non_strict_behavior(
    field, value, valid
):
    df = pd.DataFrame(
        {
            "open": [10.0],
            "high": [12.0],
            "low": [9.0],
            "close": [11.0],
            "volume": [100],
            "money": [1100],
        },
        index=pd.to_datetime(["2026-06-18"]),
    )
    df[field] = value

    assert MarketDataValidator.validate(df, "600000.SS") is valid
    if valid:
        assert MarketDataValidator.validate(df, "600000.SS", strict=True) is True
    else:
        with pytest.raises(DataQualityError, match="Data range issues"):
            MarketDataValidator.validate(df, "600000.SS", strict=True)


def test_market_missing_price_fails_before_range_checks():
    df = pd.DataFrame({"volume": [100]}, index=pd.to_datetime(["2026-06-18"]))
    assert MarketDataValidator.validate(df, "600000.SS") is False
    with pytest.raises(DataQualityError, match="Missing required fields"):
        MarketDataValidator.validate(df, "600000.SS", strict=True)


@pytest.mark.parametrize('ratio', [-2.0, -150.0, '-300.0'])
def test_negative_equity_ratio_is_not_a_range_error(ratio, caplog):
    df = pd.DataFrame({'debt_equity_ratio': [ratio]}, index=pd.to_datetime(['2026-03-31']))
    assert FundamentalDataValidator.validate(df, '600217.SS', strict=True)
    assert 'Fundamental range issues' not in caplog.text


@pytest.mark.parametrize('ratio', [float('inf'), float('-inf'), 'inf'])
def test_infinite_equity_ratio_is_reported(ratio, caplog):
    df = pd.DataFrame({'debt_equity_ratio': [ratio]}, index=pd.to_datetime(['2026-03-31']))
    assert FundamentalDataValidator.validate(df, '600217.SS')
    assert 'non-finite debt_equity_ratio' in caplog.text
