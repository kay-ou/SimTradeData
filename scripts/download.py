# -*- coding: utf-8 -*-
"""
Unified data download entry point for SimTradeData

This script orchestrates downloads from multiple data sources:
- Phase 0 (optional): TDX bulk import - fast OHLCV from local TDX .day files
- Phase 1: Mootdx - OHLCV (incremental), adjust factors, XDXR, batch financials, calendar, benchmark
- Phase 2: BaoStock - Valuation (PE/PB/PS/PCF/turnover), ST/HALT status, index constituents

Each source only downloads data it's best suited for, avoiding redundancy.
"""

import argparse
import sys
from pathlib import Path

from simtradedata.writers.duckdb_writer import DEFAULT_DB_PATH, DuckDBWriter

# Add scripts directory to path for importing sibling scripts
_scripts_dir = str(Path(__file__).parent)
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)


def print_data_status(db_path: str = DEFAULT_DB_PATH) -> None:
    """Print data completeness status for all tables."""
    db_file = Path(db_path)
    if not db_file.exists():
        print(f"Database not found: {db_path}")
        return

    writer = DuckDBWriter(db_path=db_path)
    try:
        status = writer.get_data_status()

        print("=" * 70)
        print("SimTradeData Status Report")
        print("=" * 70)

        # Per-symbol tables
        print("\n[Per-Symbol Tables]")
        for table in ["stocks", "valuation", "fundamentals", "exrights"]:
            info = status.get(table, {})
            rows = info.get("rows", 0)
            stocks = info.get("stocks", 0)
            min_date = info.get("min_date", "N/A")
            max_date = info.get("max_date", "N/A")
            print(f"  {table:18s}: {rows:>10,} rows, {stocks:>5} stocks, {min_date} ~ {max_date}")

        # Fundamentals quarters
        quarters = status.get("fundamentals_quarters", 0)
        print(f"\n  Completed fundamentals quarters: {quarters}")

        # Metadata tables
        print("\n[Metadata Tables]")
        for table in ["benchmark", "trade_days", "index_constituents", "stock_status"]:
            info = status.get(table, {})
            rows = info.get("rows", 0)
            print(f"  {table:18s}: {rows:>10,} rows")

        # Database size
        print("\n[Database]")
        db_size = db_file.stat().st_size / (1024 * 1024)
        print(f"  Path: {db_path}")
        print(f"  Size: {db_size:.1f} MB")

        print("=" * 70)
    finally:
        writer.close()


def run_tdx_import(source_path: str, db_path: str = DEFAULT_DB_PATH) -> bool:
    """Run Phase 0: TDX bulk OHLCV import from local .day files.

    Args:
        source_path: Path to ZIP file or extracted directory.
        db_path: Path to DuckDB database.

    Returns:
        True if import succeeded.
    """
    print("\n" + "=" * 70)
    print("Phase 0: TDX Bulk OHLCV Import")
    print("  - Import daily OHLCV from TDX .day files (fast, local)")
    print(f"  - Source: {source_path}")
    print("=" * 70)

    try:
        from import_tdx_day import TdxDayImporter

        path = Path(source_path)
        if not path.exists():
            print(f"Error: TDX source not found: {source_path}")
            return False

        importer = TdxDayImporter(db_path=db_path)
        try:
            stats = importer.import_from_source(path)

            print()
            print(f"  Files processed: {stats['files_processed']}")
            print(f"  Files skipped:   {stats['files_skipped']}")
            print(f"  Records imported: {stats['records_imported']}")
            if stats["records_backfilled"] > 0:
                print(f"    Backfilled (historical): {stats['records_backfilled']}")
            return True
        finally:
            importer.close()

    except Exception as e:
        print(f"Error in TDX import: {e}")
        return False


