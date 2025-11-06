"""
metadata.py

API endpoints for metadata building and status checking.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Dict, Any
import logging
from datetime import datetime
from pydantic import BaseModel

from app.core.database import get_db
from app.core.celery_app import celery_app
from app.services.metadata_builder import MetadataBuilder
from app.core.redis_client import get_redis
from app.services.tmdb_client import fetch_tmdb_metadata
from app.services.trakt_client import TraktClient

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/status")
async def get_metadata_status(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Get metadata build status and readiness.
    
    Returns:
        status: Build status (not_started, running, complete, error)
        ready: Whether metadata is ready for use
        build_progress: Current build progress if running
    """
    builder = MetadataBuilder()
    
    # Get build status from Redis
    build_status = await builder.get_build_status()
    
    # Check if metadata is ready
    is_ready = await builder.check_metadata_ready(db)
    
    return {
        "ready": is_ready,
        "build_status": build_status
    }

class BuildRequest(BaseModel):
    force: bool = False
    user_id: int = 1


@router.post("/build/start")
async def start_metadata_build(
    req: BuildRequest,
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Start metadata building process using Celery task.
    
    Args:
        force: Force rebuild even if already complete
        user_id: User ID for Trakt authentication
        
    Returns:
        message: Status message
    """
    builder = MetadataBuilder()
    
    # Check current status
    current_status = await builder.get_build_status()
    
    if current_status["status"] == "running" and not req.force:
        # Stale detection: if no update for > 120s, allow restart
        updated_at = current_status.get("updated_at")
        is_stale = False
        if updated_at:
            try:
                last = datetime.fromisoformat(updated_at)
                age = (datetime.utcnow() - last).total_seconds()
                if age > 120:
                    is_stale = True
            except Exception:
                is_stale = True
        if not is_stale:
            return {
                "message": "Metadata build already in progress",
                "progress_percent": current_status.get("progress_percent", 0)
            }
    
    # Start build via Celery using the configured app (Redis broker)
    celery_app.send_task(
        "app.services.tasks.build_metadata",
        kwargs={"user_id": req.user_id, "force": req.force}
    )
    
    return {
        "message": "Metadata build started",
        "status": "running"
    }

@router.get("/build/status")
async def get_build_status() -> Dict[str, Any]:
    """
    Get current metadata build progress.
    
    Returns detailed progress information including:
    - status: Current status (not_started, running, complete, error)
    - total: Total items to process
    - processed: Items processed so far
    - progress_percent: Percentage complete
    - errors: Number of errors encountered
    """
    builder = MetadataBuilder()
    return await builder.get_build_status()

@router.post("/skip")
async def skip_metadata_build() -> Dict[str, str]:
    """
    Skip metadata building and mark as completed.
    
    This allows users to proceed even if Trakt ID mapping is incomplete.
    The periodic retry task will continue trying to map remaining items in the background.
    """
    builder = MetadataBuilder()
    
    # Set completion flag
    r = get_redis()
    await r.set("metadata_build:scan_completed", "true")
    
    logger.info("Metadata build skipped by user - marked as completed")
    
    return {
        "message": "Metadata build skipped - you can proceed to use the app",
        "status": "completed"
    }


@router.get("/tmdb/{media_type}/{tmdb_id}")
async def get_tmdb_metadata(media_type: str, tmdb_id: int, user_id: int = 1):
    """
    Get metadata from TMDB for hover cards.
    Returns: title, overview, vote_average, release_date, genres, runtime
    """
    try:
        if media_type not in ['movie', 'tv', 'show']:
            raise HTTPException(status_code=400, detail="Invalid media_type")
        
        # Normalize media type for TMDB
        tmdb_type = 'tv' if media_type in ['tv', 'show'] else 'movie'
        
        # Fetch from TMDB using existing client
        data = await fetch_tmdb_metadata(tmdb_id, tmdb_type)
        
        if not data:
            raise HTTPException(status_code=404, detail="Metadata not found")
        
        # Extract relevant fields
        result = {
            'title': data.get('title') or data.get('name'),
            'overview': data.get('overview'),
            'vote_average': data.get('vote_average'),
            'release_date': data.get('release_date'),
            'first_air_date': data.get('first_air_date'),
            'media_type': media_type,
            'genres': [g['name'] for g in data.get('genres', [])] if data.get('genres') else None,
            'runtime': data.get('runtime') or (data.get('episode_run_time', [None])[0] if data.get('episode_run_time') else None)
        }
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.debug(f"Error fetching TMDB metadata for {media_type}/{tmdb_id}: {e}")
        raise HTTPException(status_code=404, detail="Metadata not found")


@router.get("/trakt/{trakt_id}")
async def get_trakt_metadata(trakt_id: int, user_id: int = 1, db: Session = Depends(get_db)):
    """
    Get metadata from Trakt for hover cards (fallback when TMDB ID not available).
    Returns: title, overview, rating, year, genres, runtime
    """
    try:
        # Check persistent_candidates table first (much faster)
        from app.models import PersistentCandidate
        candidate = db.query(PersistentCandidate).filter(
            PersistentCandidate.trakt_id == trakt_id
        ).first()
        
        if candidate:
            # Use cached data from persistent pool
            result = {
                'title': candidate.title,
                'overview': candidate.overview,
                'vote_average': candidate.vote_average,
                'release_date': candidate.release_date,
                'first_air_date': candidate.first_air_date,
                'media_type': candidate.media_type,
                'genres': candidate.genres.split(',') if candidate.genres else None,
                'runtime': candidate.runtime
            }
            return result
        
        # Fallback to Trakt API (slower, requires authentication)
        client = TraktClient(user_id, db)
        data = None
        media_type = None
        
        # Try both movie and show
        try:
            movie_data = await client.get_movie_by_trakt_id(trakt_id)
            if movie_data:
                data = movie_data
                media_type = 'movie'
        except:
            pass
        
        if not data:
            try:
                show_data = await client.get_show_by_trakt_id(trakt_id)
                if show_data:
                    data = show_data
                    media_type = 'tv'
            except:
                pass
        
        if not data:
            raise HTTPException(status_code=404, detail="Metadata not found")
        
        result = {
            'title': data.get('title'),
            'overview': data.get('overview'),
            'vote_average': data.get('rating'),
            'release_date': data.get('released') if media_type == 'movie' else None,
            'first_air_date': data.get('first_aired') if media_type == 'tv' else None,
            'media_type': media_type,
            'genres': data.get('genres'),
            'runtime': data.get('runtime')
        }
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.debug(f"Error fetching Trakt metadata for {trakt_id}: {e}")
        raise HTTPException(status_code=404, detail="Metadata not found")
