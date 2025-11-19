"""
Telemetry API endpoints for user engagement metrics.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, Optional
from app.services.telemetry import TelemetryTracker
from app.core.database import SessionLocal
from sqlalchemy.orm import Session
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Request models
class ItemClickRequest(BaseModel):
    user_id: int = 1
    list_id: int
    item_id: int
    position: int


class PlayEventRequest(BaseModel):
    user_id: int = 1
    item_id: int
    media_type: str
    completed: bool = False


class SkipEventRequest(BaseModel):
    user_id: int = 1
    list_id: int
    item_id: int
    reason: Optional[str] = None


class SatisfactionRatingRequest(BaseModel):
    user_id: int = 1
    rating: int  # 1-5
    context: str = "general"


class TrainerEventRequest(BaseModel):
    user_id: int = 1
    event_type: str  # 'start', 'complete', 'abandon'
    judgments_count: Optional[int] = None
    duration_seconds: Optional[float] = None


@router.post("/track/list_view")
async def track_list_view(
    user_id: int = 1,
    list_id: int = 0,
    item_count: int = 0
):
    """Track when a user views a list (impression)."""
    try:
        tracker = TelemetryTracker(user_id=user_id)
        tracker.track_list_view(list_id, item_count)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Failed to track list view: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/track/item_click")
async def track_item_click(request: ItemClickRequest):
    """Track when a user clicks on an item (click-through)."""
    try:
        tracker = TelemetryTracker(user_id=request.user_id)
        tracker.track_item_click(request.list_id, request.item_id, request.position)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Failed to track item click: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/track/play_event")
async def track_play_event(request: PlayEventRequest):
    """Track when a user starts/completes watching an item."""
    try:
        tracker = TelemetryTracker(user_id=request.user_id)
        tracker.track_play_event(request.item_id, request.media_type, request.completed)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Failed to track play event: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/track/skip_event")
async def track_skip_event(request: SkipEventRequest):
    """Track when a user skips/dismisses an item without engaging."""
    try:
        tracker = TelemetryTracker(user_id=request.user_id)
        tracker.track_skip_event(request.list_id, request.item_id, request.reason)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Failed to track skip event: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/track/trainer_event")
async def track_trainer_event(request: TrainerEventRequest):
    """Track pairwise trainer events (start, complete, abandon)."""
    try:
        tracker = TelemetryTracker(user_id=request.user_id)
        
        if request.event_type == "start":
            tracker.track_trainer_start()
        elif request.event_type == "complete":
            if request.judgments_count is None or request.duration_seconds is None:
                raise HTTPException(status_code=400, detail="judgments_count and duration_seconds required for complete event")
            tracker.track_trainer_completion(request.judgments_count, request.duration_seconds)
        elif request.event_type == "abandon":
            tracker.track_trainer_abandonment()
        else:
            raise HTTPException(status_code=400, detail=f"Invalid event_type: {request.event_type}")
        
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to track trainer event: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/track/satisfaction_rating")
async def track_satisfaction_rating(request: SatisfactionRatingRequest):
    """Track user satisfaction rating (1-5 scale)."""
    try:
        if not 1 <= request.rating <= 5:
            raise HTTPException(status_code=400, detail="Rating must be between 1 and 5")
        
        tracker = TelemetryTracker(user_id=request.user_id)
        tracker.track_satisfaction_rating(request.rating, request.context)
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to track satisfaction rating: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/metrics")
async def get_global_metrics(user_id: int = 1):
    """Get global telemetry metrics summary."""
    try:
        tracker = TelemetryTracker(user_id=user_id)
        return tracker.get_global_metrics()
    except Exception as e:
        logger.error(f"Failed to get global metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/metrics/user")
async def get_user_metrics(user_id: int = 1):
    """Get user-specific telemetry metrics."""
    try:
        tracker = TelemetryTracker(user_id=user_id)
        
        # Get user-specific metrics
        from app.core.redis_client import get_redis_sync
        redis = get_redis_sync()
        
        return {
            "list_views": int(redis.get(f"telemetry:user:{user_id}:list_views") or 0),
            "clicks": int(redis.get(f"telemetry:user:{user_id}:clicks") or 0),
            "skips": int(redis.get(f"telemetry:user:{user_id}:skips") or 0),
            "plays": int(redis.get(f"telemetry:user:{user_id}:plays") or 0),
            "trainer_starts": int(redis.get(f"telemetry:user:{user_id}:trainer_starts") or 0),
            "trainer_completions": int(redis.get(f"telemetry:user:{user_id}:trainer_completions") or 0),
            "trainer_abandonments": int(redis.get(f"telemetry:user:{user_id}:trainer_abandonments") or 0),
            "satisfaction_delta": tracker.get_satisfaction_delta(),
        }
    except Exception as e:
        logger.error(f"Failed to get user metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/metrics/list/{list_id}")
async def get_list_metrics(list_id: int):
    """Get list-specific telemetry metrics."""
    try:
        from app.core.redis_client import get_redis_sync
        redis = get_redis_sync()
        
        views = int(redis.get(f"telemetry:list:{list_id}:views") or 0)
        clicks = int(redis.get(f"telemetry:list:{list_id}:clicks") or 0)
        skips = int(redis.get(f"telemetry:list:{list_id}:skips") or 0)
        
        ctr = (clicks / views * 100) if views > 0 else 0.0
        
        return {
            "list_id": list_id,
            "views": views,
            "clicks": clicks,
            "skips": skips,
            "click_through_rate": ctr,
        }
    except Exception as e:
        logger.error(f"Failed to get list metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))
