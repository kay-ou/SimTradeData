import importlib
from types import SimpleNamespace

import pandas as pd
import pytest

from simtradedata.fetchers.mootdx_fetcher import MootdxFetcher


def test_fetch_f10_detail_returns_none_when_source_unavailable(monkeypatch):
    fetcher = MootdxFetcher()
    monkeypatch.setattr(fetcher, "_ensure_source_available", lambda: False)

    assert fetcher.fetch_f10_detail("000001.SZ", "latest") is None


@pytest.mark.parametrize("records", [None, [], [{"name": "公司概况", "filename": "test.txt", "start": 0, "length": 20}]])
def test_f10_catalog_normalizes_vendor_records(monkeypatch, records):
    fetcher = MootdxFetcher()
    fetcher._client = SimpleNamespace(F10C=lambda **kwargs: records)
    monkeypatch.setattr(fetcher, "_ensure_source_available", lambda: True)
    result = fetcher.fetch_f10_catalog("600000.SS")
    assert isinstance(result, pd.DataFrame)
    assert len(result) == (len(records) if records else 0)
    if records:
        assert result.iloc[0]["name"] == "公司概况"


class FakeQuotesClient:
    def bars(self, **_kwargs):
        return pd.DataFrame(
            {
                "open": [9.20],
                "high": [9.25],
                "low": [9.07],
                "close": [9.09],
                "volume": [836563.0],
                "vol": [836563.0],
                "amount": [763280640.0],
            },
            index=pd.to_datetime(["2026-06-18"]),
        )


class FakeQuotesClientWithDatetimeColumn:
    def bars(self, **_kwargs):
        return pd.DataFrame(
            {
                "datetime": ["2026-06-18"],
                "open": [9.20],
                "high": [9.25],
                "low": [9.07],
                "close": [9.09],
                "volume": [836563.0],
                "amount": [763280640.0],
            }
        )


def test_fetch_daily_bars_drops_duplicate_volume(monkeypatch):
    fetcher = MootdxFetcher()
    fetcher._client = FakeQuotesClient()
    monkeypatch.setattr(fetcher, "_ensure_source_available", lambda: True)
    monkeypatch.setattr(fetcher, "_ensure_client", lambda: None)

    df = fetcher.fetch_daily_bars("600000.SS", "2026-06-18", "2026-06-25")

    assert not df.empty
    assert df.columns.tolist().count("volume") == 1
    assert df.columns.is_unique


def test_fetch_daily_bars_accepts_datetime_column(monkeypatch):
    fetcher = MootdxFetcher()
    fetcher._client = FakeQuotesClientWithDatetimeColumn()
    monkeypatch.setattr(fetcher, "_ensure_source_available", lambda: True)
    monkeypatch.setattr(fetcher, "_ensure_client", lambda: None)

    df = fetcher.fetch_daily_bars("600000.SS", "2026-06-18", "2026-06-25")

    assert not df.empty
    assert "date" in df.columns
    assert df["date"].dt.strftime("%Y-%m-%d").tolist() == ["2026-06-18"]


def _daily_page(dates):
    return pd.DataFrame({
        "datetime": pd.DatetimeIndex(dates) + pd.Timedelta(hours=15),
        "open": 4.0, "high": 4.1, "low": 3.9, "close": 4.018,
        "volume": 100.0, "amount": 401.8,
    })


def test_historical_query_pages_by_actual_dates_and_includes_end(monkeypatch):
    recent = _daily_page(pd.bdate_range("2022-01-03", periods=800))
    older = _daily_page(pd.bdate_range("2015-01-05", periods=3))
    calls = []

    def bars(**kwargs):
        calls.append(kwargs["start"])
        return recent if kwargs["start"] == 0 else older

    fetcher = MootdxFetcher()
    fetcher._client = SimpleNamespace(bars=bars)
    monkeypatch.setattr(fetcher, "_ensure_source_available", lambda: True)
    df = fetcher.fetch_daily_bars("600000.SS", "2015-01-05", "2015-01-07")
    assert calls == [0, 800]
    assert df["date"].dt.strftime("%Y-%m-%d").tolist() == [
        "2015-01-05", "2015-01-06", "2015-01-07",
    ]
    assert df["date"].is_monotonic_increasing


