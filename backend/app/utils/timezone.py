"""
Timezone utilities for WatchBuddy.
Provides consistent UTC datetime handling and timezone-aware operations.
"""
from datetime import datetime, timezone
from typing import Optional


def utc_now() -> datetime:
    """
    Get current UTC datetime with timezone info.
    Replacement for deprecated datetime.utcnow().
    """
    return datetime.now(timezone.utc)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """
    Ensure a datetime is UTC and timezone-aware.
    If timezone-naive, assumes it's already UTC and adds UTC timezone.
    If timezone-aware, converts to UTC.
    """
    if dt is None:
        return None
    
    if dt.tzinfo is None:
        # Assume naive datetime is already UTC
        return dt.replace(tzinfo=timezone.utc)
    else:
        # Convert to UTC if not already
        return dt.astimezone(timezone.utc)


def get_user_local_time(user_timezone: str = "UTC") -> datetime:
    """
    Get current time in user's timezone.
    For now defaults to UTC, but can be extended for user preference storage.
    """
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(user_timezone)
        return datetime.now(tz)
    except (ImportError, Exception):
        # Fallback to UTC if zoneinfo not available or timezone invalid
        return utc_now()


def get_user_hour(user_timezone: str = "UTC") -> int:
    """
    Get current hour in user's timezone (0-23).
    Used for contextual mood adjustments.
    """
    local_time = get_user_local_time(user_timezone)
    return local_time.hour


def get_user_weekday(user_timezone: str = "UTC") -> int:
    """
    Get current weekday in user's timezone (0=Monday, 6=Sunday).
    Used for contextual mood adjustments.
    """
    local_time = get_user_local_time(user_timezone)
    return local_time.weekday()


def safe_datetime_diff_days(dt1: Optional[datetime], dt2: Optional[datetime]) -> float:
    """
    Safely calculate difference between two datetimes in days.
    Handles timezone-naive and timezone-aware datetimes consistently.
    Returns 0.0 if either datetime is None.
    """
    if dt1 is None or dt2 is None:
        return 0.0
    
    # Ensure both are timezone-aware UTC
    dt1_utc = ensure_utc(dt1)
    dt2_utc = ensure_utc(dt2)
    
    if dt1_utc is None or dt2_utc is None:
        return 0.0
    
    diff = dt1_utc - dt2_utc
    return diff.total_seconds() / (24 * 3600)  # Convert to days


def format_iso_utc(dt: Optional[datetime]) -> str:
    """
    Format datetime as ISO string in UTC.
    Returns empty string if datetime is None.
    """
    if dt is None:
        return ""
    
    utc_dt = ensure_utc(dt)
    if utc_dt is None:
        return ""
    
    return utc_dt.isoformat()


def convert_utc_to_user_timezone(dt: Optional[datetime], user_timezone: str = "UTC") -> Optional[datetime]:
    """
    Convert UTC datetime to user's timezone.
    Returns None if input is None.
    """
    if dt is None:
        return None
    
    try:
        import zoneinfo
        utc_dt = ensure_utc(dt)
        if utc_dt is None:
            return None
        
        target_tz = zoneinfo.ZoneInfo(user_timezone)
        return utc_dt.astimezone(target_tz)
    except (ImportError, Exception):
        return dt  # Fallback to original datetime
        

def format_datetime_in_timezone(dt: Optional[datetime], user_timezone: str = "UTC") -> Optional[str]:
    """
    Format datetime in user's timezone as ISO string.
    Returns None if datetime is None.
    """
    if dt is None:
        return None
    
    user_dt = convert_utc_to_user_timezone(dt, user_timezone)
    if user_dt is None:
        return None
        
    return user_dt.isoformat()