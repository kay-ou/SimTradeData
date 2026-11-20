"""Regression coverage for writes that preserve fields from other sources."""

from datetime import date

import pandas as pd
import pytest

from simtradedata.writers.duckdb_writer import DuckDBWriter


@pytest.fixture
def writer():
    with DuckDBWriter(db_path=":memory:") as instance:
        yield instance


@pytest.mark.parametrize(
    "table,method,field,preserved",
    [
        ("valuation", "write_valuation", "pe_ttm", "pb"),
        ("fundamentals", "write_fundamentals", "roe", "total_shares"),
        ("exrights", "write_exrights", "bonus_ps", "exer_forward_a"),
    ],
)
@pytest.mark.parametrize("date_format", ["column", "named_index", "unnamed_index"])
def test_partial_upsert_preserves_other_fields_and_records_changes(
    writer, table, method, field, preserved, date_format
):
    writer.conn.execute(f"""
        INSERT INTO {table} (symbol, date, {field}, {preserved})
        VALUES ('000001.SZ', '2026-03-31', 10.0, 2.0)
    """)
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-03-31", "2026-04-01"]),
            field: [12.0, 13.0],
            "ignored_field": [99.0, 99.0],
        }
    )
    if date_format != "column":
        frame = frame.set_index("date")
        if date_format == "unnamed_index":
            frame.index.name = None
    original = frame.copy(deep=True)

    assert getattr(writer, method)("000001.SZ", frame) == 2

    rows = writer.conn.execute(f"""
        SELECT {field}, {preserved} FROM {table}
        WHERE symbol = '000001.SZ' ORDER BY date
    """).fetchall()
    assert rows == [(12.0, 2.0), (13.0, None)]
    changes = writer.conn.execute("""
        SELECT table_name, symbol, date FROM data_change_log ORDER BY date
    """).fetchall()
    assert changes == [
        (table, "000001.SZ", date(2026, 3, 31)),
        (table, "000001.SZ", date(2026, 4, 1)),
    ]
    pd.testing.assert_frame_equal(frame, original)


@pytest.mark.parametrize(
    "table,method,field",
    [
        ("valuation", "write_valuation", "pe_ttm"),
        ("fundamentals", "write_fundamentals", "roe"),
    ],
)
def test_explicit_null_still_overwrites_supplied_field(writer, table, method, field):
    writer.conn.execute(f"""
        INSERT INTO {table} (symbol, date, {field})
        VALUES ('000001.SZ', '2026-03-31', 10.0)
    """)
    frame = pd.DataFrame({"date": ["2026-03-31"], field: [None]})

    assert getattr(writer, method)("000001.SZ", frame) == 1
    assert writer.conn.execute(f"SELECT {field} FROM {table}").fetchone() == (None,)


@pytest.mark.parametrize(
    "table,method,field",
    [
        ("valuation", "write_valuation", "pe_ttm"),
        ("fundamentals", "write_fundamentals", "roe"),
    ],
)
def test_key_only_write_preserves_existing_row_and_inserts_new_row(
    writer, table, method, field
):
    writer.conn.execute(f"""
        INSERT INTO {table} (symbol, date, {field})
        VALUES ('000001.SZ', '2026-03-31', 10.0)
    """)
    frame = pd.DataFrame({"date": ["2026-03-31", "2026-04-01"]})

    assert getattr(writer, method)("000001.SZ", frame) == 2
    assert writer.conn.execute(
        f"SELECT {field} FROM {table} ORDER BY date"
    ).fetchall() == [(10.0,), (None,)]
