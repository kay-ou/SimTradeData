"""Source import fingerprints kept in the existing DuckDB quarter table."""

import hashlib
import inspect
import json

from simtradedata.config.mootdx_finvalue_map import FINVALUE_TO_PTRADE
from simtradedata.fetchers.mootdx_affair_fetcher import MootdxAffairFetcher


def file_signature(path):
    stat = path.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def financial_signature():
    return hashlib.sha256((repr(FINVALUE_TO_PTRADE) + inspect.getsource(MootdxAffairFetcher._convert_to_ptrade_format)).encode()).hexdigest()


def migrate_financial_markers(data_dir):
    """Move legacy markers into the existing quarter table once, then remove JSON."""
    import duckdb
    path = data_dir / "mootdx_financial" / "imports.json"
    database = data_dir / "cn.duckdb"
    if not path.exists() or not database.exists():
        return
    try:
        with duckdb.connect(str(database)) as conn:
            if "fundamentals_progress" not in {row[0] for row in conn.execute("SHOW TABLES").fetchall()}:
                return
            for day, signature in json.loads(path.read_text()).items():
                conn.execute("UPDATE fundamentals_progress SET file_hash=? WHERE year=? AND quarter=? AND stock_count>0",
                             ["local:" + signature, int(day[:4]), int(day[5:7]) // 3])
        path.unlink()
    except duckdb.Error:
        return  # Leave the small legacy marker intact while another writer holds the DB.
