"""
prompt_cache.py
- Normalized prompt hashing and cache for AI lists (DB or Redis, TTL 24h).
"""
import hashlib
import json
from app.core.redis_client import get_redis

CACHE_TTL = 86400  # 24h


def prompt_hash(normalized_prompt: str) -> str:
    return hashlib.sha256(normalized_prompt.encode("utf-8")).hexdigest()


def cache_prompt_result(normalized_prompt: str, result: dict):
    r = get_redis()
    key = f"ai:prompt_cache:{prompt_hash(normalized_prompt)}"
    r.set(key, json.dumps(result), ex=CACHE_TTL)


def get_cached_prompt_result(normalized_prompt: str):
    r = get_redis()
    key = f"ai:prompt_cache:{prompt_hash(normalized_prompt)}"
    val = r.get(key)
    if val:
        return json.loads(val)
    return None
