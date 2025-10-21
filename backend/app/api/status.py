from datetime import datetime, timedelta
import json
from typing import Dict, List, Optional
from fastapi import APIRouter, HTTPException
from sqlalchemy import func, and_


from app.core.database import SessionLocal
from app.core.redis_client import get_redis
from app.models import UserList as WatchList, MediaMetadata
from app.models_ai import AiList
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
        
        # Get active syncs from Redis (sync and populate operations)
        active_syncs = []
        redis = get_redis()
        
        # Check list_sync:{list_id}:status (manual sync operations)
        list_sync_keys = await redis.keys("list_sync:*:status")
        for key in list_sync_keys:
            try:
                status_json = await redis.get(key)
                if not status_json:
                    continue
                status_data = json.loads(status_json)
                
                # Only include running syncs
                if status_data.get("status") != "running":
                    continue
                
                list_id = status_data.get("list_id")
                if not list_id:
                    # Extract from key: list_sync:{list_id}:status
                    list_id = key.decode('utf-8').split(':')[1] if isinstance(key, bytes) else key.split(':')[1]
                
                # Get list title from database
                db = SessionLocal()
                try:
                    watch_list = db.query(WatchList).filter(WatchList.id == int(list_id)).first()
                finally:
                    db.close()

                if watch_list:
                    started_at = status_data.get("started_at")
                    try:
                        started_iso = datetime.utcfromtimestamp(float(started_at)).isoformat() if started_at else ""
                    except Exception:
                        started_iso = str(started_at) if started_at else ""
                    
                    active_syncs.append({
                        "list_id": int(list_id),
                        "list_title": watch_list.title,
                        "started_at": started_iso,
                        "progress": status_data.get("progress", 0),
                        "message": status_data.get("message", "Syncing..."),
                        "operation": "sync",
                        "sync_type": "async"
                    })
            except Exception as e:
                import logging
                logging.warning(f"Failed to parse list_sync status: {e}")
                continue
        
        # Check list_populate:{list_id}:status (list population operations)
        list_populate_keys = await redis.keys("list_populate:*:status")
        for key in list_populate_keys:
            try:
                status_json = await redis.get(key)
                if not status_json:
                    continue
                status_data = json.loads(status_json)
                
                # Only include running population tasks
                if status_data.get("status") != "running":
                    continue
                
                list_id = status_data.get("list_id")
                if not list_id:
                    # Extract from key: list_populate:{list_id}:status
                    list_id = key.decode('utf-8').split(':')[1] if isinstance(key, bytes) else key.split(':')[1]
                
                # Get list title from database
                db = SessionLocal()
                try:
                    watch_list = db.query(WatchList).filter(WatchList.id == int(list_id)).first()
                finally:
                    db.close()

                if watch_list:
                    started_at = status_data.get("started_at")
                    try:
                        started_iso = datetime.utcfromtimestamp(float(started_at)).isoformat() if started_at else ""
                    except Exception:
                        started_iso = str(started_at) if started_at else ""
                    
                    active_syncs.append({
                        "list_id": int(list_id),
                        "list_title": watch_list.title,
                        "started_at": started_iso,
                        "progress": status_data.get("progress", 0),
                        "message": status_data.get("message", "Populating..."),
                        "operation": "populate",
                        "sync_type": "async"
                    })
            except Exception as e:
                import logging
                logging.warning(f"Failed to parse list_populate status: {e}")
                continue
        
        # Check old format: sync_lock:* (legacy)
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
            
            # Skip if already in active_syncs (avoid duplicates)
            if any(s["list_id"] == int(list_id) for s in active_syncs):
                continue
            
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
                    "progress": None,
                    "message": "Syncing...",
                    "operation": "sync",
                    "sync_type": "legacy"
                })

        # Check AI list locks: lock:ai_list:* (AI-powered lists)
        ai_list_keys = await redis.keys("lock:ai_list:*")
        for key in ai_list_keys:
            try:
                lock_json = await redis.get(key)
                if not lock_json:
                    continue
                lock_data = json.loads(lock_json) if isinstance(lock_json, (str, bytes)) else {}
            except Exception:
                lock_data = {}

            # Extract AI list ID from key: lock:ai_list:{id}
            key_str = key.decode('utf-8') if isinstance(key, bytes) else key
            ai_list_id = key_str.split(":")[-1]
            
            # Skip if already in active_syncs (avoid duplicates)
            try:
                if any(s.get("ai_list_id") == ai_list_id for s in active_syncs):
                    continue
            except Exception:
                pass
            
            # Get AI list title from database
            db = SessionLocal()
            try:
                ai_list = db.query(AiList).filter(AiList.id == ai_list_id).first()
            finally:
                db.close()

            if ai_list:
                started_at = lock_data.get("started_at")
                try:
                    started_iso = datetime.utcfromtimestamp(float(started_at)).isoformat() if started_at else ""
                except Exception:
                    started_iso = ""
                active_syncs.append({
                    "ai_list_id": ai_list_id,
                    "list_title": ai_list.generated_title or ai_list.prompt_text[:50] or "AI List",
                    "started_at": started_iso,
                    "progress": None,
                    "message": "Generating AI recommendations...",
                    "operation": "ai_generate",
                    "sync_type": "ai"
                })

        # Get statistics from database
        db = SessionLocal()

        # Count both user_lists and ai_lists
        total_user_lists = db.query(func.count(WatchList.id)).scalar() or 0
        total_ai_lists = db.query(func.count(AiList.id)).scalar() or 0
        total_lists = total_user_lists + total_ai_lists

        # Lists synced today (both types)
        today = datetime.utcnow().date()
        user_lists_today = db.query(func.count(WatchList.id)).filter(
            func.date(WatchList.last_updated) == today
        ).scalar() or 0
        ai_lists_today = db.query(func.count(AiList.id)).filter(
            func.date(AiList.last_synced_at) == today
        ).scalar() or 0
        completed_today = user_lists_today + ai_lists_today

        # Last sync time (check both tables, formatted in user's timezone)
        last_sync_user_list = db.query(WatchList).filter(
            WatchList.last_updated.isnot(None)
        ).order_by(WatchList.last_updated.desc()).first()
        
        last_sync_ai_list = db.query(AiList).filter(
            AiList.last_synced_at.isnot(None)
        ).order_by(AiList.last_synced_at.desc()).first()
        
        # Pick the most recent sync from both types
        # Ensure both datetimes are timezone-aware for comparison
        last_sync_time = None
        if last_sync_user_list and last_sync_ai_list:
            from datetime import timezone
            user_time = last_sync_user_list.last_updated
            ai_time = last_sync_ai_list.last_synced_at
            # Make naive datetimes timezone-aware (assume UTC)
            if user_time and user_time.tzinfo is None:
                user_time = user_time.replace(tzinfo=timezone.utc)
            if ai_time and ai_time.tzinfo is None:
                ai_time = ai_time.replace(tzinfo=timezone.utc)
            last_sync_time = max(user_time, ai_time)
        elif last_sync_user_list:
            last_sync_time = last_sync_user_list.last_updated
        elif last_sync_ai_list:
            last_sync_time = last_sync_ai_list.last_synced_at
        
        last_sync = format_datetime_in_timezone(last_sync_time, user_timezone) if last_sync_time else None

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