def run_tdx_download_and_import(db_path: str = DEFAULT_DB_PATH) -> bool:
    """Download hsjday.zip from TDX website and import.

    Returns:
        True if download and import succeeded.
    """
    print("\n" + "=" * 70)
    print("Phase 0: TDX Download & Import")
    print("  - Download hsjday.zip from data.tdx.com.cn")
    print("  - Import daily OHLCV from TDX .day files")
    print("=" * 70)

    try:
        from download_tdx_day import (
            DOWNLOAD_DIR,
            DOWNLOAD_URL,
            download_file,
            get_remote_file_info,
            needs_update,
        )
        from import_tdx_day import TdxDayImporter

        zip_path = DOWNLOAD_DIR / "hsjday.zip"

        print("\nChecking remote file...")
        remote_info = get_remote_file_info(DOWNLOAD_URL)
        if remote_info.get("size"):
            print(f"  Remote size: {remote_info['size'] / 1024 / 1024:.1f} MB")

        if needs_update(zip_path, remote_info):
            print("\nDownloading hsjday.zip...")
            if not download_file(DOWNLOAD_URL, zip_path):
                return False
            print(f"Downloaded to: {zip_path}")
        else:
            print("Local file is up to date, skipping download.")

        # Import
        print("\nImporting data...")
        importer = TdxDayImporter(db_path=db_path)
        try:
            stats = importer.import_from_source(zip_path)
            print(f"  Files processed: {stats['files_processed']}")
            print(f"  Records imported: {stats['records_imported']}")

            # Benchmark indices (skipped by the stock importer). Missing
            # index files only warn: stock data is already imported and the
            # benchmark history floor has its own integrity gate.
            from import_tdx_day import import_benchmark_indices
            try:
                imported = import_benchmark_indices(zip_path, importer)
                print(f"  Benchmark indices imported: {sorted(imported)}")
            except ValueError as e:
                print(f"  WARNING: benchmark index import skipped: {e}")
            return True
        finally:
            importer.close()

    except Exception as e:
        print(f"Error in TDX download & import: {e}")
        return False


def run_mootdx_download(
    skip_fundamentals: bool = False,
    download_dir: str | None = None,
    refresh_fundamentals: bool = False,
    skip_ohlcv: bool = False,
    skip_bonus_fix: bool = False,
) -> bool:
    """Run Mootdx download phase."""
    print("\n" + "=" * 70)
    print("Phase 1: Mootdx Data Download")
    if skip_ohlcv:
        print("  - OHLCV skipped (handled by TDX bulk import)")
    else:
        print("  - OHLCV market data")
    print("  - Adjust factors (backward)")
    print("  - XDXR (ex-rights/ex-dividend)")
    if not skip_fundamentals:
        print("  - Batch financial data (from Affair ZIP)")
    print("  - Trading calendar")
    print("  - Benchmark index")
    print("=" * 70)

    try:
        from download_mootdx import download_all_data as mootdx_download
        mootdx_download(
            skip_fundamentals=skip_fundamentals,
            download_dir=download_dir,
            refresh_fundamentals=refresh_fundamentals,
            skip_ohlcv=skip_ohlcv,
            skip_bonus_fix=skip_bonus_fix,
        )
        return True
    except Exception as e:
        print(f"Error in Mootdx download: {e}")
        return False


