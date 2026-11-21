import duckdb
import pandas as pd
import pytest

from simtradedata.fetchers.unified_fetcher import UnifiedDataFetcher
from simtradedata.web.daily_repair import repair_daily
from simtradedata.web.plans import build_plan, validate_request
from tests.ui_server import seed
from tests.test_webadmin import request


def broken_data(tmp_path):
    seed(tmp_path)
    with duckdb.connect(str(tmp_path / 'cn.duckdb')) as conn:
        conn.execute("UPDATE stocks SET volume=NULL,money=NULL,high_limit=15 WHERE date='2026-11-19'")
    return build_plan(tmp_path, request(mode='repair'))


def test_repair_uses_cache_and_preserves_other_rows_and_fields(tmp_path, monkeypatch):
    plan = broken_data(tmp_path)
    monkeypatch.setattr(UnifiedDataFetcher, 'login', lambda *a: pytest.fail('Cache must avoid network'))
    assert plan['units'] == ['600000.SS']
    result = repair_daily(tmp_path, plan)
    assert result['written'] == 1 and result['unresolved'] == 0
    assert result['source_counts'] == {'cache': 1, 'baostock': 0}
    with duckdb.connect(str(tmp_path / 'cn.duckdb'), read_only=True) as conn:
        assert conn.execute("SELECT volume,money,high_limit FROM stocks WHERE date='2026-11-19'").fetchone() == (1000, 10500., 15.)
        assert conn.execute("SELECT close FROM stocks WHERE date='2026-11-17'").fetchone() == (5.5,)
        assert conn.execute('SELECT COUNT(*) FROM stocks').fetchone() == (2,)
    assert not build_plan(tmp_path, request(mode='repair'))['work_pending']


@pytest.mark.parametrize('status,expected', [(0, 1), (1, 0), (None, 0)])
@pytest.mark.parametrize('trades', [None, 0])
def test_backup_zero_trades_require_explicit_suspension(tmp_path, monkeypatch, status, expected, trades):
    plan = broken_data(tmp_path)
    (tmp_path / 'downloads' / 'hsjday.zip').unlink()
    calls = []
    monkeypatch.setattr(UnifiedDataFetcher, 'login', lambda *a: None)
    monkeypatch.setattr(UnifiedDataFetcher, 'logout', lambda *a: None)
    def fetch(self, symbol, start, end, adjustflag):
        calls.append((symbol, start, end, adjustflag))
        row = {'date': ['2026-11-19'], 'open': [10.], 'high': [11.], 'low': [9.], 'close': [10.5], 'volume': [trades], 'amount': [trades]}
        if status is not None:
            row['tradestatus'] = [status]
        return pd.DataFrame(row)
    monkeypatch.setattr(UnifiedDataFetcher, 'fetch_unified_daily_data', fetch)
    result = repair_daily(tmp_path, plan)
    assert result['written'] == expected
    assert result['unresolved'] == 1 - expected
    assert calls == [('600000.SS', '2026-11-19', '2026-11-19', '3')]
    with duckdb.connect(str(tmp_path / 'cn.duckdb'), read_only=True) as conn:
        assert conn.execute("SELECT volume,money FROM stocks WHERE date='2026-11-19'").fetchone() == ((0, 0.) if expected else (None, None))


def test_backup_other_dates_or_different_prices_do_not_overwrite(tmp_path, monkeypatch):
    plan = broken_data(tmp_path)
    (tmp_path / 'downloads' / 'hsjday.zip').unlink()
    monkeypatch.setattr(UnifiedDataFetcher, 'login', lambda *a: None)
    monkeypatch.setattr(UnifiedDataFetcher, 'logout', lambda *a: None)
    monkeypatch.setattr(UnifiedDataFetcher, 'fetch_unified_daily_data', lambda *a, **k: pd.DataFrame({
        'date': ['2026-11-19', '2026-11-20'], 'open': [20., 10.], 'high': [22., 11.], 'low': [18., 9.], 'close': [21., 10.5], 'volume': [1000, 1000], 'amount': [21000., 10500.]}))
    result = repair_daily(tmp_path, plan)
    assert result['written'] == 0 and result['unresolved'] == 1
    assert len(result['failed_units']) == 1
    with duckdb.connect(str(tmp_path / 'cn.duckdb'), read_only=True) as conn:
        assert conn.execute('SELECT COUNT(*) FROM stocks').fetchone() == (2,)
        assert conn.execute("SELECT close,money,high_limit FROM stocks WHERE date='2026-11-19'").fetchone() == (10.5, None, 15.)


def test_repair_mode_is_limited_to_daily():
    for dataset in ['fundamentals', 'pcf', 'calendar']:
        with pytest.raises(ValueError, match='仅支持日线'):
            validate_request(request(mode='repair', dataset=dataset))


def test_fractional_share_quantity_is_not_accepted_as_valid_source():
    from simtradedata.web.daily_repair import source_rows
    frame = pd.DataFrame({'date': ['2026-11-19'], 'open': [10.], 'high': [11.], 'low': [9.], 'close': [10.5], 'volume': [1.5], 'money': [15.75]})
    assert source_rows(frame) == {}


@pytest.mark.parametrize('price,expected', [(10., True), (20., False)])
def test_inconsistent_bounds_preserve_open_and_close(price, expected):
    from simtradedata.web.daily_repair import merge_repair, source_rows
    existing = {'open': 10., 'close': 10., 'high': 9., 'low': 11., 'volume': 1000, 'money': 10000.}
    candidate = {**existing, 'open': price, 'close': price, 'high': price + 1, 'low': price - 1}
    assert source_rows(pd.DataFrame([{**existing, 'date': '2026-11-19'}])) == {}
    result = merge_repair(existing, candidate)
    if expected:
        assert result == {**existing, 'high': 11., 'low': 9.}
    else:
        assert result is None


def test_fatal_commit_keeps_original_error_without_attempting_rollback(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from simtradedata.writers.duckdb_writer import DuckDBWriter
    plan = broken_data(tmp_path)
    rollback = Mock()
    monkeypatch.setattr(DuckDBWriter, 'rollback', rollback)
    monkeypatch.setattr(DuckDBWriter, 'commit', Mock(side_effect=duckdb.FatalException('original index failure')))
    with pytest.raises(duckdb.FatalException, match='original index failure'):
        repair_daily(tmp_path, plan)
    rollback.assert_not_called()
