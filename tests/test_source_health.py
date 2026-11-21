import hashlib
import threading
import time
from datetime import datetime, timedelta
from unittest.mock import Mock

import duckdb
import pytest

from simtradedata.web import source_health as health


def wait_done(service):
    deadline = time.monotonic() + 3
    while service.snapshot()['running'] and time.monotonic() < deadline:
        time.sleep(.005)
    assert not service.snapshot()['running']


def test_failure_threshold_recovery_and_no_history(tmp_path):
    fail = [True]
    def probe(_):
        if fail[0]:
            raise ValueError('empty response')
        return {'freshness': 'current', 'latest': '2026-11-20', 'detail': 'valid'}
    service = health.SourceHealth(tmp_path, probes={key: probe for key in health.NAMES})
    for count in range(1, 4):
        service.check()
        wait_done(service)
        assert all(row['failures'] == count for row in service.snapshot()['sources'])
        assert all(row['status'] == ('unhealthy' if count == 3 else 'retry') for row in service.snapshot()['sources'])
    fail[0] = False
    service.check()
    wait_done(service)
    assert all(row['failures'] == 0 and row['status'] == 'healthy' for row in service.snapshot()['sources'])
    assert len(service.snapshot()['sources']) == 3
    assert not list(tmp_path.iterdir())


def test_manual_checks_do_not_overlap(tmp_path):
    started, release = threading.Event(), threading.Event()
    calls = []
    def probe(_):
        calls.append(1)
        started.set()
        release.wait(2)
        return {'freshness': 'current'}
    service = health.SourceHealth(tmp_path, probes={'daily': probe})
    service.check()
    assert started.wait(1)
    service.check()
    assert len(calls) == 1
    release.set()
    wait_done(service)


def test_daily_scheduler_stops(tmp_path):
    calls = []
    service = health.SourceHealth(tmp_path, probes={'daily': lambda _: calls.append(1) or {'freshness': 'current'}})
    service.start(interval=.02)
    deadline = time.monotonic() + 2
    while len(calls) < 2 and time.monotonic() < deadline:
        time.sleep(.01)
    assert len(calls) >= 2
    service.stop()
    wait_done(service)
    count = len(calls)
    time.sleep(.04)
    assert len(calls) == count


def response(content, **headers):
    result = Mock()
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    result.status_code = 206
    result.headers = headers
    result.raw.read.return_value = content
    return result


def test_daily_probe_reads_only_zip_header(tmp_path, monkeypatch):
    result = response(b'PK\x03\x04', **{'Content-Range': 'bytes 0-3/100'})
    get = Mock(return_value=result)
    monkeypatch.setattr(health.requests, 'get', get)
    assert health.daily_probe(tmp_path)['freshness'] == 'update'
    result.raw.read.assert_called_once_with(4)
    assert get.call_args.kwargs['stream'] is True
    assert get.call_args.kwargs['headers']['Range'] == 'bytes=0-3'
    assert not list(tmp_path.iterdir())


def test_daily_html_response_is_not_healthy(tmp_path, monkeypatch):
    monkeypatch.setattr(health.requests, 'get', lambda *a, **k: response(b'<htm'))
    with pytest.raises(ValueError, match='ZIP'):
        health.daily_probe(tmp_path)


def test_financial_probe_detects_revision_even_when_size_matches(tmp_path, monkeypatch):
    folder = tmp_path / 'mootdx_financial'
    folder.mkdir()
    (folder / 'gpcw20260331.zip').write_bytes(b'old')
    reports = [{'filename': 'gpcw20260331.zip', 'filesize': 3, 'hash': hashlib.md5(b'new').hexdigest()}]
    monkeypatch.setattr(health, 'financial_reports', lambda: reports)
    assert health.financial_probe(tmp_path)['freshness'] == 'update'
    reports[0]['hash'] = hashlib.md5(b'old').hexdigest()
    assert health.financial_probe(tmp_path)['freshness'] == 'current'
    assert (folder / 'gpcw20260331.zip').read_bytes() == b'old'


@pytest.mark.parametrize('reports', [[], None, [{'filename': '../../bad', 'hash': '', 'filesize': 0}]])
def test_financial_invalid_directory_is_rejected(tmp_path, monkeypatch, reports):
    monkeypatch.setattr(health, 'financial_reports', lambda: reports)
    with pytest.raises(ValueError):
        health.financial_probe(tmp_path)


def test_financial_transport_has_overall_timeout(monkeypatch):
    run = Mock(return_value=Mock(stdout='[{"filename":"gpcw20260331.zip"}]'))
    monkeypatch.setattr(health.subprocess, 'run', run)
    assert health.financial_reports()[0]['filename'] == 'gpcw20260331.zip'
    assert run.call_args.kwargs['timeout'] == 25


def test_pcf_holiday_calendar_and_stale_sample(tmp_path, monkeypatch):
    import pandas as pd
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 21, 17, tzinfo=tz)
    monkeypatch.setattr(health, 'datetime', Clock)
    sample = ['2026-11-20']
    fetcher = Mock()
    fetcher.__enter__ = Mock(return_value=fetcher)
    fetcher.__exit__ = Mock(return_value=False)
    fetcher._download.side_effect = lambda *a: pd.DataFrame({'date': [sample[0]]})
    monkeypatch.setattr(health, 'EtfPcfFetcher', lambda **k: fetcher)
    with duckdb.connect(str(tmp_path / 'cn.duckdb')) as conn:
        conn.execute('CREATE TABLE exchange_calendar(date DATE PRIMARY KEY, is_trading_day BOOLEAN)')
        for offset in range(14):
            day = Clock(2026, 11, 21).date() - timedelta(days=offset)
            conn.execute('INSERT INTO exchange_calendar VALUES (?,?)', [day, day.weekday() < 5])
    assert health.pcf_probe(tmp_path)['freshness'] == 'update'  # Saturday is not stale.
    sample[0] = '2026-11-19'
    assert health.pcf_probe(tmp_path)['freshness'] == 'stale'
    sample[0] = '2026-11-22'
    with pytest.raises(ValueError, match='未来'):
        health.pcf_probe(tmp_path)


def test_financial_future_quarter_does_not_poison_directory(tmp_path, monkeypatch):
    digest = hashlib.md5(b'x').hexdigest()
    reports = [{'filename': f'gpcw{day}.zip', 'filesize': 1, 'hash': digest}
               for day in ['20260331', '20991231']]
    monkeypatch.setattr(health, 'financial_reports', lambda: reports)
    assert health.financial_probe(tmp_path)['latest'] == '2026-03-31'
