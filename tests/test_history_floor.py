"""Regression tests for the configured CN history floor."""

import json

import duckdb
import pandas as pd
import pytest

from scripts import check_integrity as integrity


def _create_floor_db(path, *, metadata_date="20050430", status_date="20050430"):
    conn = duckdb.connect(str(path))
    try:
        conn.execute("CREATE TABLE stocks (symbol VARCHAR, date DATE)")
        conn.execute("CREATE TABLE valuation (symbol VARCHAR, date DATE)")
        conn.execute(
            "CREATE TABLE stock_metadata (symbol VARCHAR, de_listed_date DATE, security_type VARCHAR, blocks VARCHAR)"
        )
        conn.execute(
            "CREATE TABLE benchmark (date DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE, money DOUBLE)"
        )
        conn.execute("CREATE TABLE index_constituents (date VARCHAR, index_code VARCHAR, symbols VARCHAR)")
        conn.execute("CREATE TABLE stock_status (date VARCHAR, status_type VARCHAR, symbols VARCHAR)")
        conn.execute("INSERT INTO stocks VALUES ('000001.SZ', '2005-05-09')")
        conn.execute("INSERT INTO valuation VALUES ('000001.SZ', '2005-05-09')")
        conn.execute(
            "INSERT INTO stock_metadata VALUES ('000001.SZ', '2900-01-01', '1', '{\"ZJHHY\": []}')"
        )
        conn.execute(
            "INSERT INTO benchmark VALUES ('2005-05-09', 1, 1, 1, 1, 1, 1)"
        )
        conn.execute(
            "INSERT INTO index_constituents VALUES (?, '399005.SZ', ?)",
            [metadata_date, json.dumps(["000001.SZ"])],
        )
        conn.execute(
            "INSERT INTO stock_status VALUES (?, 'HALT', ?)",
            [status_date, json.dumps(["000001.SZ"])],
        )
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("metadata_date", "status_date", "expect_floor_fail"),
    [
        ("20050430", "20050430", True),
        ("20050509", "20050509", False),
    ],
)
def test_integrity_history_floor_date_tables(
    tmp_path, monkeypatch, metadata_date, status_date, expect_floor_fail
):
    db_path = tmp_path / "cn.duckdb"
    _create_floor_db(db_path, metadata_date=metadata_date, status_date=status_date)
    monkeypatch.setattr(integrity, "CN_HISTORY_START", "2005-05-09")

    report = integrity.check_integrity(str(db_path), target_date="2005-05-09")

    assert (report["status"] == "fail") is expect_floor_fail
    floor_failed = {
        check["name"]
        for check in report["checks"]
        if check["status"] == "fail" and check["name"].endswith("_history_floor")
    }
    assert bool(floor_failed) is expect_floor_fail
    if expect_floor_fail:
        assert {"index_constituents_history_floor", "stock_status_history_floor"} <= floor_failed


def test_integrity_rejects_pre_floor_export_metadata(tmp_path, monkeypatch):
    db_path = tmp_path / "cn.duckdb"
    _create_floor_db(db_path, metadata_date="20050509", status_date="20050509")
    export_dir = tmp_path / "export"
    (export_dir / "stocks").mkdir(parents=True)
    (export_dir / "valuation").mkdir()
    (export_dir / "metadata").mkdir()
    # The export floor checks read only the date column from these files.
    pd.DataFrame({"date": pd.to_datetime(["2005-05-09"])}).to_parquet(
        export_dir / "stocks" / "000001.SZ.parquet", index=False
    )
    pd.DataFrame({"date": pd.to_datetime(["2005-05-09"])}).to_parquet(
        export_dir / "valuation" / "000001.SZ.parquet", index=False
    )
    pd.DataFrame({"symbol": ["000001.SZ"]}).to_parquet(
        export_dir / "metadata" / "stock_metadata.parquet", index=False
    )
    pd.DataFrame({"date": pd.to_datetime(["2005-05-09"])}).to_parquet(
        export_dir / "metadata" / "benchmark.parquet", index=False
    )
    pd.DataFrame({"date": ["20050430"]}).to_parquet(
        export_dir / "metadata" / "index_constituents.parquet", index=False
    )
    (export_dir / "manifest.json").write_text(
        json.dumps({"version": "2005-05-09", "date_range": {"end": "2005-05-09"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(integrity, "CN_HISTORY_START", "2005-05-09")

    report = integrity.check_integrity(
        str(db_path), target_date="2005-05-09", export_dir=str(export_dir)
    )

    failed = {check["name"] for check in report["checks"] if check["status"] == "fail"}
    assert "index_constituents_export_history_floor" in failed
