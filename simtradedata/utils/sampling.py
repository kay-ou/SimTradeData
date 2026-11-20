"""
Unified sampling date generation utilities

Provides consistent date sampling across all data types:
- Stock pool sampling: Monthly (first of month)
- Index constituent sampling: Monthly (end of month)
- Quarterly end dates: For fundamentals progress tracking
"""

from datetime import datetime

import pandas as pd


def generate_monthly_start_dates(start_date: str, end_date: str = None) -> list:
    """Generate monthly sampling dates (first of each month) for stock pool.

    Args:
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD), defaults to today

    Returns:
        List of datetime objects at month starts, plus end_date if not included
    """
    end = end_date or datetime.now().strftime("%Y-%m-%d")
    end_dt = pd.to_datetime(end).to_pydatetime()

    dates = pd.date_range(start=start_date, end=end, freq="MS").to_pydatetime().tolist()

    if end_dt not in dates:
        dates.append(end_dt)

    return dates


def generate_monthly_end_dates(start_date: str, end_date: str = None) -> list:
    """Generate monthly sampling dates (end of each month) for index constituents.

    Args:
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD), defaults to today

    Returns:
        List of datetime objects at month ends
    """
    end = end_date or datetime.now().strftime("%Y-%m-%d")
    return pd.date_range(start=start_date, end=end, freq="ME").to_pydatetime().tolist()


def quarter_end_date(year: int, quarter: int) -> str:
    """Get quarter end date string for a given year and quarter.

    Args:
        year: Year (e.g., 2024)
        quarter: Quarter number (1-4)

    Returns:
        Date string like '2024-03-31'
    """
    if not 1 <= quarter <= 4:
        raise ValueError(f"Quarter must be 1-4, got {quarter}")
    month = quarter * 3
    day = 31 if month in (3, 12) else 30
    return f"{year}-{month:02d}-{day:02d}"