@pytest.mark.parametrize("symbol", ["510300.SS", "510500.SS", "159915.SZ"])
def test_etf_decoded_prices_are_not_divided_again(monkeypatch, symbol):
    fetcher = MootdxFetcher()
    fetcher._client = SimpleNamespace(
        bars=lambda **kwargs: _daily_page(["2026-11-20"]),
    )
    monkeypatch.setattr(fetcher, "_ensure_source_available", lambda: True)
    df = fetcher.fetch_daily_bars(symbol, "2026-11-20", "2026-11-20")
    assert df["close"].tolist() == [4.018]
    assert df["volume"].tolist() == [10000.0]
    assert df["date"].dt.hour.tolist() == [0]


def test_repeated_history_page_fails_instead_of_looping(monkeypatch):
    page = _daily_page(pd.bdate_range("2022-01-03", periods=800))
    fetcher = MootdxFetcher()
    fetcher._client = SimpleNamespace(bars=lambda **kwargs: page)
    monkeypatch.setattr(fetcher, "_ensure_source_available", lambda: True)
    with pytest.raises(ValueError, match="pagination did not advance"):
        fetcher.fetch_daily_bars("600000.SS", "2015-01-01", "2026-11-20")


def test_adjustment_receives_all_history_pages(monkeypatch):
    from mootdx.utils import adjust as adjustment

    recent = _daily_page(pd.bdate_range("2022-01-03", periods=800))
    older = _daily_page(pd.bdate_range("2015-01-05", periods=3))
    seen = []

    def to_adjust(data, **kwargs):
        seen.append((len(data), kwargs))
        # Vendor ETF adjustment may remove the date column.
        return data.drop(columns="date").assign(close=data["close"] * 2)

    monkeypatch.setattr(adjustment, "to_adjust", to_adjust)
    fetcher = MootdxFetcher()
    fetcher._client = SimpleNamespace(
        bars=lambda **kwargs: recent if kwargs["start"] == 0 else older,
    )
    monkeypatch.setattr(fetcher, "_ensure_source_available", lambda: True)
    df = fetcher.fetch_daily_bars("510300.SS", "2015-01-05", "2026-11-20", adjust="qfq")
    assert seen == [(803, {"symbol": "510300", "adjust": "qfq"})]
    assert len(df) == 803
    assert df["close"].eq(8.036).all()
    assert df["date"].is_unique


@pytest.mark.parametrize("canonical_volume", [None, 1000.0])
def test_minute_bars_have_one_volume_column(monkeypatch, canonical_volume):
    data = {
        "datetime": ["2026-11-20 15:00"],
        "open": [10.8], "high": [10.9], "low": [10.7], "close": [10.87],
        "vol": [10.0],
    }
    if canonical_volume is not None:
        data["volume"] = [canonical_volume]
    fetcher = MootdxFetcher()
    fetcher._client = SimpleNamespace(bars=lambda **kwargs: pd.DataFrame(data))
    monkeypatch.setattr(fetcher, "_ensure_source_available", lambda: True)
    result = fetcher.fetch_minute_bars("600000.SS", offset=1)
    assert result.columns.is_unique
    assert result["volume"].tolist() == [
        canonical_volume if canonical_volume is not None else 10.0
    ]


