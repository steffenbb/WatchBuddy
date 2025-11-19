from __future__ import annotations
import time
from typing import Any, Dict

from app.core.redis_client import get_redis


COUNTERS_KEY = "metrics:counters"


async def increment(name: str, amount: int = 1) -> None:
    r = get_redis()
    try:
        await r.hincrby(COUNTERS_KEY, name, amount)
    except Exception:
        pass


async def timing(name: str, milliseconds: float) -> None:
    """Record latency aggregates (count/sum/min/max)."""
    r = get_redis()
    try:
        key = f"metrics:latency:{name}"
        ms = float(milliseconds)
        # optimistic update: ensure min/max consistency
        pipe = r.pipeline()
        pipe.hincrby(key, "count", 1)
        pipe.hincrbyfloat(key, "sum", ms)
        pipe.hget(key, "min")
        pipe.hget(key, "max")
        res = await pipe.execute()
        # res: [count, sum, min, max]
        cur_min = res[2]
        cur_max = res[3]
        try:
            if cur_min is None or ms < float(cur_min):
                await r.hset(key, "min", ms)
            if cur_max is None or ms > float(cur_max):
                await r.hset(key, "max", ms)
        except Exception:
            pass
    except Exception:
        pass


async def counters_snapshot() -> Dict[str, int]:
    r = get_redis()
    out: Dict[str, int] = {}
    try:
        data = await r.hgetall(COUNTERS_KEY)
        for k, v in (data or {}).items():
            key = k.decode("utf-8") if isinstance(k, bytes) else str(k)
            try:
                out[key] = int(v)
            except Exception:
                out[key] = 0
    except Exception:
        pass
    return out


async def latency_snapshot() -> Dict[str, Dict[str, Any]]:
    r = get_redis()
    out: Dict[str, Dict[str, Any]] = {}
    try:
        keys = await r.keys("metrics:latency:*")
        for k in keys:
            key_str = k.decode("utf-8") if isinstance(k, bytes) else str(k)
            name = key_str.split(":", 2)[-1]
            stats = await r.hgetall(k)
            try:
                out[name] = {
                    "count": int(stats.get(b"count", 0)) if isinstance(stats.get(b"count"), (bytes, bytearray)) else int(stats.get("count", 0) or 0),
                    "sum": float(stats.get(b"sum", 0.0)) if isinstance(stats.get(b"sum"), (bytes, bytearray)) else float(stats.get("sum", 0.0) or 0.0),
                    "min": float(stats.get(b"min", 0.0)) if isinstance(stats.get(b"min"), (bytes, bytearray)) else float(stats.get("min", 0.0) or 0.0),
                    "max": float(stats.get(b"max", 0.0)) if isinstance(stats.get(b"max"), (bytes, bytearray)) else float(stats.get("max", 0.0) or 0.0),
                    "avg": 0.0,
                }
                c = out[name]["count"] or 0
                out[name]["avg"] = (out[name]["sum"] / c) if c else 0.0
            except Exception:
                out[name] = {"count": 0, "sum": 0.0, "min": 0.0, "max": 0.0, "avg": 0.0}
    except Exception:
        pass
    return out


class Timer:
    def __init__(self, name: str):
        self.name = name
        self._start = None

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._start is not None:
                ms = (time.perf_counter() - self._start) * 1000.0
                # fire and forget
                import asyncio
                loop = asyncio.get_event_loop()
                if loop and loop.is_running():
                    loop.create_task(timing(self.name, ms))
        except Exception:
            pass
