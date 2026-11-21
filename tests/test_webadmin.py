import json
import time
from unittest.mock import Mock

import pandas as pd
import pytest

from simtradedata.web.coverage import inspect_coverage
from simtradedata.web.jobs import JobManager
from simtradedata.web.plans import build_plan, validate_request
from simtradedata.writers.duckdb_writer import DuckDBWriter


def request(**kwargs):
    return {"dataset": "daily", "mode": "incremental", "start_date": "2026-11-18",
            "end_date": "2026-11-20", "symbols": [], **kwargs}


@pytest.mark.parametrize("change", [
    {"command": "echo bad"}, {"dataset": "shell"}, {"mode": "delete"},
    {"start_date": "bad"}, {"end_date": "2026-11-17"}, {"end_date": "2099-01-01"},
    {"symbols": ["../../file"]}, {"symbols": "600000.SS"},
    {"dataset": "pcf", "symbols": []}, {"dataset": "pcf", "symbols": ["510300.SS"]},
    {"dataset": "pcf", "symbols": ["159919.SZ"], "mode": "reparse"},
    {"dataset": "fundamentals", "symbols": ["600000.SS"]},
])
def test_update_request_rejects_unsafe_or_unsupported_intents(change):
    with pytest.raises(ValueError):
        validate_request(request(**change))


def test_empty_coverage_does_not_create_database(tmp_path):
    result = inspect_coverage(tmp_path, "2026-11-18", "2026-11-20")
    assert all(row["records"] == 0 for row in result["datasets"])
    assert not (tmp_path / "cn.duckdb").exists()
    assert not result["calendar_available"]


def test_coverage_counts_real_gaps_and_missing_values(tmp_path):
    writer = DuckDBWriter(str(tmp_path / "cn.duckdb"))
    writer.conn.execute("INSERT INTO trade_days VALUES ('2026-11-18'), ('2026-11-19'), ('2026-11-20')")
    writer.conn.execute("INSERT INTO stocks(symbol, date, open, close) VALUES ('600000.SS', '2026-11-19', 10, 11)")
    writer.close()
    result = inspect_coverage(tmp_path, "2026-11-18", "2026-11-20")
    daily = result["datasets"][0]
    assert daily["records"] == 1 and daily["securities"] == 1
    assert daily["missing_dates"] == ["2026-11-18", "2026-11-20"]
    assert {row["field"] for row in daily["null_fields"] if row["missing"] == 1} == {"high", "low", "volume", "money"}


def test_reparse_requires_existing_cache_and_pcf_requires_calendar(tmp_path):
    with pytest.raises(ValueError, match="缓存"):
        build_plan(tmp_path, request(mode="reparse"))
    with pytest.raises(ValueError, match="交易日历"):
        build_plan(tmp_path, request(dataset="pcf", symbols=["159919.SZ"]))


def test_quarter_plan_and_exact_cache_names(tmp_path):
    plan = build_plan(tmp_path, request(dataset="fundamentals", start_date="2026-01-01"))
    assert plan["units"] == ["2026-03-31", "2026-06-30", "2026-09-30"]
    assert plan["unit_count"] == 3
    with pytest.raises(ValueError, match="季度缓存"):
        build_plan(tmp_path, request(dataset="fundamentals", mode="reparse", start_date="2026-01-01"))


def test_dated_pcf_coverage_does_not_count_mislabeled_archive(tmp_path):
    folder = tmp_path / "etf_pcf" / "159919.SZ"
    folder.mkdir(parents=True)
    pd.DataFrame({"date": ["2026-11-19"], "etf_code": ["159919.SZ"]}).to_parquet(folder / "2026-11-20.parquet")
    item = inspect_coverage(tmp_path, "2026-11-18", "2026-11-20")["datasets"][2]
    assert item["records"] == 0
    assert len(item["invalid_archives"]) == 1


def test_real_cached_job_preserves_other_dates_and_rejects_reused_preview(tmp_path):
    from tests.ui_server import seed

    seed(tmp_path)
    pcf_path = tmp_path / "etf_pcf" / "159919.SZ" / "2026-11-20.parquet"
    pcf_before = pcf_path.read_bytes()
    manager = JobManager(tmp_path)
    preview = manager.preview(request(mode="reparse", symbols=["600000.SS"]))
    assert preview["existing_records"] == 1
    job = manager.start(preview["plan_id"])
    deadline = time.monotonic() + 30
    while manager.active and time.monotonic() < deadline:
        time.sleep(.05)
    finished = manager.list_jobs()[0]
    assert finished["status"] == "completed", finished["logs"]
    assert finished["result"]["written"] == 3
    assert finished["before"][0]["records"] == 1
    assert finished["after"][0]["records"] == 3
    assert pcf_path.read_bytes() == pcf_before
    import duckdb
    with duckdb.connect(str(tmp_path / "cn.duckdb"), read_only=True) as conn:
        assert conn.execute("SELECT close FROM stocks WHERE date='2026-11-17'").fetchone() == (5.5,)
        assert conn.execute("SELECT COUNT(*) FROM stocks WHERE date='2026-11-21'").fetchone() == (0,)
    assert inspect_coverage(tmp_path, "2026-11-18", "2026-11-20")["datasets"][2]["records"] == 301
    assert job["id"] == finished["id"]
    with pytest.raises(ValueError, match="预览"):
        manager.start(preview["plan_id"])
    assert json.loads((tmp_path / "ui-jobs" / "history.json").read_text())[0]["status"] == "completed"


