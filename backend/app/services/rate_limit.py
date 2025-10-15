"""
rate_limit.py

Redis-based AsyncLimiter for API quota protection with exponential backoff.
Handles Trakt & TMDB quotas, logs failures, marks lists as 'sync delayed'.
"""
import time
import asyncio
import logging
from typing import Optional, Dict, Any
from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

# Rate limit configurations
RATE_LIMITS = {
    "trakt_api": {"limit": 1000, "window": 300},  # 1000 requests per 5 minutes
    "tmdb_api": {"limit": 40, "window": 10},      # 40 requests per 10 seconds
}

class AsyncLimiter:
    """Redis-based rate limiter with sliding window."""
    
    def __init__(self, service: str, user_id: str = "global"):
        self.service = service
        self.user_id = user_id
        self.redis = get_redis()
        self.config = RATE_LIMITS.get(service, {"limit": 10, "window": 60})
    
    async def acquire(self) -> bool:
        """Attempt to acquire a token. Returns True if allowed, False if rate limited."""
        key = f"rate_limit:{self.service}:{self.user_id}"
        now = int(time.time())
        window = self.config["window"]
        limit = self.config["limit"]
        
        # Use sliding window with Redis pipeline
        pipe = self.redis.pipeline()
        # Remove expired entries
        pipe.zremrangebyscore(key, 0, now - window)
        # Add current request
        pipe.zadd(key, {str(now): now})
        # Count current requests
        pipe.zcard(key)
        # Set expiry
        pipe.expire(key, window)
        
        results = await pipe.execute()
        current_count = results[2]
        
        if current_count > limit:
            logger.warning(f"Rate limit exceeded for {self.service} (user: {self.user_id}): {current_count}/{limit}")
            return False
        
        return True
    
    async def get_status(self) -> Dict[str, Any]:
        """Get current quota status."""
        key = f"rate_limit:{self.service}:{self.user_id}"
        now = int(time.time())
        window = self.config["window"]
        limit = self.config["limit"]
        
        # Count current requests
        current_count = await self.redis.zcard(key)
        remaining = max(0, limit - current_count)
        reset_time = now + window
        
        return {
            "service": self.service,
            "user_id": self.user_id,
            "limit": limit,
            "remaining": remaining,
            "reset_time": reset_time,
            "current_count": current_count
        }

async def check_rate_limit(user_id: str, service: str) -> None:
    """Check rate limit and raise exception if exceeded."""
    limiter = AsyncLimiter(service, user_id)
    if not await limiter.acquire():
        status = await limiter.get_status()
        raise RateLimitExceeded(
            f"Rate limit exceeded for {service}",
            service=service,
            user_id=user_id,
            status=status
        )

async def with_backoff(func, *args, max_retries: int = 5, service: str = None, user_id: str = None, **kwargs):
    """Execute function with exponential backoff on rate limit errors."""
    delay = 1
    last_exception = None
    
    for attempt in range(max_retries):
        try:
            # Check rate limit before attempt
            if service and user_id:
                await check_rate_limit(user_id, service)
            
            return await func(*args, **kwargs)
            
        except RateLimitExceeded as e:
            last_exception = e
            logger.warning(f"Rate limited on attempt {attempt + 1}/{max_retries}, sleeping {delay}s")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)  # Cap at 30 seconds
            
        except Exception as e:
            # For non-rate-limit errors, apply backoff but don't check rate limit
            if "429" in str(e) or "rate" in str(e).lower():
                last_exception = e
                logger.warning(f"API rate limit response on attempt {attempt + 1}/{max_retries}, sleeping {delay}s")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
            else:
                raise
    
    # Log failure and mark sync delayed
    if service and user_id:
        await mark_sync_delayed(user_id, service, str(last_exception))
    
    raise last_exception or Exception(f"Max retries ({max_retries}) exceeded")

async def mark_sync_delayed(user_id: str, service: str, reason: str):
    """Mark list sync as delayed due to rate limiting."""
    redis = get_redis()
    key = f"sync_delayed:{user_id}:{service}"
    data = {
        "reason": reason,
        "timestamp": int(time.time()),
        "service": service
    }
    await redis.hset(key, mapping=data)
    await redis.expire(key, 3600)  # Expire after 1 hour
    
    logger.error(f"Marked sync delayed for user {user_id} on {service}: {reason}")

async def get_sync_delays(user_id: str) -> Dict[str, Any]:
    """Get current sync delays for a user."""
    redis = get_redis()
    delays = {}
    
    for service in RATE_LIMITS.keys():
        key = f"sync_delayed:{user_id}:{service}"
        data = await redis.hgetall(key)
        if data:
            delays[service] = {
                "reason": data.get("reason"),
                "timestamp": int(data.get("timestamp", 0)),
                "service": data.get("service")
            }
    
    return delays

class RateLimitExceeded(Exception):
    """Exception raised when rate limit is exceeded."""
    
    def __init__(self, message: str, service: str = None, user_id: str = None, status: Dict = None):
        super().__init__(message)
        self.service = service
        self.user_id = user_id
        self.status = status or {}