@pytest.mark.parametrize("default_healthy", [True, False])
def test_login_uses_healthy_vendor_server_without_writing_config(
    monkeypatch, default_healthy
):
    server = importlib.import_module("mootdx.server")
    from mootdx.quotes import Quotes

    events = []
    connected = []
    timeouts = []
    factory_args = []

    def connect(host, port, **kwargs):
        events.append((host, port))
        if host == "old" and not default_healthy:
            raise ConnectionError("Initialization rejected")
        connected.append(True)
        return True

    api = SimpleNamespace(
        client=SimpleNamespace(settimeout=lambda timeout: timeouts.append(timeout)),
        get_security_count=lambda *args: 5000 if default_healthy or connected else None,
        get_security_bars=lambda *args: pytest.fail(
            "Login must not request daily bars"
        ),
        connect=connect,
    )
    client = SimpleNamespace(
        client=api, server=("old", 7709), close=lambda: events.append("close")
    )
    monkeypatch.setattr(
        Quotes, "factory", lambda **kwargs: factory_args.append(kwargs) or client
    )
    monkeypatch.setattr(server, "hosts", {"HQ": [{"addr": "new", "port": 80}]})
    monkeypatch.setattr(server, "connect2", lambda host: {**host, "time": 1})
    fetcher = MootdxFetcher()
    fetcher.login()
    assert fetcher._logged_in is True
    assert events == (
        ["close", ("old", 7709)] if default_healthy
        else ["close", ("old", 7709), "close", ("new", 80)]
    )
    assert client.server == (("old", 7709) if default_healthy else ("new", 80))
    assert timeouts == [fetcher._timeout]
    assert client.client.auto_retry is True
    assert factory_args[0]["auto_retry"] is False
    assert factory_args[0]["bestip"] is False
    assert "server" not in factory_args[0]


def test_failed_login_closes_client_and_keeps_logged_out(monkeypatch):
    server = importlib.import_module("mootdx.server")
    from mootdx.quotes import Quotes

    closed = []
    client = SimpleNamespace(
        client=SimpleNamespace(
            get_security_count=lambda *args: None, connect=lambda *args, **kwargs: False,
        ),
        server=("old", 7709),
        close=lambda: closed.append(True),
    )
    monkeypatch.setattr(Quotes, "factory", lambda **kwargs: client)
    monkeypatch.setattr(server, "hosts", {"HQ": [{"addr": "new", "port": 80}]})
    monkeypatch.setattr(server, "connect2", lambda host: {**host, "time": None})
    fetcher = MootdxFetcher()
    with pytest.raises(ConnectionError, match="No available Mootdx quote server"):
        fetcher.login()
    assert fetcher._client is None
    assert fetcher._logged_in is False
    assert closed


def test_server_selection_stops_at_timeout(monkeypatch):
    server = importlib.import_module("mootdx.server")
    from mootdx.quotes import Quotes

    client = SimpleNamespace(
        client=SimpleNamespace(
            get_security_count=lambda *args: None, connect=lambda *args, **kwargs: False,
        ),
        server=("old", 7709),
        close=lambda: None,
    )
    clock = iter([0, 2, 2])
    monkeypatch.setattr(Quotes, "factory", lambda **kwargs: client)
    monkeypatch.setattr(server, "hosts", {"HQ": [{"addr": "new", "port": 80}]})
    monkeypatch.setattr(
        server, "connect2", lambda host: pytest.fail("Probe after deadline")
    )
    monkeypatch.setattr(
        "simtradedata.fetchers.mootdx_fetcher.time",
        SimpleNamespace(monotonic=lambda: next(clock)),
    )
    with pytest.raises(ConnectionError, match="within timeout"):
        MootdxFetcher(timeout=1).login()


def test_logout_releases_quote_connection():
    fetcher = MootdxFetcher()
    closed = []
    fetcher._client = SimpleNamespace(close=lambda: closed.append(True))
    fetcher._logged_in = True
    fetcher.logout()
    assert closed == [True]
    assert fetcher._client is None
    assert fetcher._logged_in is False


def test_server_without_stock_metadata_is_skipped(monkeypatch):
    server = importlib.import_module("mootdx.server")
    from mootdx.quotes import Quotes

    connected = []

    def connect(host, port, **kwargs):
        connected.append(host)
        return True

    api = SimpleNamespace(
        client=SimpleNamespace(settimeout=lambda timeout: None),
        get_security_count=lambda *args: (
            5000 if connected and connected[-1] == "good" else 0
        ),
        connect=connect,
    )
    client = SimpleNamespace(client=api, server=("old", 7709), close=lambda: None)
    monkeypatch.setattr(Quotes, "factory", lambda **kwargs: client)
    monkeypatch.setattr(
        server,
        "hosts",
        {"HQ": [{"addr": host, "port": 80} for host in ["bad", "good"]]},
    )
    monkeypatch.setattr(server, "connect2", lambda host: {**host, "time": 1})
    fetcher = MootdxFetcher()
    fetcher.login()
    assert connected == ["old", "bad", "good"]
    assert client.server == ("good", 80)
