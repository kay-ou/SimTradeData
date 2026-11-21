from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from simtradedata.fetchers.etf_pcf_fetcher import EtfPcfFetcher, parse_pcf
from simtradedata.router import SmartRouter

FIXTURES = Path(__file__).parent / "fixtures" / "pcf"


def sample(symbol):
    return (FIXTURES / f"{symbol[:6]}-20261120.xml").read_bytes()


@pytest.mark.parametrize("symbol,count,nav,cash", [
    ("510300.SS", 300, 4.0594, 100306.12),
    ("159919.SZ", 301, 4.2387, 5842.96),
])
def test_real_exchange_fixtures_preserve_units_markets_and_dates(symbol, count, nav, cash):
    data = parse_pcf(sample(symbol), symbol)
    assert len(data) == count
    row = data.iloc[0]
    assert row["date"] == "2026-11-20" and row["pre_date"] == "2026-11-19"
    assert row["report_unit"] == 900000
    assert row["nav_pre"] == nav
    assert row["cash_balance"] == cash
    assert row["nav_percu"] / row["report_unit"] == pytest.approx(nav, abs=.0001)
    stock = data[data["symbol"] == "000001.SZ"].iloc[0]
    assert stock["code"] == "000001" and stock["code_num"] == 1600
    assert stock["market_code"] == "102"
    assert not data.duplicated(["code", "market_code"]).any()


def test_cash_component_is_not_a_security_and_two_cash_directions_are_retained():
    data = parse_pcf(sample("159919.SZ"), "159919.SZ")
    cash = data[data["is_cash_component"]].iloc[0]
    assert cash["code"] == "159900" and cash["symbol"] is None
    assert cash["creation_cash"] == 2741315.5
    assert cash["redemption_cash"] == 1744473.5


@pytest.mark.parametrize("old,new", [
    (b"<FundInstrumentID>510300", b"<FundInstrumentID>510050"),
    (b"<TradingDay>20261120", b"<TradingDay>bad"),
    (b"<PreTradingDay>20261119", b"<PreTradingDay>20261120"),
    (b"<RecordNumber>300", b"<RecordNumber>299"),
    (b"<Quantity>1600", b"<Quantity>-1"),
    (b"<NAV>4.0594", b"<NAV>NaN"),
    (b"<CreationRedemptionUnit>900000", b"<CreationRedemptionUnit>0"),
])
def test_corrupt_pcf_is_rejected(old, new):
    with pytest.raises(ValueError):
        parse_pcf(sample("510300.SS").replace(old, new), "510300.SS")


def test_ptrade_subset_does_not_invent_fields_or_duplicate_cash_semantics(monkeypatch):
    router = SmartRouter()
    monkeypatch.setattr(router, "get_etf_pcf", lambda symbol, date=None: parse_pcf(sample(symbol), symbol))
    info = router.get_etf_info("510300.SS")["510300.SS"]
    assert not {"etf_redemption_code", "pre_cash_component", "allot_max"} & info.keys()
    stocks = router.get_etf_stock_info("159919.SZ", ["000001.SZ", "159900.SZ"])
    assert stocks["000001.SZ"]["replace_ratio"] == .34
    assert "is_open" not in stocks["000001.SZ"]
    assert "159900.SZ" not in stocks
    data = parse_pcf(sample("510300.SS"), "510300.SS")
    data.loc[data["symbol"] == "000001.SZ", "redemption_cash"] = 1
    monkeypatch.setattr(router, "get_etf_pcf", lambda *args: data)
    assert "replace_balance" not in router.get_etf_stock_info("510300.SS", "000001.SZ")["000001.SZ"]


def test_sse_rejects_latest_file_for_a_different_requested_date(monkeypatch):
    fetcher = EtfPcfFetcher()
    monkeypatch.setattr(fetcher, "_get", lambda *args, **kwargs: Mock(content=sample("510300.SS")))
    with pytest.raises(ValueError, match="trading date differs"):
        fetcher._download("510300.SS", pd.Timestamp("2026-11-19").date(), pd.Timestamp("2026-11-20").date())


