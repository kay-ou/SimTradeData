import io
import json
import tarfile
import threading
import time
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

import pandas as pd
import pytest

from simtradedata.web.exports import export_bundle, export_status
from simtradedata.web.jobs import JobManager
from simtradedata.web.server import handler_for
from simtradedata.writers.duckdb_writer import DuckDBWriter
from tests.ui_server import seed


def source(directory):
    seed(directory)
    with DuckDBWriter(str(directory / 'cn.duckdb')) as writer:
        frame = pd.DataFrame({'open':1.,'high':1.1,'low':.9,'close':1.05,'volume':1000,'money':1050.},
                             index=pd.to_datetime(['2026-11-17','2026-11-19']))
        writer.write_market_data('159919.SZ', frame)
        writer.write_benchmark(pd.DataFrame({'open':10.,'high':11.,'low':9.,'close':10.5,'volume':1000,'money':10500.},
                             index=pd.to_datetime(['2014-12-31','2026-11-17','2026-11-18','2026-11-19','2026-11-20'])))


def test_export_preserves_unknown_gaps_and_includes_etfs_pcf_and_manifest(tmp_path):
    source(tmp_path)
    result = export_bundle(tmp_path, 'cn')
    assert result['files']['stocks'] == 1 and result['files']['etfs'] == 1 and result['files']['etf_pcf'] == 1
    with tarfile.open(tmp_path / 'export/simtradedata-cn.tar.gz') as archive:
        assert {'stocks/600000.SS.parquet','stocks/159919.SZ.parquet','metadata/benchmark.parquet',
                'etf_pcf/159919.SZ/2026-11-20.parquet','manifest.json'} <= set(archive.getnames())
        frame = pd.read_parquet(io.BytesIO(archive.extractfile('stocks/159919.SZ.parquet').read()))
        assert frame['date'].dt.strftime('%Y-%m-%d').tolist() == ['2026-11-17','2026-11-19']
        assert frame['close'].tolist() == [1.05,1.05]
        manifest = json.load(archive.extractfile('manifest.json'))
        assert manifest['package_format'] == 'simtrade-data-market-v1'
    assert not list((tmp_path / 'export').glob('.*-staging'))


def test_failed_export_keeps_previous_download(tmp_path, monkeypatch):
    source(tmp_path)
    path = tmp_path / 'export/simtradedata-cn.tar.gz'
    export_bundle(tmp_path, 'cn')
    previous = path.read_bytes()
    monkeypatch.setattr(DuckDBWriter, '_export_stocks_batch', lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('failed')))
    with pytest.raises(ValueError, match='failed'):
        export_bundle(tmp_path, 'cn')
    assert path.read_bytes() == previous


def test_background_export_is_serialized_and_downloads_real_bundle(tmp_path, socket_enabled):
    source(tmp_path)
    manager = JobManager(tmp_path)
    manager.active = 'update'
    with pytest.raises(RuntimeError):
        manager.start_export({'market':'cn'})
    manager.active = None
    for body in [{'market':'../cn'}, {'market':'cn','output':'/tmp/other'}, {'market':'cn','command':'delete'}]:
        with pytest.raises(ValueError):
            manager.start_export(body)
    job = manager.start_export({'market':'cn'})
    deadline = time.monotonic() + 30
    while manager.active and time.monotonic() < deadline:
        time.sleep(.05)
    latest = manager.list_jobs()[0]
    assert latest['id'] == job['id'] and latest['status'] == 'completed', latest['logs']
    assert latest['result']['files']['etfs'] == 1
    server = ThreadingHTTPServer(('127.0.0.1',0), handler_for(manager))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f'http://127.0.0.1:{server.server_port}/api/exports/cn/download') as response:
            assert response.headers['Content-Disposition'] == 'attachment; filename="simtradedata-cn.tar.gz"'
            with tarfile.open(fileobj=io.BytesIO(response.read())) as archive:
                assert 'stocks/159919.SZ.parquet' in archive.getnames()
    finally:
        server.shutdown(); server.server_close(); thread.join()
    assert export_status(tmp_path)['markets'][0]['artifact']['bytes'] > 0


def test_export_status_without_database_does_not_create_one(tmp_path):
    assert all(not row['available'] for row in export_status(tmp_path)['markets'])
    assert not (tmp_path / 'cn.duckdb').exists()


def test_yahoo_missing_amount_stays_unknown_in_us_export(tmp_path, monkeypatch):
    from simtradedata.fetchers.yfinance_fetcher import YFinanceFetcher
    frame = pd.DataFrame({'Open':[10.], 'High':[11.], 'Low':[9.], 'Close':[10.5], 'Volume':[1000]},
                         index=pd.to_datetime(['2026-11-19']))
    monkeypatch.setattr('yfinance.download', lambda **kwargs: frame)
    rows, _ = YFinanceFetcher().fetch_batch_ohlcv(['AAPL.US'],'2026-11-19','2026-11-20')
    assert rows['AAPL.US']['money'].isna().all()
    with DuckDBWriter(str(tmp_path / 'us.duckdb')) as writer:
        writer.write_market_data('AAPL.US', rows['AAPL.US'])
    export_bundle(tmp_path, 'us')
    with tarfile.open(tmp_path / 'export/simtradedata-us.tar.gz') as archive:
        data = pd.read_parquet(io.BytesIO(archive.extractfile('stocks/AAPL.US.parquet').read()))
        assert data['money'].isna().all()


def test_export_merges_complete_benchmark_with_partial_stock_index_without_network(tmp_path, monkeypatch):
    source(tmp_path)
    monkeypatch.setattr('simtradedata.router.SmartRouter.get_index_data', lambda *args: pytest.fail('Complete local benchmark must avoid network'))
    with DuckDBWriter(str(tmp_path / 'cn.duckdb')) as writer:
        frame = pd.DataFrame({'open':20.,'high':21.,'low':19.,'close':20.5,'volume':1000,'money':20500.},
                             index=pd.to_datetime(['2026-11-20']))
        writer.write_market_data('000300.SS', frame)
    export_bundle(tmp_path, 'cn')
    with tarfile.open(tmp_path / 'export/simtradedata-cn.tar.gz') as archive:
        frame = pd.read_parquet(io.BytesIO(archive.extractfile('metadata/benchmark.parquet').read()))
        assert pd.to_datetime(frame['date']).dt.strftime('%Y-%m-%d').tolist() == ['2014-12-31','2026-11-17','2026-11-18','2026-11-19','2026-11-20']
        assert frame.iloc[-1]['close'] == 20.5
