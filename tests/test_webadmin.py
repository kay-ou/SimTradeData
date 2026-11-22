import json
import time
from unittest.mock import Mock

import pandas as pd
import pytest

from simtradedata.web.coverage import inspect_coverage
from simtradedata.web.jobs import JobManager
from simtradedata.web.plans import build_plan, validate_request
from simtradedata.writers.duckdb_writer import DuckDBWriter


@pytest.fixture(autouse=True)
def offline_etf_directory(monkeypatch):
    monkeypatch.setattr('simtradedata.web.etf_catalogue.load_etfs', lambda: ([], []))
    monkeypatch.setattr('simtradedata.web.etf_history.fetch_etf_daily_tencent', lambda *args: pd.DataFrame())
    monkeypatch.setattr('simtradedata.web.etf_history.fetch_etf_daily_fallback', lambda *args: pd.DataFrame())


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
    assert not (tmp_path / "ui-jobs" / "history.json").exists()


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


def test_history_is_not_loaded_and_old_files_are_removed(tmp_path):
    directory = tmp_path / "ui-jobs"
    directory.mkdir()
    (directory / "history.json").write_text(json.dumps([{"id": "old", "status": "running"}]))
    assert JobManager(tmp_path).list_jobs() == []
    assert not (directory / "history.json").exists()


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
    assert result["timings"]["download"] == 0
    assert result["timings"]["parse"] > 0 and result["timings"]["write"] > 0
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


def test_smart_daily_reuses_cache_without_network_and_never_replaces_rows(tmp_path, monkeypatch):
    from tests.ui_server import seed
    from scripts import download_tdx_day
    from simtradedata.web.operations import update_daily

    seed(tmp_path)
    monkeypatch.setattr(download_tdx_day, "get_remote_file_info", lambda *args: pytest.fail("Valid cache must avoid network"))
    monkeypatch.setattr(download_tdx_day, "download_file", lambda *args, **kwargs: pytest.fail("Must not redownload"))
    plan = build_plan(tmp_path, request(mode="auto"))
    assert update_daily(tmp_path, plan)["written"] == 2
    assert update_daily(tmp_path, plan)["written"] == 0
    assert not (tmp_path / "downloads" / "hsjday.inventory.json").exists()
    assert build_plan(tmp_path, request(mode="auto"))["work_pending"] is False


def test_smart_financial_reparses_old_cache_once_then_skips_it(tmp_path, monkeypatch):
    import numpy as np
    from simtradedata.fetchers.mootdx_affair_fetcher import MootdxAffairFetcher
    from simtradedata.web.operations import update_fundamentals

    folder = tmp_path / "mootdx_financial"
    folder.mkdir()
    (folder / "gpcw20260930.zip").write_bytes(b"fixture")
    raw = pd.DataFrame(np.zeros((1, 659)), index=["600519"])
    raw.iloc[0, 0] = 260930
    raw.iloc[0, 40] = 100
    calls = []
    def parse(*args):
        calls.append(True)
        return raw
    monkeypatch.setattr(MootdxAffairFetcher, "parse_local", parse)
    monkeypatch.setattr(MootdxAffairFetcher, "list_available_reports", lambda *args: pytest.fail("Cached financial package must avoid network"))
    plan = build_plan(tmp_path, request(dataset="fundamentals", mode="auto", start_date="2026-07-01"))
    assert update_fundamentals(tmp_path, plan)["written"] == 1
    second = update_fundamentals(tmp_path, plan)
    assert second["written"] == 0 and second["skipped"] == 1
    assert len(calls) == 1
    assert build_plan(tmp_path, request(dataset="fundamentals", mode="auto", start_date="2026-07-01"))["work_pending"] is False
    assert not (folder / "imports.json").exists()


def test_noop_does_not_launch_process_or_create_task(tmp_path, monkeypatch):
    from tests.ui_server import seed
    from simtradedata.web.operations import update_daily
    import simtradedata.web.jobs as jobs

    seed(tmp_path)
    update_daily(tmp_path, build_plan(tmp_path, request(mode="auto")))
    manager = JobManager(tmp_path)
    preview = manager.preview(request(mode="auto"))
    assert preview["work_pending"] is False
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("No-op must not start process"))
    assert manager.start(preview["plan_id"])["status"] == "up_to_date"
    assert manager.list_jobs() == []


