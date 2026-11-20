"""SDK request errors must not look like the end of listing history."""

from types import SimpleNamespace

import pandas as pd
import pytest
from mootdx.quotes import Quotes, StdQuotes
from tdxpy.base_socket_client import last_ack_time
from tdxpy.exceptions import TdxFunctionCallError

from simtradedata.fetchers.mootdx_fetcher import MootdxFetcher
from simtradedata.router import SmartRouter


@pytest.fixture
def sdk_quotes(monkeypatch):
    def records(dates):
        return pd.DataFrame(
            {
                "datetime": dates,
                "open": 4.0,
                "high": 4.1,
                "low": 3.9,
                "close": 4.0,
                "volume": 100.0,
                "amount": 400.0,
            }
        ).to_dict("records")

    recent = records(pd.bdate_range("2022-01-03", periods=800))
    older = records(pd.bdate_range("2021-12-29", periods=3))

    class API:
        def __init__(self):
            self.client = SimpleNamespace(settimeout=lambda timeout: None)
            self.auto_retry = False
            self.raise_exception = False
            self.retry_strategy = SimpleNamespace(generate=lambda: iter([0]))
            self.calls = []
            self.mode = "failed"
            self.connection_modes = []

        def connect(self, host, port, **kwargs):
            self.ip, self.port = host, port
            self.connection_modes.append(self.raise_exception)
            return True

        def disconnect(self):
            pass

        def get_security_count(self, market):
            return 5000

        @last_ack_time
        def get_security_bars(self, frequency, market, symbol, start, count):
            self.calls.append(start)
            if start == 0:
                return recent
            if self.mode == "empty":
                return []
            if self.mode == "transient" and self.calls.count(start) > 1:
                return older
            raise TimeoutError("Later page timed out")

    api = API()
    # Use the real bars() adapter and SDK error wrapper; replace only transport.
    quotes = object.__new__(StdQuotes)
    quotes.client = api
    quotes.server = ("test", 7709)
    monkeypatch.setattr(Quotes, "factory", lambda **kwargs: quotes)
    fetcher = MootdxFetcher()
    monkeypatch.setattr(fetcher, "_ensure_source_available", lambda: True)
    fetcher.login()
    return fetcher, api


def test_failed_later_page_raises_instead_of_returning_partial_history(sdk_quotes):
    fetcher, api = sdk_quotes
    with pytest.raises(TdxFunctionCallError) as caught:
        fetcher.fetch_daily_bars("600000.SS", "2015-01-01", "2026-11-20")
    assert isinstance(caught.value.original_exception, TimeoutError)
    assert api.calls == [0, 800, 800]
    assert api.connection_modes[0] is False


def test_genuine_empty_page_still_allows_shorter_listing_history(sdk_quotes):
    fetcher, api = sdk_quotes
    api.mode = "empty"
    result = fetcher.fetch_daily_bars("600000.SS", "2015-01-01", "2026-11-20")
    assert len(result) == 800
    assert api.calls == [0, 800]
    assert result["date"].is_monotonic_increasing


def test_transient_later_page_failure_recovers_through_sdk_retry(sdk_quotes):
    fetcher, api = sdk_quotes
    api.mode = "transient"
    result = fetcher.fetch_daily_bars("600000.SS", "2015-01-01", "2026-11-20")
    assert len(result) == 803
    assert api.calls == [0, 800, 800]
    assert result["date"].min() == pd.Timestamp("2021-12-29")


def test_failed_later_page_reaches_router_fallback(sdk_quotes):
    fetcher, api = sdk_quotes
    fallback = pd.DataFrame({"date": [pd.Timestamp("2015-01-02")], "close": [999.0]})
    router = SmartRouter(config={"daily_bars": {"cn": ["mootdx", "eastmoney"]}})
    router._fetchers.update(
        {
            "mootdx": SimpleNamespace(fetch_daily_data=fetcher.fetch_daily_bars),
            "eastmoney": SimpleNamespace(fetch_daily_bars=lambda *args: fallback),
        }
    )
    result = router.get_daily_bars("600000.SS", "2015-01-01", "2026-11-20")
    pd.testing.assert_frame_equal(result, fallback)
    assert api.calls == [0, 800, 800]
