from typing import Any, Dict
from fastapi import APIRouter
import logging

from app.core.metrics import counters_snapshot, latency_snapshot


router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/metrics/snapshot")
async def get_metrics_snapshot() -> Dict[str, Any]:
    counters = await counters_snapshot()
    lat = await latency_snapshot()
    try:
        logger.info(f"[METRICS] Snapshot counters={len(counters)} latency={len(lat)}")
    except Exception:
        pass
    return {"counters": counters, "latency": lat}