@router.get("/workers")
async def get_worker_status(user_id: int = 1):
    """
    Get background worker status for ingestion tasks.
    
    Returns status for movie and TV show ingestion workers including:
    - Current status (idle/running/completed/error)
    - Last run timestamp
    - Next scheduled run estimate
    - Items processed in last run
    - Error message if failed
    """
    try:
        redis = get_redis()
        user_timezone = await get_user_timezone(user_id)
        
        workers = {}
        
        for media_type in ["movie", "show"]:
            status_json = await redis.get(f"worker_status:{media_type}")
            
            if status_json:
                status_data = json.loads(status_json)
                
                # Format timestamps in user's timezone
                last_run = None
                next_run = None
                if status_data.get("last_run"):
                    last_run_dt = datetime.fromisoformat(status_data["last_run"])
                    last_run = format_datetime_in_timezone(last_run_dt, user_timezone)
                    
                    # Estimate next run (workers run every 2 hours)
                    next_run_dt = last_run_dt + timedelta(hours=2)
                    next_run = format_datetime_in_timezone(next_run_dt, user_timezone)
                
                workers[media_type] = {
                    "status": status_data.get("status", "idle"),
                    "last_run": last_run,
                    "next_run": next_run,
                    "items_processed": status_data.get("items_processed", 0),
                    "error": status_data.get("error")
                }
            else:
                # No status data yet (first run pending)
                workers[media_type] = {
                    "status": "idle",
                    "last_run": None,
                    "next_run": None,
                    "items_processed": 0,
                    "error": None
                }
        
        return workers
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get worker status: {str(e)}")
