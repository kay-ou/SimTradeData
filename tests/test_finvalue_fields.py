"""Financial field, unit, migration and export contracts."""


import numpy as np
import pandas as pd
import pytest

from simtradedata.config.mootdx_finvalue_map import FULL_FUNDAMENTAL_FIELDS
from simtradedata.fetchers.mootdx_affair_fetcher import MootdxAffairFetcher
from simtradedata.writers.duckdb_writer import DuckDBWriter


def raw_report(columns=659):
    raw = pd.DataFrame(np.zeros((1, columns)), index=["600519"])
    raw.iloc[0, 0] = 260930
    if columns > 314:
        raw.iloc[0, 314] = 261030
    return raw


@pytest.mark.parametrize("position,field", [
    (9, "trading_assets"), (10, "bill_receivable"), (35, "good_will"),
    (65, "capital_reserve_fund"), (77, "operating_expense"),
    (78, "administration_expense"), (93, "income_tax_cost"),
    (98, "goods_sale_service_render_cash"), (101, "subtotal_operate_cash_inflow"),
    (102, "goods_and_services_cash_paid"), (103, "staff_behalf_paid"),
    (104, "all_taxes_paid"), (132, "begin_period_cash"),
    (133, "end_period_cash_equivalent"),
])
def test_confirmed_statement_fields_are_exported_without_unit_change(position, field):
    raw = raw_report()
    raw.iloc[0, position] = 1234567.0
    result = MootdxAffairFetcher()._convert_to_ptrade_format(raw)
    assert result.loc[0, field] == 1234567.0
    assert result.loc[0, "publ_date"] == pd.Timestamp("2026-10-30")


@pytest.mark.parametrize("position,field", [
    (402, "settlement_provi"), (503, "exchange_income"),
    (562, "net_deposit_increase"),
])
def test_extended_amounts_convert_wan_yuan_to_yuan(position, field):
    raw = raw_report()
    raw.iloc[0, position] = 2.5
    result = MootdxAffairFetcher()._convert_to_ptrade_format(raw, fields=[field])
    assert result.loc[0, field] == 25000.0


def test_total_revenue_uses_cumulative_field_not_single_quarter():
    raw = raw_report()
    raw.iloc[0, 312] = 99.0  # Single-quarter revenue, not this PTrade field.
    raw.iloc[0, 502] = 2.5
    raw.iloc[0, 501] = 1.25
    result = MootdxAffairFetcher()._convert_to_ptrade_format(raw)
    assert result.loc[0, "total_operating_revenue"] == 25000.0
    assert result.loc[0, "diluted_eps"] == 1.25


def test_disclosed_weighted_roe_and_debt_equity_have_correct_meanings():
    raw = raw_report()
    raw.iloc[0, 197] = 7.0
    raw.iloc[0, 281] = 12.25
    raw.iloc[0, 63] = 60.0
    raw.iloc[0, 72] = 40.0
    raw.iloc[0, 210] = 60.0  # Asset-liability percentage is not debt/equity.
    result = MootdxAffairFetcher()._convert_to_ptrade_format(raw)
    assert result.loc[0, "roe_weighted"] == 12.25
    assert result.loc[0, "debt_equity_ratio"] == 150.0
    raw.iloc[0, 72] = 0
    result = MootdxAffairFetcher()._convert_to_ptrade_format(raw, fields=["debt_equity_ratio"])
    assert pd.isna(result.loc[0, "debt_equity_ratio"])


def test_missing_columns_are_not_invented_as_zero():
    fetcher = MootdxAffairFetcher()
    short = fetcher._convert_to_ptrade_format(raw_report(315))
    assert "diluted_eps" not in short.columns
    assert "total_operating_revenue" not in short.columns
    raw = raw_report()
    raw.iloc[0, 35] = float("nan")
    full = fetcher._convert_to_ptrade_format(raw)
    assert pd.isna(full.loc[0, "good_will"])
    assert full.loc[0, "bill_receivable"] == 0.0


def test_existing_db_gains_new_columns_without_losing_data(tmp_path):
    path = tmp_path / "financial.duckdb"
    writer = DuckDBWriter(str(path))
    writer.write_fundamentals("600519.SS", pd.DataFrame({
        "date": ["2026-09-30"], "basic_eps": [1.25],
    }))
    # Recreate the relevant part of the pre-expansion schema.
    for field in ["good_will", "settlement_provi", "diluted_eps"]:
        writer.conn.execute(f"ALTER TABLE fundamentals DROP COLUMN {field}")
    writer.close()
    writer = DuckDBWriter(str(path))
    try:
        old = writer.conn.execute(
            "SELECT basic_eps, good_will, settlement_provi, diluted_eps FROM fundamentals"
        ).fetchone()
        assert old == (1.25, None, None, None)
        writer.write_fundamentals("600519.SS", pd.DataFrame({
            "date": ["2026-09-30"], "good_will": [123.0],
            "settlement_provi": [25000.0], "diluted_eps": [1.2],
        }))
        assert writer.conn.execute(
            "SELECT basic_eps, good_will, settlement_provi, diluted_eps FROM fundamentals"
        ).fetchone() == (1.25, 123.0, 25000.0, 1.2)
    finally:
        writer.close()


def test_new_fields_survive_parser_database_and_full_parquet_export(tmp_path, monkeypatch):
    raw = raw_report()
    raw.iloc[0, 35] = 1234567.0
    raw.iloc[0, 402] = 2.5
    raw.iloc[0, 502] = 9.5
    raw.iloc[0, 501] = 1.25
    data = MootdxAffairFetcher()._convert_to_ptrade_format(raw)
    writer = DuckDBWriter(":memory:")
    try:
        columns = {row[1] for row in writer.conn.execute("PRAGMA table_info('fundamentals')").fetchall()}
        assert set(FULL_FUNDAMENTAL_FIELDS) <= columns
        writer.write_fundamentals("600519.SS", data.drop(columns="code").set_index("end_date"))
        writer.write_stock_metadata(pd.DataFrame({
            "symbol": ["600519.SS"], "stock_name": ["贵州茅台"],
            "listed_date": ["2001-08-27"], "security_type": ["1"],
            "listing_status": ["1"], "blocks": ["{}"],
        }))
        writer.conn.execute("INSERT INTO benchmark VALUES ('2015-01-01', 1, 1, 1, 1, 1, 1)")
        monkeypatch.setattr(writer, "_ensure_stock_metadata_from_pool", lambda: None)
        writer.export_to_parquet(str(tmp_path), market="cn")
        exported = pd.read_parquet(tmp_path / "fundamentals" / "600519.SS.parquet")
        assert set(FULL_FUNDAMENTAL_FIELDS) <= set(exported.columns)
        row = exported.iloc[0]
        assert row["good_will"] == 1234567.0
        assert row["settlement_provi"] == 25000.0
        assert row["total_operating_revenue"] == 95000.0
        assert row["diluted_eps"] == 1.25
    finally:
        writer.close()
