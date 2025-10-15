from redis import asyncio as aioredis  # Async client
import redis as redis_sync  # Sync client
from ..core.config import settings

_redis_async: aioredis.Redis | None = None
_redis_sync: redis_sync.Redis | None = None

def get_redis() -> aioredis.Redis:
	"""Get a singleton async Redis client (redis.asyncio)."""
	global _redis_async
	if _redis_async is None:
		_redis_async = aioredis.from_url(settings.redis_url, decode_responses=True)
	return _redis_async

def get_redis_sync() -> redis_sync.Redis:
	"""Get a singleton sync Redis client (redis)."""
	global _redis_sync
	if _redis_sync is None:
		_redis_sync = redis_sync.Redis.from_url(settings.redis_url, decode_responses=True)
	return _redis_sync

# Backward-compatible export expected to be sync in older modules
redis_client = get_redis_sync()
