import importlib
from types import SimpleNamespace

import pandas as pd
import pytest

from simtradedata.fetchers.mootdx_fetcher import MootdxFetcher


def test_fetch_f10_detail_returns_none_when_source_unavailable(monkeypatch):
    fetcher = MootdxFetcher()
    monkeypatch.setattr(fetcher, "_ensure_source_available", lambda: False)

    assert fetcher.fetch_f10_detail("000001.SZ", "latest") is None


class FakeQuotesClient:
    def k(self, **_kwargs):
        return pd.DataFrame()

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
    def k(self, **_kwargs):
        return pd.DataFrame()

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


def test_fetch_daily_bars_drops_duplicate_volume_from_bars_fallback(monkeypatch):
    fetcher = MootdxFetcher()
    fetcher._client = FakeQuotesClient()
    monkeypatch.setattr(fetcher, "_ensure_source_available", lambda: True)
    monkeypatch.setattr(fetcher, "_ensure_client", lambda: None)

    df = fetcher.fetch_daily_bars("600000.SS", "2026-06-18", "2026-06-25")

    assert not df.empty
    assert df.columns.tolist().count("volume") == 1
    assert df.columns.is_unique


def test_fetch_daily_bars_preserves_datetime_column_from_bars_fallback(monkeypatch):
    fetcher = MootdxFetcher()
    fetcher._client = FakeQuotesClientWithDatetimeColumn()
    monkeypatch.setattr(fetcher, "_ensure_source_available", lambda: True)
    monkeypatch.setattr(fetcher, "_ensure_client", lambda: None)

    df = fetcher.fetch_daily_bars("600000.SS", "2026-06-18", "2026-06-25")

    assert not df.empty
    assert "date" in df.columns
    assert df["date"].dt.strftime("%Y-%m-%d").tolist() == ["2026-06-18"]


def test_daily_bars_passes_iso_dates_to_mootdx(monkeypatch):
    calls = []

    class Client(FakeQuotesClientWithDatetimeColumn):
        def k(self, **kwargs):
            calls.append(kwargs)
            return self.bars()

    fetcher = MootdxFetcher()
    fetcher._client = Client()
    monkeypatch.setattr(fetcher, "_ensure_source_available", lambda: True)
    df = fetcher.fetch_daily_bars("600000.SS", "2026-06-18", "2026-06-25")

    assert len(df) == 1
    assert calls == [{"symbol": "600000", "begin": "2026-06-18", "end": "2026-06-25"}]


def test_empty_k_response_key_error_uses_existing_bars_fallback(monkeypatch):
    class Client(FakeQuotesClientWithDatetimeColumn):
        def k(self, **kwargs):
            raise KeyError("datetime")

        def bars(self, **kwargs):
            assert kwargs["offset"] == 800
            return super().bars()

    fetcher = MootdxFetcher()
    fetcher._client = Client()
    monkeypatch.setattr(fetcher, "_ensure_source_available", lambda: True)
    df = fetcher.fetch_daily_bars("600000.SS", "2026-06-18", "2026-06-25")
    assert df["date"].dt.strftime("%Y-%m-%d").tolist() == ["2026-06-18"]


def test_unrelated_k_schema_error_is_not_swallowed(monkeypatch):
    class Client(FakeQuotesClient):
        def k(self, **kwargs):
            raise KeyError("close")

    fetcher = MootdxFetcher()
    fetcher._client = Client()
    monkeypatch.setattr(fetcher, "_ensure_source_available", lambda: True)
    with pytest.raises(KeyError, match="close"):
        fetcher.fetch_daily_bars("600000.SS", "2026-06-18", "2026-06-25")


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
    assert events == ([] if default_healthy else ["close", ("new", 80)])
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
        client=SimpleNamespace(get_security_count=lambda *args: None),
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
        client=SimpleNamespace(get_security_count=lambda *args: None),
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
    client = SimpleNamespace(client=api, close=lambda: None)
    monkeypatch.setattr(Quotes, "factory", lambda **kwargs: client)
    monkeypatch.setattr(
        server,
        "hosts",
        {"HQ": [{"addr": host, "port": 80} for host in ["bad", "good"]]},
    )
    monkeypatch.setattr(server, "connect2", lambda host: {**host, "time": 1})
    fetcher = MootdxFetcher()
    fetcher.login()
    assert connected == ["bad", "good"]
    assert client.server == ("good", 80)
