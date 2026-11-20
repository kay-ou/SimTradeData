"""Percent debt/equity must retain its units through ROA derivation and export."""

from types import SimpleNamespace

import pandas as pd
import pytest

from simtradedata.fetchers import baostock_fetcher as baostock
from simtradedata.writers.duckdb_writer import DuckDBWriter


@pytest.mark.parametrize(
    "debt_equity,expected",
    [(0.0, 15.0), (150.0, 6.0), (-1.0, 15.0 / 0.99), (-100.0, None), (None, None)],
)
def test_roa_uses_percent_debt_equity_and_guards_zero_denominator(
    debt_equity, expected
):
    with DuckDBWriter(":memory:") as writer:
        writer.write_fundamentals(
            "600519.SS",
            pd.DataFrame(
                {
                    "date": ["2026-09-30"],
                    "roe": [15.0],
                    "debt_equity_ratio": [debt_equity],
                }
            ),
        )
        writer.compute_derived_fundamentals()
        roa, roa_ttm = writer.conn.execute(
            "SELECT roa, roa_ttm FROM fundamentals"
        ).fetchone()
        if expected is None:
            assert roa is roa_ttm is None
        else:
            assert roa == pytest.approx(expected)
            assert roa_ttm == pytest.approx(expected)


def test_roa_derivation_preserves_source_values_and_symbol_scope():
    with DuckDBWriter(":memory:") as writer:
        for symbol, provided in [
            ("000001.SZ", None),
            ("000002.SZ", None),
            ("000003.SZ", 7.5),
        ]:
            writer.write_fundamentals(
                symbol,
                pd.DataFrame(
                    {
                        "date": ["2026-09-30"],
                        "roe": [15.0],
                        "debt_equity_ratio": [150.0],
                        "roa": [provided],
                    }
                ),
            )
        for _ in range(2):
            writer.compute_derived_fundamentals(symbols=["000001.SZ", "000003.SZ"])
            assert writer.conn.execute(
                "SELECT symbol, roa, roa_ttm FROM fundamentals ORDER BY symbol"
            ).fetchall() == [
                ("000001.SZ", 6.0, 6.0),
                ("000002.SZ", None, None),
                ("000003.SZ", 7.5, 7.5),
            ]


def test_baostock_roa_and_ttm_survive_full_parquet_export(monkeypatch, tmp_path):
    dates = ["2026-03-31", "2026-06-30", "2026-09-30", "2026-12-31"]

    def report(quarter, **values):
        return SimpleNamespace(
            error_code="0",
            data=pd.DataFrame(
                {
                    "code": ["sh.600519"],
                    "statDate": [dates[quarter - 1]],
                    "pubDate": ["2027-04-30"],
                    **{key: [value] for key, value in values.items()},
                }
            ),
        )

    monkeypatch.setattr(
        baostock.bs,
        "query_profit_data",
        lambda **kwargs: report(
            kwargs["quarter"],
            roeAvg=str(5 * (kwargs["quarter"] + 1)),
        ),
    )
    monkeypatch.setattr(
        baostock.bs,
        "query_balance_data",
        lambda **kwargs: report(
            kwargs["quarter"],
            liabilityToAsset="0.6",
        ),
    )
    for name in ["query_growth_data", "query_operation_data", "query_cash_flow_data"]:
        monkeypatch.setattr(
            baostock.bs, name, lambda **kwargs: SimpleNamespace(error_code="empty")
        )
    monkeypatch.setattr(baostock, "read_baostock_result", lambda result: result.data)
    fetcher = baostock.BaoStockFetcher()
    data = pd.concat(
        [
            fetcher.fetch_quarterly_fundamentals("600519.SS", 2026, quarter)
            for quarter in range(1, 5)
        ],
        ignore_index=True,
    )
    assert data["debt_equity_ratio"].tolist() == pytest.approx([150.0] * 4)

    with DuckDBWriter(":memory:") as writer:
        writer.write_fundamentals(
            "600519.SS", data.drop(columns="code").set_index("end_date")
        )
        writer.write_stock_metadata(
            pd.DataFrame(
                {
                    "symbol": ["600519.SS"],
                    "stock_name": ["贵州茅台"],
                    "listed_date": ["2001-08-27"],
                    "security_type": ["1"],
                    "listing_status": ["1"],
                    "blocks": ["{}"],
                }
            )
        )
        writer.conn.execute(
            "INSERT INTO benchmark VALUES ('2015-01-01', 1, 1, 1, 1, 1, 1)"
        )
        monkeypatch.setattr(writer, "_ensure_stock_metadata_from_pool", lambda: None)
        output = tmp_path / "export"
        writer.export_to_parquet(str(output), market="cn")
        stored = writer.conn.execute(
            "SELECT roa, roa_ttm FROM fundamentals ORDER BY date"
        ).fetchdf()
        exported = pd.read_parquet(output / "fundamentals" / "600519.SS.parquet")
        for frame in [stored, exported]:
            assert frame["roa"].tolist() == pytest.approx([4.0, 6.0, 8.0, 10.0])
            assert frame["roa_ttm"].tolist() == pytest.approx([4.0, 5.0, 6.0, 7.0])
