"""Small read-only probes; retain only the latest source state in memory."""

import hashlib
import json
import re
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import requests

from simtradedata.fetchers.etf_pcf_fetcher import EtfPcfFetcher
from simtradedata.resilience.monitor import RequestMonitor

NAMES = {'daily': '通达信日线', 'fundamentals': '通达信季度财务', 'pcf': '深市 PCF（159919.SZ 样本）'}
DAILY_URL = 'https://data.tdx.com.cn/vipdoc/hsjday.zip'


def daily_probe(data_dir):
    # Read four bytes even when a server ignores Range; never consume the ZIP.
    with requests.get(DAILY_URL, headers={'Range': 'bytes=0-3'}, stream=True, timeout=(5, 10)) as response:
        response.raise_for_status()
        if response.raw.read(4) != b'PK\x03\x04':
            raise ValueError('日线地址返回的不是 ZIP 数据')
        modified = response.headers.get('Last-Modified')
        content_range = response.headers.get('Content-Range', '')
        size = content_range.rsplit('/', 1)[-1] if '/' in content_range else response.headers.get('Content-Length') if response.status_code == 200 else None
    local = Path(data_dir) / 'downloads' / 'hsjday.zip'
    changed = not local.exists()
    if local.exists():
        changed = bool(size and size.isdigit() and int(size) != local.stat().st_size)
        if modified:
            changed |= parsedate_to_datetime(modified).timestamp() > local.stat().st_mtime
    return {'freshness': 'update' if changed else 'unknown', 'latest': modified,
            'detail': '发现新历史包或本地尚无缓存。' if changed else 'ZIP 样本有效；包发布时间不能证明最新交易日覆盖。'}


def financial_reports():
    # Existing Mootdx directory transport has no overall deadline. Isolate it.
    command = 'import json; from mootdx.affair import Affair; print(json.dumps(Affair.files()))'
    result = subprocess.run([sys.executable, '-c', command], capture_output=True, text=True, timeout=25, check=True)
    return json.loads(result.stdout.strip().splitlines()[-1])


def financial_probe(data_dir):
    reports = financial_reports()
    if not isinstance(reports, list) or not reports:
        raise ValueError('财务目录为空或格式已改变')
    dates = []
    changed = 0
    for report in reports:
        match = re.fullmatch(r'gpcw(\d{8})\.zip', report.get('filename', ''))
        if not match or not re.fullmatch(r'[a-fA-F0-9]{32}', str(report.get('hash', ''))) or int(report.get('filesize', 0)) <= 0:
            raise ValueError('财务目录文件名、哈希或大小无效')
        day = datetime.strptime(match[1], '%Y%m%d').date()
        if (day.month, day.day) not in {(3, 31), (6, 30), (9, 30), (12, 31)}:
            raise ValueError('财务目录报告期无效')
        if day > datetime.now(ZoneInfo('Asia/Shanghai')).date():
            continue  # The official directory can list a future quarter early.
        dates.append(day.isoformat())
        local = Path(data_dir) / 'mootdx_financial' / report['filename']
        if local.exists():
            if local.stat().st_size != int(report['filesize']):
                changed += 1
            else:
                digest = hashlib.md5()
                with local.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(chunk)
                changed += digest.hexdigest().lower() != report['hash'].lower()
    if not dates:
        raise ValueError('财务目录没有已结束报告期')
    latest_file = Path(data_dir) / 'mootdx_financial' / ('gpcw' + max(dates).replace('-', '') + '.zip')
    pending = changed or not latest_file.exists()
    return {'freshness': 'update' if pending else 'current', 'latest': max(dates),
            'detail': f'目录有效；{changed} 个本地财务包与官方版本不同。' + ('最新已发布季度尚未缓存。' if not latest_file.exists() else '最新已发布季度已有缓存；未发布季度不算过期。')}