def test_completion_is_invalidated_by_missing_database_rows(tmp_path):
    import duckdb
    from tests.ui_server import seed
    from simtradedata.web.operations import update_daily

    seed(tmp_path)
    update_daily(tmp_path, build_plan(tmp_path, request(mode="auto")))
    assert build_plan(tmp_path, request(mode="auto"))["work_pending"] is False
    with duckdb.connect(str(tmp_path / "cn.duckdb")) as conn:
        conn.execute("DELETE FROM stocks WHERE date='2026-11-20'")
    assert build_plan(tmp_path, request(mode="auto"))["work_pending"] is True
    assert not (tmp_path / "downloads" / "completed_scopes.json").exists()


@pytest.mark.parametrize("money", [None, float("inf"), float("-inf"), float("nan")])
def test_smart_daily_repairs_missing_values_from_cache_without_losing_other_fields(tmp_path, monkeypatch, money):
    import duckdb
    from tests.ui_server import seed
    from scripts import download_tdx_day
    from simtradedata.web.operations import update_daily

    seed(tmp_path)
    with duckdb.connect(str(tmp_path / "cn.duckdb")) as conn:
        conn.execute("UPDATE stocks SET money=?, high_limit=15 WHERE date='2026-11-19'", [money])
    monkeypatch.setattr(download_tdx_day, "get_remote_file_info", lambda *args: pytest.fail("Must use local cache"))
    plan = build_plan(tmp_path, request(mode="auto"))
    assert update_daily(tmp_path, plan)["written"] == 3
    with duckdb.connect(str(tmp_path / "cn.duckdb"), read_only=True) as conn:
        assert conn.execute("SELECT money, high_limit FROM stocks WHERE date='2026-11-19'").fetchone() == (10500, 15)
    assert build_plan(tmp_path, request(mode="auto"))["work_pending"] is False


@pytest.mark.parametrize("damage", ["invalid", "truncated"])
def test_recommendation_recovers_from_damaged_daily_cache(tmp_path, monkeypatch, damage):
    from tests.ui_server import seed
    from scripts import download_tdx_day
    from simtradedata.web.recommendations import recommend
    from simtradedata.web.operations import update_daily

    seed(tmp_path)
    cache = tmp_path / "downloads" / "hsjday.zip"
    valid = cache.read_bytes()
    cache.write_bytes(b"invalid zip" if damage == "invalid" else valid[:40])
    result = recommend(tmp_path, "daily")
    assert result["work_pending"] is True
    assert result["request"]["start_date"] == "2026-11-18"
    monkeypatch.setattr(download_tdx_day, "get_remote_file_info", lambda *args: {})
    def download(url, destination, **kwargs):
        destination.write_bytes(valid)
        return True
    monkeypatch.setattr(download_tdx_day, "download_file", download)
    assert update_daily(tmp_path, build_plan(tmp_path, result["request"]))["written"] == 2


def test_recommendation_detects_gaps_from_actual_rows(tmp_path, monkeypatch):
    from datetime import date
    from tests.ui_server import seed
    from simtradedata.web import recommendations
    import duckdb

    class Today(date):
        @classmethod
        def today(cls):
            return cls(2026, 11, 21)

    monkeypatch.setattr(recommendations, 'date', Today)
    seed(tmp_path)
    result = recommendations.recommend(tmp_path, 'daily')
    assert result['request']['start_date'] == '2026-11-18'
    assert result['request']['end_date'] == '2026-11-20'
    assert result['scope_start'] == '2026-11-17'
    assert result['work_pending']
    with duckdb.connect(str(tmp_path / 'cn.duckdb')) as conn:
        conn.execute("INSERT INTO stocks(symbol,date,open,high,low,close,volume,money) SELECT symbol, DATE '2026-11-18', open, high, low, close, volume, money FROM stocks WHERE date='2026-11-19'")
        conn.execute("INSERT INTO stocks(symbol,date,open,high,low,close,volume,money) SELECT symbol, DATE '2026-11-20', open, high, low, close, volume, money FROM stocks WHERE date='2026-11-19'")
    assert not recommendations.recommend(tmp_path, 'daily')['work_pending']
    with duckdb.connect(str(tmp_path / 'cn.duckdb')) as conn:
        conn.execute("DELETE FROM stocks WHERE date='2026-11-19'")
    result = recommendations.recommend(tmp_path, 'daily')
    assert result['request']['start_date'] == result['request']['end_date'] == '2026-11-19'
    assert result['work_pending']


def test_recommendation_validates_symbols_before_access(tmp_path):
    from simtradedata.web.recommendations import recommend
    with pytest.raises(ValueError):
        recommend(tmp_path, 'pcf', ['../../file'])
    assert not list(tmp_path.iterdir())


