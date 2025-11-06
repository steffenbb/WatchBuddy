from redis import asyncio as aioredis  # Async client
import redis as redis_sync  # Sync client
from redis.asyncio.connection import ConnectionPool as AsyncConnectionPool
from redis.connection import ConnectionPool as SyncConnectionPool
from ..core.config import settings
import asyncio
import threading
from typing import Dict

# Per-event-loop async Redis clients and pools to avoid cross-loop issues
_redis_async_by_loop: Dict[str, aioredis.Redis] = {}
_async_pool_by_loop: Dict[str, AsyncConnectionPool] = {}

# Single sync client/pool is fine (no event loop binding)
_redis_sync: redis_sync.Redis | None = None
_sync_pool: SyncConnectionPool | None = None

def _current_loop_key() -> str:
	"""Generate a stable key for the current async context.

	Prefer the running event loop identity; if none, fall back to thread id.
	"""
	try:
		loop = asyncio.get_running_loop()
		return f"loop-{id(loop)}"
	except RuntimeError:
		# No running loop (likely called from sync context)
		return f"thread-{threading.get_ident()}"

def get_redis() -> aioredis.Redis:
	"""Get an async Redis client bound to the current event loop.

	This avoids reusing a client created in a different loop which can cause
	"Future attached to a different loop" errors when awaited.
	"""
	key = _current_loop_key()
	client = _redis_async_by_loop.get(key)
	if client is not None:
		return client

	# Create a dedicated connection pool and client for this loop
	pool = AsyncConnectionPool.from_url(
		settings.redis_url,
		decode_responses=True,
		max_connections=50,
		socket_connect_timeout=5,
		socket_timeout=5,
		retry_on_timeout=True,
	)
	client = aioredis.Redis(connection_pool=pool)
	_async_pool_by_loop[key] = pool
	_redis_async_by_loop[key] = client
	return client

def get_redis_sync() -> redis_sync.Redis:
	"""Get a singleton sync Redis client (redis) with connection pooling."""
	global _redis_sync, _sync_pool
	if _redis_sync is None:
		# Create connection pool with optimized settings
		_sync_pool = SyncConnectionPool.from_url(
			settings.redis_url,
			decode_responses=True,
			max_connections=50,
			socket_connect_timeout=5,
			socket_timeout=5,
			retry_on_timeout=True,
		)
		_redis_sync = redis_sync.Redis(connection_pool=_sync_pool)
	return _redis_sync

# Backward-compatible export expected to be sync in older modules
redis_client = get_redis_sync()
