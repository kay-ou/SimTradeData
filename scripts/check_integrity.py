# -*- coding: utf-8 -*-
"""Data integrity gate for SimTradeData DuckDB and exported Parquet files."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

from simtradedata.config.field_mappings import (
    BENCHMARK_CONFIG,
    BENCHMARK_HISTORY_FLOOR,
    BLOCKS_COVERAGE_FLOOR,
    benchmark_history_ok,
)
from simtradedata.utils.paths import DUCKDB_PATH

DB_PATH = str(DUCKDB_PATH)

CN_FALLBACK_PREFIXES = {
    "000",
    "001",
    "002",
    "003",
    "300",
    "301",
    "302",
    "600",
    "601",
    "603",
    "605",
    "688",
    "689",
}
CN_STANDARD_PREFIXES = CN_FALLBACK_PREFIXES


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _date_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)[:10]


def _is_cn_symbol(symbol: str) -> bool:
    if not isinstance(symbol, str) or "." not in symbol:
        return False
    code, suffix = symbol.split(".", 1)
    return len(code) == 6 and suffix in {"SZ", "SS"} and code[:3] in CN_FALLBACK_PREFIXES


def _has_standard_cn_prefix(symbol: str) -> bool:
    code = symbol.split(".", 1)[0] if isinstance(symbol, str) else ""
    return len(code) == 6 and code[:3] in CN_STANDARD_PREFIXES


def _is_active(de_listed_date: Any, target_date: str) -> bool:
    text = _date_text(de_listed_date)
    if not text or text.lower() in {"none", "null", "nat"}:
        return True
    return text > target_date


def _is_listed(listed_date: Any, target_date: str) -> bool:
    text = _date_text(listed_date)
    if not text or text.lower() in {"none", "null", "nat"}:
        return True
    return text <= target_date


def _is_delisted_name(stock_name: Any) -> bool:
    if stock_name is None:
        return False
    return str(stock_name).strip("\x00").startswith("退市")


def _table_exists(conn: duckdb.DuckDBPyConnection, table: str) -> bool:
    return (
        conn.execute(
            """
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema='main' AND table_name=?
            """,
            [table],
        ).fetchone()[0]
        > 0
    )


def _table_columns(conn: duckdb.DuckDBPyConnection, table: str) -> set[str]:
    rows = conn.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema='main' AND table_name=?
        """,
        [table],
    ).fetchall()
    return {row[0] for row in rows}


