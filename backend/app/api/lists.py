def extract_error_message(e: Exception) -> str:
    import traceback
    if hasattr(e, 'detail') and e.detail:
        return str(e.detail)
    elif hasattr(e, 'args') and e.args:
        return str(e.args[0])
    else:
        return f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"

from fastapi import APIRouter, HTTPException, Query
from ..schemas import ListCreate
from .. import crud
from ..core.database import SessionLocal
from ..models import UserList  # Import here for Trakt list creation
from app.services.list_sync import ListSyncService
from fastapi import Body
import json
import logging
import traceback
from app.api.notifications import send_notification
from app.utils.timezone import format_datetime_in_timezone
from app.core.redis_client import get_redis

router = APIRouter()
logger = logging.getLogger(__name__)

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

@router.get("/quota")
async def get_quota():
    """Return current list quotas (max lists and max items per list) based on Trakt VIP."""
    try:
        from app.services.trakt_client import TraktClient
        vip = False
        try:
            client = TraktClient(user_id=1)
            settings = await client.get_user_settings()
            # Prefer user.vip and user.vip_ep flags per Trakt API response
            user_info = settings.get("user") if isinstance(settings, dict) else None
            if isinstance(user_info, dict):
                vip = bool(user_info.get("vip") or user_info.get("vip_ep"))
            else:
                account = settings.get("account") if isinstance(settings, dict) else None
                vip = bool(account.get("vip")) if isinstance(account, dict) else False
        except Exception:
            vip = False

        if vip:
            return {"vip": True, "max_lists": None, "max_items_per_list": 5000, "message": "VIP: Unlimited lists, 5000 items each"}
        else:
            return {"vip": False, "max_lists": 2, "max_items_per_list": 100, "message": "Free: Up to 2 lists, 100 items each"}
    except Exception as e:
        logger.error(f"Failed to compute quota: {e}\n{traceback.format_exc()}")
        # Conservative default on error
        return {"vip": False, "max_lists": 2, "max_items_per_list": 100, "message": "Free: Up to 2 lists, 100 items each"}

