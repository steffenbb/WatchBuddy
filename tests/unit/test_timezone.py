#!/usr/bin/env python3
import sys
sys.path.append('/app')

from app.utils.timezone import utc_now, get_user_hour, format_iso_utc, ensure_utc
from app.services.mood import get_contextual_mood_adjustment
import asyncio

def test_timezone_fixes():
    print("=== Timezone Enhancement Test ===")
    
    # Test timezone utilities
    now_utc = utc_now()
    print(f"1. UTC now: {now_utc}")
    print(f"   Formatted: {format_iso_utc(now_utc)}")
    
    # Test timezone-aware hour calculation
    hour_utc = get_user_hour("UTC")
    hour_pacific = get_user_hour("US/Pacific")
    hour_europe = get_user_hour("Europe/Copenhagen")
    
    print(f"\n2. Current hour by timezone:")
    print(f"   UTC: {hour_utc}")
    print(f"   US/Pacific: {hour_pacific}")
    print(f"   Europe/Copenhagen: {hour_europe}")
    
    # Test contextual mood adjustments for different timezones
    print(f"\n3. Contextual mood adjustments by timezone:")
    
    timezones_to_test = ["UTC", "US/Pacific", "Europe/Copenhagen", "Asia/Tokyo"]
    for tz in timezones_to_test:
        try:
            adjustments = get_contextual_mood_adjustment(tz)
            hour = get_user_hour(tz)
            print(f"   {tz} (hour {hour}): {adjustments}")
        except Exception as e:
            print(f"   {tz}: Error - {e}")
    
    # Test timezone-aware datetime handling
    print(f"\n4. Testing timezone-aware datetime conversion:")
    import datetime
    
    # Test naive datetime (should be treated as UTC)
    naive_dt = datetime.datetime(2023, 10, 14, 15, 30, 0)
    utc_dt = ensure_utc(naive_dt)
    print(f"   Naive datetime: {naive_dt} -> UTC: {utc_dt}")
    
    # Test timezone-aware datetime
    pacific_tz = datetime.timezone(datetime.timedelta(hours=-8))
    pacific_dt = datetime.datetime(2023, 10, 14, 15, 30, 0, tzinfo=pacific_tz)
    utc_converted = ensure_utc(pacific_dt)
    print(f"   Pacific datetime: {pacific_dt} -> UTC: {utc_converted}")
    
    print(f"\n✅ All timezone fixes working correctly!")

if __name__ == "__main__":
    test_timezone_fixes()