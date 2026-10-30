"""Tests for EastMoneyFetcher static methods (parsers and secid conversion)."""

import pytest
import pandas as pd

from simtradedata.fetchers.eastmoney_fetcher import EastMoneyFetcher


@pytest.mark.unit
class TestSecidConversion:
    """Test to_secid static method for various market codes."""

    def test_sz_stock(self):
        assert EastMoneyFetcher.to_secid("000001.SZ") == "0.000001"

    def test_sh_stock(self):
        assert EastMoneyFetcher.to_secid("600000.SS") == "1.600000"

    def test_sz_etf(self):
        assert EastMoneyFetcher.to_secid("159919.SZ") == "0.159919"

    def test_sh_etf(self):
        assert EastMoneyFetcher.to_secid("510050.SS") == "1.510050"

    def test_star_market(self):
        # STAR Market (science and technology innovation board) is SS
        assert EastMoneyFetcher.to_secid("688001.SS") == "1.688001"

    def test_chinext(self):
        # ChiNext board is SZ
        assert EastMoneyFetcher.to_secid("300001.SZ") == "0.300001"

    def test_bj_stock(self):
        assert EastMoneyFetcher.to_secid("430047.BJ") == "0.430047"


@pytest.mark.unit
class TestParseKlines:
    """Test parse_klines static method."""

    def test_single_row(self):
        klines = [
            "2024-01-02,10.50,10.80,11.00,10.30,100000,1050000.00,6.67"
        ]
        df = EastMoneyFetcher.parse_klines(klines)
        assert len(df) == 1
        assert df.iloc[0]["date"] == "2024-01-02"
        assert df.iloc[0]["open"] == 10.50
        assert df.iloc[0]["close"] == 10.80
        assert df.iloc[0]["high"] == 11.00
        assert df.iloc[0]["low"] == 10.30
        assert df.iloc[0]["volume"] == 100000
        assert df.iloc[0]["amount"] == 1050000.00
        assert df.iloc[0]["amplitude"] == 6.67

    def test_multiple_rows(self):
        klines = [
            "2024-01-02,10.50,10.80,11.00,10.30,100000,1050000.00,6.67",
            "2024-01-03,10.80,11.20,11.50,10.70,120000,1300000.00,7.41",
        ]
        df = EastMoneyFetcher.parse_klines(klines)
        assert len(df) == 2
        assert df.iloc[0]["date"] == "2024-01-02"
        assert df.iloc[1]["date"] == "2024-01-03"

    def test_empty(self):
        df = EastMoneyFetcher.parse_klines([])
        assert isinstance(df, pd.DataFrame)
        assert df.empty


@pytest.mark.unit
class TestParseMoneyFlow:
    """Test parse_money_flow static method."""

    def test_single_row(self):
        klines = [
            "2024-01-02,-500000.00,100000.00,-200000.00,300000.00,-700000.00"
        ]
        df = EastMoneyFetcher.parse_money_flow(klines)
        assert len(df) == 1
        assert df.iloc[0]["date"] == "2024-01-02"
        assert df.iloc[0]["net_main"] == -500000.00
        assert df.iloc[0]["net_super"] == 100000.00
        assert df.iloc[0]["net_large"] == -200000.00
        assert df.iloc[0]["net_medium"] == 300000.00
        assert df.iloc[0]["net_small"] == -700000.00

    def test_empty(self):
        df = EastMoneyFetcher.parse_money_flow([])
        assert isinstance(df, pd.DataFrame)
        assert df.empty


@pytest.mark.unit
class TestParseLhb:
    """Test parse_lhb static method."""

    def test_single_record(self):
        records = [
            {
                "SECUCODE": "000001.SZ",
                "TRADE_DATE": "2024-01-02T00:00:00.000",
                "EXPLAIN": "daily_limit_up",
                "BILLBOARD_NET_AMT": 5000000.00,
                "BILLBOARD_BUY_AMT": 8000000.00,
                "BILLBOARD_SELL_AMT": 3000000.00,
            }
        ]
        df = EastMoneyFetcher.parse_lhb(records)
        assert len(df) == 1
        assert df.iloc[0]["symbol"] == "000001.SZ"
        assert df.iloc[0]["date"] == "2024-01-02"
        assert df.iloc[0]["reason"] == "daily_limit_up"
        assert df.iloc[0]["net_buy"] == 5000000.00
        assert df.iloc[0]["buy_amount"] == 8000000.00
        assert df.iloc[0]["sell_amount"] == 3000000.00

    def test_empty(self):
        df = EastMoneyFetcher.parse_lhb([])
        assert isinstance(df, pd.DataFrame)
        assert df.empty


