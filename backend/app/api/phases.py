"""
phases.py

API endpoints for user viewing phases.
Provides current phase, history, refresh, detail, and conversion endpoints.
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import logging
import json
from datetime import datetime

from app.core.database import SessionLocal
from app.core.redis_client import redis_client
from app.models import UserPhase, UserPhaseEvent, TraktWatchHistory
from app.services.phase_detector import PhaseDetector
from app.services.watch_history_sync import WatchHistorySync

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["phases"])

# Cache until recomputation: store without TTL and clear on recompute
PHASE_CACHE_TTL = None


class PhaseResponse(BaseModel):
    """Phase data returned to frontend."""
    id: int
    label: str
    icon: Optional[str]
    start_at: datetime
    end_at: Optional[datetime]
    item_count: int
    movie_count: int
    show_count: int
    phase_type: str
    phase_score: float
    explanation: Optional[str]
    dominant_genres: List[str]
    dominant_keywords: List[str]
    representative_posters: List[str]
    franchise_name: Optional[str]
    avg_runtime: Optional[int]
    top_language: Optional[str]
    cohesion: float
    watch_density: float


class PhaseDetailResponse(PhaseResponse):
    """Extended phase data with member items."""
    tmdb_ids: List[int]
    trakt_ids: List[int]
    media_types: List[str]


class RefreshRequest(BaseModel):
    """Request to refresh phases."""
    user_id: int = 1


class ConvertRequest(BaseModel):
    """Request to convert phase to AI list."""
    user_id: int = 1
    list_name: Optional[str] = None


def _serialize_phase(phase: UserPhase) -> Dict[str, Any]:
    """Convert UserPhase model to dict for API response."""
    try:
        return {
            "id": phase.id,
            "label": phase.label,
            "icon": phase.icon,
            "start_at": phase.start_at.isoformat() if phase.start_at else None,
            "end_at": phase.end_at.isoformat() if phase.end_at else None,
            "item_count": phase.item_count,
            "movie_count": phase.movie_count,
            "show_count": phase.show_count,
            "phase_type": phase.phase_type,
            "phase_score": phase.phase_score,
            "explanation": phase.explanation,
            "dominant_genres": json.loads(phase.dominant_genres) if phase.dominant_genres else [],
            "dominant_keywords": json.loads(phase.dominant_keywords) if phase.dominant_keywords else [],
            "representative_posters": json.loads(phase.representative_posters) if phase.representative_posters else [],
            "franchise_name": phase.franchise_name,
            "avg_runtime": phase.avg_runtime,
            "top_language": phase.top_language,
            "cohesion": phase.cohesion,
            "watch_density": phase.watch_density
        }
    except Exception as e:
        logger.error(f"Failed to serialize phase {phase.id}: {e}")
        raise


def _serialize_phase_detail(phase: UserPhase) -> Dict[str, Any]:
    """Convert UserPhase model to detailed dict with member items."""
    base = _serialize_phase(phase)
    base.update({
        "tmdb_ids": json.loads(phase.tmdb_ids) if phase.tmdb_ids else [],
        "trakt_ids": json.loads(phase.trakt_ids) if phase.trakt_ids else [],
        "media_types": json.loads(phase.media_types) if phase.media_types else []
    })
    return base


@router.get("/users/{user_id}/phases/current")
async def get_current_phase(user_id: int):
    """
    Get user's current active phase.
    Cached until recomputation clears it.
    """
    cache_key = f"phase:current:{user_id}"
    
    # Check cache
    try:
        cached = redis_client.get(cache_key)
        if cached:
            logger.debug(f"[PhasesAPI] Cache hit for current phase (user {user_id})")
            return json.loads(cached)
    except Exception as e:
        logger.warning(f"[PhasesAPI] Cache read failed: {e}")
    
    # Query database
    db = SessionLocal()
    try:
        detector = PhaseDetector(user_id)
        phase = detector.get_current_phase()
        
        if not phase:
            response = {"phase": None}
        else:
            response = {"phase": _serialize_phase(phase)}
        
        # If no phase exists, trigger initial one-time backfill and compute
        if not phase:
            try:
                history_count = db.query(TraktWatchHistory).filter(TraktWatchHistory.user_id == user_id).count()
            except Exception:
                history_count = 0
            if history_count == 0:
                try:
                    from app.services.tasks import sync_user_watch_history_task, compute_user_phases_task
                    sync_user_watch_history_task.delay(user_id, True)
                    compute_user_phases_task.delay(user_id)
                except Exception as e:
                    logger.warning(f"[PhasesAPI] Failed to enqueue initial phase backfill: {e}")

        # Cache result (no TTL)
        try:
            redis_client.set(
                cache_key,
                json.dumps(response, default=str)
            )
        except Exception as e:
            logger.warning(f"[PhasesAPI] Cache write failed: {e}")
        
        return response
        
    except Exception as e:
        logger.error(f"[PhasesAPI] Failed to get current phase: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.get("/users/{user_id}/phases/predicted")
async def get_predicted_phase(user_id: int, lookback_days: int = 42):
    """
    Get predicted next phase based on recent 4-6 weeks.
    Returns prediction dict (not a persisted phase).
    Cached for 6 hours.
    """
    cache_key = f"phase:predicted:{user_id}:{lookback_days}"
    
    # Check cache
    try:
        cached = redis_client.get(cache_key)
        if cached:
            logger.debug(f"[PhasesAPI] Cache hit for predicted phase (user {user_id})")
            return json.loads(cached)
    except Exception as e:
        logger.warning(f"[PhasesAPI] Cache read failed: {e}")
    
    db = SessionLocal()
    try:
        detector = PhaseDetector(user_id)
        prediction = detector.predict_next_phase(lookback_days=lookback_days)
        
        response = {"prediction": prediction}
        
        # Cache result for 6 hours
        try:
            redis_client.setex(
                cache_key,
                6 * 3600,  # 6 hours
                json.dumps(response, default=str)
            )
        except Exception as e:
            logger.warning(f"[PhasesAPI] Cache write failed: {e}")
        
        return response
        
    except Exception as e:
        logger.error(f"[PhasesAPI] Failed to predict next phase: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.get("/users/{user_id}/phases")
async def get_phase_history(user_id: int, limit: int = 10):
    """
    Get user's phase history (past phases).
    Returns newest first.
    """
    cache_key = f"phase:history:{user_id}:{limit}"
    
    # Check cache
    try:
        cached = redis_client.get(cache_key)
        if cached:
            logger.debug(f"[PhasesAPI] Cache hit for phase history (user {user_id})")
            return json.loads(cached)
    except Exception as e:
        logger.warning(f"[PhasesAPI] Cache read failed: {e}")
    
    db = SessionLocal()
    try:
        detector = PhaseDetector(user_id)
        phases = detector.get_phase_history(limit=limit)
        
        response = {
            "phases": [_serialize_phase(p) for p in phases],
            "total": len(phases)
        }
        
        # Cache result (no TTL)
        try:
            redis_client.set(
                cache_key,
                json.dumps(response, default=str)
            )
        except Exception as e:
            logger.warning(f"[PhasesAPI] Cache write failed: {e}")
        
        return response
        
    except Exception as e:
        logger.error(f"[PhasesAPI] Failed to get phase history: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.get("/users/{user_id}/phases/timeline")
async def get_phase_timeline(user_id: int):
    """
    Get all phases formatted for timeline visualization.
    Returns phases with time ranges and stats for rendering.
    """
    cache_key = f"phase:timeline:{user_id}"
    
    # Check cache
    try:
        cached = redis_client.get(cache_key)
        if cached:
            logger.debug(f"[PhasesAPI] Cache hit for phase timeline (user {user_id})")
            return json.loads(cached)
    except Exception as e:
        logger.warning(f"[PhasesAPI] Cache read failed: {e}")
    
    db = SessionLocal()
    try:
        # Get all phases for user, ordered by start date
        phases = db.query(UserPhase).filter(
            UserPhase.user_id == user_id
        ).order_by(UserPhase.start_at.asc()).all()
        
        timeline_data = []
        for phase in phases:
            timeline_data.append({
                "id": phase.id,
                "label": phase.label,
                "icon": phase.icon,
                "start": phase.start_at.isoformat() if phase.start_at else None,
                "end": phase.end_at.isoformat() if phase.end_at else datetime.utcnow().isoformat(),
                "type": phase.phase_type,
                "item_count": phase.item_count,
                "score": phase.phase_score,
                "genres": json.loads(phase.dominant_genres) if phase.dominant_genres else [],
                "color": _get_phase_color(phase)
            })
        
        response = {
            "timeline": timeline_data,
            "total_phases": len(timeline_data)
        }
        
        # Cache result (no TTL)
        try:
            redis_client.set(
                cache_key,
                json.dumps(response, default=str)
            )
        except Exception as e:
            logger.warning(f"[PhasesAPI] Cache write failed: {e}")
        
        return response
        
    except Exception as e:
        logger.error(f"[PhasesAPI] Failed to get phase timeline: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@router.get("/users/{user_id}/phases/{phase_id}")
async def get_phase_detail(user_id: int, phase_id: int):
    """
    Get detailed information about a specific phase.
    Includes full member item lists.
    """
    cache_key = f"phase:detail:{user_id}:{phase_id}"
    
    # Check cache
    try:
        cached = redis_client.get(cache_key)
        if cached:
            logger.debug(f"[PhasesAPI] Cache hit for phase detail {phase_id}")
            return json.loads(cached)
    except Exception as e:
        logger.warning(f"[PhasesAPI] Cache read failed: {e}")
    
    db = SessionLocal()
    try:
        phase = db.query(UserPhase).filter(
            UserPhase.id == phase_id,
            UserPhase.user_id == user_id
        ).first()
        
        if not phase:
            raise HTTPException(status_code=404, detail="Phase not found")
        
        response = {"phase": _serialize_phase_detail(phase)}
        
        # Cache result (no TTL)
        try:
            redis_client.set(
                cache_key,
                json.dumps(response, default=str)
            )
        except Exception as e:
            logger.warning(f"[PhasesAPI] Cache write failed: {e}")
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[PhasesAPI] Failed to get phase detail: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.post("/users/{user_id}/phases/refresh")
async def refresh_phases(user_id: int, background_tasks: BackgroundTasks, request: RefreshRequest):
    """
    Trigger phase recomputation for user.
    Runs in background, returns immediately with job status.
    """
    try:
        # Clear cache
        cache_keys = [
            f"phase:current:{user_id}",
            f"phase:history:{user_id}:*",
            f"phase:detail:{user_id}:*"
        ]
        for pattern in cache_keys:
            try:
                if "*" in pattern:
                    # Delete pattern (requires scan in production)
                    for key in redis_client.scan_iter(pattern):
                        redis_client.delete(key)
                else:
                    redis_client.delete(pattern)
            except Exception as e:
                logger.warning(f"[PhasesAPI] Cache clear failed for {pattern}: {e}")
        
        # Enqueue background tasks: always perform a FULL watch history sync first,
        # then (also) queue a compute to ensure recomputation even if no new items were added.
        from app.services.tasks import compute_user_phases_task, sync_user_watch_history_task
        sync_task = sync_user_watch_history_task.delay(user_id, True)
        task = compute_user_phases_task.delay(user_id)
        
        return {
            "status": "queued",
            "task_id": task.id,
            "sync_task_id": sync_task.id,
            "message": "Full watch history sync + phase recomputation started"
        }
        
    except Exception as e:
        logger.error(f"[PhasesAPI] Failed to trigger phase refresh: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/users/{user_id}/phases/{phase_id}/convert")
async def convert_phase_to_list(user_id: int, phase_id: int, request: ConvertRequest):
    """
    Convert a phase into an AI-powered mood list.
    Creates a new list with phase members and similar recommendations.
    """
    db = SessionLocal()
    try:
        # Get phase
        phase = db.query(UserPhase).filter(
            UserPhase.id == phase_id,
            UserPhase.user_id == user_id
        ).first()
        
        if not phase:
            raise HTTPException(status_code=404, detail="Phase not found")
        
        # Import here to avoid circular imports
        from app.tasks_ai import generate_chat_list_task
        from app.models import AIList
        
        # Create AI list based on phase
        list_name = request.list_name or f"{phase.label} List"
        
        # Build prompt from phase characteristics
        genres = json.loads(phase.dominant_genres) if phase.dominant_genres else []
        keywords = json.loads(phase.dominant_keywords) if phase.dominant_keywords else []
        
        prompt = f"More {', '.join(genres[:2])} titles"
        if keywords:
            prompt += f" with themes like {', '.join(keywords[:3])}"
        
        # Create AI list entry
        ai_list = AIList(
            user_id=user_id,
            name=list_name,
            prompt=prompt,
            status="pending",
            media_type="both" if phase.movie_count > 0 and phase.show_count > 0 else ("movie" if phase.movie_count > phase.show_count else "show"),
            target_count=20,
            discovery_weight=0.3,  # Moderate discovery
            fusion_balance=0.5
        )
        db.add(ai_list)
        db.commit()
        db.refresh(ai_list)
        
        # Enqueue list generation
        task = generate_chat_list_task.delay(ai_list.id)
        
        # Log event
        event = UserPhaseEvent(
            user_id=user_id,
            phase_id=phase_id,
            action="converted",
            meta=json.dumps({"list_id": ai_list.id, "list_name": list_name})
        )
        db.add(event)
        db.commit()
        
        return {
            "status": "success",
            "list_id": ai_list.id,
            "list_name": list_name,
            "task_id": task.id,
            "message": f"Creating list from {phase.label}"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[PhasesAPI] Failed to convert phase to list: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.post("/users/{user_id}/phases/{phase_id}/share")
async def share_phase(user_id: int, phase_id: int):
    """
    Generate shareable link or export data for a phase.
    TODO: Implement sharing functionality (export JSON, generate image, etc.)
    """
    db = SessionLocal()
    try:
        phase = db.query(UserPhase).filter(
            UserPhase.id == phase_id,
            UserPhase.user_id == user_id
        ).first()
        
        if not phase:
            raise HTTPException(status_code=404, detail="Phase not found")
        
        # Log event
        event = UserPhaseEvent(
            user_id=user_id,
            phase_id=phase_id,
            action="shared",
            meta=json.dumps({"timestamp": datetime.utcnow().isoformat()})
        )
        db.add(event)
        db.commit()
        
        # For now, return phase data as shareable JSON
        return {
            "status": "success",
            "shareable_data": _serialize_phase_detail(phase),
            "message": "Phase data exported successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[PhasesAPI] Failed to share phase: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()




def _get_phase_color(phase: UserPhase) -> str:
    """Map phase characteristics to color for timeline visualization."""
    try:
        genres = json.loads(phase.dominant_genres) if phase.dominant_genres else []
        if not genres:
            return "#4a5568"  # Gray default
        
        primary_genre = genres[0].lower()
        
        # Genre color mapping
        color_map = {
            "sci-fi": "#0f2a44",
            "science fiction": "#0f2a44",
            "thriller": "#3a2b2b",
            "horror": "#2b1d1d",
            "comedy": "#2a6f4a",
            "romance": "#4a2d3a",
            "action": "#4a3a2d",
            "drama": "#3a4a5a",
            "fantasy": "#4a2d5a",
            "mystery": "#2d3a4a",
            "documentary": "#3a4a3a"
        }
        
        for key, color in color_map.items():
            if key in primary_genre:
                return color
        
        return "#4a5568"
        
    except Exception:
        return "#4a5568"
