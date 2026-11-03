"""
Main entry point for command-line interface

Usage:
    uv run python scripts/download_efficient.py  # Download data
    uv run python scripts/export_parquet.py      # Export to Parquet
"""


def main():
    print("SimTradeData - Download and export stock data")
    print()
    print("Usage:")
    print("  uv run python scripts/download_efficient.py  # Download data")
    print("  uv run python scripts/export_parquet.py      # Export to Parquet")
    print()
    print("Download options:")
    print("  --skip-fundamentals  Skip quarterly financial data")
    print("  --skip-metadata      Skip stock basic info")
    print("  --start-date DATE    Override start date (YYYY-MM-DD)")
    print()
    print("Export options:")
    print("  --db PATH            DuckDB database path")
    print("  --output PATH        Output directory for Parquet files")


if __name__ == "__main__":
    main()
