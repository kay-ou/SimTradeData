"""SmartRouter: unified data access layer with automatic source selection."""

import importlib
import logging

import pandas as pd

from simtradedata.router.exceptions import DataSourceError, NoSourceAvailable
from simtradedata.router.route_config import DEFAULT_ROUTE_TABLE, FETCHER_REGISTRY

logger = logging.getLogger(__name__)

_MARKET_SUFFIXES = {
    "SS": "cn",
    "SZ": "cn",
    "BJ": "cn",
    "US": "us",
}


class SmartRouter:
    """Unified data access layer with automatic source selection and fallback."""

    def __init__(self, config: dict = None):
        self._config = config or DEFAULT_ROUTE_TABLE
        self._fetchers: dict = {}

    # -- Lifecycle --

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        for name, fetcher in self._fetchers.items():
            try:
                fetcher.logout()
            except Exception as e:
                logger.warning("Failed to logout %s: %s", name, e)
        self._fetchers.clear()
        return False

    # -- Internal helpers --

    @staticmethod
    def _detect_market(symbol: str) -> str:
        """Detect market from PTrade symbol suffix.

        Args:
            symbol: PTrade format code (e.g. '600000.SS', 'AAPL.US').

        Returns:
            Market identifier: 'cn' or 'us'.

        Raises:
            ValueError: If the suffix is not recognized.
        """
        suffix = symbol.rsplit(".", 1)[-1]
        market = _MARKET_SUFFIXES.get(suffix)
        if market is None:
            raise ValueError(f"Unknown market suffix: .{suffix}")
        return market

    def _resolve_sources(self, data_type: str, market: str) -> list[str]:
        """Look up the prioritized source list for a data_type and market.

        Raises:
            NoSourceAvailable: If no sources are configured.
        """
        routes = self._config.get(data_type, {})
        sources = routes.get(market)
        if not sources:
            raise NoSourceAvailable(
                f"No source configured for {data_type}/{market}"
            )
        return sources

    def _get_fetcher(self, source_name: str):
        """Return a cached fetcher instance, creating it on first access."""
        if source_name not in self._fetchers:
            cls_path = FETCHER_REGISTRY.get(source_name)
            if cls_path is None:
                raise NoSourceAvailable(
                    f"No fetcher registered for '{source_name}'"
                )
            module_path, cls_name = cls_path.rsplit(".", 1)
            module = importlib.import_module(module_path)
            cls = getattr(module, cls_name)
            fetcher = cls()
            fetcher.login()
            self._fetchers[source_name] = fetcher
        return self._fetchers[source_name]

    @staticmethod
    def _is_source_healthy(fetcher) -> bool:
        """Check circuit breaker health, tolerating fetchers without one."""
        cb = getattr(fetcher, "_circuit_breaker", None)
        if cb is None:
            # MootdxUnifiedFetcher wraps an inner fetcher; check it
            inner = getattr(fetcher, "_quotes_fetcher", None)
            if inner is not None:
                cb = getattr(inner, "_circuit_breaker", None)
        if cb is None:
            return True
        return cb.is_available()

    # -- Core fallback mechanism --

    def _try_fetch(self, data_type, fetch_from, symbol=None, market=None):
        """Try each source in priority order, falling back on failure.

        Args:
            data_type: Route table key (e.g. 'daily_bars').
            fetch_from: Callable(fetcher, source_name) -> pd.DataFrame.
            symbol: PTrade symbol for auto market detection.
            market: Explicit market override (used when no symbol).

        Returns:
            DataFrame from the first successful source.

        Raises:
            DataSourceError: If all sources fail with exceptions.
            NoSourceAvailable: If no sources are configured.
        """
        if market is None:
            if symbol is not None:
                market = self._detect_market(symbol)
            else:
                market = "cn"

        sources = self._resolve_sources(data_type, market)
        errors = []
        skipped_unhealthy = []

        for source_name in sources:
            fetcher = self._get_fetcher(source_name)

            if not self._is_source_healthy(fetcher):
                logger.debug("Skipping %s: circuit breaker open", source_name)
                skipped_unhealthy.append(source_name)
                continue

            try:
                result = fetch_from(fetcher, source_name)
                if result is not None and not result.empty:
                    logger.info(
                        "Fetched %s from %s (%d rows)",
                        data_type, source_name, len(result),
                    )
                    return result
                logger.debug(
                    "Empty result from %s for %s", source_name, data_type,
                )
            except Exception as e:
                logger.warning(
                    "Failed to fetch %s from %s: %s",
                    data_type, source_name, e,
                )
                errors.append((source_name, e))

        if errors:
            detail = "; ".join(f"{s}: {e}" for s, e in errors)
            raise DataSourceError(
                f"All sources failed for {data_type}: {detail}"
            ) from errors[-1][1]
        if skipped_unhealthy and not errors:
            raise DataSourceError(
                f"All sources skipped for {data_type}: "
                f"{', '.join(skipped_unhealthy)} (circuit breaker open)"
            )
        return pd.DataFrame()

    # -- Public API --

    def get_daily_bars(self, symbol, start_date, end_date):
        """Fetch daily OHLCV bars with automatic source selection."""
        standard_cols = [
            "date", "open", "high", "low", "close", "volume", "amount",
        ]

        def fetch_from(fetcher, source_name):
            if source_name == "mootdx":
                df = fetcher.fetch_daily_data(symbol, start_date, end_date)
            elif source_name == "eastmoney":
                df = fetcher.fetch_daily_bars(symbol, start_date, end_date)
            elif source_name == "baostock":
                df = fetcher.fetch_unified_daily_data(
                    symbol, start_date, end_date,
                )
            elif source_name == "yfinance":
                result, _ = fetcher.fetch_batch_ohlcv(
                    [symbol], start_date, end_date,
                )
                df = result.get(symbol, pd.DataFrame())
            else:
                raise ValueError(f"Unknown source for daily_bars: {source_name}")
            if df is None or df.empty:
                return pd.DataFrame()
            # Normalize to standard OHLCV columns
            available = [c for c in standard_cols if c in df.columns]
            return df[available]

        return self._try_fetch("daily_bars", fetch_from, symbol=symbol)

    def get_xdxr(self, symbol):
        """Fetch ex-dividend/ex-rights data."""

        def fetch_from(fetcher, source_name):
            return fetcher.fetch_xdxr(symbol)

        return self._try_fetch("xdxr", fetch_from, symbol=symbol)

    def get_money_flow(self, symbol, start_date, end_date):
        """Fetch daily money flow data."""

        def fetch_from(fetcher, source_name):
            return fetcher.fetch_money_flow(symbol, start_date, end_date)

        return self._try_fetch("money_flow", fetch_from, symbol=symbol)

    def get_lhb(self, start_date, end_date):
        """Fetch Dragon Tiger Board records."""

        def fetch_from(fetcher, source_name):
            return fetcher.fetch_lhb(start_date, end_date)

        return self._try_fetch("lhb", fetch_from, market="cn")

    def get_margin(self, symbol, start_date, end_date):
        """Fetch margin trading data."""

        def fetch_from(fetcher, source_name):
            return fetcher.fetch_margin(symbol, start_date, end_date)

        return self._try_fetch("margin", fetch_from, symbol=symbol)

    def get_stock_list(self, market="cn"):
        """Fetch stock list for the given market."""

        def fetch_from(fetcher, source_name):
            result = fetcher.fetch_stock_list()
            if isinstance(result, pd.DataFrame):
                return result
            # Wrap list in DataFrame so _try_fetch empty-check works
            if isinstance(result, list) and result:
                return pd.DataFrame({"symbol": result})
            return pd.DataFrame()

        df = self._try_fetch("stock_list", fetch_from, market=market)
        if df.empty:
            return []
        if "symbol" in df.columns:
            return df["symbol"].tolist()
        return df

    def get_trade_calendar(self, start_date, end_date):
        """Fetch trading calendar."""

        def fetch_from(fetcher, source_name):
            return fetcher.fetch_trade_calendar(start_date, end_date)

        return self._try_fetch("trade_calendar", fetch_from, market="cn")

    def get_index_data(self, symbol, start_date, end_date):
        """Fetch index OHLCV data."""

        def fetch_from(fetcher, source_name):
            return fetcher.fetch_index_data(symbol, start_date, end_date)

        return self._try_fetch("index_data", fetch_from, symbol=symbol)

    def get_realtime_quotes(self, symbols):
        """Fetch real-time quotes for multiple stocks."""

        def fetch_from(fetcher, source_name):
            return fetcher.fetch_realtime_quotes(symbols)

        return self._try_fetch("realtime_quotes", fetch_from, market="cn")

    def get_snapshot(self, security):
        """Confirmed PTrade Level-1 subset; unavailable fields are omitted."""
        from simtradedata.fetchers.tdx_snapshot import snapshot_from_quotes

        symbols = [security] if isinstance(security, str) else list(security)
        if not symbols:
            return {}
        return snapshot_from_quotes(self.get_realtime_quotes(symbols))

    def get_gear_price(self, sids):
        """Five levels; single string returns the book, lists return keyed books."""
        snapshots = self.get_snapshot(sids)
        books = {
            symbol: {side: {level: values[:3] for level, values in snapshot[side].items()}
                     for side in ["bid_grp", "offer_grp"]}
            for symbol, snapshot in snapshots.items()
        }
        if isinstance(sids, str):
            return books.get(sids, {"bid_grp": {}, "offer_grp": {}})
        return books

    def get_blocks(self, filename="block.dat"):
        """Current TDX classification; does not claim PTrade XBHS equivalence."""
        return self._try_fetch("blocks", lambda fetcher, _: fetcher.fetch_blocks(filename), market="cn")

    def get_f10_catalog(self, symbol):
        return self._try_fetch("f10", lambda fetcher, _: fetcher.fetch_f10_catalog(symbol), symbol=symbol)

    def get_f10_detail(self, symbol, name):
        def fetch_from(fetcher, _):
            text = fetcher.fetch_f10_detail(symbol, name)
            return pd.DataFrame({"text": [text]}) if text else pd.DataFrame()

        result = self._try_fetch("f10", fetch_from, symbol=symbol)
        return None if result.empty else result.iloc[0]["text"]

    def get_history_transactions(self, symbol, date, start=0, offset=800):
        return self._try_fetch(
            "history_transactions",
            lambda fetcher, _: fetcher.fetch_history_transactions(symbol, date, start, offset),
            symbol=symbol,
        )

    def get_history_minutes(self, symbol, date):
        return self._try_fetch(
            "history_minutes", lambda fetcher, _: fetcher.fetch_history_minutes(symbol, date),
            symbol=symbol,
        )

    def get_minute_bars(self, symbol, frequency=0, offset=800):
        """Fetch minute-level K-line data.

        Args:
            symbol: PTrade format code.
            frequency: Bar frequency (0=5m, 1=15m, 2=30m, 3=1h, 7=1m).
            offset: Number of bars to fetch (max 800).
        """

        def fetch_from(fetcher, source_name):
            return fetcher.fetch_minute_bars(symbol, frequency, offset)

        return self._try_fetch("minute_bars", fetch_from, symbol=symbol)

    def get_fundamentals(self, symbol=None, year=None, quarter=None):
        """Fetch financial fundamentals.

        Two access patterns:
        - Batch quarterly (A-share): get_fundamentals(year=2024, quarter=1)
        - Per-stock (US): get_fundamentals(symbol='AAPL.US')
        """
        if year is not None and quarter is not None:
            market = "cn" if symbol is None else self._detect_market(symbol)

            def fetch_from(fetcher, source_name):
                return fetcher.fetch_fundamentals_for_quarter(year, quarter)

            return self._try_fetch("fundamentals", fetch_from, market=market)

        if symbol is not None:
            def fetch_from(fetcher, source_name):
                return fetcher.fetch_fundamentals(symbol)

            return self._try_fetch("fundamentals", fetch_from, symbol=symbol)

        raise ValueError("Provide (year, quarter) or symbol")

    def get_valuation(self, symbol, start_date, end_date):
        """Fetch valuation metrics (PE, PB, PS, etc.)."""
        valuation_cols = [
            "date", "peTTM", "pbMRQ", "psTTM", "pcfNcfTTM", "turn",
        ]

        def fetch_from(fetcher, source_name):
            if source_name == "baostock":
                df = fetcher.fetch_unified_daily_data(
                    symbol, start_date, end_date,
                )
                if df.empty:
                    return df
                available = [c for c in valuation_cols if c in df.columns]
                return df[available]
            elif source_name == "yfinance":
                ohlcv_result, _ = fetcher.fetch_batch_ohlcv(
                    [symbol], start_date, end_date,
                )
                ohlcv_df = ohlcv_result.get(symbol, pd.DataFrame())
                if ohlcv_df.empty:
                    return pd.DataFrame()
                return fetcher.fetch_valuation_data(symbol, ohlcv_df)
            raise ValueError(f"valuation not supported by {source_name}")

        return self._try_fetch("valuation", fetch_from, symbol=symbol)