@router.post("/")
async def create_list(payload: ListCreate):
    # Basic creation
    user_id = 1  # TODO: Replace with real user_id from auth
    try:
        logger.info(f"Creating list with payload: {payload}")
        # Enforce quotas
        quota = await get_quota()
        vip = bool(quota.get("vip"))
        max_lists = quota.get("max_lists")
        max_items = int(quota.get("max_items_per_list") or 5000)
        # Check count only if limited
        if not vip and max_lists is not None:
            from ..core.database import SessionLocal
            db = SessionLocal()
            try:
                count = db.query(UserList).filter(UserList.user_id == user_id).count()
            finally:
                db.close()
            if count >= max_lists:
                raise HTTPException(status_code=403, detail=f"Quota exceeded: Free accounts can create up to {max_lists} lists. Upgrade to VIP for unlimited lists.")
        # Cap item_limit
        if payload.item_limit and payload.item_limit > max_items:
            payload.item_limit = max_items

        l = crud.create_list(payload)
        logger.info(f"Successfully created list with ID: {l.id}")
        
        # DEBUG: Log list type explicitly
        logger.info(f"[TRAKT_DEBUG] List ID {l.id} - list_type='{l.list_type}'")
        
        # Create corresponding Trakt list for custom/manual lists (SmartLists handle this in their own flow)
        logger.info(f"List type for list {l.id}: '{l.list_type}'")
        if l.list_type in ("custom", "manual"):
            # Attempt to create Trakt list; TraktClient handles token presence and refresh
            logger.info(f"[TRAKT_DEBUG] Attempting to create Trakt list for custom list {l.id}...")
            try:
                from ..services.trakt_client import TraktClient
                trakt = TraktClient(user_id=user_id)
                logger.info(f"[TRAKT_DEBUG] TraktClient initialized, calling create_list...")
                trakt_result = await trakt.create_list(
                    name=l.title,
                    description=f"Custom list managed by WatchBuddy (ID: {l.id})",
                    privacy="private"
                )
                logger.info(f"[TRAKT_DEBUG] Trakt API response: {trakt_result}")
                trakt_list_id = str(trakt_result.get("ids", {}).get("trakt"))
                logger.info(f"[TRAKT_DEBUG] Extracted trakt_list_id: {trakt_list_id}")
                if trakt_list_id and trakt_list_id != "None":
                    logger.info(f"Created Trakt list {trakt_list_id} for custom list {l.id}")
                    # Update the list with the Trakt ID
                    from ..core.database import SessionLocal as DBSession
                    db_session = DBSession()
                    try:
                        db_session.query(UserList).filter(UserList.id == l.id).update({"trakt_list_id": trakt_list_id})
                        db_session.commit()
                        logger.info(f"Updated list {l.id} with trakt_list_id: {trakt_list_id}")
                        # Notify success
                        await send_notification(user_id, f"Created Trakt list for '{l.title}'", "success")
                    finally:
                        db_session.close()
            except Exception as trakt_err:
                logger.warning(f"Failed to create Trakt list for custom list {l.id}: {trakt_err}")
                # Non-fatal - list still created locally, just won't sync to Trakt
                try:
                    await send_notification(user_id, f"List '{l.title}' created locally, but Trakt list creation failed", "warning")
                except Exception:
                    pass

            # Always queue population task for custom/manual lists
            try:
                # Import the correct tasks module
                try:
                    from app.tasks_ai import populate_new_list_async
                except ImportError:
                    # Fallback to smartlist tasks if available
                    from app.tasks_smartlist import populate_new_list_async
                
                # Use default discovery and media_types for custom lists
                populate_new_list_async.delay(
                    list_id=l.id,
                    user_id=user_id,
                    discovery="balanced",
                    media_types=["movies", "shows"],
                    items_per_list=payload.item_limit or 20,
                    fusion_mode=False,
                    list_type=l.list_type
                )
                logger.info(f"Queued populate_new_list_async for custom list {l.id}")
            except ImportError as import_err:
                logger.warning(f"Task module not found for custom list {l.id}: {import_err}. List created but not auto-populated.")
            except Exception as pop_err:
                logger.error(f"Failed to queue population task for custom list {l.id}: {pop_err}")
        else:
            logger.info(f"Skipping Trakt list creation for non-custom list type: {l.list_type}")
        
        # Send persistent and toast notification
        await send_notification(user_id, f"List '{l.title}' created successfully.", "success")
        return {"id": l.id, "title": l.title, "filters": l.filters}
    except Exception as e:
        msg = extract_error_message(e)
        logger.error(f"Failed to create list: {msg}")
        await send_notification(user_id, f"Failed to create list: {msg}", "error")
        raise HTTPException(status_code=500, detail=f"Failed to create list: {msg}")

@router.get("/")
async def get_lists(user_id: int = 1):
    try:
        user_timezone = await get_user_timezone(user_id)
        rows = crud.list_all()
        
        # Pre-calculate current time once
        from datetime import datetime, timedelta
        now = datetime.utcnow()
        
        out = []
        for r in rows:
            # Calculate cooldown information efficiently
            cooldown_active = False
            cooldown_remaining_minutes = 0
            next_sync_available = None
            
            if r.last_sync_at:
                sync_interval_hours = r.sync_interval if r.sync_interval is not None else 0.5
                hours_since_sync = (now - r.last_sync_at).total_seconds() / 3600
                
                if hours_since_sync < sync_interval_hours:
                    cooldown_active = True
                    cooldown_remaining_minutes = int((sync_interval_hours - hours_since_sync) * 60)
                    next_sync_available = format_datetime_in_timezone(
                        r.last_sync_at + timedelta(hours=sync_interval_hours),
                        user_timezone
                    )
            
            out.append({
                "id": r.id,
                "title": r.title,
                "last_updated": format_datetime_in_timezone(r.last_updated, user_timezone),
                "list_type": r.list_type,
                "item_limit": r.item_limit,
                "exclude_watched": r.exclude_watched,
                "sync_interval": r.sync_interval,
                "last_error": r.last_error,
                "filters": r.filters,
                "poster_path": getattr(r, "poster_path", None),
                "cooldown_active": cooldown_active,
                "cooldown_remaining_minutes": cooldown_remaining_minutes,
                "next_sync_available": next_sync_available
            })
        
        return out
    except Exception as e:
        logger.error(f"Failed to fetch lists: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch lists: {str(e)}")

