"""
Data validators for ensuring data quality before writing to storage

This module provides validation functions to check data integrity,
field completeness, and value ranges before persisting data.
"""

import logging

import pandas as pd

logger = logging.getLogger(__name__)


class DataQualityError(Exception):
    """Raised when data quality validation fails"""
    pass


class MarketDataValidator:
    """Validator for market data (OHLCV)"""

    REQUIRED_FIELDS = ["open", "high", "low", "close", "volume", "money"]

    @staticmethod
    def validate(df: pd.DataFrame, symbol: str, strict: bool = False) -> bool:
        """
        Validate market data quality

        Args:
            df: Market data DataFrame
            symbol: Stock code for logging
            strict: If True, raise exception on validation failure

        Returns:
            True if validation passes

        Raises:
            DataQualityError: If strict=True and validation fails
        """
        if df.empty:
            msg = f"{symbol}: Empty DataFrame"
            if strict:
                raise DataQualityError(msg)
            logger.warning(msg)
            return False

        # 1. Check required fields
        missing = set(MarketDataValidator.REQUIRED_FIELDS) - set(df.columns)
        if missing:
            msg = f"{symbol}: Missing required fields {missing}"
            if strict:
                raise DataQualityError(msg)
            logger.error(msg)
            return False

        # 2. Check index type
        if not isinstance(df.index, pd.DatetimeIndex):
            msg = f"{symbol}: Index must be DatetimeIndex, got {type(df.index).__name__}"
            if strict:
                raise DataQualityError(msg)
            logger.error(msg)
            return False

        # 3. Check for duplicate dates
        if df.index.has_duplicates:
            dups = df.index[df.index.duplicated()].unique()
            msg = f"{symbol}: Duplicate dates in index: {dups.tolist()[:5]}"
            if strict:
                raise DataQualityError(msg)
            logger.error(msg)
            return False

        # 3b. Check dates are monotonically increasing
        if not df.index.is_monotonic_increasing:
            msg = f"{symbol}: Dates are not monotonically increasing"
            if strict:
                raise DataQualityError(msg)
            logger.warning(msg)

        # 4. Value range checks
        issues = []

        # OHLC prices should be positive
        for field in ["open", "high", "low", "close"]:
            invalid_count = (df[field] <= 0).sum()
            if invalid_count:
                issues.append(f"{invalid_count} non-positive {field} prices")

        # High >= Low
        invalid_count = (df["high"] < df["low"]).sum()
        if invalid_count:
            issues.append(f"{invalid_count} rows where high < low")

        # Close should be between high and low
        out_of_range = (df["close"] > df["high"]) | (df["close"] < df["low"])
        invalid_count = out_of_range.sum()
        if invalid_count:
            issues.append(f"{invalid_count} rows where close not in [low, high]")

        # Volume should be non-negative
        invalid_count = (df["volume"] < 0).sum()
        if invalid_count:
            issues.append(f"{invalid_count} negative volume values")

        if issues:
            msg = f"{symbol}: Data range issues: {'; '.join(issues)}"
            if strict:
                raise DataQualityError(msg)
            logger.warning(msg)
            return False

        # 5. Check for excessive NaN values
        nan_pct = df.isna().sum() / len(df) * 100
        high_nan_fields = nan_pct[nan_pct > 10].to_dict()
        if high_nan_fields:
            msg = (
                f"{symbol}: High NaN percentage: "
                f"{', '.join(f'{k}={v:.1f}%' for k, v in high_nan_fields.items())}"
            )
            logger.warning(msg)
            # Don't fail on high NaN, just warn

        logger.debug(f"{symbol}: Market data validation passed")
        return True


class ValuationDataValidator:
    """Validator for valuation data"""

    REQUIRED_FIELDS = ["pe_ttm", "pb", "ps_ttm", "pcf", "turnover_rate"]

    @staticmethod
    def validate(df: pd.DataFrame, symbol: str, strict: bool = False) -> bool:
        """
        Validate valuation data quality

        Args:
            df: Valuation data DataFrame
            symbol: Stock code for logging
            strict: If True, raise exception on validation failure

        Returns:
            True if validation passes
        """
        if df.empty:
            msg = f"{symbol}: Empty valuation DataFrame"
            if strict:
                raise DataQualityError(msg)
            logger.warning(msg)
            return False

        # 1. Check required fields (at least some should exist)
        available = set(ValuationDataValidator.REQUIRED_FIELDS) & set(df.columns)
        if len(available) == 0:
            msg = f"{symbol}: No valuation fields available"
            if strict:
                raise DataQualityError(msg)
            logger.error(msg)
            return False

        # 2. Check index type
        if not isinstance(df.index, pd.DatetimeIndex):
            msg = f"{symbol}: Index must be DatetimeIndex"
            if strict:
                raise DataQualityError(msg)
            logger.error(msg)
            return False

        # 3. Value range checks
        issues = []

        # PE, PB, PS, PCF should generally be positive (negative means loss)
        for field in ["pb", "pcf"]:
            if field in df.columns and (df[field] < 0).any():
                invalid_count = (df[field] < 0).sum()
                issues.append(f"{invalid_count} negative {field} values")

        # Turnover rate should be in reasonable range [0, 100]%
        if "turnover_rate" in df.columns:
            invalid = (df["turnover_rate"] < 0) | (df["turnover_rate"] > 100)
            if invalid.any():
                invalid_count = invalid.sum()
                issues.append(f"{invalid_count} turnover_rate out of [0, 100] range")

        if issues:
            msg = f"{symbol}: Valuation data issues: {'; '.join(issues)}"
            logger.warning(msg)

        # Check for excessive NaN values
        nan_pct = df.isna().sum() / len(df) * 100
        high_nan_fields = nan_pct[nan_pct > 10].to_dict()
        if high_nan_fields:
            logger.warning(
                "%s: High NaN percentage in valuation: %s",
                symbol,
                ", ".join(f"{k}={v:.1f}%" for k, v in high_nan_fields.items()),
            )

        logger.debug(f"{symbol}: Valuation data validation passed")
        return True


