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



def test_legacy_roa_is_restored_from_source_and_ttm_rebuilt(tmp_path, monkeypatch):
    directory = tmp_path / "mootdx_financial"
    directory.mkdir()
    (directory / "gpcw20260930.zip").touch()
    from simtradedata.fetchers.mootdx_affair_fetcher import MootdxAffairFetcher
    monkeypatch.setattr(MootdxAffairFetcher, "parse_local", lambda *a: pd.DataFrame({"raw":[1]}))
    monkeypatch.setattr(MootdxAffairFetcher, "_convert_to_ptrade_format", lambda *a, **k:
        pd.DataFrame({"code":["600000"], "end_date":["2026-09-30"], "roa":[5.0]}))
    with DuckDBWriter(str(tmp_path / "cn.duckdb")) as writer:
        writer.conn.execute("""INSERT INTO fundamentals(symbol,date,roe,debt_equity_ratio,roa,roe_ttm,roa_ttm)
            VALUES ('600000.SS','2026-09-30',10,100,10.0/101,10,10.0/101)""")
        writer.compute_derived_fundamentals()
        writer.compute_derived_fundamentals()
        assert writer.conn.execute("SELECT roa,roa_ttm,roa_derived FROM fundamentals").fetchone() == (5.0, 5.0, False)


def test_unknown_legacy_roa_blocks_export_without_changing_data(tmp_path):
    with DuckDBWriter(str(tmp_path / "cn.duckdb")) as writer:
        writer.conn.execute("""INSERT INTO fundamentals(symbol,date,roe,debt_equity_ratio,roa,roe_ttm,roa_ttm)
            VALUES ('600000.SS','2026-09-30',10,100,10.0/101,10,10.0/101)""")
        before = writer.conn.execute("SELECT * FROM fundamentals").fetchall()
        with pytest.raises(ValueError, match="重新导入季度财务"):
            writer.export_to_parquet(str(tmp_path / "export"))
        assert writer.conn.execute("SELECT * FROM fundamentals").fetchall() == before
        assert not (tmp_path / "export").exists()


def test_source_roa_matching_old_formula_is_preserved_and_derived_values_refresh():
    with DuckDBWriter(":memory:") as writer:
        writer.write_fundamentals("600000.SS", pd.DataFrame({"date":["2026-09-30"], "roe":[10.0],
            "debt_equity_ratio":[100.0], "roa":[10.0/101], "roa_ttm":[7.0]}))
        writer.write_fundamentals("600001.SS", pd.DataFrame({"date":["2026-09-30"], "roe":[10.0],
            "debt_equity_ratio":[100.0]}))
        writer.compute_derived_fundamentals()
        writer.write_fundamentals("600001.SS", pd.DataFrame({"date":["2026-09-30"], "roe":[20.0]}))
        writer.compute_derived_fundamentals()
        rows = writer.conn.execute("SELECT symbol,roa,roa_ttm FROM fundamentals ORDER BY symbol").fetchall()
        assert rows == [("600000.SS",10.0/101,7.0),("600001.SS",10.0,10.0)]


def test_restoring_legacy_roa_refreshes_following_ttm_and_preserves_known_source_ttm(tmp_path, monkeypatch):
    directory = tmp_path / "mootdx_financial"
    directory.mkdir()
    (directory / "gpcw20260630.zip").touch()
    from simtradedata.fetchers.mootdx_affair_fetcher import MootdxAffairFetcher
    monkeypatch.setattr(MootdxAffairFetcher, "parse_local", lambda *a: pd.DataFrame({"raw":[1]}))
    monkeypatch.setattr(MootdxAffairFetcher, "_convert_to_ptrade_format", lambda *a, **k:
        pd.DataFrame({"code":["600000"], "end_date":["2026-06-30"], "roa":[5.0]}))
    with DuckDBWriter(str(tmp_path / "cn.duckdb")) as writer:
        writer.conn.execute("""INSERT INTO fundamentals(symbol,date,roe,debt_equity_ratio,roa,roe_ttm,roa_ttm)
            VALUES ('600000.SS','2026-06-30',10,100,10.0/101,10,10.0/101),
                   ('600000.SS','2026-09-30',20,100,10,15,(10+10.0/101)/2)""")
        writer.write_fundamentals("600000.SS", pd.DataFrame({"date":["2026-12-31"], "roa":[12.0], "roa_ttm":[9.0]}))
        writer.compute_derived_fundamentals()
        assert writer.conn.execute("SELECT roa_ttm FROM fundamentals ORDER BY date").fetchall() == [(5.0,),(7.5,),(9.0,)]



