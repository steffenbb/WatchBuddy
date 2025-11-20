import json
import hashlib
from typing import Any, Dict, Optional

from app.core.redis_client import get_redis


def _flag_key(key: str) -> str:
    return f"feature_flags:{key}"


def _canary_key(key: str) -> str:
    return f"feature_flags:canary:{key}"


async def set_flag(key: str, value: Any) -> None:
    r = get_redis()
    try:
        await r.set(_flag_key(key), json.dumps(value))
    except Exception:
        # best-effort; do not raise in prod paths
        pass


async def get_flag(key: str, default: Any = None) -> Any:
    r = get_redis()
    try:
        data = await r.get(_flag_key(key))
        if not data:
            return default
        try:
            return json.loads(data)
        except Exception:
            return data
    except Exception:
        return default


async def set_canary_ratio(key: str, ratio: float) -> None:
    ratio = max(0.0, min(1.0, float(ratio)))
    r = get_redis()
    try:
        await r.set(_canary_key(key), json.dumps(ratio))
    except Exception:
        pass


async def get_canary_ratio(key: str, default: float = 0.0) -> float:
    r = get_redis()
    try:
        data = await r.get(_canary_key(key))
        if not data:
            return float(default)
        try:
            return float(json.loads(data))
        except Exception:
            return float(default)
    except Exception:
        return float(default)


async def is_enabled_for_user(key: str, user_id: int, default: bool = False) -> bool:
    """Return True if flag is enabled for this user via canary ratio or boolean flag.

    Precedence: explicit boolean flag (true/false) overrides canary; otherwise use canary ratio.
    """
    flag = await get_flag(key, None)
    if isinstance(flag, bool):
        return flag
    ratio = await get_canary_ratio(key, 0.0)
    if ratio <= 0:
        return bool(default)
    if ratio >= 1:
        return True
    # Stable bucketing by user_id
    h = hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()
    bucket = int(h[:8], 16) / 0xFFFFFFFF
    return bucket < ratio


async def list_flags(prefix: Optional[str] = None) -> Dict[str, Any]:
    r = get_redis()
    out: Dict[str, Any] = {}
    try:
        pat = _flag_key("*") if not prefix else _flag_key(prefix + "*")
        keys = await r.keys(pat)
        for k in keys:
            key_str = k.decode("utf-8") if isinstance(k, bytes) else str(k)
            short = key_str.split(":", 1)[-1].split(":", 1)[-1]
            try:
                val = await r.get(k)
                out[short] = json.loads(val) if val else None
            except Exception:
                out[short] = None
    except Exception:
        pass
    return out