def test_szse_index_chooses_latest_nonfuture_announcement(monkeypatch):
    fetcher = EtfPcfFetcher()
    urls = []

    def get(url, **kwargs):
        urls.append(url)
        if "ShowReport" in url:
            return Mock(json=lambda: {"urls": ["pcf_159919_20261120", "pcf_159919_20261123"]})
        return Mock(content=sample("159919.SZ"))

    monkeypatch.setattr(fetcher, "_get", get)
    result = fetcher._download("159919.SZ", None, pd.Timestamp("2026-11-20").date())
    assert result.iloc[0]["date"] == "2026-11-20"
    assert urls[-1].endswith("pcf_159919_20261120.xml")


def test_pcf_route_uses_exchange_source():
    router = SmartRouter()
    fetcher = Mock(_circuit_breaker=None)
    fetcher._quotes_fetcher = None
    expected = parse_pcf(sample("510300.SS"), "510300.SS")
    fetcher.fetch_pcf.return_value = expected
    router._fetchers["etf_pcf"] = fetcher
    assert router.get_etf_pcf("510300.SS", "2026-11-20") is expected
    fetcher.fetch_pcf.assert_called_once_with("510300.SS", "2026-11-20")


def test_pcf_parquet_roundtrip_retains_cash_row_and_metadata(tmp_path):
    data = parse_pcf(sample("159919.SZ"), "159919.SZ")
    path = tmp_path / "pcf.parquet"
    data.to_parquet(path, index=False)
    pd.testing.assert_frame_equal(data, pd.read_parquet(path))


def test_health_probe_preserves_stale_status_and_continues_after_failure(monkeypatch, tmp_path):
    import json
    from scripts import check_etf_pcf as checks

    monkeypatch.setattr(checks, "latest_due_session", lambda *args: "2026-11-23")

    class Fetcher:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def fetch_pcf(self, symbol):
            if symbol == "510300.SS":
                raise ConnectionError("Unavailable source")
            return parse_pcf(sample(symbol), symbol)

    monkeypatch.setattr(checks, "EtfPcfFetcher", Fetcher)
    output = tmp_path / "health.json"
    archive = tmp_path / "archive"
    assert checks.main(["--json-output", str(output), "--archive-dir", str(archive)]) == 1
    report = json.loads(output.read_text())
    assert [row["status"] for row in report["results"]] == ["unavailable", "stale"]
    assert (archive / "159919.SZ" / "2026-11-20.parquet").exists()


def test_historical_health_checks_exact_requested_date(monkeypatch, tmp_path):
    from scripts import check_etf_pcf as checks

    fetcher = Mock()
    fetcher.__enter__ = Mock(return_value=fetcher)
    fetcher.__exit__ = Mock(return_value=False)
    fetcher.fetch_pcf.return_value = parse_pcf(sample("159919.SZ"), "159919.SZ")
    monkeypatch.setattr(checks, "EtfPcfFetcher", lambda **kwargs: fetcher)
    monkeypatch.setattr(checks, "latest_due_session", lambda *args: pytest.fail("Historical request cannot use latest session"))
    assert checks.main(["--symbols", "159919.SZ", "--date", "2026-11-20", "--json-output", str(tmp_path / "history.json")]) == 0
    fetcher.fetch_pcf.assert_called_once_with("159919.SZ", "2026-11-20")


def test_missing_historical_announcement_does_not_block_latest_request(monkeypatch):
    from simtradedata.fetchers.etf_pcf_fetcher import PcfNotAvailable
    fetcher = EtfPcfFetcher()
    fetcher._cooldown = Mock()
    fetcher._cooldown.is_in_cooldown.return_value = False
    fetcher._circuit_breaker = Mock()
    fetcher._circuit_breaker.is_available.return_value = True
    fetcher._monitor = Mock()
    def get(url, **kwargs):
        if 'ShowReport' in url:
            old = kwargs['params']['txtEnd'].startswith('2015')
            return Mock(json=lambda: [] if old else {'urls': ['pcf_159919_20261120']})
        return Mock(content=sample('159919.SZ'))
    monkeypatch.setattr(fetcher, '_get', get)
    with fetcher:
        with pytest.raises(PcfNotAvailable, match='未找到'):
            fetcher.fetch_pcf('159919.SZ', '2015-11-20')
        assert len(fetcher.fetch_pcf('159919.SZ', '2026-11-20')) == 301
    fetcher._cooldown.record_failure.assert_not_called()
    fetcher._circuit_breaker.record_failure.assert_not_called()
    assert fetcher._circuit_breaker.record_success.call_count == 2
