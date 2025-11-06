"""
API endpoints for Your Overview feature.

GET /api/overview - Retrieve cached overview modules with optional mood filtering
POST /api/overview/refresh - Manually trigger overview computation
"""
import logging
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.core.database import get_db
from app.services.overview_service import OverviewService
from app.services.tasks import compute_user_overview_task

logger = logging.getLogger(__name__)
router = APIRouter()


class MoodFilters(BaseModel):
    """Mood slider parameters (0-100 scale)."""
    energy: Optional[int] = Field(None, ge=0, le=100, description="Energy level: 0=calm, 100=intense")
    exploration: Optional[int] = Field(None, ge=0, le=100, description="Exploration: 0=familiar, 100=discover")
    commitment: Optional[int] = Field(None, ge=0, le=100, description="Commitment: 0=quick, 100=epic")


class OverviewRequest(BaseModel):
    """Request body for overview retrieval."""
    user_id: int = 1
    mood: Optional[MoodFilters] = None


class RefreshRequest(BaseModel):
    """Request body for manual overview refresh."""
    user_id: int = 1
    skip_recent_days: int = Field(7, ge=0, le=30, description="Skip updating items refreshed within the last N days")


@router.post("/overview")
async def get_overview(
    request: OverviewRequest,
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Retrieve cached overview modules with optional mood filtering.
    
    Returns:
    {
        "sections": [
            {
                "type": "investment_tracker",
                "priority": 95.5,
                "data": {...},
                "computed_at": "2025-01-15T10:30:00",
                "item_count": 25
            },
            ...
        ],
        "user_id": 1,
        "retrieved_at": "2025-01-15T12:00:00"
    }
    """
    try:
        service = OverviewService(request.user_id)
        
        # Convert mood filters to dict
        mood_dict = None
        if request.mood:
            mood_dict = {
                k: v for k, v in request.mood.dict().items() if v is not None
            }
        
        result = service.get_cached_overview(db, apply_mood=mood_dict)
        
        if not result.get('sections'):
            logger.info(f"[OverviewAPI] No cached data for user {request.user_id}, triggering background compute")
            # Queue background computation
            compute_user_overview_task.delay(request.user_id)
            return {
                "sections": [],
                "message": "Overview is being computed. Please check back in a few minutes.",
                "user_id": request.user_id,
                "status": "computing"
            }
        
        return result
        
    except Exception as e:
        logger.error(f"[OverviewAPI] Failed to retrieve overview for user {request.user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve overview: {str(e)}")


@router.post("/overview/refresh")
async def refresh_overview(
    request: RefreshRequest,
    db: Session = Depends(get_db)
) -> Dict[str, str]:
    """
    Manually trigger overview computation in background.
    
    Normally runs nightly via Celery Beat, but can be triggered manually.
    
    Returns:
    {
        "status": "queued",
        "message": "Overview computation queued",
        "user_id": 1
    }
    """
    try:
        # Queue Celery task
        task = compute_user_overview_task.delay(request.user_id, request.skip_recent_days)
        
        logger.info(f"[OverviewAPI] Queued manual refresh for user {request.user_id}, task_id={task.id}")
        
        return {
            "status": "queued",
            "message": f"Overview computation queued (skip_recent_days={request.skip_recent_days}). Results will be available in a few minutes.",
            "user_id": request.user_id,
            "task_id": task.id
        }
        
    except Exception as e:
        logger.error(f"[OverviewAPI] Failed to queue refresh for user {request.user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to queue refresh: {str(e)}")


@router.get("/overview/status")
async def get_overview_status(
    user_id: int = 1,
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Check overview cache status.
    
    Returns:
    {
        "user_id": 1,
        "has_cache": true,
        "module_count": 4,
        "last_computed": "2025-01-15T03:00:00",
        "expires_at": "2025-01-16T03:00:00"
    }
    """
    try:
        from app.models import OverviewCache
        from app.utils.timezone import utc_now
        
        # Check for valid cache entries
        cache_entries = db.query(OverviewCache).filter(
            OverviewCache.user_id == user_id,
            OverviewCache.expires_at > utc_now()
        ).all()
        
        if not cache_entries:
            return {
                "user_id": user_id,
                "has_cache": False,
                "module_count": 0,
                "message": "No cached overview available. Will be computed nightly or on manual refresh."
            }
        
        # Get latest computed timestamp
        latest = max(cache_entries, key=lambda x: x.computed_at)
        
        return {
            "user_id": user_id,
            "has_cache": True,
            "module_count": len(cache_entries),
            "modules": [entry.module_type for entry in cache_entries],
            "last_computed": latest.computed_at.isoformat(),
            "expires_at": latest.expires_at.isoformat()
        }
        
    except Exception as e:
        logger.error(f"[OverviewAPI] Failed to check status for user {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to check status: {str(e)}")
