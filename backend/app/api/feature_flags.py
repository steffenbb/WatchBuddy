from typing import Any, Dict
from fastapi import APIRouter, Body, HTTPException
import logging

from app.core.feature_flags import (
    get_flag, set_flag, list_flags, set_canary_ratio, get_canary_ratio,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/flags")
async def get_all_flags(prefix: str | None = None) -> Dict[str, Any]:
    vals = await list_flags(prefix)
    try:
        logger.info(f"[FLAGS] Listed flags prefix={prefix!r} count={len(vals)}")
    except Exception:
        pass
    return vals


@router.get("/flags/{key}")
async def get_flag_value(key: str) -> Dict[str, Any]:
    val = await get_flag(key, None)
    canary = await get_canary_ratio(key, 0.0)
    try:
        logger.info(f"[FLAGS] Get flag {key} value={val} canary={canary}")
    except Exception:
        pass
    return {"key": key, "value": val, "canary_ratio": canary}


@router.post("/flags/{key}")
async def set_flag_value(
    key: str,
    payload: Dict[str, Any] = Body(..., example={"value": True, "kind": "flag"})
):
    kind = str(payload.get("kind", "flag")).lower()
    if kind == "flag":
        await set_flag(key, payload.get("value"))
        val = await get_flag(key)
        try:
            logger.info(f"[FLAGS] Set flag {key}={val}")
        except Exception:
            pass
        return {"status": "ok", "key": key, "value": val}
    elif kind == "canary":
        try:
            ratio = float(payload.get("value", 0.0))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid ratio value")
        await set_canary_ratio(key, ratio)
        cr = await get_canary_ratio(key)
        try:
            logger.info(f"[FLAGS] Set canary {key}={cr}")
        except Exception:
            pass
        return {"status": "ok", "key": key, "canary_ratio": cr}
    else:
        raise HTTPException(status_code=400, detail="kind must be 'flag' or 'canary'")