def test_reimported_source_roa_rebuilds_untracked_legacy_ttm():
    with DuckDBWriter(":memory:") as writer:
        writer.conn.execute("""INSERT INTO fundamentals(symbol,date,roe,debt_equity_ratio,roa,roe_ttm,roa_ttm)
            VALUES ('600000.SS','2026-09-30',10,100,10.0/101,10,10.0/101)""")
        writer.write_fundamentals("600000.SS", pd.DataFrame({"date":["2026-09-30"],"roa":[5.0]}))
        writer.compute_derived_fundamentals()
        assert writer.conn.execute("SELECT roa,roa_ttm FROM fundamentals").fetchone() == (5.0,5.0)


def test_derived_roa_is_cleared_when_inputs_become_invalid():
    with DuckDBWriter(":memory:") as writer:
        writer.write_fundamentals("600000.SS", pd.DataFrame({"date":["2026-09-30"],"roe":[10.0],"debt_equity_ratio":[100.0]}))
        writer.compute_derived_fundamentals()
        writer.write_fundamentals("600000.SS", pd.DataFrame({"date":["2026-09-30"],"debt_equity_ratio":[-100.0]}))
        writer.compute_derived_fundamentals()
        assert writer.conn.execute("SELECT roa,roa_ttm FROM fundamentals").fetchone() == (None,None)



@pytest.mark.parametrize("damage", ["missing_code", "wrong_date", "duplicate", "missing_roa", "nonfinite"])
def test_unverified_source_roa_does_not_mutate_legacy_rows(tmp_path, monkeypatch, damage):
    directory = tmp_path / "mootdx_financial"
    directory.mkdir()
    (directory / "gpcw20260930.zip").touch()
    report = pd.DataFrame({"code":["600000"],"end_date":["2026-09-30"],"roa":[5.0]})
    if damage == "missing_code": report["code"] = "600001"
    elif damage == "wrong_date": report["end_date"] = "2026-06-30"
    elif damage == "duplicate": report = pd.concat([report,report],ignore_index=True)
    elif damage == "missing_roa": report = report.drop(columns="roa")
    else: report["roa"] = float("inf")
    from simtradedata.fetchers.mootdx_affair_fetcher import MootdxAffairFetcher
    monkeypatch.setattr(MootdxAffairFetcher, "parse_local", lambda *a: pd.DataFrame({"raw":[1]}))
    monkeypatch.setattr(MootdxAffairFetcher, "_convert_to_ptrade_format", lambda *a, **k: report)
    with DuckDBWriter(str(tmp_path / "cn.duckdb")) as writer:
        writer.conn.execute("""INSERT INTO fundamentals(symbol,date,roe,debt_equity_ratio,roa)
            VALUES ('600000.SS','2026-09-30',10,100,10.0/101)""")
        before = writer.conn.execute("SELECT * FROM fundamentals").fetchall()
        with pytest.raises(ValueError, match="重新导入季度财务"):
            writer.compute_derived_fundamentals()
        assert writer.conn.execute("SELECT * FROM fundamentals").fetchall() == before


def test_unresolved_legacy_roa_preserves_previous_delta_package(tmp_path):
    output = tmp_path / "export"
    output.mkdir()
    sentinel = output / "previous.txt"
    sentinel.write_text("previous package")
    with DuckDBWriter(str(tmp_path / "cn.duckdb")) as writer:
        writer.conn.execute("""INSERT INTO fundamentals(symbol,date,roe,debt_equity_ratio,roa)
            VALUES ('600000.SS','2026-09-30',10,100,10.0/101)""")
        with pytest.raises(ValueError, match="重新导入季度财务"):
            writer.export_delta(str(output), "2026-09-30", "2026-11-23")
    assert sentinel.read_text() == "previous package"