@pytest.mark.unit
class TestParseMargin:
    """Test parse_margin static method."""

    def test_single_record(self):
        records = [
            {
                "SECUCODE": "600000.SH",
                "STATISTICS_DATE": "2024-01-02T00:00:00.000",
                "FIN_BALANCE": 1000000.00,
                "LOAN_BALANCE": 500000.00,
                "MARGIN_BALANCE": 1500000.00,
            }
        ]
        df = EastMoneyFetcher.parse_margin(records)
        assert len(df) == 1
        assert df.iloc[0]["symbol"] == "600000.SH"
        assert df.iloc[0]["date"] == "2024-01-02"
        assert df.iloc[0]["rzye"] == 1000000.00
        assert df.iloc[0]["rqyl"] == 500000.00
        assert df.iloc[0]["rzrqye"] == 1500000.00

    def test_empty(self):
        df = EastMoneyFetcher.parse_margin([])
        assert isinstance(df, pd.DataFrame)
        assert df.empty

    def test_new_margin_record_shape(self):
        records = [
            {
                "SECUCODE": "600000.SH",
                "DATE": "2026-06-24 00:00:00",
                "RZYE": 1000000.00,
                "RQYL": 500000.00,
                "RZRQYE": 1500000.00,
            }
        ]
        df = EastMoneyFetcher.parse_margin(records)
        assert len(df) == 1
        assert df.iloc[0]["symbol"] == "600000.SH"
        assert df.iloc[0]["date"] == "2026-06-24"
        assert df.iloc[0]["rzye"] == 1000000.00
        assert df.iloc[0]["rqyl"] == 500000.00
        assert df.iloc[0]["rzrqye"] == 1500000.00

    def test_fetch_margin_uses_new_endpoint_and_filters_dates(self, monkeypatch):
        fetcher = EastMoneyFetcher()

        def fake_get(_url, params):
            assert params["reportName"] == "RPTA_WEB_RZRQ_GGMX"
            assert params["filter"] == '(SCODE="600000")'
            return {
                "result": {
                    "pages": 1,
                    "data": [
                        {
                            "SECUCODE": "600000.SH",
                            "DATE": "2026-06-24 00:00:00",
                            "RZYE": 1.0,
                            "RQYL": 2.0,
                            "RZRQYE": 3.0,
                        },
                        {
                            "SECUCODE": "600000.SH",
                            "DATE": "2026-05-30 00:00:00",
                            "RZYE": 4.0,
                            "RQYL": 5.0,
                            "RZRQYE": 6.0,
                        },
                    ],
                }
            }

        monkeypatch.setattr(fetcher, "_get", fake_get)

        df = fetcher.fetch_margin("600000.SS", "2026-06-01", "2026-06-25")

        assert len(df) == 1
        assert df.iloc[0]["date"] == "2026-06-24"


@pytest.mark.unit
class TestParseDividends:
    """parse_dividends must emit the full event-local exrights contract."""

    def test_maps_cash_and_bonus_shares(self):
        records = [{
            "EX_DIVIDEND_DATE": "2025-06-12 00:00:00",
            "PRETAX_BONUS_RMB": 3.62,   # 10派3.62元
            "BONUS_RATIO": 2.0,         # 10送2
            "IT_RATIO": 1.0,            # 10转1
        }]
        df = EastMoneyFetcher.parse_dividends(records)
        row = df.iloc[0]
        assert row["date"] == "2025-06-12"
        assert row["bonus_ps"] == pytest.approx(0.362)
        assert row["allotted_ps"] == pytest.approx(0.3)
        assert row["rationed_ps"] == pytest.approx(0.0)
        assert row["rationed_px"] == pytest.approx(0.0)
        assert row["dividend"] == pytest.approx(0.362)
