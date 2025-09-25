"""
Standardized WIB (Western Indonesia Time) timezone utilities for TrackEco backend.
This module ensures consistent timezone handling across all backend services.
"""

import datetime
import pytz
from typing import Union, Optional

# WIB timezone constant
WIB_TZ = pytz.timezone('Asia/Jakarta')

def get_current_wib_datetime() -> datetime.datetime:
    """
    Get current datetime in WIB timezone.
    
    Returns:
        datetime.datetime: Current datetime in WIB timezone
    """
    return datetime.datetime.now(WIB_TZ)

def get_current_wib_date() -> datetime.date:
    """
    Get current date in WIB timezone.
    
    Returns:
        datetime.date: Current date in WIB timezone
    """
    return get_current_wib_datetime().date()

def convert_to_wib(dt: Union[datetime.datetime, datetime.date]) -> datetime.datetime:
    """
    Convert a datetime or date to WIB timezone.
    
    Args:
        dt: datetime or date object to convert
        
    Returns:
        datetime.datetime: Converted datetime in WIB timezone
    """
    if isinstance(dt, datetime.date) and not isinstance(dt, datetime.datetime):
        dt = datetime.datetime.combine(dt, datetime.time.min)
    
    if dt.tzinfo is None:
        dt = WIB_TZ.localize(dt)
    else:
        dt = dt.astimezone(WIB_TZ)
    
    return dt

def get_wib_start_of_day(dt: Optional[datetime.datetime] = None) -> datetime.datetime:
    """
    Get start of day (00:00:00) in WIB timezone for the given datetime.
    
    Args:
        dt: Datetime to get start of day for (defaults to current time)
        
    Returns:
        datetime.datetime: Start of day in WIB timezone
    """
    if dt is None:
        dt = get_current_wib_datetime()
    else:
        dt = convert_to_wib(dt)
    
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)

def get_wib_end_of_day(dt: Optional[datetime.datetime] = None) -> datetime.datetime:
    """
    Get end of day (23:59:59) in WIB timezone for the given datetime.
    
    Args:
        dt: Datetime to get end of day for (defaults to current time)
        
    Returns:
        datetime.datetime: End of day in WIB timezone
    """
    if dt is None:
        dt = get_current_wib_datetime()
    else:
        dt = convert_to_wib(dt)
    
    return dt.replace(hour=23, minute=59, second=59, microsecond=999999)

def format_wib_datetime(dt: datetime.datetime, format_str: str = "%Y-%m-%d %H:%M:%S %Z") -> str:
    """
    Format a datetime as a string with WIB timezone.
    
    Args:
        dt: Datetime to format
        format_str: Format string (default: ISO format with timezone)
        
    Returns:
        str: Formatted datetime string
    """
    dt_wib = convert_to_wib(dt)
    return dt_wib.strftime(format_str)

def is_same_wib_day(dt1: datetime.datetime, dt2: datetime.datetime) -> bool:
    """
    Check if two datetimes fall on the same day in WIB timezone.
    
    Args:
        dt1: First datetime
        dt2: Second datetime
        
    Returns:
        bool: True if same day in WIB, False otherwise
    """
    dt1_wib = convert_to_wib(dt1).date()
    dt2_wib = convert_to_wib(dt2).date()
    return dt1_wib == dt2_wib

def is_consecutive_wib_days(dt1: datetime.datetime, dt2: datetime.datetime) -> bool:
    """
    Check if two datetimes are consecutive days in WIB timezone.
    
    Args:
        dt1: First datetime
        dt2: Second datetime
        
    Returns:
        bool: True if consecutive days in WIB, False otherwise
    """
    dt1_wib = convert_to_wib(dt1).date()
    dt2_wib = convert_to_wib(dt2).date()
    return abs((dt1_wib - dt2_wib).days) == 1

def get_wib_week_start(dt: Optional[datetime.datetime] = None) -> datetime.datetime:
    """
    Get start of week (Monday) in WIB timezone.
    
    Args:
        dt: Datetime to get week start for (defaults to current time)
        
    Returns:
        datetime.datetime: Start of week in WIB timezone
    """
    if dt is None:
        dt = get_current_wib_datetime()
    else:
        dt = convert_to_wib(dt)
    
    # Monday is day 0 in Python weekday (0=Monday, 6=Sunday)
    days_since_monday = dt.weekday()
    return (dt - datetime.timedelta(days=days_since_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

def get_wib_month_end(dt: Optional[datetime.datetime] = None) -> datetime.datetime:
    """
    Get end of month in WIB timezone.
    
    Args:
        dt: Datetime to get month end for (defaults to current time)
        
    Returns:
        datetime.datetime: End of month in WIB timezone
    """
    if dt is None:
        dt = get_current_wib_datetime()
    else:
        dt = convert_to_wib(dt)
    
    # Get first day of next month, then subtract one day
    next_month = dt.replace(day=28) + datetime.timedelta(days=4)
    end_of_month = next_month - datetime.timedelta(days=next_month.day)
    return end_of_month.replace(hour=23, minute=59, second=59, microsecond=999999)

# Export the main timezone constant for easy import
__all__ = [
    'WIB_TZ',
    'get_current_wib_datetime',
    'get_current_wib_date',
    'convert_to_wib',
    'get_wib_start_of_day',
    'get_wib_end_of_day',
    'format_wib_datetime',
    'is_same_wib_day',
    'is_consecutive_wib_days',
    'get_wib_week_start',
    'get_wib_month_end'
]