def test_financial_failure_after_first_batch_rolls_back_whole_quarter(tmp_path, monkeypatch):
    import duckdb
    from simtradedata.fetchers.mootdx_affair_fetcher import MootdxAffairFetcher
    from simtradedata.web.operations import update_fundamentals
    cache = tmp_path / 'mootdx_financial'
    cache.mkdir()
    (cache / 'gpcw20260930.zip').write_bytes(b'fixture')
    frame = pd.DataFrame({'code': [f'{i:06d}' for i in range(1, 502)],
                          'end_date': pd.to_datetime(['2026-09-30'] * 501), 'total_assets': [100.] * 501})
    monkeypatch.setattr(MootdxAffairFetcher, 'parse_local', lambda *a: frame)
    monkeypatch.setattr(MootdxAffairFetcher, '_convert_to_ptrade_format', lambda self, raw: raw)
    original = DuckDBWriter.write_fundamentals_batch
    calls = []
    def write(self, frames):
        calls.append(len(frames))
        if len(calls) == 2:
            assert self.conn.execute('SELECT COUNT(*) FROM fundamentals').fetchone() == (500,)
            raise RuntimeError('second batch failed')
        return original(self, frames)
    monkeypatch.setattr(DuckDBWriter, 'write_fundamentals_batch', write)
    plan = build_plan(tmp_path, request(dataset='fundamentals', mode='reparse', start_date='2026-07-01'))
    result = update_fundamentals(tmp_path, plan)
    assert calls == [500, 1]
    assert result['written'] == 0 and len(result['failed_units']) == 1
    with duckdb.connect(str(tmp_path / 'cn.duckdb'), read_only=True) as conn:
        assert conn.execute('SELECT COUNT(*) FROM fundamentals').fetchone() == (0,)
        assert conn.execute('SELECT COUNT(*) FROM fundamentals_progress').fetchone() == (0,)
        assert conn.execute('SELECT COUNT(*) FROM data_change_log').fetchone() == (0,)


def test_daily_recommendation_scans_source_only_once(tmp_path, monkeypatch):
    from tests.ui_server import seed
    from simtradedata.web import recommendations
    seed(tmp_path)
    calls = []
    def pending(directory, request, coverage, gap):
        calls.append(True)
        gap.update(start_date='2026-11-18', end_date='2026-11-20')
        return True
    monkeypatch.setattr(recommendations, 'daily_database_pending', pending)
    monkeypatch.setattr(recommendations, 'build_plan', lambda *a: pytest.fail('must not repeat ZIP check'))
    result = recommendations.recommend(tmp_path, 'daily')
    assert calls == [True]
    assert result['existing_records'] == 1
    assert result['work_pending']


def test_default_daily_checks_never_open_zip(tmp_path, monkeypatch):
    from tests.ui_server import seed
    from simtradedata.web import completeness
    from simtradedata.web.recommendations import recommend
    seed(tmp_path)
    monkeypatch.setattr(completeness.zipfile, 'ZipFile', lambda *a, **k: pytest.fail('DB check must not open ZIP'))
    assert recommend(tmp_path, 'daily')['work_pending']
    assert build_plan(tmp_path, request(mode='auto'))['work_pending']


def test_financial_summary_distinguishes_cache_reuse_and_download(tmp_path):
    folder = tmp_path / 'mootdx_financial'
    folder.mkdir()
    for day in ['20260331', '20260630']:
        (folder / f'gpcw{day}.zip').write_bytes(b'fixture')
    body = request(dataset='fundamentals', mode='auto', start_date='2026-01-01', end_date='2026-09-30')
    summary = build_plan(tmp_path, body)['update_summary']
    assert summary == {'quality_issues': 0, 'pending_units': 3, 'reuse_units': 2, 'download_units': 1, 'unit_label': '季度'}
    assert build_plan(tmp_path, {**body, 'mode': 'refresh'})['update_summary']['download_units'] == 3


def test_job_retains_overall_progress_separately_from_current_company_progress(tmp_path):
    from simtradedata.web.operations import EVENT_PREFIX
    manager = JobManager(tmp_path)
    job = {'status': 'running', 'stage': 'write', 'current_unit': '2019-12-31', 'downloaded_bytes': 100, 'logs': [], 'request': request(), 'after': None}
    event = {'stage': 'write', 'overall_completed': 18, 'overall_total': 47,
             'current_unit': '2020-03-31', 'completed': 500, 'total': 5500}
    process = Mock()
    process.stdout = [EVENT_PREFIX + json.dumps(event)]
    process.wait.return_value = 0
    temporary = tmp_path / 'request.json'
    temporary.write_text('{}')
    manager._watch(job, process, temporary)
    assert job['overall_completed'] == 18 and job['overall_total'] == 47
    assert job['completed'] == 500 and job['total'] == 5500
    assert job['current_unit'] == '2020-03-31'
    assert 'downloaded_bytes' not in job


