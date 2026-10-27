#!/usr/bin/env bash
# Rebuild/compact the market DuckDB file.
#
# Nightly upserts keep MVCC history; DuckDB never shrinks its file on its
# own, so the database grows over time. This script exports the logical
# data, verifies row counts against the live database, imports into a new
# file, verifies the result, and atomically replaces the old database.
#
# Usage:
#   MARKET=cn bash scripts/rebuild_db.sh
#
# Environment:
#   SIMTRADE_DATA_DIR                  Repository root (default: script's parent directory)
#   MARKET                             cn | us (default: cn)
#   MIN_FREE_GB                        Required free space before rebuilding (default: 20)
#   PYTHON                             Interpreter command (default: poetry run python)
#   SIMTRADE_DUCKDB_MEMORY_LIMIT       Export memory limit (default: 1.5GB)
#   SIMTRADE_REBUILD_IMPORT_MEMORY_LIMIT  Import memory limit (default: 2GB)
#   LOCK_FILE                          Pipeline lock (default: /tmp/simtradedata_daily_<market>.lock)
#   KEEP_EXPORT                        1 to keep the export directory after success

set -euo pipefail

SIMTRADE_DATA_DIR="${SIMTRADE_DATA_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
MARKET="${MARKET:-cn}"
MIN_FREE_GB="${MIN_FREE_GB:-20}"
PYTHON="${PYTHON:-poetry run python}"
EXPORT_MEMORY_LIMIT="${SIMTRADE_DUCKDB_MEMORY_LIMIT:-1.5GB}"
IMPORT_MEMORY_LIMIT="${SIMTRADE_REBUILD_IMPORT_MEMORY_LIMIT:-2GB}"
LOCK_FILE="${LOCK_FILE:-/tmp/simtradedata_daily_${MARKET}.lock}"
KEEP_EXPORT="${KEEP_EXPORT:-0}"

if [[ ! "$EXPORT_MEMORY_LIMIT" =~ ^[0-9]+(\.[0-9]+)?(MB|GB|TB)$ || ! "$IMPORT_MEMORY_LIMIT" =~ ^[0-9]+(\.[0-9]+)?(MB|GB|TB)$ ]]; then
  echo "ERROR: invalid DuckDB memory limit (${EXPORT_MEMORY_LIMIT}/${IMPORT_MEMORY_LIMIT})" >&2
  exit 1
fi

DB="$SIMTRADE_DATA_DIR/data/${MARKET}.duckdb"
NEW_DB="$SIMTRADE_DATA_DIR/data/${MARKET}.rebuild.duckdb"
EXPORT_DIR="$SIMTRADE_DATA_DIR/data/rebuild_${MARKET}"
COUNTS_FILE="$EXPORT_DIR/row_counts.json"

cd "$SIMTRADE_DATA_DIR"

if [[ ! -f "$DB" ]]; then
  echo "ERROR: database not found: $DB" >&2
  exit 1
fi

# Share the nightly pipeline lock: rebuilding while run_daily writes would
# either fail the read-only export or corrupt the replace step.
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
  echo "ERROR: another pipeline holds $LOCK_FILE" >&2
  exit 1
fi

free_gb=$(df -P "$SIMTRADE_DATA_DIR" | awk 'NR==2 {printf "%d", $4/1024/1024}')
if (( free_gb < MIN_FREE_GB )); then
  echo "ERROR: only ${free_gb}G free; ${MIN_FREE_GB}G required for rebuild" >&2
  exit 1
fi

for path in "$EXPORT_DIR" "$NEW_DB"; do
  if [[ -e "$path" ]]; then
    echo "ERROR: stale path exists: $path (verify or remove it first)" >&2
    exit 1
  fi
done

echo "=== Export $DB -> $EXPORT_DIR ==="
$PYTHON - "$DB" "$EXPORT_DIR" "$COUNTS_FILE" "$EXPORT_MEMORY_LIMIT" <<'PY'
import json
import sys

