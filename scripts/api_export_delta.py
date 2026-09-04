#!/usr/bin/env python
"""Export table-level delta package for customer data sync."""

import argparse
import datetime as dt
import hashlib
import json
import tarfile
import tempfile
from pathlib import Path

import duckdb

from check_integrity import _halted_symbols_on, _stock_universe
from export_parquet import _resolve_db


def _parse_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit(f"invalid date: {value}") from exc


def _sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _latest_stock_date(conn: duckdb.DuckDBPyConnection) -> dt.date:
    value = conn.execute("SELECT MAX(date) FROM stocks").fetchone()[0]
    if value is None:
        raise SystemExit("stocks table is empty")
    if isinstance(value, dt.datetime):
        return value.date()
    return value


def _count_symbols_on_date(
    conn: duckdb.DuckDBPyConnection,
    *,
    table: str,
    symbols: list[str],
    date_value: dt.date,
) -> int:
    if not symbols:
        return 0
    placeholders = ",".join(["?"] * len(symbols))
    return conn.execute(
        f"""
        SELECT COUNT(DISTINCT symbol)
        FROM {table}
        WHERE date = ? AND symbol IN ({placeholders})
        """,
        [date_value, *symbols],
    ).fetchone()[0]


def _latest_complete_date(conn: duckdb.DuckDBPyConnection, market: str) -> dt.date | None:
    latest = _latest_stock_date(conn)
    if market != "cn":
        return latest

    recent_dates = conn.execute(
        """
        SELECT date
        FROM stocks
        GROUP BY date
        ORDER BY date DESC
        LIMIT 60
        """
    ).fetchall()

    for (candidate,) in recent_dates:
        candidate_date = candidate.date() if isinstance(candidate, dt.datetime) else candidate
        candidate_text = candidate_date.isoformat()
        active_symbols, _ = _stock_universe(conn, market, candidate_text)
        halted_symbols = _halted_symbols_on(conn, candidate_text)
        required_symbols = [
            symbol for symbol in active_symbols if symbol not in halted_symbols
        ]
        if not required_symbols:
            return candidate_date

        expected = len(required_symbols)
        if (
            _count_symbols_on_date(
                conn,
                table="stocks",
                symbols=required_symbols,
                date_value=candidate_date,
            )
            == expected
            and _count_symbols_on_date(
                conn,
                table="valuation",
                symbols=required_symbols,
                date_value=candidate_date,
            )
            == expected
        ):
            return candidate_date

    return None


def _copy_table(
    conn: duckdb.DuckDBPyConnection,
    *,
    table: str,
    sql: str,
    output_dir: Path,
) -> dict:
    output = output_dir / f"{table}.parquet"
    rows = conn.execute(f"SELECT COUNT(*) FROM ({sql})").fetchone()[0]
    conn.execute(
        f"COPY ({sql}) TO '{_sql_path(output)}' (FORMAT PARQUET, CODEC 'ZSTD')"
    )
    return {
        "table": table,
        "path": output.name,
        "rows": rows,
        "size": output.stat().st_size,
        "sha256": _sha256(output),
    }