def run_baostock_download(valuation_only: bool = True) -> bool:
    """Run BaoStock download phase.

    Args:
        valuation_only: If True, only download valuation + status + index constituents.
                       If False, run full BaoStock download.
    """
    print("\n" + "=" * 70)
    print("Phase 2: BaoStock Data Download")
    if valuation_only:
        print("  - Valuation indicators (PE/PB/PS/PCF/turnover)")
        print("  - ST/HALT status")
        print("  - Index constituents (000016, 000300, 000905)")
    else:
        print("  - Full data download (market + valuation + fundamentals)")
    print("=" * 70)

    try:
        from download_efficient import download_all_data as baostock_download
        baostock_download(
            skip_fundamentals=True,  # Mootdx handles fundamentals
            skip_metadata=False,  # BaoStock owns index constituent metadata
            valuation_only=valuation_only,
        )
        return True
    except Exception as e:
        print(f"Error in BaoStock download: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Unified data download for SimTradeData",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  poetry run python scripts/download.py                # Full download
  poetry run python scripts/download.py --status       # Show data status only
  poetry run python scripts/download.py --source mootdx    # Mootdx only
  poetry run python scripts/download.py --source baostock  # BaoStock only
  poetry run python scripts/download.py --skip-fundamentals
  poetry run python scripts/download.py --tdx-source data/downloads/hsjday.zip --source mootdx
  poetry run python scripts/download.py --tdx-download --source mootdx
"""
    )

    parser.add_argument(
        "--status",
        action="store_true",
        help="Show data status and exit (no download)",
    )
    parser.add_argument(
        "--source",
        type=str,
        choices=["mootdx", "baostock", "all"],
        default="all",
        help="Data source to download from (default: all)",
    )
    parser.add_argument(
        "--skip-fundamentals",
        action="store_true",
        help="Skip batch financial data download (Mootdx Affair)",
    )
    parser.add_argument(
        "--download-dir",
        type=str,
        default=None,
        help="Directory for downloading financial data ZIP files",
    )
    parser.add_argument(
        "--baostock-full",
        action="store_true",
        help="Run BaoStock in full mode instead of valuation-only mode",
    )
    parser.add_argument(
        "--refresh-fundamentals",
        action="store_true",
        help="Force re-download all financial data quarters",
    )
    parser.add_argument(
        "--skip-mootdx-ohlcv",
        action="store_true",
        help="Skip mootdx per-symbol OHLCV download",
    )
    parser.add_argument(
        "--skip-bonus-fix",
        action="store_true",
        help="Skip slow baostock preclose bonus_ps correction",
    )

    # TDX import options (mutually exclusive)
    tdx_group = parser.add_mutually_exclusive_group()
    tdx_group.add_argument(
        "--tdx-source",
        type=str,
        default=None,
        help="Import TDX .day files from ZIP or directory before mootdx download",
    )
    tdx_group.add_argument(
        "--tdx-download",
        action="store_true",
        help="Auto-download hsjday.zip from TDX website and import before mootdx download",
    )

    args = parser.parse_args()

    # Status only mode
    if args.status:
        print_data_status()
        return

    print("=" * 70)
    print("SimTradeData Unified Download")
    print("=" * 70)
    print("\nData source responsibilities:")
    if args.tdx_source or args.tdx_download:
        print("  TDX:      OHLCV bulk import (fast, from .day files)")
    print("  Mootdx:   OHLCV (incremental), adjust factors, XDXR, batch financials, calendar, benchmark")
    print("  BaoStock: Valuation (PE/PB/PS/PCF/turnover), ST/HALT status, index constituents")
    print("=" * 70)

    success = True

    # Phase 0: TDX import (optional)
    if args.tdx_source:
        if not run_tdx_import(args.tdx_source):
            success = False
    elif args.tdx_download:
        if not run_tdx_download_and_import():
            success = False

    # Phase 1: Mootdx
    if args.source in ["mootdx", "all"]:
        skip_mootdx_ohlcv = (
            args.skip_mootdx_ohlcv or args.tdx_source is not None or args.tdx_download
        )
        if not run_mootdx_download(
            skip_fundamentals=args.skip_fundamentals,
            download_dir=args.download_dir,
            refresh_fundamentals=args.refresh_fundamentals,
            skip_ohlcv=skip_mootdx_ohlcv,
            skip_bonus_fix=args.skip_bonus_fix,
        ):
            success = False

    # Phase 2: BaoStock
    if args.source in ["baostock", "all"]:
        if not run_baostock_download(valuation_only=not args.baostock_full):
            success = False

    # Final status
    print("\n")
    print_data_status()

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
