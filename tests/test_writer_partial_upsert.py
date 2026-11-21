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


def test_identical_quarters_are_written_once(writer, caplog):
    frame = pd.DataFrame({'date': ['2017-06-30'] * 2, 'roe': [10., 10.]})
    assert writer.write_fundamentals('601857.SS', frame) == 1
    assert writer.conn.execute('SELECT COUNT(*) FROM fundamentals').fetchone() == (1,)
    assert 'Duplicate quarters' not in caplog.text
    assert len(frame) == 2


def test_quarter_revision_uses_latest_announcement_not_input_order(writer):
    frame = pd.DataFrame({'date': ['2017-06-30'] * 2,
                          'publ_date': ['2017-09-01', '2017-08-01'], 'roe': [12., 10.]})
    assert writer.write_fundamentals('601857.SS', frame) == 1
    assert writer.conn.execute('SELECT roe,publ_date FROM fundamentals').fetchone() == (12., '20170901')


@pytest.mark.parametrize('announcements', [None, [None, '2017-08-01'], ['2017-08-01'] * 2])
def test_ambiguous_quarters_do_not_write_any_rows(writer, announcements):
    from simtradedata.validators.data_validator import DataQualityError
    frame = pd.DataFrame({'date': ['2017-06-30'] * 2, 'roe': [10., 12.]})
    if announcements is not None:
        frame['publ_date'] = announcements
    with pytest.raises(DataQualityError, match='Conflicting quarters'):
        writer.write_fundamentals('601857.SS', frame)
    assert writer.conn.execute('SELECT COUNT(*) FROM fundamentals').fetchone() == (0,)
    assert writer.conn.execute('SELECT COUNT(*) FROM data_change_log').fetchone() == (0,)


def test_fundamental_batch_preserves_fields_absent_from_each_source(writer):
    writer.conn.execute("INSERT INTO fundamentals(symbol,date,roe,total_shares) VALUES ('000001.SZ','2026-03-31',10,100),('000002.SZ','2026-03-31',20,200)")
    frames = {'000001.SZ': pd.DataFrame({'date': ['2026-03-31'], 'roe': [12.]}),
              '000002.SZ': pd.DataFrame({'date': ['2026-03-31'], 'total_shares': [250.]})}
    originals = {key: frame.copy(deep=True) for key, frame in frames.items()}
    assert writer.write_fundamentals_batch(frames) == 2
    assert writer.conn.execute('SELECT roe,total_shares FROM fundamentals ORDER BY symbol').fetchall() == [(12., 100.), (20., 250.)]
    assert writer.conn.execute('SELECT COUNT(*) FROM data_change_log').fetchone() == (2,)
    for key in frames:
        pd.testing.assert_frame_equal(frames[key], originals[key])


def test_fundamental_batch_validates_all_companies_before_writing(writer):
    from simtradedata.validators.data_validator import DataQualityError
    frames = {'601857.SS': pd.DataFrame({'date': ['2017-06-30'], 'roe': [10.]}),
              '000001.SZ': pd.DataFrame({'date': ['2017-06-30'] * 2, 'roe': [10., 12.]})}
    with pytest.raises(DataQualityError, match='Conflicting quarters'):
        writer.write_fundamentals_batch(frames)
    assert writer.conn.execute('SELECT COUNT(*) FROM fundamentals').fetchone() == (0,)


def test_fundamental_batch_rollback_keeps_existing_values(writer):
    writer.conn.execute("INSERT INTO fundamentals(symbol,date,roe) VALUES ('601857.SS','2017-06-30',5)")
    writer.begin()
    writer.write_fundamentals_batch({'601857.SS': pd.DataFrame({'date': ['2017-06-30'], 'roe': [10.]})})
    writer.rollback()
    assert writer.conn.execute('SELECT roe FROM fundamentals').fetchone() == (5.,)
    assert writer.conn.execute('SELECT COUNT(*) FROM data_change_log').fetchone() == (0,)


def test_change_index_does_not_index_mutable_timestamp_and_repeated_updates_commit(writer):
    expression = writer.conn.execute("SELECT expressions FROM duckdb_indexes() WHERE index_name='idx_data_change_log_table_date'").fetchone()[0]
    assert 'changed_at' not in expression
    frame = pd.DataFrame({'symbol': ['000016.SZ'] * 15,
                          'date': pd.date_range('2026-09-01', periods=15).date, 'close': [10.] * 15})
    for _ in range(5):
        writer.begin()
        writer._upsert_symbol_rows('stocks', frame)
        writer.commit()
    assert writer.conn.execute('SELECT COUNT(*) FROM data_change_log').fetchone() == (15,)
    assert writer.conn.execute('SELECT COUNT(*) FROM stocks').fetchone() == (15,)


def test_legacy_mutable_change_index_is_rebuilt_without_losing_rows(tmp_path):
    path = tmp_path / 'legacy.duckdb'
    with DuckDBWriter(str(path)) as old:
        old.conn.execute("INSERT INTO data_change_log(table_name,symbol,date) VALUES ('stocks','000016.SZ','2026-09-01')")
        old.conn.execute('DROP INDEX idx_data_change_log_table_date')
        old.conn.execute('CREATE INDEX idx_data_change_log_table_date ON data_change_log(table_name,changed_at,date)')
    with DuckDBWriter(str(path)) as updated:
        expression = updated.conn.execute("SELECT expressions FROM duckdb_indexes() WHERE index_name='idx_data_change_log_table_date'").fetchone()[0]
        assert 'changed_at' not in expression
        assert updated.conn.execute('SELECT COUNT(*) FROM data_change_log').fetchone() == (1,)