def _write_manifest(path: Path, manifest: dict) -> None:
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_archive(output: Path, package_dir: Path, files: list[str]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:gz") as tar:
        for name in files:
            tar.add(package_dir / name, arcname=name)


def _write_manifest_archive(output: Path, manifest: dict) -> None:
    with tempfile.TemporaryDirectory(prefix="simtradedata-delta-") as tmp:
        package_dir = Path(tmp)
        _write_manifest(package_dir / "manifest.json", manifest)
        _write_archive(output, package_dir, ["manifest.json"])


def _write_busy_manifest_archive(
    output: Path,
    *,
    market: str,
    last_sync: dt.date,
    retry_after: int,
) -> None:
    _write_manifest_archive(output, {
        "package_format": "simtradedata_api_delta_v1",
        "schema_version": 1,
        "market": market,
        "from_version": last_sync.isoformat(),
        "to_version": None,
        "up_to_date": False,
        "fallback_to_baseline": False,
        "pipeline_busy": True,
        "retry_after": retry_after,
        "tables": [],
    })


def export_api_delta(
    *,
    market: str,
    db_path: Path,
    last_sync: dt.date,
    output: Path,
    max_days: int,
    retry_after: int = 300,
) -> None:
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    try:
        conn = duckdb.connect(str(db_path), read_only=True)
    except duckdb.IOException as exc:
        message = str(exc)
        if "Could not set lock" not in message and "Conflicting lock" not in message:
            raise
        _write_busy_manifest_archive(
            output,
            market=market,
            last_sync=last_sync,
            retry_after=retry_after,
        )
        return

    with conn:
        latest = _latest_complete_date(conn, market)
        if latest is None:
            _write_busy_manifest_archive(
                output,
                market=market,
                last_sync=last_sync,
                retry_after=retry_after,
            )
            return

        base_manifest = {
            "package_format": "simtradedata_api_delta_v1",
            "schema_version": 1,
            "market": market,
            "from_version": last_sync.isoformat(),
            "to_version": latest.isoformat(),
            "up_to_date": last_sync >= latest,
            "fallback_to_baseline": False,
            "pipeline_busy": False,
            "retry_after": None,
            "tables": [],
        }

        with tempfile.TemporaryDirectory(prefix="simtradedata-delta-") as tmp:
            package_dir = Path(tmp)

            if last_sync >= latest:
                _write_manifest(package_dir / "manifest.json", base_manifest)
                _write_archive(output, package_dir, ["manifest.json"])
                return

            if (latest - last_sync).days > max_days:
                manifest = {
                    **base_manifest,
                    "fallback_to_baseline": True,
                    "reason": "delta_window_too_large",
                }
                _write_manifest(package_dir / "manifest.json", manifest)
                _write_archive(output, package_dir, ["manifest.json"])
                return

            start = last_sync.isoformat()
            end = latest.isoformat()
            start_key = start.replace("-", "")
            end_key = end.replace("-", "")

            tables = [
                _copy_table(
                    conn,
                    table="stocks",
                    sql=(
                        "SELECT * FROM stocks "
                        f"WHERE date > DATE '{start}' AND date <= DATE '{end}' "
                        "ORDER BY symbol, date"
                    ),
                    output_dir=package_dir,
                ),
                _copy_table(
                    conn,
                    table="valuation",
                    sql=(
                        "SELECT * FROM valuation "
                        f"WHERE date > DATE '{start}' AND date <= DATE '{end}' "
                        "ORDER BY symbol, date"
                    ),
                    output_dir=package_dir,
                ),
                _copy_table(
                    conn,
                    table="stock_status",
                    sql=(
                        # DB 中 symbols 存的是 JSON 字符串，导出为 list 与全量包格式对齐
                        "SELECT date, status_type, symbols::JSON::VARCHAR[] AS symbols "
                        "FROM stock_status "
                        f"WHERE date > '{start_key}' AND date <= '{end_key}' "
                        "ORDER BY date, status_type"
                    ),
                    output_dir=package_dir,
                ),
                _copy_table(
                    conn,
                    table="stock_metadata",
                    sql="SELECT * FROM stock_metadata ORDER BY symbol",
                    output_dir=package_dir,
                ),
            ]

            manifest = {**base_manifest, "tables": tables}
            _write_manifest(package_dir / "manifest.json", manifest)
            _write_archive(
                output,
                package_dir,
                [
                    "manifest.json",
                    "stocks.parquet",
                    "valuation.parquet",
                    "stock_status.parquet",
                    "stock_metadata.parquet",
                ],
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", choices=["cn", "us"], default="cn")
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--last-sync", required=True, help="Last local version, YYYY-MM-DD")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-days", type=int, default=60)
    parser.add_argument("--retry-after", type=int, default=300)
    args = parser.parse_args()

    export_api_delta(
        market=args.market,
        db_path=args.db or Path(_resolve_db(args.market)),
        last_sync=_parse_date(args.last_sync),
        output=args.output,
        max_days=args.max_days,
        retry_after=args.retry_after,
    )


if __name__ == "__main__":
    main()
