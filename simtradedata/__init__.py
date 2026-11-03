"""
SimTradeData - Download market data and store in DuckDB with Parquet export

This package fetches data from BaoStock, converts it to PTrade-compatible
format, stores in DuckDB for incremental updates, and exports to Parquet.
"""

__version__ = "1.2.0"

__all__ = [
    "BaoStockFetcher",
    "DuckDBWriter",
]


def __getattr__(name: str):
    if name == "BaoStockFetcher":
        from simtradedata.fetchers.baostock_fetcher import BaoStockFetcher
        return BaoStockFetcher
    if name == "DuckDBWriter":
        from simtradedata.writers.duckdb_writer import DuckDBWriter
        return DuckDBWriter
    raise AttributeError("module 'simtradedata' has no attribute %r" % name)
