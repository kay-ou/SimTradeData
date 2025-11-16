"""
HDF5 writer for PTrade-compatible format
"""

import logging
import warnings
from pathlib import Path
from typing import Dict, List

import pandas as pd
from tables import NaturalNameWarning

warnings.filterwarnings("ignore", category=NaturalNameWarning)

logger = logging.getLogger(__name__)


class HDF5Writer:
    """
    Write data to HDF5 files in PTrade-compatible format

    This handles writing data to:
    - ptrade_data.h5: market data, exrights, metadata
    - ptrade_fundamentals.h5: fundamentals, valuation
    - ptrade_adj_pre.h5: adjust factors
    - ptrade_dividend_cache.h5: dividend cache (optional)
    """

    def __init__(self, output_dir: str = "data"):
        """
        Initialize HDF5 writer

        Args:
            output_dir: Directory to save HDF5 files
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # File paths
        self.ptrade_data_path = self.output_dir / "ptrade_data.h5"
        self.ptrade_fundamentals_path = self.output_dir / "ptrade_fundamentals.h5"
        self.ptrade_adj_pre_path = self.output_dir / "ptrade_adj_pre.h5"
        self.ptrade_dividend_cache_path = self.output_dir / "ptrade_dividend_cache.h5"

    def write_market_data(
        self, symbol: str, data: pd.DataFrame, mode: str = "a"
    ) -> None:
        """
        Write market data to ptrade_data.h5/stock_data/{symbol}

        Args:
            symbol: Stock code in PTrade format (e.g., '000001.SZ')
            data: DataFrame with columns [open, high, low, close, volume, money]
            mode: 'a' for append, 'w' for overwrite
        """
        if data.empty:
            logger.warning(f"No data to write for {symbol}")
            return

        # Ensure datetime index
        if not isinstance(data.index, pd.DatetimeIndex):
            data.index = pd.to_datetime(data.index)

        key = f"stock_data/{symbol}"

        with pd.HDFStore(self.ptrade_data_path, mode=mode) as store:
            store.put(
                key,
                data,
                format="table",
                data_columns=True,
                complevel=9,
                complib="blosc",
            )

        logger.info(
            f"Wrote market data for {symbol} to {self.ptrade_data_path}: "
            f"{len(data)} rows"
        )

    def write_benchmark(self, data: pd.DataFrame, mode: str = "a") -> None:
        """
        Write benchmark index data to ptrade_data.h5/benchmark

        Args:
            data: DataFrame with columns [open, high, low, close, volume, money]
            mode: 'a' for append, 'w' for overwrite
        """
        if data.empty:
            logger.warning("No benchmark data to write")
            return

        if not isinstance(data.index, pd.DatetimeIndex):
            data.index = pd.to_datetime(data.index)

        with pd.HDFStore(self.ptrade_data_path, mode=mode) as store:
            store.put(
                "benchmark",
                data,
                format="table",
                data_columns=True,
                complevel=9,
                complib="blosc",
            )

        logger.info(
            f"Wrote benchmark data to {self.ptrade_data_path}: {len(data)} rows"
        )

    def write_exrights(self, symbol: str, data: pd.DataFrame, mode: str = "a") -> None:
        """
        Write exrights data to ptrade_data.h5/exrights/{symbol}

        Args:
            symbol: Stock code in PTrade format
            data: DataFrame with exrights fields
            mode: 'a' for append, 'w' for overwrite
        """
        if data.empty:
            logger.warning(f"No exrights data to write for {symbol}")
            return

        key = f"exrights/{symbol}"

        with pd.HDFStore(self.ptrade_data_path, mode=mode) as store:
            store.put(
                key,
                data,
                format="table",
                data_columns=True,
                complevel=9,
                complib="blosc",
            )

        logger.info(
            f"Wrote exrights data for {symbol} to {self.ptrade_data_path}: "
            f"{len(data)} rows"
        )

    def write_stock_metadata(self, metadata_df: pd.DataFrame, mode: str = "a") -> None:
        """
        Write stock metadata to ptrade_data.h5/stock_metadata

        Args:
            metadata_df: DataFrame with columns [blocks, de_listed_date, has_info, listed_date, stock_name]
                        and stock_code as index
            mode: 'a' for append, 'w' for overwrite
        """
        if metadata_df.empty:
            logger.warning("No stock metadata to write")
            return

        with pd.HDFStore(self.ptrade_data_path, mode=mode) as store:
            store.put(
                "stock_metadata",
                metadata_df,
                format="table",
                data_columns=True,
                complevel=9,
                complib="blosc",
                min_itemsize={"stock_name": 100},
            )

        logger.info(
            f"Wrote stock metadata to {self.ptrade_data_path}: "
            f"{len(metadata_df)} stocks"
        )

    def write_fundamentals(
        self, symbol: str, data: pd.DataFrame, mode: str = "a"
    ) -> None:
        """
        Write fundamental data to ptrade_fundamentals.h5/fundamentals/{symbol}

        Args:
            symbol: Stock code in PTrade format
            data: DataFrame with 23 fundamental indicators
            mode: 'a' for append, 'w' for overwrite
        """
        if data.empty:
            logger.warning(f"No fundamentals data to write for {symbol}")
            return

        if not isinstance(data.index, pd.DatetimeIndex):
            data.index = pd.to_datetime(data.index)

        key = f"fundamentals/{symbol}"

        with pd.HDFStore(self.ptrade_fundamentals_path, mode=mode) as store:
            store.put(
                key,
                data,
                format="table",
                data_columns=True,
                complevel=9,
                complib="blosc",
            )

        logger.info(
            f"Wrote fundamentals for {symbol} to {self.ptrade_fundamentals_path}: "
            f"{len(data)} quarters"
        )

    def write_valuation(self, symbol: str, data: pd.DataFrame, mode: str = "a") -> None:
        """
        Write valuation data to ptrade_fundamentals.h5/valuation/{symbol}

        Args:
            symbol: Stock code in PTrade format
            data: DataFrame with valuation indicators
            mode: 'a' for append, 'w' for overwrite
        """
        if data.empty:
            logger.warning(f"No valuation data to write for {symbol}")
            return

        if not isinstance(data.index, pd.DatetimeIndex):
            data.index = pd.to_datetime(data.index)

        key = f"valuation/{symbol}"

        with pd.HDFStore(self.ptrade_fundamentals_path, mode=mode) as store:
            store.put(
                key,
                data,
                format="table",
                data_columns=True,
                complevel=9,
                complib="blosc",
            )

        logger.info(
            f"Wrote valuation for {symbol} to {self.ptrade_fundamentals_path}: "
            f"{len(data)} days"
        )

    def write_adjust_factor(
        self, symbol: str, data: pd.Series, mode: str = "a"
    ) -> None:
        """
        Write adjust factor to ptrade_adj_pre.h5/{symbol}

        Args:
            symbol: Stock code in PTrade format
            data: Series with backward adjust factor
            mode: 'a' for append, 'w' for overwrite
        """
        if data.empty:
            logger.warning(f"No adjust factor data to write for {symbol}")
            return

        if not isinstance(data.index, pd.DatetimeIndex):
            data.index = pd.to_datetime(data.index)

        # Ensure Series name is 'backward_a'
        data.name = "backward_a"

        with pd.HDFStore(self.ptrade_adj_pre_path, mode=mode) as store:
            store.put(
                symbol,
                data,
                format="table",
                complevel=9,
                complib="blosc",
            )

        logger.info(
            f"Wrote adjust factor for {symbol} to {self.ptrade_adj_pre_path}: "
            f"{len(data)} days"
        )

    def write_all_for_stock(
        self,
        symbol: str,
        market_data: pd.DataFrame = None,
        valuation_data: pd.DataFrame = None,
        fundamentals_data: pd.DataFrame = None,
        adjust_factor: pd.Series = None,
        exrights_data: pd.DataFrame = None,
        metadata: Dict = None,
    ) -> None:
        """
        Write all data types for a single stock

        This is a convenience method to write all data in one call.
        Optimized to minimize file open/close operations.

        Args:
            symbol: Stock code in PTrade format
            market_data: Market OHLCV data
            valuation_data: Valuation indicators
            fundamentals_data: Fundamental financial data
            adjust_factor: Adjust factor series
            exrights_data: Exrights/dividend data
            metadata: Stock metadata dict
        """
        # Write to ptrade_data.h5 (market, exrights, metadata) in one session
        has_ptrade_data = (
            (market_data is not None and not market_data.empty)
            or (exrights_data is not None and not exrights_data.empty)
            or metadata
        )

        if has_ptrade_data:
            with pd.HDFStore(self.ptrade_data_path, mode="a") as store:
                # Write market data
                if market_data is not None and not market_data.empty:
                    market_data = market_data.copy()
                    if not isinstance(market_data.index, pd.DatetimeIndex):
                        market_data.index = pd.to_datetime(market_data.index)
                    key = f"stock_data/{symbol}"
                    store.put(
                        key,
                        market_data,
                        format="table",
                        data_columns=True,
                        complevel=9,
                        complib="blosc",
                    )

                # Write exrights data
                if exrights_data is not None and not exrights_data.empty:
                    exrights_data = exrights_data.copy()
                    if not isinstance(exrights_data.index, pd.DatetimeIndex):
                        exrights_data.index = pd.to_datetime(exrights_data.index)
                    key = f"exrights/{symbol}"
                    store.put(
                        key,
                        exrights_data,
                        format="table",
                        data_columns=True,
                        complevel=9,
                        complib="blosc",
                    )

                # Write metadata
                if metadata:
                    metadata_df = pd.DataFrame([metadata], index=[symbol])
                    metadata_df.index.name = "stock_code"
                    store.put(
                        "stock_metadata",
                        metadata_df,
                        format="table",
                        data_columns=True,
                        append=True,
                        min_itemsize={"stock_name": 100},
                    )

        # Write to ptrade_fundamentals.h5 (valuation, fundamentals) in one session
        has_fundamentals = (
            valuation_data is not None and not valuation_data.empty
        ) or (fundamentals_data is not None and not fundamentals_data.empty)

        if has_fundamentals:
            with pd.HDFStore(self.ptrade_fundamentals_path, mode="a") as store:
                # Write valuation data
                if valuation_data is not None and not valuation_data.empty:
                    valuation_data = valuation_data.copy()
                    if not isinstance(valuation_data.index, pd.DatetimeIndex):
                        valuation_data.index = pd.to_datetime(valuation_data.index)
                    key = f"valuation/{symbol}"
                    store.put(
                        key,
                        valuation_data,
                        format="table",
                        data_columns=True,
                        complevel=9,
                        complib="blosc",
                    )

                # Write fundamentals data
                if fundamentals_data is not None and not fundamentals_data.empty:
                    fundamentals_data = fundamentals_data.copy()
                    if not isinstance(fundamentals_data.index, pd.DatetimeIndex):
                        fundamentals_data.index = pd.to_datetime(
                            fundamentals_data.index
                        )
                    key = f"fundamentals/{symbol}"
                    store.put(
                        key,
                        fundamentals_data,
                        format="table",
                        data_columns=True,
                        complevel=9,
                        complib="blosc",
                    )

        # Write to ptrade_adj_pre.h5 (adjust factor)
        if adjust_factor is not None and not adjust_factor.empty:
            adjust_factor = adjust_factor.copy()
            if not isinstance(adjust_factor.index, pd.DatetimeIndex):
                adjust_factor.index = pd.to_datetime(adjust_factor.index)
            adjust_factor.name = "backward_a"

            with pd.HDFStore(self.ptrade_adj_pre_path, mode="a") as store:
                store.put(
                    symbol,
                    adjust_factor,
                    format="table",
                    complevel=9,
                    complib="blosc:zstd",
                )

        logger.info(f"Wrote all data for {symbol}")

    def get_existing_stocks(self, file_type: str = "market") -> List[str]:
        """
        Get list of stocks already in HDF5 file

        Args:
            file_type: 'market', 'fundamentals', or 'adjust'

        Returns:
            List of stock codes
        """
        file_map = {
            "market": self.ptrade_data_path,
            "fundamentals": self.ptrade_fundamentals_path,
            "adjust": self.ptrade_adj_pre_path,
        }

        filepath = file_map.get(file_type)
        if not filepath or not filepath.exists():
            return []

        try:
            with pd.HDFStore(filepath, mode="r") as store:
                keys = store.keys()

                if file_type == "market":
                    # Extract stock codes from /stock_data/* keys
                    stocks = [
                        k.split("/")[-1] for k in keys if k.startswith("/stock_data/")
                    ]
                elif file_type == "fundamentals":
                    # Extract from /fundamentals/* or /valuation/* keys
                    stocks = [
                        k.split("/")[-1]
                        for k in keys
                        if k.startswith("/fundamentals/") or k.startswith("/valuation/")
                    ]
                else:
                    # For adjust factor, keys are stock codes directly
                    stocks = [k.lstrip("/") for k in keys]

                return list(set(stocks))
        except Exception as e:
            logger.error(f"Error reading existing stocks from {filepath}: {e}")
            return []

    def check_file_integrity(self, file_type: str = "market") -> bool:
        """
        Check if HDF5 file is valid and readable

        Args:
            file_type: 'market', 'fundamentals', or 'adjust'

        Returns:
            True if file is valid, False otherwise
        """
        file_map = {
            "market": self.ptrade_data_path,
            "fundamentals": self.ptrade_fundamentals_path,
            "adjust": self.ptrade_adj_pre_path,
        }

        filepath = file_map.get(file_type)
        if not filepath or not filepath.exists():
            return False

        try:
            with pd.HDFStore(filepath, mode="r") as store:
                keys = store.keys()
                return len(keys) > 0
        except Exception as e:
            logger.error(f"File integrity check failed for {filepath}: {e}")
            return False
