"""
One-shot script: backfill a_floats into fundamentals table.

Re-downloads gpcw*.zip files from TDX server and parses them using the
updated FINVALUE mapping (position 239 = a_floats), then upserts into
the fundamentals table.
"""

import sys
import time

from simtradedata.fetchers.mootdx_affair_fetcher import MootdxAffairFetcher
from simtradedata.utils.code_utils import convert_to_ptrade_code
from simtradedata.utils.paths import DUCKDB_PATH
from simtradedata.writers.duckdb_writer import DuckDBWriter

DB_PATH = str(DUCKDB_PATH)


def print_usage() -> None:
    print(
        "Usage: uv run python scripts/refresh_a_floats.py\n\n"
        "Backfill a_floats into the fundamentals table from cached or "
        "downloaded mootdx gpcw*.zip files."
    )


def main():
    if any(arg in {"-h", "--help"} for arg in sys.argv[1:]):
        print_usage()
        return

    affair = MootdxAffairFetcher()
    writer = DuckDBWriter(db_path=DB_PATH)

    # Find all cached quarter files
    zip_files = sorted(affair._download_dir.glob("gpcw*.zip"))
    print(f"Found {len(zip_files)} cached quarter files")
    print(f"Download dir: {affair._download_dir}")

    t0 = time.time()
    total_updated = 0

    for zi, zf in enumerate(zip_files, 1):
        filename = zf.name
        # Extract year/quarter from filename like gpcw20240331.zip
        date_part = filename.replace("gpcw", "").replace(".zip", "")
        year = int(date_part[:4])
        mmdd = date_part[4:]
        quarter = {"0331": 1, "0630": 2, "0930": 3, "1231": 4}.get(mmdd)
        if not quarter:
            print(f"  Skipping {filename}: unknown quarter")
            continue

        print(f"  [{zi}/{len(zip_files)}] {year}Q{quarter} ({filename})...", end=" ")

        try:
            fund_df = affair.fetch_fundamentals_for_quarter(year, quarter)
            if fund_df.empty:
                print("empty")
                continue

            # Write per-stock fundamentals (partial upsert preserves other columns)
            count = 0
            if "code" not in fund_df.columns:
                print("no code column")
                continue

            writer.begin()
            try:
                for code, group in fund_df.groupby("code"):
                    ptrade_code = convert_to_ptrade_code(str(code), source="qstock")
                    if not ptrade_code:
                        continue
                    write_df = group.drop(columns=["code"])
                    if "end_date" in write_df.columns:
                        write_df = write_df.sort_values("end_date")
                        write_df = write_df.set_index("end_date")
                    writer.write_fundamentals(ptrade_code, write_df)
                    count += 1
                writer.commit()
            except Exception:
                writer.rollback()
                raise

            total_updated += count
            print(f"{count} stocks")

        except Exception as e:
            print(f"ERROR: {e}")

    elapsed = time.time() - t0
    print(f"\nDone: {total_updated} stock-quarters updated in {elapsed:.1f}s")

    # Verify
    result = writer.conn.execute(
        "SELECT COUNT(*) as total, COUNT(a_floats) as has_a_floats "
        "FROM fundamentals WHERE a_floats > 0"
    ).fetchdf()
    print(f"Verification: {result.to_string()}")

    writer.close()


if __name__ == "__main__":
    main()