def test_quality_warning_does_not_repeat_completed_daily_download(tmp_path):
    import duckdb
    from tests.ui_server import seed
    from simtradedata.web.operations import update_daily
    from simtradedata.web.recommendations import recommend
    seed(tmp_path)
    update_daily(tmp_path, build_plan(tmp_path, request(mode='auto')))
    with duckdb.connect(str(tmp_path / 'cn.duckdb')) as conn:
        conn.execute("UPDATE stocks SET volume=NULL,money=NULL WHERE date='2026-11-19'")
    plan = build_plan(tmp_path, request(mode='auto'))
    assert not plan['work_pending']
    assert plan['update_summary']['quality_issues'] == 1
    recommended = recommend(tmp_path, 'daily')
    assert not recommended['work_pending']
    assert recommended['update_summary']['quality_issues'] == 1
    manager = JobManager(tmp_path)
    assert manager.start(manager.preview(request(mode='auto'))['plan_id'])['status'] == 'up_to_date'
    assert not manager.list_jobs()


def test_daily_overview_and_plan_do_not_scan_pcf_archives(tmp_path, monkeypatch):
    from tests.ui_server import seed
    from simtradedata.web import coverage
    seed(tmp_path)
    monkeypatch.setattr(coverage, 'read_pcf_archive', lambda *args: pytest.fail('Daily checks must not read PCF'))
    result = coverage.inspect_coverage(tmp_path, '2026-11-18', '2026-11-20', include_pcf=False)
    assert [row['key'] for row in result['datasets']] == ['daily', 'fundamentals']
    assert result['datasets'][0]['records'] == 1
    assert len(result['calendar_dates']) == 3
    assert build_plan(tmp_path, request(mode='auto'))['work_pending']


