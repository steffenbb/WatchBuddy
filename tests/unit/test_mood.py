#!/usr/bin/env python3
import sys
sys.path.append('/app')

import asyncio
from app.services.mood import ensure_user_mood, _compute_fallback_mood
from app.core.redis_client import redis_client

async def test_mood():
    # Clear cache first
    redis_client.delete('user:1:mood')
    print("Cleared mood cache")
    
    # Test fallback directly
    print("\n1. Testing fallback mood computation...")
    fallback_mood = await _compute_fallback_mood(1)
    print(f"Fallback mood: {fallback_mood}")
    
    # Test ensure with fallback disabled
    print("\n2. Testing ensure_user_mood without fallback...")
    mood_no_fallback = await ensure_user_mood(1, fallback_strategy=False)
    print(f"Mood without fallback: {mood_no_fallback}")
    
    # Clear cache again
    redis_client.delete('user:1:mood')
    
    # Test ensure with fallback enabled
    print("\n3. Testing ensure_user_mood with fallback...")
    mood_with_fallback = await ensure_user_mood(1, fallback_strategy=True)
    print(f"Mood with fallback: {mood_with_fallback}")

if __name__ == "__main__":
    asyncio.run(test_mood())