def pcf_probe(data_dir):
    today = datetime.now(ZoneInfo('Asia/Shanghai')).date()
    with EtfPcfFetcher(timeout=10) as fetcher:
        frame = fetcher._download('159919.SZ', None, today)
    if frame.empty:
        raise ValueError('PCF 样本为空')
    latest = frame.iloc[0]['date']
    if datetime.fromisoformat(latest).date() > today:
        raise ValueError('PCF 样本日期在未来')
    path = Path(data_dir) / 'etf_pcf' / '159919.SZ' / f'{latest}.parquet'
    cached = False
    if path.exists():
        from simtradedata.web.coverage import read_pcf_archive
        try:
            read_pcf_archive(path, '159919.SZ', latest)
            cached = True
        except ValueError:
            pass
    # Compare only to a real, completely covered local calendar window.
    target = None
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    end = today if now.hour >= 16 else today - timedelta(days=1)
    start = end - timedelta(days=13)
    database = Path(data_dir) / 'cn.duckdb'
    if database.exists():
        try:
            with duckdb.connect(str(database), read_only=True) as conn:
                count, last = conn.execute('SELECT COUNT(is_trading_day), MAX(CASE WHEN is_trading_day THEN date END) FROM exchange_calendar WHERE date BETWEEN ? AND ?', [start, end]).fetchone()
                if count == 14 and last:
                    target = str(last)
        except duckdb.Error:
            pass  # Active writer/missing calendar: freshness is unknown.
    freshness = 'stale' if target and latest < target else 'update' if not cached else 'current' if target else 'unknown'
    return {'freshness': freshness, 'latest': latest, 'detail': 'XML 日期、基金代码和篮子字段已校验。' + (f'最近已完成交易日：{target}。' if target else '交易日历缺失或数据库忙，暂不判断源是否过期。')}


class SourceHealth:
    def __init__(self, data_dir, probes=None):
        self.lock = threading.RLock()
        self.monitor = RequestMonitor()
        self.rows = {key: {'key': key, 'name': name, 'status': 'unchecked', 'failures': 0, 'checked_at': None} for key, name in NAMES.items()}
        self.running = False
        self.stop_event = threading.Event()
        self.scheduler = None
        self.worker = None
        probes = probes or {'daily': daily_probe, 'fundamentals': financial_probe, 'pcf': pcf_probe}
        for key, probe in probes.items():
            self.monitor.register_probe(key, lambda key=key, probe=probe: self._probe(key, probe, data_dir))

    def _probe(self, key, probe, data_dir):
        try:
            value = probe(data_dir)
            healthy, failures = True, 0
            status = 'healthy'
        except Exception as error:
            with self.lock:
                failures = self.rows[key]['failures'] + 1
            value = {'detail': str(error)[:300], 'freshness': 'unknown', 'latest': None}
            healthy, status = False, 'unhealthy' if failures >= 3 else 'retry'
        with self.lock:
            self.rows[key] = {'key': key, 'name': NAMES[key], **value, 'status': status, 'failures': failures,
                              'checked_at': datetime.now(timezone.utc).isoformat()}
        return healthy

    def snapshot(self):
        with self.lock:
            return {'running': self.running, 'interval_hours': 24, 'sources': [dict(row) for row in self.rows.values()]}

    def check(self):
        with self.lock:
            if self.running or self.stop_event.is_set():
                return self.snapshot()
            self.running = True
            def run():
                try:
                    self.monitor.probe_all()
                finally:
                    with self.lock:
                        self.running = False
            self.worker = threading.Thread(target=run, daemon=True)
            self.worker.start()
            return self.snapshot()

    def start(self, interval=86400):
        if self.scheduler is not None:
            return
        def run():
            while not self.stop_event.is_set():
                self.check()
                self.stop_event.wait(interval)
        self.scheduler = threading.Thread(target=run, daemon=True)
        self.scheduler.start()

    def stop(self):
        self.stop_event.set()
        if self.scheduler:
            self.scheduler.join(timeout=1)
