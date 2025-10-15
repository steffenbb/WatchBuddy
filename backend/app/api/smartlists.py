from fastapi import APIRouter, HTTPException, Body, Query
from typing import Optional, List, Dict, Any
from ..core.database import SessionLocal
from ..models import User, UserList, ListItem
from ..services.trakt_client import TraktClient
from ..services.scoring_engine import ScoringEngine
from ..services.list_sync import ListSyncService
from ..services.bulk_candidate_provider import BulkCandidateProvider
from ..services.fusion import FusionEngine
from ..services.mood import ensure_user_mood
from ..services.dynamic_titles import DynamicTitleGenerator
import json
import traceback
import logging
from app.core.redis_client import get_redis

router = APIRouter()

@router.post("/create")
async def create_smartlists(
    count: int = Body(1),
    auto_refresh: bool = Body(False),
    interval: int = Body(0),
    fusion_mode: bool = Body(False),
    list_type: str = Body("smartlist"),
    discovery: Optional[str] = Body(None),  # obscure/popular/balanced
    media_types: Optional[List[str]] = Body(["movies", "shows"]),
    items_per_list: int = Body(20),
    user_id: Optional[int] = Body(1, description="User ID to use for Trakt (default 1)")
) -> Dict[str, Any]:
    """
    Create and generate smartlists using BulkCandidateProvider for sourcing and
    mood/semantic-aware scoring. If fusion_mode is enabled, use FusionEngine.
    """
    import logging
    logger = logging.getLogger(__name__)
    
    # user_id now always defaults to 1 for single-user mode

    # Ensure user mood cached for proper mood-aware scoring
    try:
        await ensure_user_mood(user_id)
    except Exception:
        pass

    # Quota enforcement based on Trakt VIP
    # Check if user has Trakt tokens - if yes, get VIP status
    vip = False
    
    try:
        r = get_redis()
        # Check for Trakt tokens using the correct Redis key pattern
        token_json = await r.get(f"trakt_tokens:{user_id}")
        
        if token_json:
            from app.services.trakt_client import TraktClient
            client = TraktClient(user_id=user_id)
            settings = await client.get_user_settings()
            
            if isinstance(settings, dict):
                user_info = settings.get("user")
                if isinstance(user_info, dict):
                    vip = bool(user_info.get("vip") or user_info.get("vip_ep"))
        
        logger.info(f"[QUOTA] VIP status for user {user_id}: {vip}")
    except Exception as e:
        logger.error(f"[QUOTA] Exception checking VIP: {e}", exc_info=True)
        vip = False

    max_lists = None if vip else 2
    max_items = 5000 if vip else 100

    # Check existing lists count if limited
    if max_lists is not None:
        db_chk = SessionLocal()
        try:
            existing = db_chk.query(UserList).filter(UserList.user_id == user_id).count()
        finally:
            db_chk.close()
        if existing >= max_lists:
            from app.api.notifications import send_notification
            await send_notification(user_id, f"Quota exceeded: Free accounts can create up to {max_lists} lists. Upgrade to VIP for unlimited lists.", "error")
            raise HTTPException(status_code=403, detail=f"Quota exceeded: Free accounts can create up to {max_lists} lists. Upgrade to VIP for unlimited lists.")

    # Cap items per list
    items_per_list = min(items_per_list, max_items)

    # Build lists
    results = []
    
    # Check for duplicate lists (same config)
    import hashlib
    config_hash_input = json.dumps({
        "discovery": discovery or "balanced",
        "media_types": sorted(media_types or ["movies", "shows"]),
        "fusion_mode": fusion_mode,
        "list_type": list_type
    }, sort_keys=True)
    config_hash = hashlib.md5(config_hash_input.encode()).hexdigest()
    
    db_check = SessionLocal()
    try:
        existing_lists = db_check.query(UserList).filter(
            UserList.user_id == user_id
        ).all()
        
        for existing in existing_lists:
            if existing.filters:
                try:
                    existing_filters = json.loads(existing.filters)
                    existing_hash_input = json.dumps({
                        "discovery": existing_filters.get("discovery", "balanced"),
                        "media_types": sorted(existing_filters.get("media_types", [])),
                        "fusion_mode": existing_filters.get("fusion_mode", False),
                        "list_type": existing.list_type or "smartlist"
                    }, sort_keys=True)
                    existing_hash = hashlib.md5(existing_hash_input.encode()).hexdigest()
                    
                    if existing_hash == config_hash:
                        from app.api.notifications import send_notification
                        await send_notification(user_id, f"Similar list '{existing.title}' already exists", "warning")
                        raise HTTPException(
                            status_code=409,
                            detail=f"A list with this configuration already exists: '{existing.title}'"
                        )
                except json.JSONDecodeError:
                    continue
    finally:
        db_check.close()

    for i in range(max(1, min(count, 5))):
        db = SessionLocal()
        try:
            filters_payload = {
                "discovery": discovery or "balanced",
                "media_types": media_types,
                "fusion_mode": fusion_mode
            }
            
            # Generate dynamic title based on user preferences
            if list_type == "smartlist":
                title_generator = DynamicTitleGenerator(user_id)
                title = await title_generator.generate_title(
                    list_type=list_type,
                    discovery=discovery or "balanced",
                    media_types=media_types,
                    fusion_mode=fusion_mode
                )
            else:
                title = list_type.title()
            
            logging.info(f"Creating smartlist: title={title}, user_id={user_id}")
            user_list = UserList(
                user_id=user_id,
                title=title,
                filters=json.dumps(filters_payload),
                item_limit=items_per_list,
                list_type=list_type,
                sync_interval=6,  # default 6 hours incremental cadence
                sync_status="queued"  # Mark as queued for population
            )
            db.add(user_list)
            try:
                db.commit()
                db.refresh(user_list)
                logging.info(f"Successfully created smartlist with ID: {user_list.id}")
            except Exception as e:
                db.rollback()
                logging.error(f"Failed to create smartlist: {e}\n{traceback.format_exc()}")
                raise HTTPException(status_code=500, detail=f"Failed to create smartlist: {e}")

            if not user_list.id:
                db.close()
                raise HTTPException(status_code=500, detail="Failed to create smartlist: No ID returned.")
            
            # Create corresponding Trakt list
            trakt_list_id = None
            try:
                trakt_client = TraktClient(user_id=user_id)
                trakt_list = await trakt_client.create_list(
                    name=title,
                    description="Created and managed by WatchBuddy",
                    privacy="private"
                )
                trakt_list_id = trakt_list.get("ids", {}).get("trakt")
                if trakt_list_id:
                    user_list.trakt_list_id = str(trakt_list_id)
                    db.commit()
                    logging.info(f"Created Trakt list with ID: {trakt_list_id} for smartlist {user_list.id}")
                    
                    # Send success notification
                    from app.api.notifications import send_notification
                    await send_notification(user_id, f"Created list '{title}' on Trakt", "success")
            except Exception as e:
                # Don't fail list creation if Trakt sync fails
                logging.warning(f"Failed to create Trakt list for smartlist {user_list.id}: {e}")
                from app.api.notifications import send_notification
                try:
                    await send_notification(user_id, f"List created locally, but Trakt sync failed", "warning")
                except Exception:
                    pass

            # Queue async population task instead of synchronous item creation
            from app.services.tasks import populate_new_list_async
            task = populate_new_list_async.delay(
                list_id=user_list.id,
                user_id=user_id,
                discovery=discovery or "balanced",
                media_types=media_types or ["movies", "shows"],
                items_per_list=items_per_list,
                fusion_mode=fusion_mode,
                list_type=list_type
            )
            
            logging.info(f"Queued population task {task.id} for smartlist {user_list.id}")
            
            # Send notification that list is being populated
            from app.api.notifications import send_notification
            try:
                await send_notification(user_id, f"Populating '{title}' with recommendations...", "info")
            except Exception:
                pass
            
            # Items will be synced to Trakt after population completes
            # The populate task will handle Trakt sync

            results.append({
                "id": user_list.id,
                "title": user_list.title,
                "description": "Mood and semantic-aware recommendations",
                "status": "populating",
                "task_id": task.id,
                "config": {
                    "fusion_mode": fusion_mode,
                    "list_type": list_type,
                    "discovery": discovery or "balanced",
                    "media_types": media_types,
                    "items_per_list": items_per_list
                }
            })
        finally:
            db.close()

    # TODO: If auto_refresh, schedule a periodic task (Celery Beat or similar)
    return {
        "smartlists": results,
        "auto_refresh": auto_refresh,
        "interval": interval,
        "message": f"Created {len(results)} list(s), populating in background"
    }

