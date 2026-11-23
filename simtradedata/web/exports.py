"""Build the shared SimTradeLab/SimTradeDesk Parquet contract into an atomic local download."""

import json
import shutil
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq

from simtradedata.web.daily_categories import is_exchange_etf
from simtradedata.utils.paths import safe_rmtree


def artifact_path(data_dir, market):
    if market not in {'cn', 'us'}:
        raise ValueError('请选择 cn 或 us 市场')
    root = Path(data_dir) / 'export'
    path = root / f'simtradedata-{market}.tar.gz'
    if root.is_symlink() or path.is_symlink():
        raise ValueError('导出路径不能是符号链接')
    return path


def export_status(data_dir):
    rows = []
    for market in ['cn', 'us']:
        path = artifact_path(data_dir, market)
        artifact = None
        if path.is_file():
            stat = path.stat()
            artifact = {'bytes': stat.st_size, 'created_at': datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                        'download_url': f'/api/exports/{market}/download'}
        rows.append({'market': market, 'available': (Path(data_dir) / f'{market}.duckdb').is_file(), 'artifact': artifact})
    return {'markets': rows}


def cleanup_export(data_dir, market):
    path = artifact_path(data_dir, market)
    stage = path.parent / f'.simtradedata-{market}-staging'
    if stage.exists() and not stage.is_symlink():
        safe_rmtree(stage)
    path.with_suffix('.tmp').unlink(missing_ok=True)


def export_bundle(data_dir, market):
    from simtradedata.writers.duckdb_writer import DuckDBWriter
    from simtradedata.web.operations import emit
    from simtradedata.web.coverage import read_pcf_archive
    database = Path(data_dir) / f'{market}.duckdb'
    if not database.is_file():
        raise ValueError('所选市场尚无数据库，请先获取数据')
    output = artifact_path(data_dir, market)
    output.parent.mkdir(parents=True, exist_ok=True)
    if (output.parent / f'.simtradedata-{market}-staging').is_symlink():
        raise ValueError('导出路径不能是符号链接')
    cleanup_export(data_dir, market)
    stage = output.parent / f'.simtradedata-{market}-staging'
    temporary = output.with_suffix('.tmp')
    try:
        emit(stage='export', message='正在导出股票与ETF行情', completed=0, total=7)
        with DuckDBWriter(str(database)) as writer:
            where = writer._cn_stock_filter_sql('s.symbol') if market == 'cn' else 'TRUE'
            rows = writer.conn.execute(f'SELECT COUNT(*) FROM stocks s WHERE {where}').fetchone()[0]
            if not rows:
                raise ValueError('所选市场尚无可导出的行情')
            invalid = writer.conn.execute(f"""SELECT COUNT(*) FROM stocks s WHERE {where} AND
                (volume IS NULL OR NOT isfinite(volume) OR volume<0 OR
                 (volume>0 AND (open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL OR
                  NOT isfinite(open) OR NOT isfinite(high) OR NOT isfinite(low) OR NOT isfinite(close) OR
                  open<=0 OR high<greatest(open,close,low) OR low>least(open,close,high) OR low<=0)))""").fetchone()[0]
            if invalid:
                raise ValueError(f'行情中有 {invalid} 条异常记录，请先修补不完整数据')
            if market == 'cn':
                ensure_benchmark(writer, emit)
            labels = {'stocks': '行情导出完成', 'exrights': '除权除息导出完成',
                      'fundamentals': '财务导出完成', 'valuation': '估值导出完成', 'metadata': '元数据导出完成'}
            writer.export_to_parquet(str(stage), market=market,
                progress_callback=lambda name, step: emit(stage='export', message='正在导出股票与ETF行情' if name == 'stocks' and step < 1 else labels[name], completed=step, total=7))
        counts = {}
        for folder in ['stocks', 'exrights', 'fundamentals', 'valuation', 'metadata']:
            files = list((stage / folder).glob('*.parquet'))
            counts[folder] = len(files)
            for path in files:
                table = pq.ParquetFile(path)
                if folder == 'stocks' and not {'date','open','high','low','close','volume','money','preclose','high_limit','low_limit'} <= set(table.schema.names):
                    raise ValueError(f'{path.name}: 导出行情字段不符合格式规范')
        counts['etfs'] = sum(is_exchange_etf(path.stem) for path in (stage / 'stocks').glob('*.parquet'))
        counts['stocks'] -= counts['etfs']
        counts['etf_pcf'] = 0
        if market == 'cn':
            for path in (Path(data_dir) / 'etf_pcf').glob('*/*.parquet'):
                read_pcf_archive(path, path.parent.name, path.stem)
                target = stage / 'etf_pcf' / path.parent.name / path.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
                counts['etf_pcf'] += 1
        manifest_path = stage / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['files'] = counts
        manifest['description'] = f"SimTradeData export ({counts['stocks']} stocks, {counts['etfs']} ETFs)"
        manifest['coverage_note'] = 'Available validated records; missing dates without confirmed HALT are not synthesized. Coverage is not certified complete.'
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        emit(stage='export', message='格式校验完成，正在打包', completed=6, total=7)
        paths = sorted(stage.rglob('*'))
        with tarfile.open(temporary, 'w:gz', compresslevel=1) as archive:
            for index, path in enumerate(paths, 1):
                archive.add(path, arcname=str(path.relative_to(stage)), recursive=False)
                if index % 250 == 0 or index == len(paths):
                    emit(stage='export', message='正在打包数据文件', completed=6 + index / max(len(paths),1), total=7)
        temporary.replace(output)
        emit(stage='export', message='回测数据包已生成', completed=7, total=7)
        return {'market': market, 'files': counts, 'bytes': output.stat().st_size,
                'date_range': manifest['date_range'], 'download_url': f'/api/exports/{market}/download',
                'failed_units': []}
    finally:
        cleanup_export(data_dir, market)


def ensure_benchmark(writer, emit):
    """Reuse the authoritative index adapter for missing export prerequisites."""
    from simtradedata.router import SmartRouter
    from simtradedata.config.field_mappings import BENCHMARK_CONFIG, BENCHMARK_HISTORY_FLOOR
    import numpy as np
    import pandas as pd
    symbol = BENCHMARK_CONFIG['default_index']
    first, last = writer.conn.execute(
        f'SELECT MIN(date),MAX(date) FROM ({writer._benchmark_export_query()})'
    ).fetchone()
    target = writer.conn.execute(f"SELECT MAX(date) FROM stocks s WHERE {writer._cn_stock_filter_sql('s.symbol')}").fetchone()[0]
    if first and str(first) <= BENCHMARK_HISTORY_FLOOR and last >= target:
        return
    emit(stage='export', message='按需补齐导出所需的沪深300历史', completed=0, total=7)
    start = (pd.Timestamp(BENCHMARK_HISTORY_FLOOR) - pd.Timedelta(days=31)).strftime('%Y-%m-%d')
    with SmartRouter() as router:
        frame = router.get_index_data(symbol, start, str(target))
    if frame.empty or not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError('基准历史不可用，未生成数据包')
    values = frame[['open','high','low','close','volume','money']]
    if not np.isfinite(values).all().all() or (values[['open','high','low','close']] <= 0).any().any():
        raise ValueError('基准历史包含无效行情，未生成数据包')
    if str(frame.index.min().date()) > BENCHMARK_HISTORY_FLOOR or frame.index.max().date() < target or frame.index.has_duplicates:
        raise ValueError('基准历史覆盖不足，未生成数据包')
    writer.write_market_data(symbol, frame, insert_only=True)