def test_only_one_task_and_cancellation_targets_owned_process(tmp_path, monkeypatch):
    import simtradedata.web.jobs as jobs

    manager = JobManager(tmp_path)
    manager.active = "owned"
    manager.jobs = [{"id": "owned", "status": "running"}]
    manager.process = Mock(pid=1234)
    manager.process.poll.return_value = None
    killed = []
    monkeypatch.setattr(jobs.os, "killpg", lambda *args: killed.append(args))
    with pytest.raises(RuntimeError):
        manager.preview(request())
    with pytest.raises(ValueError):
        manager.cancel("unrelated")
    manager.cancel("owned")
    assert manager.jobs[0]["status"] == "cancelling"
    if jobs.os.name != "nt":
        assert killed[0][0] == 1234


def test_restart_marks_inflight_jobs_interrupted(tmp_path):
    directory = tmp_path / "ui-jobs"
    directory.mkdir()
    (directory / "history.json").write_text(json.dumps([{"id": "old", "status": "running"}]))
    assert JobManager(tmp_path).list_jobs()[0]["status"] == "interrupted"


def test_calendar_plan_and_operation_use_real_source_dates(tmp_path, monkeypatch):
    from simtradedata.web.operations import update_calendar
    import simtradedata.router as routers
    import duckdb

    plan = build_plan(tmp_path, request(dataset="calendar"))
    assert plan["unit_count"] == 1
    with pytest.raises(ValueError):
        validate_request(request(dataset="calendar", mode="refresh"))

    class Router:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get_trade_calendar(self, start, end):
            return pd.DataFrame({"calendar_date": ["2026-11-18", "2026-11-19", "2026-11-20"], "is_trading_day": ["1", "0", "1"]})

    monkeypatch.setattr(routers, "SmartRouter", Router)
    assert update_calendar(tmp_path, plan)["written"] == 2
    with duckdb.connect(str(tmp_path / "cn.duckdb"), read_only=True) as conn:
        assert [str(row[0]) for row in conn.execute("SELECT date FROM trade_days ORDER BY date").fetchall()] == ["2026-11-18", "2026-11-20"]
        assert conn.execute("SELECT COUNT(*) FROM stocks").fetchone() == (0,)


def test_financial_reparse_populates_new_fields_and_commits_quarter(tmp_path, monkeypatch):
    import numpy as np
    import duckdb
    from simtradedata.fetchers.mootdx_affair_fetcher import MootdxAffairFetcher
    from simtradedata.web.operations import update_fundamentals

    cache = tmp_path / "mootdx_financial"
    cache.mkdir()
    (cache / "gpcw20260930.zip").write_bytes(b"fixture")
    raw = pd.DataFrame(np.zeros((1, 659)), index=["600519"])
    raw.iloc[0, 0] = 260930
    raw.iloc[0, 40] = 100
    raw.iloc[0, 501] = 1.25
    monkeypatch.setattr(MootdxAffairFetcher, "parse_local", lambda *args: raw)
    plan = build_plan(tmp_path, request(dataset="fundamentals", mode="reparse", start_date="2026-07-01"))
    result = update_fundamentals(tmp_path, plan)
    assert result["failed_units"] == [] and result["written"] == 1
    with duckdb.connect(str(tmp_path / "cn.duckdb"), read_only=True) as conn:
        assert conn.execute("SELECT total_assets, diluted_eps FROM fundamentals WHERE symbol='600519.SS'").fetchone() == (100, 1.25)
        assert conn.execute("SELECT year, quarter, stock_count FROM fundamentals_progress").fetchone() == (2026, 3, 1)


def test_pcf_incremental_repairs_invalid_archive_then_skips_valid_one(tmp_path, monkeypatch):
    from tests.ui_server import seed
    from simtradedata.web.operations import update_pcf
    import simtradedata.fetchers.etf_pcf_fetcher as pcf

    seed(tmp_path)
    path = tmp_path / "etf_pcf" / "159919.SZ" / "2026-11-20.parquet"
    valid = pd.read_parquet(path)
    pd.DataFrame({"date": ["2026-11-20"], "etf_code": ["159919.SZ"]}).to_parquet(path)
    calls = []

    class Fetcher:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def fetch_pcf(self, symbol, day):
            calls.append((symbol, day))
            return valid

    monkeypatch.setattr(pcf, "EtfPcfFetcher", Fetcher)
    plan = build_plan(tmp_path, request(dataset="pcf", symbols=["159919.SZ"], start_date="2026-11-20"))
    assert update_pcf(tmp_path, plan)["written"] == 301
    result = update_pcf(tmp_path, plan)
    assert result["skipped"] == 1 and result["written"] == 0
    assert calls == [("159919.SZ", "2026-11-20")]
