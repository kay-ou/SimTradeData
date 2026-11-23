import struct
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from simtradedata.fetchers.mootdx_fetcher import MootdxFetcher
from simtradedata.fetchers.tdx_blocks import parse_blocks
from simtradedata.router.smart_router import SmartRouter


def block_file():
    records = []
    for name, codes in [("沪深300", ["600000", "000001"]), ("测试", ["510300"])]:
        header = name.encode("gbk").ljust(9, b"\0") + struct.pack("<HH", len(codes), 2)
        body = b"".join(code.encode().ljust(7, b"\0") for code in codes).ljust(2800, b"\0")
        records.append(header + body)
    return bytes(384) + struct.pack("<H", len(records)) + b"".join(records)


def fetcher_with(api):
    fetcher = MootdxFetcher()
    fetcher._client = SimpleNamespace(client=api)
    return fetcher


def test_quote_identity_uses_market_and_code_not_response_order():
    api = Mock()
    api.get_security_quotes.return_value = [
        {"market": 0, "code": "000001", "price": 12},
        {"market": 1, "code": "000001", "price": 3000},
    ]
    result = fetcher_with(api).fetch_realtime_quotes(["000001.SS", "600000.SS", "000001.SZ"])
    assert result["ptrade_code"].tolist() == ["000001.SZ", "000001.SS"]
    assert api.get_security_quotes.call_args.args[0] == [(1, "000001"), (1, "600000"), (0, "000001")]


def test_quote_batches_and_empty_requests():
    api = Mock()
    api.get_security_quotes.return_value = []
    fetcher = fetcher_with(api)
    assert fetcher.fetch_realtime_quotes([]).empty
    api.get_security_quotes.assert_not_called()
    assert fetcher.fetch_realtime_quotes(["600000.SS"] * 81).empty
    assert [len(call.args[0]) for call in api.get_security_quotes.call_args_list] == [80, 1]


def test_snapshot_and_gear_units_shapes_and_unavailable_fields(monkeypatch):
    router = SmartRouter()
    row = {"ptrade_code": "510300.SS", "price": 4.1, "last_close": 4,
           "vol": 100, "s_vol": 40, "b_vol": 60, "amount": 41000}
    for level in range(1, 6):
        row.update({f"bid{level}": round(4.1 - level * .001, 3), f"ask{level}": round(4.1 + level * .001, 3),
                    f"bid_vol{level}": level * 2, f"ask_vol{level}": level * 3})
    monkeypatch.setattr(router, "get_realtime_quotes", lambda _: pd.DataFrame([row]))
    snapshot = router.get_snapshot("510300.SS")["510300.SS"]
    assert snapshot["business_amount"] == 10000
    assert snapshot["business_amount_in"] == 4000
    assert snapshot["business_amount_out"] == 6000
    assert snapshot["wavg_px"] == 4.1
    assert snapshot["px_change_rate"] == pytest.approx(2.5)
    assert snapshot["bid_grp"][1] == [4.099, 200, 0, {}]
    assert snapshot["offer_grp"][5][1] == 1500
    assert not {"trade_status", "hsTimeStamp", "up_px", "total_bidqty", "business_count"} & snapshot.keys()
    assert router.get_gear_price("510300.SS")["bid_grp"][1] == [4.099, 200, 0]
    assert "510300.SS" in router.get_gear_price(["510300.SS"])
    assert router.get_snapshot([]) == {}
    monkeypatch.setattr(router, "get_realtime_quotes", lambda _: pd.DataFrame())
    assert router.get_snapshot("510300.SS") == {}
    assert router.get_gear_price("510300.SS") == {"bid_grp": {}, "offer_grp": {}}


def test_block_download_advances_by_actual_response_size():
    data = block_file()
    api = Mock()
    api.get_block_info_meta.return_value = {"size": len(data)}
    api.get_block_info.side_effect = lambda _, start, size: data[start:start + 1000]
    result = fetcher_with(api).fetch_blocks()
    assert result["code"].tolist() == ["600000", "000001", "510300"]
    assert result["blockname"].tolist() == ["沪深300", "沪深300", "测试"]
    assert [call.args[1] for call in api.get_block_info.call_args_list] == list(range(0, len(data), 1000))


@pytest.mark.parametrize("damage", ["truncated", "code", "count"])
def test_invalid_block_data_fails_instead_of_publishing_garbage(damage):
    data = bytearray(block_file())
    if damage == "truncated":
        data = data[:-1]
    elif damage == "code":
        data[399] = ord("X")
    else:
        struct.pack_into("<H", data, 395, 401)
    with pytest.raises(ValueError):
        parse_blocks(data)


def test_extra_interfaces_route_to_existing_fetcher():
    router = SmartRouter()
    fetcher = Mock()
    fetcher._circuit_breaker = None
    fetcher._quotes_fetcher = None
    router._fetchers["mootdx"] = fetcher
    data = pd.DataFrame({"value": [1]})
    for method in ["fetch_blocks", "fetch_f10_catalog", "fetch_history_transactions", "fetch_history_minutes"]:
        getattr(fetcher, method).return_value = data
    fetcher.fetch_f10_detail.return_value = "真实正文"
    assert router.get_blocks() is data
    assert router.get_f10_catalog("600000.SS") is data
    assert router.get_f10_detail("600000.SS", "最新提示") == "真实正文"
    assert router.get_history_transactions("600000.SS", "2026-11-20", 10, 20) is data
    fetcher.fetch_history_transactions.assert_called_once_with("600000.SS", "2026-11-20", 10, 20)
    assert router.get_history_minutes("600000.SS", "2026-11-20") is data


def test_historical_interfaces_preserve_market_and_raw_direction():
    api = Mock()
    api.get_history_transaction_data.return_value = [{"time": "09:31", "price": 10, "vol": 2, "buyorsell": 5}]
    api.get_history_minute_time_data.return_value = [{"price": 10, "vol": 2}]
    fetcher = fetcher_with(api)
    trades = fetcher.fetch_history_transactions("000001.SS", "2026-11-20", 10, 20)
    api.get_history_transaction_data.assert_called_once_with(1, "000001", 10, 20, 20261120)
    assert trades["buyorsell"].tolist() == [5]
    assert not {"business_id", "business_time", "buy_no", "sell_no"} & set(trades.columns)
    minutes = fetcher.fetch_history_minutes("000001.SS", "2026-11-20")
    api.get_history_minute_time_data.assert_called_once_with(1, "000001", 20261120)
    assert set(minutes.columns) == {"price", "vol"}


@pytest.mark.parametrize("symbol,market", [("000001.SS", 1), ("000001.SZ", 0), ("600000.SS", 1)])
def test_history_minutes_preserves_explicit_market(symbol, market):
    api = Mock()
    api.get_history_minute_time_data.return_value = [{"price": 1}]
    assert not fetcher_with(api).fetch_history_minutes(symbol, "2026-11-20").empty
    api.get_history_minute_time_data.assert_called_once_with(market, symbol[:6], 20261120)
