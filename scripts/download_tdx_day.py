# -*- coding: utf-8 -*-
"""
Download and import TDX (通达信) daily data automatically.

Data source: https://www.tdx.com.cn/article/vipdata.html
Downloads the complete daily data package (hsjday.zip, ~500MB) and imports to DuckDB.

Features:
1. Auto-download from official TDX server
2. Incremental import (only new data)
3. Optional full reimport

Usage:
    # Download and import (incremental)
    uv run python scripts/download_tdx_day.py

    # Full reimport
    uv run python scripts/download_tdx_day.py --full

    # Download only (no import)
    uv run python scripts/download_tdx_day.py --download-only

    # Import from existing file
    uv run python scripts/download_tdx_day.py --file hsjday.zip
"""

import argparse
import logging
from pathlib import Path
from urllib.request import Request, urlopen

from tqdm import tqdm

from scripts.import_tdx_day import TdxDayImporter
from simtradedata.writers.duckdb_writer import DEFAULT_DB_PATH

# Configuration
DOWNLOAD_URL = "https://data.tdx.com.cn/vipdoc/hsjday.zip"
DOWNLOAD_DIR = Path("data/downloads")
LOG_FILE = "data/download_tdx_day.log"

# Ensure directories exist
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    filemode="w",
)
logger = logging.getLogger(__name__)


def get_remote_file_info(url: str) -> dict:
    """
    Get remote file info (size, last-modified) via HEAD request.

    Args:
        url: Download URL

    Returns:
        Dict with 'size' and 'last_modified' keys
    """
    try:
        req = Request(url, method="HEAD")
        req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")

        with urlopen(req, timeout=30) as response:
            size = response.headers.get("Content-Length")
            last_modified = response.headers.get("Last-Modified")

            return {
                "size": int(size) if size else None,
                "last_modified": last_modified,
            }
    except Exception as e:
        logger.warning(f"Failed to get remote file info: {e}")
        return {"size": None, "last_modified": None}


def download_file(url: str, dest_path: Path, show_progress: bool = True) -> bool:
    """
    Download file with progress bar.

    Args:
        url: Download URL
        dest_path: Destination path
        show_progress: Show progress bar

    Returns:
        True if download succeeded
    """
    try:
        req = Request(url)
        req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")

        with urlopen(req, timeout=60) as response:
            total_size = response.headers.get("Content-Length")
            total_size = int(total_size) if total_size else None

            # Create temp file first, then move
            temp_path = dest_path.with_suffix(".tmp")

            with open(temp_path, "wb") as f:
                if show_progress and total_size:
                    with tqdm(
                        total=total_size,
                        unit="B",
                        unit_scale=True,
                        desc="Downloading",
                        ncols=100,
                    ) as pbar:
                        while True:
                            chunk = response.read(8192)
                            if not chunk:
                                break
                            f.write(chunk)
                            pbar.update(len(chunk))
                else:
                    while True:
                        chunk = response.read(8192)
                        if not chunk:
                            break
                        f.write(chunk)

            # Verify download size matches Content-Length
            downloaded_size = temp_path.stat().st_size
            if total_size and downloaded_size != total_size:
                temp_path.unlink(missing_ok=True)
                logger.error(
                    "Download size mismatch for %s: expected %d, got %d",
                    url, total_size, downloaded_size,
                )
                return False

            # Move temp file to final destination
            temp_path.rename(dest_path)
            return True

    except Exception as e:
        logger.error(f"Download failed: {e}")
        print(f"Error: Download failed - {e}")
        return False


def needs_update(local_path: Path, remote_info: dict) -> bool:
    """
    Check if local file needs update based on remote info.

    Args:
        local_path: Path to local file
        remote_info: Dict with remote file info

    Returns:
        True if update is needed
    """
    if not local_path.exists():
        return True

    # Check size
    if remote_info.get("size"):
        local_size = local_path.stat().st_size
        if local_size != remote_info["size"]:
            return True

    return False


def main():
    parser = argparse.ArgumentParser(
        description="Download and import TDX daily data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Download and import (incremental)
    uv run python scripts/download_tdx_day.py

    # Full reimport
    uv run python scripts/download_tdx_day.py --full

    # Download only
    uv run python scripts/download_tdx_day.py --download-only

    # Use existing file
    uv run python scripts/download_tdx_day.py --file hsjday.zip
        """,
    )
    parser.add_argument(
        "--db",
        type=str,
        default=DEFAULT_DB_PATH,
        help=f"Database path (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Full import (ignore existing data, reimport all)",
    )
    parser.add_argument(
        "--download-only",
        action="store_true",
        help="Download only, do not import",
    )
    parser.add_argument(
        "--file",
        type=str,
        default=None,
        help="Use existing ZIP file instead of downloading",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Force download even if local file is up to date",
    )

    args = parser.parse_args()

    print("=" * 60)
    print("TDX Daily Data Download & Import")
    print("=" * 60)
    print(f"Source: {DOWNLOAD_URL}")
    print(f"Database: {args.db}")
    print()

    # Determine ZIP file path
    if args.file:
        zip_path = Path(args.file)
        if not zip_path.exists():
            print(f"Error: File not found: {zip_path}")
            return 1
        print(f"Using existing file: {zip_path}")
    else:
        # Download from TDX
        zip_path = DOWNLOAD_DIR / "hsjday.zip"

        print("Checking remote file...")
        remote_info = get_remote_file_info(DOWNLOAD_URL)

        if remote_info.get("size"):
            print(f"  Remote size: {remote_info['size'] / 1024 / 1024:.1f} MB")
        if remote_info.get("last_modified"):
            print(f"  Last modified: {remote_info['last_modified']}")

        if args.force_download or needs_update(zip_path, remote_info):
            print()
            print("Downloading hsjday.zip...")
            if not download_file(DOWNLOAD_URL, zip_path):
                return 1
            print(f"Downloaded to: {zip_path}")
        else:
            print()
            print("Local file is up to date, skipping download.")
            print("  (Use --force-download to re-download)")

    if args.download_only:
        print()
        print("Download complete (--download-only specified)")
        return 0

    # Import data
    print()
    print("Importing data...")
    print()

    importer = TdxDayImporter(db_path=args.db, full_import=args.full)

    try:
        stats = importer.import_from_source(zip_path)

        print()
        print("=" * 60)
        print("Import Complete")
        print("=" * 60)
        print(f"Files processed: {stats['files_processed']}")
        print(f"Files skipped: {stats['files_skipped']}")
        print(f"Records imported: {stats['records_imported']}")

        if stats["records_backfilled"] > 0:
            print(f"  - Backfilled (historical): {stats['records_backfilled']}")

        if stats["records_skipped"] > 0:
            print(f"Records skipped (up to date): {stats['records_skipped']}")

    finally:
        importer.close()

    return 0


if __name__ == "__main__":
    exit(main())