@router.get("/{list_id}")
async def get_list(list_id: int):
    """Get details of a specific list."""
    db = SessionLocal()
    try:
        from ..models import UserList
        l = db.query(UserList).filter(UserList.id == list_id).first()
        if not l:
            raise HTTPException(status_code=404, detail="List not found")
        
        return {
            "id": l.id,
            "title": l.title,
            "list_type": l.list_type,
            "item_limit": l.item_limit,
            "exclude_watched": l.exclude_watched,
            "sync_interval": l.sync_interval,
            "filters": l.filters,
            "last_sync_at": l.last_sync_at.isoformat() if l.last_sync_at else None,
            "created_at": l.created_at.isoformat() if l.created_at else None
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get list: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to get list: {str(e)}")
    finally:
        db.close()

@router.patch("/{list_id}")
async def update_list(list_id: int,
                title: str = Body(None),
                item_limit: int = Body(None),
                exclude_watched: bool = Body(None),
                sync_interval: int = Body(None),
                full_sync_days: int = Body(None),
                discovery: str = Body(None),
                fusion_mode: bool = Body(None),
                media_types: list = Body(None),
                # Custom/Suggested list filters
                genres: list = Body(None),
                genre_mode: str = Body(None),
                languages: list = Body(None),
                year_from: int = Body(None),
                year_to: int = Body(None),
                min_rating: float = Body(None)):
    db = SessionLocal()
    user_id = 1  # TODO: Replace with real user_id from auth
    try:
        from ..models import UserList
        l = db.query(UserList).filter(UserList.id == list_id).first()
        if not l:
            raise HTTPException(status_code=404, detail="List not found")
        changed = False
        if title is not None:
            l.title = title
            changed = True
        if item_limit is not None:
            l.item_limit = item_limit
            changed = True
        if exclude_watched is not None:
            l.exclude_watched = exclude_watched
            changed = True
        if sync_interval is not None:
            l.sync_interval = sync_interval
            changed = True
        # Update filters JSON while preserving existing keys
        filters_updated = False
        try:
            f = json.loads(l.filters or "{}")
            if not isinstance(f, dict):
                f = {}
        except Exception:
            f = {}
        if full_sync_days is not None:
            try:
                f["full_sync_days"] = int(full_sync_days)
                filters_updated = True
            except Exception:
                pass
        if discovery is not None:
            f["discovery"] = discovery
            filters_updated = True
        if fusion_mode is not None:
            f["fusion_mode"] = bool(fusion_mode)
            filters_updated = True
        if media_types is not None:
            try:
                if isinstance(media_types, list):
                    mt = [str(x) for x in media_types if str(x) in ("movies", "shows")]
                    if mt:
                        f["media_types"] = mt
                        filters_updated = True
            except Exception:
                pass
        # Custom/Suggested list filters
        if genres is not None:
            try:
                if isinstance(genres, list):
                    f["genres"] = [str(g).lower() for g in genres]
                    filters_updated = True
            except Exception:
                pass
        if genre_mode is not None:
            if genre_mode in ("any", "all"):
                f["genre_mode"] = genre_mode
                filters_updated = True
        if languages is not None:
            try:
                if isinstance(languages, list):
                    f["languages"] = [str(lang).lower() for lang in languages]
                    filters_updated = True
            except Exception:
                pass
        if year_from is not None:
            try:
                f["year_from"] = int(year_from)
                filters_updated = True
            except Exception:
                pass
        if year_to is not None:
            try:
                f["year_to"] = int(year_to)
                filters_updated = True
            except Exception:
                pass
        if min_rating is not None:
            try:
                f["min_rating"] = float(min_rating)
                filters_updated = True
            except Exception:
                pass
        if filters_updated:
            l.filters = json.dumps(f)
            changed = True
        db.commit()
        # Send notification if anything changed
        if changed or filters_updated:
            await send_notification(user_id, f"List '{l.title}' updated successfully. Full sync triggered.", "success")
        return {"status":"ok"}
    finally:
        db.close()

@router.delete("/{list_id}")
async def delete_list(list_id: int):
    user_id = 1  # TODO: Replace with real user_id from auth
    
    # Get list details before deletion for Trakt cleanup
    from ..core.database import SessionLocal
    from ..models import UserList
    db = SessionLocal()
    trakt_list_id = None
    list_title = None
    
    try:
        user_list = db.query(UserList).filter(UserList.id == list_id).first()
        if user_list:
            trakt_list_id = user_list.trakt_list_id
            list_title = user_list.title
    finally:
        db.close()
    
    # Delete from Trakt if it has a Trakt list ID
    if trakt_list_id:
        try:
            from ..services.trakt_client import TraktClient
            trakt_client = TraktClient(user_id=user_id)
            success = await trakt_client.delete_list(trakt_list_id)
            if success:
                import logging
                logger = logging.getLogger(__name__)
                logger.info(f"Deleted Trakt list {trakt_list_id} for list {list_id}")
                await send_notification(user_id, f"Deleted '{list_title}' from Trakt", "info")
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to delete Trakt list {trakt_list_id}: {e}")
            await send_notification(user_id, f"List deleted locally, but Trakt deletion failed", "warning")
    
    # Delete from local database
    ok = crud.delete_list(list_id)
    if not ok:
        raise HTTPException(status_code=404, detail="List not found")
    
    # Send persistent and toast notification
    await send_notification(user_id, f"List {list_id} deleted.", "info")
    return {"status":"deleted"}

@router.post("/{list_id}/sync")
async def sync(list_id: int, force_full: bool = Query(False), watched_only: bool = Query(False)):
    # Temporary: assume user_id 1
    user_id = 1  # TODO: Replace with real user_id from auth
    sync_service = ListSyncService(user_id=user_id)
    try:
        if watched_only:
            logger.info(f"[SYNC] Watched-only sync requested for list {list_id}")
            await send_notification(user_id, f"Sync started for list {list_id} (watched-only)", "info")
            result = await sync_service.sync_watched_status_only(list_id)
            logger.info(f"[SYNC] Watched-only sync complete for list {list_id}: {result}")
            await send_notification(user_id, f"Sync finished for list {list_id} (watched-only)", "success")
            return {"status":"success","updated":result.get("updated",0),"total":result.get("total",0)}
        else:
            # Run a sync only for this list
            from ..core.database import SessionLocal
            db = SessionLocal()
            try:
                from ..models import UserList
                user_list = db.query(UserList).filter(UserList.id == list_id).first()
                if not user_list:
                    raise HTTPException(status_code=404, detail="List not found")
                logger.info(f"[SYNC] Starting sync for list {list_id} (force_full={force_full})")
                await send_notification(user_id, f"Sync started for list {list_id}", "info")
                result = await sync_service._sync_single_list(user_list, force_full=force_full)
                logger.info(f"[SYNC] Completed sync for list {list_id}: {result}")
                await send_notification(user_id, f"Sync finished for list {list_id}", "success")
                return {"status":"success","result":result}
            finally:
                db.close()
    except Exception as e:
        await send_notification(user_id, f"Sync error for list {list_id}: {str(e)}", "error")
        raise

@router.get("/{list_id}/items")
@router.get("/{list_id}/items/")
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
        
        # Bulk-load metadata to avoid N+1 queries
        # Load metadata per media_type to avoid show/movie collisions on same trakt_id
        ids_by_type = {"movie": [], "show": []}
        for it in items:
            if it.trakt_id:
                ids_by_type[it.media_type].append(it.trakt_id)
        meta_by_trakt: dict[int, MediaMetadata] = {}
        for mt, ids in ids_by_type.items():
            if ids:
                metas = db.query(MediaMetadata).filter(
                    MediaMetadata.trakt_id.in_(ids),
                    MediaMetadata.media_type == mt
                ).all()
                for m in metas:
                    meta_by_trakt[m.trakt_id] = m

        # Identify which items lack a poster and should fetch from Trakt/TMDB
        missing: list[tuple[int, str]] = []  # (trakt_id, media_type)
        for it in items:
            if not it.trakt_id:
                continue
            m = meta_by_trakt.get(it.trakt_id)
            # Fallback criteria: we care about posters; fetch if poster is missing
            if not m or not getattr(m, 'poster_path', None):
                missing.append((it.trakt_id, it.media_type))

        # Fetch missing titles with bounded concurrency, then cache them
        # To avoid slow responses on first load, cap per-page fallback fetches
        if missing:
            # Fetch up to the current page size to ensure all visible rows get posters
            missing = missing[:limit]
            from app.services.trakt_client import TraktClient
            import asyncio as _asyncio
            from datetime import datetime as _dt
            from app.services.tmdb_client import fetch_tmdb_metadata, get_tmdb_api_key
            trakt_client = TraktClient(user_id=1)
            sem = _asyncio.Semaphore(4)

            async def fetch_one(tid: int, mt: str):
                try:
                    async with sem:
                        details = await trakt_client.get_item_details(mt, tid)
                        title = details.get('title') if isinstance(details, dict) else None
                        year = details.get('year') if isinstance(details, dict) else None
                        tmdb_id = None
                        try:
                            tmdb_id = (details.get('ids') or {}).get('tmdb')
                        except Exception:
                            tmdb_id = None
                        poster_url = None
                        backdrop_url = None
                        # If we have a TMDB id and TMDB is configured, fetch poster/backdrop
                        try:
                            tmdb_key = await get_tmdb_api_key()
                        except Exception:
                            tmdb_key = None
                        if tmdb_id and tmdb_key:
                            try:
                                tmdb = await fetch_tmdb_metadata(tmdb_id, 'movie' if mt == 'movie' else 'tv')
                                if tmdb:
                                    pp = tmdb.get('poster_path')
                                    bp = tmdb.get('backdrop_path')
                                    if pp:
                                        poster_url = f"https://image.tmdb.org/t/p/w342{pp}"
                                    if bp:
                                        backdrop_url = f"https://image.tmdb.org/t/p/w780{bp}"
                            except Exception:
                                pass
                        if title:
                            # Upsert minimal metadata cache
                            existing = meta_by_trakt.get(tid)
                            if existing:
                                if not existing.title:
                                    existing.title = title
                                existing.media_type = mt
                                if year is not None:
                                    existing.year = year
                                if tmdb_id is not None:
                                    existing.tmdb_id = tmdb_id
                                # Store poster/backdrop as full URLs for convenience
                                if poster_url:
                                    existing.poster_path = poster_url
                                if backdrop_url:
                                    existing.backdrop_path = backdrop_url
                                existing.last_updated = _dt.utcnow()
                            else:
                                new_meta = MediaMetadata(
                                    trakt_id=tid,
                                    media_type=mt,
                                    title=title,
                                    year=year,
                                    tmdb_id=tmdb_id,
                                    poster_path=poster_url,
                                    backdrop_path=backdrop_url,
                                    last_updated=_dt.utcnow()
                                )
                                db.add(new_meta)
                                meta_by_trakt[tid] = new_meta
                except Exception:
                    pass

            await _asyncio.gather(*[fetch_one(tid, mt) for tid, mt in missing])
            try:
                db.commit()
            except Exception:
                db.rollback()

        # Convert to response format
        response_items = []
        for item in items:
            meta = meta_by_trakt.get(item.trakt_id) if item.trakt_id else None
            # Prefer ListItem.title, fallback to MediaMetadata.title
            title = item.title or (meta.title if (meta and meta.title) else None)
            # Build poster URL for response
            poster_url = None
            if meta and meta.poster_path:
                poster_url = meta.poster_path
                if isinstance(poster_url, str) and not poster_url.startswith('http'):
                    poster_url = f"https://image.tmdb.org/t/p/w342{poster_url}"
            # Include year if available from metadata for client-side sorting
            year = None
            try:
                year = meta.year if meta else None
            except Exception:
                year = None
            response_items.append({
                "id": item.id,
                "trakt_id": item.trakt_id,
                "media_type": item.media_type,
                "score": item.score,
                "is_watched": item.is_watched,
                "watched": item.is_watched,  # alias for frontend compatibility
                "watched_at": item.watched_at.isoformat() if item.watched_at else None,
                "added_at": item.added_at.isoformat(),
                "explanation": item.explanation,
                "title": title,
                "poster_url": poster_url,
                "year": year,
            })
        
        return {
            "status": "success",
            "total": total,
            "page": page,
            "limit": limit,
            "items": response_items
        }
        
    except Exception as e:
        logger.error(f"Failed to get items: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to get items: {str(e)}")
    finally:
        db.close()
