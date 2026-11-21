"""Compare financial import paths using a local ZIP and temporary databases."""

import argparse
import json
import logging
import shutil
import tempfile
import time
from pathlib import Path

from simtradedata.fetchers.mootdx_affair_fetcher import MootdxAffairFetcher
from simtradedata.utils.code_utils import convert_to_ptrade_code
from simtradedata.writers.duckdb_writer import DuckDBWriter


def benchmark(package, companies=100):
    with tempfile.TemporaryDirectory(prefix='simtrade-financial-benchmark-') as temporary:
        root = Path(temporary)
        shutil.copyfile(package, root / package.name)
        fetcher = MootdxAffairFetcher(str(root))
        started = time.perf_counter()
        raw = fetcher.parse_local(package.name)
        parse = time.perf_counter() - started
        started = time.perf_counter()
        frame = fetcher._convert_to_ptrade_format(raw)
        conversion = time.perf_counter() - started
        frames = {convert_to_ptrade_code(str(code), 'qstock'): group.drop(columns='code').set_index('end_date')
                  for code, group in list(frame.groupby('code'))[:companies]}
        timings = {}
        with DuckDBWriter(str(root / 'benchmark.duckdb')) as writer:
            for mode in ['individual', 'batch']:
                writer.conn.execute('DELETE FROM fundamentals')
                writer.conn.execute('DELETE FROM data_change_log')
                writer.begin()
                started = time.perf_counter()
                if mode == 'individual':
                    rows = sum(writer.write_fundamentals(symbol, data) for symbol, data in frames.items())
                else:
                    rows = 0
                    items = list(frames.items())
                    for offset in range(0, len(items), 500):
                        rows += writer.write_fundamentals_batch(dict(items[offset:offset + 500]))
                writer.commit()
                timings[mode] = time.perf_counter() - started
                if mode == 'individual':
                    writer.conn.execute('CREATE TEMP TABLE baseline AS SELECT * FROM fundamentals')
                else:
                    difference = writer.conn.execute('SELECT COUNT(*) FROM ((SELECT * FROM baseline EXCEPT ALL SELECT * FROM fundamentals) UNION ALL (SELECT * FROM fundamentals EXCEPT ALL SELECT * FROM baseline))').fetchone()[0]
                    if difference:
                        raise AssertionError('Batch import differs from individual import')
        return {'package': package.name, 'companies': len(frames), 'rows': rows, 'parse_seconds': parse,
                'conversion_seconds': conversion, 'write_seconds': timings,
                'speedup': timings['individual'] / timings['batch'], 'data_equal': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    parser.add_argument('--companies', type=int, default=100)
    args = parser.parse_args()
    if args.companies <= 0:
        parser.error('--companies must be positive')
    logging.basicConfig(level=logging.ERROR)
    print(json.dumps(benchmark(args.package, args.companies), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
