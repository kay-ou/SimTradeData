import pandas as pd

from simtradedata.config.field_mappings import (
    DATA_ROUTING,
    MARKET_FIELD_MAP,
    VALUATION_FIELD_MAP,
)
from simtradedata.processors.data_splitter import DataSplitter


def test_default_routing_preserves_field_order_and_names():
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-06-18"]),
            "open": [10.0],
            "high": [12.0],
            "low": [9.0],
            "close": [11.0],
            "preclose": [10.5],
            "volume": [100],
            "amount": [1100.0],
            "peTTM": [10.0],
            "pbMRQ": [2.0],
            "psTTM": [3.0],
            "pcfNcfTTM": [4.0],
            "turn": [5.0],
            "isST": [0],
            "tradestatus": [1],
            "unused": [99],
        }
    )
    original = frame.copy(deep=True)

    result = DataSplitter().split_data(frame)

    assert list(result) == ["market", "valuation", "status"]
    assert result["market"].columns.tolist() == [
        "open",
        "high",
        "low",
        "close",
        "preclose",
        "volume",
        "money",
    ]
    assert result["valuation"].columns.tolist() == [
        "pe_ttm",
        "pb",
        "ps_ttm",
        "pcf",
        "turnover_rate",
    ]
    assert result["status"].columns.tolist() == ["date", "isST", "tradestatus"]
    assert result["market"]["money"].tolist() == [1100.0]
    assert result["valuation"]["turnover_rate"].tolist() == [5.0]
    assert result["market"].index.name == result["valuation"].index.name == "date"
    result["market"].iloc[0, 0] = 20.0
    pd.testing.assert_frame_equal(frame, original)


def test_routing_renames_remain_independent_of_shared_maps(monkeypatch):
    monkeypatch.setitem(DATA_ROUTING["valuation"]["rename"], "peTTM", "custom_pe")
    monkeypatch.setitem(DATA_ROUTING["market"]["rename"], "amount", "custom_money")
    assert VALUATION_FIELD_MAP["peTTM"] == "pe_ttm"
    assert MARKET_FIELD_MAP["amount"] == "money"


def test_routing_skips_unavailable_fields():
    frame = pd.DataFrame({"open": [10.0], "peTTM": [12.0]})
    result = DataSplitter().split_data(frame)
    assert list(result) == ["market", "valuation"]
    assert result["market"].columns.tolist() == ["open"]
    assert result["valuation"].columns.tolist() == ["pe_ttm"]