@pytest.mark.parametrize("scope", ["selected", "all"])
def test_daily_supplements_dates_missing_inside_etf_archive(tmp_path, monkeypatch, scope):
    import struct
    import zipfile
    import duckdb
    from tests.ui_server import seed
    from simtradedata.web.operations import update_daily

    seed(tmp_path)
    with zipfile.ZipFile(tmp_path / "downloads" / "hsjday.zip", "a") as archive:
        archive.writestr("vipdoc/sz/lday/sz159919.day", b"".join(
            struct.pack("<IIIIIfII", day, 1000, 1100, 900, 1050, 1050., 1000, 0)
            for day in [20261118, 20261120]))
    calls = []

    class Fetcher:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def fetch_daily_bars(self, symbol, start, end):
            calls.append((symbol, start, end))
            return pd.DataFrame({"date": [start], "open": [1.], "high": [1.1],
                                 "low": [.9], "close": [1.05], "volume": [1000], "amount": [1050.]})

    monkeypatch.setattr('simtradedata.fetchers.mootdx_fetcher.MootdxFetcher', Fetcher)
    monkeypatch.setattr('simtradedata.web.etf_listing.fetch_etf_listing_info', lambda symbol: None)
    symbols = ['159919.SZ'] if scope == 'selected' else []
    plan = build_plan(tmp_path, request(mode='auto', symbols=symbols))
    result = update_daily(tmp_path, plan)
    assert result['failed_units'] == []
    assert calls == [('159919.SZ', '2026-11-19', '2026-11-19')]
    with duckdb.connect(str(tmp_path / 'cn.duckdb'), read_only=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM stocks WHERE symbol='159919.SZ'").fetchone()[0] == 3
    update_daily(tmp_path, plan)
    assert len(calls) == 1


@pytest.mark.parametrize('damage', ['zero_price', 'missing_field', 'invalid_date'])
def test_invalid_remote_etf_does_not_abort_subsequent_security(tmp_path, monkeypatch, damage):
    import duckdb
    from tests.ui_server import seed
    from simtradedata.web.operations import update_daily

    seed(tmp_path)
    calls = []

    class Fetcher:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def fetch_daily_bars(self, symbol, start, end):
            calls.append(symbol)
            frame = pd.DataFrame({'date': pd.date_range(start, end), 'open': 1., 'high': 1.1,
                                  'low': .9, 'close': 1.05, 'volume': 1000, 'amount': 1050.})
            if symbol == '159919.SZ':
                if damage == 'zero_price':
                    frame['open'] = 0
                elif damage == 'missing_field':
                    frame = frame.drop(columns='high')
                else:
                    frame['date'] = 'invalid'
            return frame

    monkeypatch.setattr('simtradedata.fetchers.mootdx_fetcher.MootdxFetcher', Fetcher)
    monkeypatch.setattr('simtradedata.web.etf_listing.fetch_etf_listing_info', lambda symbol: None)
    plan = build_plan(tmp_path, request(mode='auto', symbols=['159919.SZ', '159920.SZ']))
    result = update_daily(tmp_path, plan)
    assert calls == ['159919.SZ', '159920.SZ']
    assert [failure['unit'] for failure in result['failed_units']] == ['159919.SZ']
    with duckdb.connect(str(tmp_path / 'cn.duckdb'), read_only=True) as conn:
        assert conn.execute("SELECT DISTINCT symbol FROM stocks WHERE symbol LIKE '159%'").fetchall() == [('159920.SZ',)]


def test_all_market_etf_supplement_does_not_treat_lof_as_etf(tmp_path, monkeypatch):
    import struct
    import zipfile
    from tests.ui_server import seed
    from simtradedata.web.operations import update_daily
    seed(tmp_path)
    with zipfile.ZipFile(tmp_path/'downloads'/'hsjday.zip','a') as archive:
        archive.writestr('vipdoc/sz/lday/sz160323.day', b''.join(
            struct.pack('<IIIIIfII', day,1000,1100,900,1050,1050.,1000,0)
            for day in [20261118,20261120]))
    monkeypatch.setattr('simtradedata.fetchers.mootdx_fetcher.MootdxFetcher',lambda *a,**k:pytest.fail('LOF must not enter automatic ETF supplementation'))
    result=update_daily(tmp_path,build_plan(tmp_path,request(mode='auto')))
    assert result['failed_units']==[]


def test_plan_augmentation_receives_normalized_optional_symbols(tmp_path):
    manager = JobManager(tmp_path)
    manager.etf_catalogue = Mock()
    manager.etf_catalogue.get.return_value = {'running':True,'error':None,'warnings':[]}
    body = request(mode='auto')
    del body['symbols']
    plan = manager.preview(body)
    assert plan['request']['symbols'] == []
    assert plan['checking_etfs'] is True


@pytest.mark.parametrize('mode',['auto','incremental'])
def test_missing_etf_with_incomplete_calendar_is_not_marked_complete(tmp_path,monkeypatch,mode):
    from tests.ui_server import seed
    from simtradedata.web.operations import update_daily
    import duckdb
    seed(tmp_path)
    with duckdb.connect(str(tmp_path/'cn.duckdb')) as conn:
        conn.execute('DELETE FROM trade_days')
        conn.execute('DELETE FROM exchange_calendar')
    monkeypatch.setattr('simtradedata.web.operations.update_calendar',Mock(side_effect=ConnectionError('calendar unavailable')))
    monkeypatch.setattr('scripts.download_tdx_day.get_remote_file_info',lambda *a,**k:{})
    monkeypatch.setattr('scripts.download_tdx_day.download_file',lambda *a,**k:True)
    monkeypatch.setattr('simtradedata.fetchers.mootdx_fetcher.MootdxFetcher',lambda *a,**k:pytest.fail('calendar unknown; must report unresolved coverage'))
    result=update_daily(tmp_path,build_plan(tmp_path,request(mode=mode,symbols=['159919.SZ'])))
    assert result['written']==0 and result['skipped']==0
    assert result['failed_units']
    assert result['failed_units'][0]['unit']=='159919.SZ'


def test_etf_plan_cannot_claim_complete_when_calendar_is_incomplete(tmp_path):
    import duckdb
    from tests.ui_server import seed
    seed(tmp_path)
    with duckdb.connect(str(tmp_path/'cn.duckdb')) as conn:
        conn.execute("DELETE FROM exchange_calendar WHERE date='2026-11-18'")
    manager=JobManager(tmp_path)
    manager.etf_catalogue=Mock()
    manager.etf_catalogue.get.return_value={'running':False,'error':None,'warnings':[],
        'etfs':[{'symbol':'159919.SZ'}]}
    result=manager._include_etfs(request(mode='auto'),{'work_pending':False,'status':'up_to_date'})
    assert result['work_pending'] and result['needs_calendar'] and result['status']=='ready'