import duckdb

db_path, out_dir, counts_file, memory_limit = sys.argv[1:5]
# Flush any WAL left by the nightly pipeline before reading it back.
con = duckdb.connect(db_path)
con.execute("CHECKPOINT")
con.close()

con = duckdb.connect(db_path, read_only=True)
try:
    con.execute(f"SET memory_limit='{memory_limit}'")
    con.execute(f"EXPORT DATABASE '{out_dir}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    counts = {}
    for (table,) in con.execute(
        "SELECT table_name FROM duckdb_tables() ORDER BY table_name"
    ).fetchall():
        source = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        exported = con.execute(
            "SELECT count(*) FROM read_parquet(?)", [f"{out_dir}/{table}.parquet"]
        ).fetchone()[0]
        if source != exported:
            print(f"ERROR: export row count mismatch for {table}: {source} != {exported}")
            sys.exit(1)
        counts[table] = source
finally:
    con.close()

schema = open(f"{out_dir}/schema.sql", encoding="utf-8").read()
if "PRIMARY KEY" not in schema:
    print("ERROR: exported schema lost primary keys")
    sys.exit(1)
with open(counts_file, "w", encoding="utf-8") as fh:
    json.dump(counts, fh, sort_keys=True)
print(f"verified export of {len(counts)} tables")
PY

echo "=== Import into $NEW_DB ==="
$PYTHON - "$NEW_DB" "$EXPORT_DIR" "$IMPORT_MEMORY_LIMIT" <<'PY'
import sys

import duckdb

db_path, out_dir, memory_limit = sys.argv[1:4]
con = duckdb.connect(db_path)
try:
    # Bounded memory: 38M-row change log plus primary-key indexes OOMs a
    # 3 GiB user cgroup if threads stay at the default.
    con.execute(f"SET memory_limit='{memory_limit}'")
    con.execute("SET threads=1")
    con.execute("SET preserve_insertion_order=false")
    schema = open(f"{out_dir}/schema.sql", encoding="utf-8").read()
    for stmt in schema.split(";;"):
        stmt = stmt.strip()
        if stmt:
            con.execute(stmt)
    for line in open(f"{out_dir}/load.sql", encoding="utf-8"):
        stmt = line.strip()
        if stmt:
            con.execute(stmt)
    con.execute("CHECKPOINT")
finally:
    con.close()
PY

echo "=== Verify rebuilt database ==="
$PYTHON - "$NEW_DB" "$COUNTS_FILE" <<'PY'
import json
import sys

import duckdb

db_path, counts_file = sys.argv[1:3]
counts = json.load(open(counts_file, encoding="utf-8"))
con = duckdb.connect(db_path, read_only=True)
try:
    bad = []
    for table, expected in sorted(counts.items()):
        actual = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        if actual != expected:
            bad.append((table, actual, expected))
    pk_count = con.execute(
        "SELECT count(*) FROM duckdb_constraints() WHERE constraint_type='PRIMARY KEY'"
    ).fetchone()[0]
    indexes = [
        row[0]
        for row in con.execute("SELECT index_name FROM duckdb_indexes()").fetchall()
    ]
finally:
    con.close()
if bad:
    print("ERROR: rebuilt row count mismatch:", bad)
    sys.exit(1)
if pk_count != len(counts):
    print(f"ERROR: expected {len(counts)} primary keys, found {pk_count}")
    sys.exit(1)
print(f"verified {len(counts)} tables, {pk_count} primary keys, indexes={indexes}")
PY

# Atomic hand-off: the old database stays intact until every check passed.
# The WAL was checkpointed before export, so no unflushed data remains.
rm -f "$DB.wal"
mv -f "$NEW_DB" "$DB"
if [[ "$KEEP_EXPORT" != "1" ]]; then
  rm -rf "$EXPORT_DIR"
fi

echo "=== Rebuild complete: $DB ($(du -h "$DB" | cut -f1)) ==="
