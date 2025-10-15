from datetime import datetime, timedelta
import json
from typing import Dict, List, Optional
from fastapi import APIRouter, HTTPException
from sqlalchemy import func, and_


from app.core.database import SessionLocal
from app.core.redis_client import get_redis
from app.models import UserList as WatchList, MediaMetadata
from app.services.trakt_client import TraktClient, TraktAPIError, TraktNetworkError, TraktUnavailableError
from app.utils.timezone import format_datetime_in_timezone

router = APIRouter()

async def get_user_timezone(user_id: int) -> str:
    """Get user's configured timezone from Redis, defaulting to UTC."""
    try:
        redis = get_redis()
        timezone_data = await redis.get(f"settings:timezone:{user_id}")
        if timezone_data:
            data = json.loads(timezone_data)
            return data.get("timezone", "UTC")
    except Exception:
        pass
    return "UTC"

@router.get("/sync")
async def get_sync_status(user_id: int = 1):
    """Get current sync status including active syncs and statistics"""
    try:
        # Get user's timezone for timestamp formatting
        user_timezone = await get_user_timezone(user_id)
        
        # Get active syncs from Redis
        active_syncs = []
        redis = get_redis()
        sync_keys = await redis.keys("sync_lock:*")

        for key in sync_keys:
            try:
                lock_json = await redis.get(key)
                if not lock_json:
                    continue
                lock_data = json.loads(lock_json)
            except Exception:
                continue

            list_id = str(lock_data.get("list_id") or str(key).split(":")[-1])
            # Get list title from database
            db = SessionLocal()
            try:
                watch_list = db.query(WatchList).filter(WatchList.id == int(list_id)).first()
            finally:
                db.close()

            if watch_list:
                started_at = lock_data.get("started_at")
                try:
                    started_iso = datetime.utcfromtimestamp(float(started_at)).isoformat() if started_at else ""
                except Exception:
                    started_iso = str(started_at) if started_at else ""
                active_syncs.append({
                    "list_id": int(list_id),
                    "list_title": watch_list.title,
                    "started_at": started_iso,
                    "progress": None  # Could be enhanced with actual progress tracking
                })

        # Get statistics from database
        db = SessionLocal()

        total_lists = db.query(func.count(WatchList.id)).scalar()

        # Lists synced today
        today = datetime.utcnow().date()
        completed_today = db.query(func.count(WatchList.id)).filter(
            func.date(WatchList.last_updated) == today
        ).scalar()

        # Last sync time (formatted in user's timezone)
        last_sync_list = db.query(WatchList).filter(
            WatchList.last_updated.isnot(None)
        ).order_by(WatchList.last_updated.desc()).first()

        last_sync = format_datetime_in_timezone(last_sync_list.last_updated, user_timezone) if last_sync_list else None

        db.close()

        return {
            "active_syncs": active_syncs,
            "last_sync": last_sync,
            "total_lists": total_lists or 0,
            "completed_today": completed_today or 0
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get sync status: {str(e)}")

@router.get("/health")
async def get_system_health():
    """Check health of all system dependencies"""
    health = {
        "redis": False,
        "database": False,
        "celery": False,
        "trakt_api": False,
        "tmdb_api": False
    }
    
    # Check Redis
    try:
        await get_redis().ping()
        health["redis"] = True
    except Exception:
        pass
    
    # Check Database
    try:
        db = SessionLocal()
        db.execute("SELECT 1")
        db.close()
        health["database"] = True
    except Exception:
        pass
    
    # Check Celery (by checking if workers are active)
    try:
        from app.core.celery_app import celery_app
        # Use Celery's inspect to check for active workers
        inspect = celery_app.control.inspect()
        active_nodes = inspect.active()
        health["celery"] = bool(active_nodes)
    except Exception:
        pass
    

    # Trakt API health check
    try:
        trakt = TraktClient(user_id=1)
        await trakt.get_user_settings()
        health["trakt_api"] = True
    except (TraktAPIError, TraktNetworkError, TraktUnavailableError, Exception):
        health["trakt_api"] = False

    # TMDB API health check (only if key is present)
    from app.services.tmdb_client import get_tmdb_api_key
    tmdb_api_key = await get_tmdb_api_key()
    if tmdb_api_key:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                # Use a lightweight endpoint: /configuration
                resp = await client.get(
                    "https://api.themoviedb.org/3/configuration",
                    params={"api_key": tmdb_api_key}
                )
                resp.raise_for_status()
            health["tmdb_api"] = True
        except Exception:
            health["tmdb_api"] = False
    else:
        health["tmdb_api"] = False
    
    return health

@router.get("/metrics")
async def get_system_metrics():
    """Get detailed system metrics"""
    try:
        db = SessionLocal()
        
        # Database metrics
        total_lists = db.query(func.count(WatchList.id)).scalar()
        total_metadata = db.query(func.count(MediaMetadata.id)).scalar()
        
        # Recent activity (last 24 hours)
        yesterday = datetime.utcnow() - timedelta(hours=24)
        recent_syncs = db.query(func.count(WatchList.id)).filter(
            WatchList.last_updated >= yesterday
        ).scalar()
        
        # Metadata freshness
        week_ago = datetime.utcnow() - timedelta(days=7)
        stale_metadata = db.query(func.count(MediaMetadata.id)).filter(
            and_(
                MediaMetadata.last_updated < week_ago,
                MediaMetadata.is_active == True
            )
        ).scalar()
        
        db.close()
        
        # Redis metrics
        redis_info = {}
        try:
            r = get_redis()
            info = await r.info()
            redis_info = {
                "memory_used": info.get("used_memory_human", "Unknown"),
                "connected_clients": info.get("connected_clients", 0),
                "total_commands_processed": info.get("total_commands_processed", 0)
            }
        except Exception:
            pass

        return {
            "database": {
                "total_lists": total_lists or 0,
                "total_metadata": total_metadata or 0,
                "recent_syncs": recent_syncs or 0,
                "stale_metadata": stale_metadata or 0
            },
            "redis": redis_info,
            "uptime": "Unknown"  # Could be enhanced with actual uptime tracking
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get metrics: {str(e)}")