class FundamentalDataValidator:
    """Validator for fundamental data (quarterly)"""

    @staticmethod
    def validate(df: pd.DataFrame, symbol: str, strict: bool = False) -> bool:
        """
        Validate fundamental data quality

        Args:
            df: Fundamental data DataFrame
            symbol: Stock code for logging
            strict: If True, raise exception on validation failure

        Returns:
            True if validation passes
        """
        if df.empty:
            msg = f"{symbol}: Empty fundamental DataFrame"
            if strict:
                raise DataQualityError(msg)
            logger.warning(msg)
            return False

        # 1. Check index type
        if not isinstance(df.index, pd.DatetimeIndex):
            msg = f"{symbol}: Index must be DatetimeIndex (end_date)"
            if strict:
                raise DataQualityError(msg)
            logger.error(msg)
            return False

        # 2. Check for duplicate quarters
        if df.index.has_duplicates:
            dups = df.index[df.index.duplicated()].unique()
            msg = f"{symbol}: Duplicate quarters in index: {dups.tolist()}"
            if strict:
                raise DataQualityError(msg)
            logger.error(msg)
            return False

        # 3. Check for at least some non-NaN data
        non_nan_count = df.notna().sum().sum()
        total_count = df.size
        if non_nan_count == 0:
            msg = f"{symbol}: All fundamental data is NaN"
            if strict:
                raise DataQualityError(msg)
            logger.error(msg)
            return False

        # 4. Value range sanity checks
        issues = []
        if "roe" in df.columns:
            roe = pd.to_numeric(df["roe"], errors="coerce")
            extreme = (roe.abs() > 500).sum()
            if extreme:
                issues.append(f"{extreme} rows with |ROE| > 500%")
        if "current_ratio" in df.columns:
            current_ratio = pd.to_numeric(df["current_ratio"], errors="coerce")
            invalid = (current_ratio < 0).sum()
            if invalid:
                issues.append(f"{invalid} rows with negative current_ratio")
        if "debt_equity_ratio" in df.columns:
            debt_equity_ratio = pd.to_numeric(df["debt_equity_ratio"], errors="coerce")
            # Liabilities / equity * 100 can be negative when equity is negative.
            # There is no universal lower bound for this percentage ratio.
            invalid = debt_equity_ratio.isin([float("inf"), float("-inf")]).sum()
            if invalid:
                issues.append(f"{invalid} rows with non-finite debt_equity_ratio")
        if issues:
            logger.warning("%s: Fundamental range issues: %s", symbol, "; ".join(issues))

        # 5. Check percentage of available data
        data_pct = (non_nan_count / total_count) * 100
        if data_pct < 20:
            msg = f"{symbol}: Only {data_pct:.1f}% of fundamental data available"
            logger.warning(msg)

        logger.debug(f"{symbol}: Fundamental data validation passed")
        return True


class ExrightsDataValidator:
    """Validator for ex-rights/dividend data"""

    @staticmethod
    def validate(df: pd.DataFrame, symbol: str, strict: bool = False) -> bool:
        if df.empty:
            return True  # no exrights events is valid (e.g., new IPO)

        if not isinstance(df.index, pd.DatetimeIndex):
            msg = f"{symbol}: Exrights index must be DatetimeIndex"
            if strict:
                raise DataQualityError(msg)
            logger.error(msg)
            return False

        if df.index.has_duplicates:
            dups = df.index[df.index.duplicated()].unique()
            msg = f"{symbol}: Duplicate ex-dates: {dups.tolist()[:5]}"
            if strict:
                raise DataQualityError(msg)
            logger.warning(msg)

        # allotted_ps, rationed_ps, bonus_ps should be >= 0
        for field in ("allotted_ps", "rationed_ps", "bonus_ps"):
            if field in df.columns and (df[field] < 0).any():
                count = (df[field] < 0).sum()
                logger.warning("%s: %d negative %s values", symbol, count, field)

        return True


def validate_before_write(
    data: pd.DataFrame,
    data_type: str,
    symbol: str,
    strict: bool = False
) -> bool:
    """
    Validate data before writing to storage

    Args:
        data: DataFrame to validate
        data_type: Type of data ("market", "valuation", "fundamental")
        symbol: Stock code for logging
        strict: If True, raise exception on validation failure

    Returns:
        True if validation passes

    Raises:
        DataQualityError: If strict=True and validation fails
    """
    if data_type == "market":
        return MarketDataValidator.validate(data, symbol, strict)
    elif data_type == "valuation":
        return ValuationDataValidator.validate(data, symbol, strict)
    elif data_type == "fundamental":
        return FundamentalDataValidator.validate(data, symbol, strict)
    elif data_type == "exrights":
        return ExrightsDataValidator.validate(data, symbol, strict)
    else:
        logger.warning(f"Unknown data type: {data_type}, skipping validation")
        return True
