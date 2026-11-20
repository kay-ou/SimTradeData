"""
TTM (Trailing Twelve Months) indicator calculator

This module provides utilities to calculate TTM indicators from quarterly data.
"""

import logging
import pandas as pd

from simtradedata.utils.sampling import quarter_end_date

logger = logging.getLogger(__name__)


def calculate_ttm_indicators(
    df: pd.DataFrame, periods: int = 4
) -> pd.DataFrame:
    """
    Calculate TTM (Trailing Twelve Months) indicators
    
    Args:
        df: DataFrame with quarterly data, must have 'end_date' column
        periods: Number of periods for rolling calculation (default 4 for TTM)
    
    Returns:
        DataFrame with TTM fields added
    """
    if df.empty:
        logger.warning("Empty DataFrame provided to calculate_ttm_indicators")
        return df
    
    if len(df) < periods:
        logger.warning(
            f"Not enough data for TTM calculation (need {periods} periods, got {len(df)})"
        )
        # Still return the dataframe with NaN for TTM fields
    
    # Make a copy to avoid modifying original
    result = df.copy()
    
    # Ensure sorted by end_date (handle both column and index cases)
    if 'end_date' in result.columns:
        result = result.sort_values('end_date')
    elif result.index.name == 'end_date' or isinstance(result.index, pd.DatetimeIndex):
        result = result.sort_index()
    
    # Calculate TTM for ratio fields (use rolling mean)
    for field in ('roe', 'roa', 'net_profit_ratio', 'gross_income_ratio'):
        if field in result.columns:
            result[f'{field}_ttm'] = result[field].rolling(
                window=periods, min_periods=periods
            ).mean()
    
    # Note: roa_ebit_ttm and roic require additional data not available from these APIs
    # These will be left as NaN for now
    
    logger.debug(f"Calculated TTM indicators for {len(result)} rows")
    return result


def get_quarters_in_range(start_date: str, end_date: str) -> list:
    """
    Get list of (year, quarter) tuples in date range
    
    Args:
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
    
    Returns:
        List of (year, quarter) tuples
    """
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)
    
    quarters = []
    
    # Start from the quarter of start_date
    year = start.year
    quarter = (start.month - 1) // 3 + 1
    
    while True:
        # Quarter end date
        q_end = pd.Timestamp(quarter_end_date(year, quarter))
        
        if q_end > end:
            break
        
        quarters.append((year, quarter))
        
        # Next quarter
        quarter += 1
        if quarter > 4:
            quarter = 1
            year += 1
    
    return quarters