def _table_counts(conn: duckdb.DuckDBPyConnection) -> dict[str, int]:
    tables = conn.execute(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema='main'
        ORDER BY table_name
        """
    ).fetchall()
    return {
        name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        for (name,) in tables
    }


def _latest_date(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    symbol: str | None = None,
) -> str | None:
    if not _table_exists(conn, table):
        return None
    if symbol is None:
        row = conn.execute(f"SELECT MAX(date) FROM {table}").fetchone()
    else:
        row = conn.execute(
            f"SELECT MAX(date) FROM {table} WHERE symbol=?",
            [symbol],
        ).fetchone()
    return _date_text(row[0]) if row else None


def _active_cn_symbols(
    conn: duckdb.DuckDBPyConnection,
    target_date: str,
) -> tuple[list[str], list[str]]:
    """Return active CN stock symbols and non-standard active symbols."""
    current_pool = None
    if _table_exists(conn, "stock_pool"):
        stock_pool_columns = _table_columns(conn, "stock_pool")
        if "last_seen_date" in stock_pool_columns:
            latest_pool_date = _date_text(
                conn.execute("SELECT MAX(last_seen_date) FROM stock_pool").fetchone()[0]
            )
            if latest_pool_date and latest_pool_date >= target_date:
                current_pool = {
                    row[0]
                    for row in conn.execute(
                        """
                        SELECT symbol FROM stock_pool
                        WHERE last_seen_date >= ?
                        """,
                        [target_date],
                    ).fetchall()
                }

    if _table_exists(conn, "stock_metadata"):
        metadata_columns = _table_columns(conn, "stock_metadata")
        security_type_expr = (
            "security_type" if "security_type" in metadata_columns else "NULL"
        )
        stock_name_expr = "stock_name" if "stock_name" in metadata_columns else "NULL"
        listed_date_expr = "listed_date" if "listed_date" in metadata_columns else "NULL"
        rows = conn.execute(
            f"""
            SELECT symbol, {listed_date_expr} AS listed_date, de_listed_date,
                   {security_type_expr} AS security_type,
                   {stock_name_expr} AS stock_name
            FROM stock_metadata
            ORDER BY symbol
            """
        ).fetchall()
        has_security_type = any(row[3] not in (None, "") for row in rows)

        symbols = []
        anomalies = []
        for symbol, listed_date, de_listed_date, security_type, stock_name in rows:
            if not _is_listed(listed_date, target_date):
                continue
            if not _is_active(de_listed_date, target_date):
                continue
            if _is_delisted_name(stock_name):
                continue
            if current_pool is not None and symbol not in current_pool:
                continue
            if has_security_type:
                if security_type != "1":
                    continue
                if not isinstance(symbol, str) or not symbol.endswith((".SZ", ".SS")):
                    continue
            elif not _is_cn_symbol(symbol):
                continue

            symbols.append(symbol)
            if not _has_standard_cn_prefix(symbol):
                anomalies.append(symbol)
        return sorted(set(symbols)), sorted(set(anomalies))

    rows = conn.execute("SELECT DISTINCT symbol FROM stocks ORDER BY symbol").fetchall()
    symbols = [row[0] for row in rows if _is_cn_symbol(row[0])]
    anomalies = [symbol for symbol in symbols if not _has_standard_cn_prefix(symbol)]
    return sorted(set(symbols)), sorted(set(anomalies))


def _stock_universe(
    conn: duckdb.DuckDBPyConnection,
    market: str,
    target_date: str,
) -> tuple[list[str], list[str]]:
    if market == "cn":
        return _active_cn_symbols(conn, target_date)

    rows = conn.execute("SELECT DISTINCT symbol FROM stocks ORDER BY symbol").fetchall()
    return [row[0] for row in rows], []


def _symbols_latest_status(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    symbols: list[str],
    target_date: str,
    allow_stale_symbols: set[str] | None = None,
) -> dict[str, Any]:
    if not symbols or not _table_exists(conn, table):
        return {
            "latest_count": 0,
            "missing": symbols,
            "stale": [],
        }

    latest_by_symbol = {
        symbol: _date_text(max_date)
        for symbol, max_date in conn.execute(
            f"""
            SELECT symbol, MAX(date) FROM {table}
            WHERE symbol IN ({",".join(["?"] * len(symbols))})
            GROUP BY symbol
            """,
            symbols,
        ).fetchall()
    }
    missing = [symbol for symbol in symbols if symbol not in latest_by_symbol]
    allow_stale_symbols = allow_stale_symbols or set()
    stale = [
        {"symbol": symbol, "max_date": latest_by_symbol[symbol]}
        for symbol in symbols
        if symbol in latest_by_symbol
        and latest_by_symbol[symbol] != target_date
        and symbol not in allow_stale_symbols
    ]
    latest_count = len(symbols) - len(missing) - len(stale)
    return {
        "latest_count": latest_count,
        "missing": missing,
        "stale": stale,
    }


def _halted_symbols_on(
    conn: duckdb.DuckDBPyConnection,
    target_date: str,
) -> set[str]:
    if not _table_exists(conn, "stock_status"):
        return set()

    date_key = target_date.replace("-", "")
    rows = conn.execute(
        """
        SELECT symbols FROM stock_status
        WHERE date=? AND status_type='HALT'
        """,
        [date_key],
    ).fetchall()
    halted = set()
    for (symbols_json,) in rows:
        if not symbols_json:
            continue
        try:
            symbols = json.loads(symbols_json)
        except json.JSONDecodeError:
            continue
        halted.update(symbol for symbol in symbols if isinstance(symbol, str))
    return halted


def _symbols_coverage_status(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    symbols: list[str],
) -> dict[str, Any]:
    if not symbols or not _table_exists(conn, table):
        return {
            "covered_count": 0,
            "latest_count": 0,
            "missing": symbols,
            "stale": [],
        }

    latest_by_symbol = {
        symbol: _date_text(max_date)
        for symbol, max_date in conn.execute(
            f"""
            SELECT symbol, MAX(date) FROM {table}
            WHERE symbol IN ({",".join(["?"] * len(symbols))})
            GROUP BY symbol
            """,
            symbols,
        ).fetchall()
    }
    missing = [symbol for symbol in symbols if symbol not in latest_by_symbol]
    covered_count = len(symbols) - len(missing)
    return {
        "covered_count": covered_count,
        "latest_count": covered_count,
        "missing": missing,
        "stale": [],
    }


def _add_check(
    checks: list[dict[str, Any]],
    name: str,
    passed: bool,
    severity: str = "error",
    **details: Any,
) -> None:
    checks.append(
        {
            "name": name,
            "status": "pass" if passed else "fail",
            "severity": severity,
            **details,
        }
    )


def _inspect_export(
    export_dir: Path,
    market: str,
    target_date: str,
    symbols: list[str],
    max_examples: int,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(export_dir),
        "exists": export_dir.exists(),
        "checks": [],
    }
    checks = result["checks"]
    if not export_dir.exists():
        _add_check(checks, "export_dir_exists", False, path=str(export_dir))
        return result

    manifest_path = export_dir / "manifest.json"
    _add_check(checks, "manifest_exists", manifest_path.exists(), path=str(manifest_path))
    manifest = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        result["manifest"] = manifest
        _add_check(
            checks,
            "manifest_version_matches_target",
            manifest.get("version") == target_date,
            expected=target_date,
            actual=manifest.get("version"),
        )
        date_range = manifest.get("date_range") or {}
        _add_check(
            checks,
            "manifest_end_matches_target",
            date_range.get("end") == target_date,
            expected=target_date,
            actual=date_range.get("end"),
        )

    stock_files = {p.stem for p in (export_dir / "stocks").glob("*.parquet")}
    valuation_files = {p.stem for p in (export_dir / "valuation").glob("*.parquet")}
    result["stock_files"] = len(stock_files)
    result["valuation_files"] = len(valuation_files)

    missing_stock_files = sorted(set(symbols) - stock_files)
    _add_check(
        checks,
        "active_stock_files_present",
        not missing_stock_files,
        expected=len(symbols),
        actual=len(symbols) - len(missing_stock_files),
        missing=missing_stock_files[:max_examples],
        missing_count=len(missing_stock_files),
    )

    if market == "cn":
        missing_valuation_files = sorted(set(symbols) - valuation_files)
        _add_check(
            checks,
            "active_valuation_files_present",
            not missing_valuation_files,
            expected=len(symbols),
            actual=len(symbols) - len(missing_valuation_files),
            missing=missing_valuation_files[:max_examples],
            missing_count=len(missing_valuation_files),
        )

    metadata_file = export_dir / "metadata" / "stock_metadata.parquet"
    _add_check(
        checks,
        "stock_metadata_export_present",
        metadata_file.exists(),
        path=str(metadata_file),
    )

    if market == "cn":
        benchmark_file = export_dir / "benchmark.parquet"
        _add_check(
            checks,
            "benchmark_export_present",
            benchmark_file.exists(),
            path=str(benchmark_file),
        )
        if benchmark_file.exists():
            min_date = duckdb.sql(
                "SELECT MIN(date)::VARCHAR FROM read_parquet(?)",
                params=[str(benchmark_file)],
            ).fetchone()[0]
            _add_check(
                checks,
                "benchmark_export_history_coverage",
                benchmark_history_ok(min_date),
                actual=min_date,
                expected=f"<= {BENCHMARK_HISTORY_FLOOR}",
            )

    fundamentals_dir = export_dir / "fundamentals"
    _add_check(
        checks,
        "fundamentals_export_present",
        fundamentals_dir.exists() and any(fundamentals_dir.glob("*.parquet")),
        path=str(fundamentals_dir),
        severity="warning",
    )

    exrights_dir = export_dir / "exrights"
    _add_check(
        checks,
        "exrights_export_present",
        exrights_dir.exists() and any(exrights_dir.glob("*.parquet")),
        path=str(exrights_dir),
        severity="warning",
    )

    return result


def check_integrity(
    db_path: str = DB_PATH,
    market: str = "cn",
    target_date: str | None = None,
    export_dir: str | None = None,
    max_examples: int = 20,
) -> dict[str, Any]:
    """Check local data completeness and return a machine-readable report."""
    db_file = Path(db_path)
    report: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "market": market.upper(),
        "db_path": str(db_file),
        "status": "fail",
        "checks": [],
    }
    checks = report["checks"]

    if not db_file.exists():
        _add_check(checks, "database_exists", False, path=str(db_file))
        return report

    _add_check(checks, "database_exists", True, path=str(db_file))
    conn = duckdb.connect(str(db_file), read_only=True)
    try:
        table_counts = _table_counts(conn)
        report["tables"] = table_counts

        required_tables = ["stocks"]
        if market == "cn":
            required_tables.extend(["valuation", "stock_metadata"])
        missing_tables = [table for table in required_tables if table not in table_counts]
        _add_check(
            checks,
            "required_tables_present",
            not missing_tables,
            missing=missing_tables,
        )
        if missing_tables:
            return report

        if target_date is None:
            target_date = _latest_date(conn, "stocks")
        report["target_date"] = target_date
        _add_check(
            checks,
            "target_date_available",
            target_date is not None,
            actual=target_date,
        )
        if target_date is None:
            return report

        symbols, anomaly_symbols = _stock_universe(conn, market, target_date)
        halted_symbols = _halted_symbols_on(conn, target_date) if market == "cn" else set()
        report["active_symbols"] = len(symbols)
        report["anomalies"] = {
            "non_standard_prefix_symbols": anomaly_symbols[:max_examples],
            "non_standard_prefix_count": len(anomaly_symbols),
        }
        _add_check(
            checks,
            "active_symbol_universe_non_empty",
            bool(symbols),
            actual=len(symbols),
        )

        stock_status = _symbols_latest_status(
            conn,
            "stocks",
            symbols,
            target_date,
            allow_stale_symbols=halted_symbols,
        )
        report["stocks"] = stock_status
        _add_check(
            checks,
            "active_stocks_latest",
            stock_status["latest_count"] == len(symbols),
            expected=len(symbols),
            actual=stock_status["latest_count"],
            missing=stock_status["missing"][:max_examples],
            missing_count=len(stock_status["missing"]),
            stale=stock_status["stale"][:max_examples],
            stale_count=len(stock_status["stale"]),
        )

        if market == "cn":
            valuation_status = _symbols_latest_status(
                conn,
                "valuation",
                symbols,
                target_date,
                allow_stale_symbols=halted_symbols,
            )
            report["valuation"] = valuation_status
            _add_check(
                checks,
                "active_valuation_latest",
                valuation_status["latest_count"] == len(symbols),
                expected=len(symbols),
                actual=valuation_status["latest_count"],
                missing=valuation_status["missing"][:max_examples],
                missing_count=len(valuation_status["missing"]),
                stale=valuation_status["stale"][:max_examples],
                stale_count=len(valuation_status["stale"]),
            )

            delisted_missing_valuation = conn.execute(
                """
                SELECT COUNT(*) FROM stock_metadata m
                WHERE NOT (
                    m.de_listed_date IS NULL
                    OR CAST(m.de_listed_date AS VARCHAR) > ?
                )
                AND NOT EXISTS (
                    SELECT 1 FROM valuation v WHERE v.symbol=m.symbol
                )
                """,
                [target_date],
            ).fetchone()[0]
            report["delisted_missing_valuation"] = delisted_missing_valuation

            # Benchmark history coverage (fail closed on truncated index data).
            # Mirrors _export_metadata: the stocks row for the default index
            # wins over the benchmark table, so check what would be exported.
            benchmark_symbol = BENCHMARK_CONFIG["default_index"]
            if conn.execute(
                "SELECT COUNT(*) FROM stocks WHERE symbol = ?",
                [benchmark_symbol],
            ).fetchone()[0] > 0:
                benchmark_start = conn.execute(
                    "SELECT MIN(date) FROM stocks WHERE symbol = ?",
                    [benchmark_symbol],
                ).fetchone()[0]
            elif _table_exists(conn, "benchmark"):
                benchmark_start = conn.execute(
                    "SELECT MIN(date) FROM benchmark"
                ).fetchone()[0]
            else:
                benchmark_start = None
            benchmark_start_text = _date_text(benchmark_start)
            report["benchmark_start"] = benchmark_start_text
            _add_check(
                checks,
                "benchmark_history_coverage",
                benchmark_history_ok(benchmark_start_text),
                actual=benchmark_start_text,
                expected=f"<= {BENCHMARK_HISTORY_FLOOR}",
            )

            # Industry blocks coverage (fail closed when ZJHHY data is missing)
            blocks_total, blocks_zjhhy = conn.execute(
                "SELECT COUNT(*), "
                "SUM(CASE WHEN blocks LIKE '%ZJHHY%' THEN 1 ELSE 0 END) "
                "FROM stock_metadata"
            ).fetchone()
            report["stock_metadata_blocks"] = {
                "total": blocks_total,
                "zjhhy": blocks_zjhhy or 0,
            }
            blocks_ratio = (
                (blocks_zjhhy or 0) / blocks_total if blocks_total else 0.0
            )
            _add_check(
                checks,
                "stock_metadata_blocks_coverage",
                blocks_ratio >= BLOCKS_COVERAGE_FLOOR,
                expected=f">= {BLOCKS_COVERAGE_FLOOR:.0%}",
                actual=f"{blocks_ratio:.1%}",
                zjhhy=blocks_zjhhy or 0,
                total=blocks_total,
            )

            # Fundamentals coverage check
            if _table_exists(conn, "fundamentals"):
                fundamentals_status = _symbols_coverage_status(
                    conn, "fundamentals", symbols
                )
                report["fundamentals"] = fundamentals_status
                _add_check(
                    checks,
                    "active_fundamentals_coverage",
                    fundamentals_status["covered_count"] > 0,
                    actual=fundamentals_status["covered_count"],
                    missing_count=len(fundamentals_status.get("missing", [])),
                    severity="warning",
                )

            # Exrights coverage check (at least some symbols should have data)
            if _table_exists(conn, "exrights"):
                exr_symbols = conn.execute(
                    "SELECT COUNT(DISTINCT symbol) FROM exrights"
                ).fetchone()[0]
                report["exrights_symbols"] = exr_symbols
                _add_check(
                    checks,
                    "exrights_has_data",
                    exr_symbols > 0,
                    actual=exr_symbols,
                    severity="warning",
                )

        if export_dir:
            report["export"] = _inspect_export(
                Path(export_dir),
                market,
                target_date,
                symbols,
                max_examples,
            )
            checks.extend(report["export"]["checks"])

    finally:
        conn.close()

    failed_errors = [
        check for check in checks
        if check["status"] == "fail" and check.get("severity") == "error"
    ]
    report["status"] = "fail" if failed_errors else "pass"
    return report


def print_human_report(report: dict[str, Any]) -> None:
    print("=" * 70)
    print("SimTradeData Integrity Report")
    print("=" * 70)
    print(f"Market:      {report.get('market')}")
    print(f"Target date: {report.get('target_date') or 'n/a'}")
    print(f"Database:    {report.get('db_path')}")
    print(f"Status:      {report.get('status', 'fail').upper()}")

    if "tables" in report:
        print("\n--- Table Row Counts ---")
        for table, count in sorted(report["tables"].items()):
            print(f"  {table:30s} {count:>12,} rows")

    print("\n--- Active Data Coverage ---")
    active_count = report.get("active_symbols", 0)
    stocks = report.get("stocks") or {}
    print(
        f"  stocks latest:    {stocks.get('latest_count', 0):>6,} / "
        f"{active_count:>6,}"
    )
    if "valuation" in report:
        valuation = report["valuation"]
        print(
            f"  valuation latest: {valuation.get('latest_count', 0):>6,} / "
            f"{active_count:>6,}"
        )
    if report.get("delisted_missing_valuation") is not None:
        print(
            f"  delisted missing valuation: "
            f"{report['delisted_missing_valuation']:,}"
        )

    anomalies = report.get("anomalies") or {}
    if anomalies.get("non_standard_prefix_count"):
        print("\n--- Anomalies ---")
        print(
            f"  non-standard prefix symbols: "
            f"{anomalies['non_standard_prefix_count']}"
        )
        print(f"  examples: {', '.join(anomalies['non_standard_prefix_symbols'])}")

    if "export" in report:
        export = report["export"]
        print("\n--- Export ---")
        print(f"  path:            {export.get('path')}")
        print(f"  stock files:     {export.get('stock_files', 0):,}")
        print(f"  valuation files: {export.get('valuation_files', 0):,}")

    failed = [
        check for check in report.get("checks", [])
        if check["status"] == "fail"
    ]
    if failed:
        print("\n--- Failed Checks ---")
        for check in failed:
            print(f"  {check['name']}: {check}")

    print("=" * 70)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check SimTradeData integrity")
    parser.add_argument("--db-path", default=DB_PATH, help="DuckDB database path")
    parser.add_argument("--market", default="cn", choices=["cn", "us"])
    parser.add_argument(
        "--target-date",
        default=None,
        help="Expected latest date (YYYY-MM-DD). Defaults to MAX(stocks.date).",
    )
    parser.add_argument(
        "--export-dir",
        default=None,
        help="Optional Parquet export directory to verify",
    )
    parser.add_argument(
        "--json-output",
        default=None,
        help="Write machine-readable report JSON to this path",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of the human report",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when required completeness checks fail",
    )
    parser.add_argument(
        "--max-examples",
        type=int,
        default=20,
        help="Maximum missing/stale symbols to include in examples",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Deprecated. Integrity checks no longer start downloads.",
    )
    args = parser.parse_args()

    if args.fix:
        print("--fix is disabled; use a targeted remediation command instead.")
        return 2

    report = check_integrity(
        db_path=args.db_path,
        market=args.market,
        target_date=args.target_date,
        export_dir=args.export_dir,
        max_examples=args.max_examples,
    )

    if args.json_output:
        path = Path(args.json_output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=_json_default),
            encoding="utf-8",
        )

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=_json_default))
    else:
        print_human_report(report)

    if args.strict and report["status"] != "pass":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