@router.post("/sync")
async def sync_all_lists(
    force_full: bool = Body(False),
    user_id: Optional[int] = Body(1, description="User ID to use for Trakt (default 1)")
):
    """Sync all user lists with smart incremental/full sync logic."""
    sync_service = ListSyncService(user_id)
    try:
        results = await sync_service.sync_all_lists(force_full=force_full)
        return {
            "status": "success",
            "message": f"Synced {results['synced']} lists, {results['errors']} errors",
            "results": results
        }
    except Exception as e:
        logging.error(f"Sync failed: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Sync failed: {str(e)}")

@router.post("/sync/{list_id}")
async def sync_single_list(
    list_id: int,
    force_full: bool = Body(True),
    watched_only: bool = Body(False)
):
    """Trigger async sync of a specific list or just its watched status."""
    db = SessionLocal()
    try:
        user_list = db.query(UserList).filter(UserList.id == list_id).first()
        if not user_list:
            raise HTTPException(status_code=404, detail="List not found")
        
        # Queue the sync task
        from app.services.tasks import sync_single_list_async
        task = sync_single_list_async.delay(list_id, force_full, watched_only)
        
        return {
            "status": "queued",
            "message": f"List sync queued for list {list_id}",
            "task_id": task.id,
            "list_id": list_id
        }
    except Exception as e:
        logging.error(f"Failed to queue sync: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to queue sync: {str(e)}")
    finally:
        db.close()


@router.get("/sync/{list_id}/status")
async def get_sync_status(list_id: int):
    """Get the current sync status for a list."""
    try:
        from app.core.redis_client import get_redis
        import json
        
        redis = get_redis()
        status_key = f"list_sync:{list_id}:status"
        status_data = await redis.get(status_key)
        
        if not status_data:
            return {
                "status": "idle",
                "list_id": list_id,
                "message": "No sync in progress"
            }
        
        return json.loads(status_data)
    except Exception as e:
        logging.error(f"Failed to get sync status: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get sync status: {str(e)}")


@router.get("/populate/{list_id}/status")
async def get_populate_status(list_id: int):
    """Get the current population status for a list."""
    try:
        from app.core.redis_client import get_redis
        import json
        
        redis = get_redis()
        status_key = f"list_populate:{list_id}:status"
        status_data = await redis.get(status_key)
        
        if not status_data:
            return {
                "status": "idle",
                "list_id": list_id,
                "message": "No population in progress"
            }
        
        return json.loads(status_data)
    except Exception as e:
        logging.error(f"Failed to get populate status: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get populate status: {str(e)}")

@router.get("/sync/stats")
async def get_sync_stats(
    list_id: Optional[int] = Query(None),
    user_id: Optional[int] = Query(1, description="User ID to use for Trakt (default 1)")
):
    """Get sync statistics for lists."""
    sync_service = ListSyncService(user_id)
    try:
        stats = await sync_service.get_sync_stats(list_id)
        return {"status": "success", "stats": stats}
    except RuntimeError as e:
        logging.error(f"Failed to get stats (runtime): {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logging.error(f"Failed to get stats: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}")

@router.get("/{list_id}/items")
async def get_list_items(
    list_id: int,
    include_watched: bool = Query(True),
    sort_by: str = Query("score"),  # score, added_at, watched_at
    order: str = Query("desc"),  # asc, desc
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100)
):
    """Get items from a specific list with filtering and sorting options."""
    from ..models import ListItem, MediaMetadata
    
    db = SessionLocal()
    try:
        query = db.query(ListItem).filter(ListItem.smartlist_id == list_id)
        
        if not include_watched:
            query = query.filter(ListItem.is_watched == False)
        
        # Apply sorting
        if sort_by == "score":
            query = query.order_by(ListItem.score.desc() if order == "desc" else ListItem.score.asc())
        elif sort_by == "added_at":
            query = query.order_by(ListItem.added_at.desc() if order == "desc" else ListItem.added_at.asc())
        elif sort_by == "watched_at":
            query = query.order_by(ListItem.watched_at.desc() if order == "desc" else ListItem.watched_at.asc())
        
        # Get total before pagination
        total = query.count()
        
        # Apply pagination
        offset = (page - 1) * limit
        items = query.offset(offset).limit(limit).all()
        
        # Convert to response format
        response_items = []
        for item in items:
            title = None
            try:
                if item.trakt_id:
                    meta = db.query(MediaMetadata).filter(MediaMetadata.trakt_id == item.trakt_id).first()
                    if meta and meta.title:
                        title = meta.title
            except Exception:
                title = None
            response_items.append({
                "id": item.id,
                "trakt_id": item.trakt_id,
                "media_type": item.media_type,
                "score": item.score,
                "is_watched": item.is_watched,
                "watched_at": item.watched_at.isoformat() if item.watched_at else None,
                "added_at": item.added_at.isoformat(),
                "explanation": item.explanation,
                "title": title
            })
        
        return {
            "status": "success",
            "total": total,
            "page": page,
            "limit": limit,
            "items": response_items
        }
        
    except Exception as e:
        logging.error(f"Failed to get items: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to get items: {str(e)}")
    finally:
        db.close()
