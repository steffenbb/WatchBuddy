def extract_error_message(e: Exception) -> str:
    import traceback, json
    if hasattr(e, 'detail') and e.detail:
        detail = e.detail
        if isinstance(detail, (dict, list)):
            try:
                return json.dumps(detail)
            except Exception:
                return str(detail)
        return str(detail)
    elif hasattr(e, 'args') and e.args:
        arg = e.args[0]
        if isinstance(arg, (dict, list)):
            try:
                return json.dumps(arg)
            except Exception:
                return str(arg)
        return str(arg)
    else:
        return f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"

"""
tasks.py

Celery task definitions with per-user/list locking, queue management, and memory optimization.
Uses Redis locks to prevent overlap and toast notifications for queueing status.
"""
import gc
import asyncio
import logging
import time
import json
from datetime import datetime
from celery import shared_task
from celery.exceptions import Retry
from app.core.redis_client import get_redis
from app.services.scoring_engine import ScoringEngine
from app.services.mood import get_user_mood
from app.core.database import SessionLocal

logger = logging.getLogger(__name__)

class SyncLock:
    """Redis-based lock for sync operations."""
    
    def __init__(self, user_id: int, list_id: int = None, lock_type: str = "sync"):
        self.user_id = user_id
        self.list_id = list_id
        self.lock_type = lock_type
        self.redis = get_redis()
        
        if list_id:
            self.lock_key = f"lock:{lock_type}:user:{user_id}:list:{list_id}"
        else:
            self.lock_key = f"lock:{lock_type}:user:{user_id}"
    
    async def acquire(self, timeout: int = 300) -> bool:
        """Acquire lock with timeout."""
        acquired = await self.redis.set(self.lock_key, "locked", ex=timeout, nx=True)
        if not acquired:
            logger.info(f"Lock already held: {self.lock_key}")
            # Send toast notification about queueing
            await send_toast_notification(
                self.user_id,
                f"Sync queued behind other list syncs",
                "info"
            )
        return bool(acquired)
    
    async def release(self):
        """Release lock."""
        await self.redis.delete(self.lock_key)
    
    async def __aenter__(self):
        if not await self.acquire():
            raise SyncLockBusy(f"Could not acquire lock: {self.lock_key}")
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.release()

class SyncLockBusy(Exception):
    """Raised when sync lock cannot be acquired."""
    pass

async def send_toast_notification(user_id: int, message: str, notification_type: str = "info"):
    """Send toast notification via Redis pub/sub and persist it."""
    # Publish to real-time channel (for live toasts)
    redis = get_redis()
    notification = {
        "user_id": user_id,
        "message": message,
        "type": notification_type,
        "timestamp": int(time.time())
    }
    await redis.publish(f"notifications:{user_id}", json.dumps(notification))
    # Also persist notification so it shows up in the log
    try:
        # Lazy import to avoid circular dependency at module import time
        from app.api.notifications import send_notification as _persist_notify
        await _persist_notify(user_id, message, notification_type)
    except Exception as e:
        logger.warning(f"Failed to persist notification: {e}")

def format_sync_notification(list_title: str, trigger: str, updated: int = 0, removed: int = 0, total: int = 0) -> str:
    msg = f"Sync ({trigger}) for '{list_title}': "
    details = []
    if updated:
        details.append(f"{updated} updated")
    if removed:
        details.append(f"{removed} removed")
    if total:
        details.append(f"{total} total")
    if details:
        msg += ", ".join(details)
    else:
        msg += "No changes."
    return msg

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def score_smartlist(self, user_id: int, smartlist_id: int):
    """Score SmartList items with user/list locking."""
    async def _score_smartlist():
        try:
            # Acquire per-list lock
            async with SyncLock(user_id, smartlist_id, "scoring"):
                engine = ScoringEngine()
                try:
                    # Fetch SmartList and items from DB
                    from app.core.database import get_async_session
                    from app.models import SmartList, ListItem
                    from sqlalchemy import select
                    
                    async with get_async_session() as session:
                        # Get SmartList
                        stmt = select(SmartList).where(SmartList.id == smartlist_id)
                        result = await session.execute(stmt)
                        smartlist = result.scalar_one_or_none()
                        
                        if not smartlist:
                            logger.error(f"SmartList {smartlist_id} not found")
                            return
                        
                        # Get user mood
                        mood = await get_user_mood(user_id)

                        # Fetch existing trakt_ids for freshness
                        existing_trakt_ids = set()
                        if smartlist.items:
                            for item in smartlist.items:
                                if item.trakt_id:
                                    existing_trakt_ids.add(item.trakt_id)

                        # Parse criteria from SmartList (assume JSON in .criteria)
                        import json
                        criteria = {}
                        if smartlist.criteria:
                            try:
                                criteria = json.loads(smartlist.criteria)
                            except Exception:
                                logger.warning(f"Could not parse criteria for SmartList {smartlist_id}")

                        # Use BulkCandidateProvider to fetch candidates
                        from app.services.bulk_candidate_provider import BulkCandidateProvider
                        provider = BulkCandidateProvider(user_id)
                        candidates = await provider.get_candidates(
                            media_type=criteria.get("media_type", "movies"),
                            limit=criteria.get("item_limit", 50),
                            mood=mood,
                            discovery=criteria.get("discovery"),
                            include_watched=not smartlist.exclude_watched if hasattr(smartlist, 'exclude_watched') else False,
                            genres=criteria.get("genres"),
                            languages=criteria.get("languages"),
                            min_year=criteria.get("min_year"),
                            max_year=criteria.get("max_year"),
                            min_rating=criteria.get("min_rating"),
                            search_keywords=criteria.get("search_keywords"),
                            enrich_with_tmdb=True,
                            genre_mode=criteria.get("genre_mode", "any"),
                            existing_list_ids=existing_trakt_ids
                        )

                        # Score items
                        scored_items = engine.score_candidates(
                            user={"id": user_id},
                            candidates=candidates,
                            list_type="smartlist",
                            item_limit=criteria.get("item_limit", 50),
                            filters={
                                "genres": criteria.get("genres"),
                                "languages": criteria.get("languages"),
                                "min_year": criteria.get("min_year"),
                                "max_year": criteria.get("max_year")
                            }
                        )

                        # Save results (placeholder)
                        # TODO: update ListItem records
                        
                        await send_toast_notification(
                            user_id,
                            f"SmartList '{smartlist.name}' updated with {len(scored_items)} items",
                            "success"
                        )
                        
                finally:
                    # Memory cleanup
                    del engine
                    gc.collect()
                    
        except SyncLockBusy:
            # Retry later if lock is busy
            logger.info(f"SmartList scoring queued for user {user_id}, list {smartlist_id}")
            raise self.retry(countdown=30)
        except Exception as exc:
            msg = extract_error_message(exc)
            logger.error(f"Error scoring SmartList {smartlist_id}: {msg}")
            await send_toast_notification(
                user_id,
                f"Failed to update SmartList: {msg}",
                "error"
            )
            if self.request.retries < self.max_retries:
                raise self.retry(countdown=60 * (2 ** self.request.retries))
            raise
    
    # Run async function
    import asyncio
    asyncio.run(_score_smartlist())

@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def sync_user_lists(self, user_id: int):
    """Sync all lists for a user with global user lock."""
    async def _sync_user_lists():
        try:
            # Acquire per-user lock
            async with SyncLock(user_id, lock_type="user_sync"):
                from app.core.database import get_async_session
                from app.models import SmartList
                from sqlalchemy import select
                
                async with get_async_session() as session:
                    # Get user's lists
                    stmt = select(SmartList).where(SmartList.user_id == user_id)
                    result = await session.execute(stmt)
                    smartlists = result.scalars().all()
                    
                    synced_count = 0
                    for smartlist in smartlists:
                        try:
                            # Queue individual list scoring tasks
                            score_smartlist.delay(user_id, smartlist.id)
                            synced_count += 1
                        except Exception as e:
                            logger.error(f"Failed to queue SmartList {smartlist.id}: {e}")
                    
                    await send_toast_notification(
                        user_id,
                        f"Queued {synced_count} lists for sync",
                        "info"
                    )
                    
        except SyncLockBusy:
            logger.info(f"User sync already in progress for user {user_id}")
            await send_toast_notification(
                user_id,
                "Sync already in progress",
                "warning"
            )
        except Exception as exc:
            msg = extract_error_message(exc)
            logger.error(f"Error syncing user {user_id}: {msg}")
            await send_toast_notification(
                user_id,
                f"Sync failed: {msg}",
                "error"
            )
            if self.request.retries < self.max_retries:
                raise self.retry(countdown=30 * (2 ** self.request.retries))
            raise
    
    asyncio.run(_sync_user_lists())

@shared_task(bind=True, max_retries=3)
def send_user_notification(self, user_id: int, message: str):
    """Send user notification."""
    async def _send_notification():
        await send_toast_notification(user_id, message, "info")
    
    asyncio.run(_send_notification())

@shared_task(bind=True, max_retries=3)
def refresh_smartlists(self):
    """Refresh all SmartLists for all users (scheduled task)."""
    async def _refresh_all():
        from app.core.database import get_async_session
        from app.models import User
        from sqlalchemy import select
        
        async with get_async_session() as session:
            # Get all active users
            stmt = select(User.id).distinct()
            result = await session.execute(stmt)
            user_ids = [row[0] for row in result]
            
            # Queue sync for each user
            for user_id in user_ids:
                sync_user_lists.delay(user_id)
            
            logger.info(f"Queued SmartList refresh for {len(user_ids)} users")
    
    asyncio.run(_refresh_all())

@shared_task
def cleanup_orphaned_items():
    """Background cleanup of orphaned metadata."""
    async def _cleanup():
        from app.services.metadata_manager import MetadataManager
        try:
            # Clean up orphaned metadata (older than 30 days)
            deleted_count = await MetadataManager.cleanup_orphaned(retention_days=30)
            # Refresh stale metadata (older than 30 days)
            refreshed_count = await MetadataManager.refresh_stale_metadata(days_threshold=30)
            logger.info(f"Cleanup completed: {deleted_count} deleted, {refreshed_count} refreshed")
            # Memory cleanup
            import gc
            gc.collect()
        except Exception as e:
            logger.error(f"Cleanup failed: {e}")
    asyncio.run(_cleanup())

@shared_task
def ingest_new_movies():
    """Celery task to ingest new movie candidates incrementally."""
    async def _run():
        from app.services.candidate_ingestion import ingest_via_search_multi
        try:
            await ingest_via_search_multi('movies', duration_minutes=12)
        except Exception as e:
            logger.error(f"ingest_new_movies failed: {e}")
    asyncio.run(_run())

@shared_task
def ingest_new_shows():
    """Celery task to ingest new show candidates incrementally."""
    async def _run():
        from app.services.candidate_ingestion import ingest_via_search_multi
        try:
            await ingest_via_search_multi('shows', duration_minutes=12)
        except Exception as e:
            logger.error(f"ingest_new_shows failed: {e}")
    asyncio.run(_run())

@shared_task
def refresh_recent_votes_movies():
    """Refresh vote stats for recent movies."""
    async def _run():
        from app.services.candidate_ingestion import refresh_recent_votes
        db = SessionLocal()
        try:
            await refresh_recent_votes('movies', db=db)
        except Exception as e:
            logger.error(f"refresh_recent_votes_movies failed: {e}")
        finally:
            db.close()
    asyncio.run(_run())

@shared_task
def refresh_recent_votes_shows():
    """Refresh vote stats for recent shows."""
    async def _run():
        from app.services.candidate_ingestion import refresh_recent_votes
        db = SessionLocal()
        try:
            await refresh_recent_votes('shows', db=db)
        except Exception as e:
            logger.error(f"refresh_recent_votes_shows failed: {e}")
        finally:
            db.close()
    asyncio.run(_run())

@shared_task(bind=True)
def build_metadata(self, user_id: int = 1, force: bool = False):
    """
    Build Trakt IDs for persistent candidates with progress tracking.
    
    Args:
        user_id: User ID for Trakt authentication
        force: Force rebuild even if already complete
    """
    from app.core.database import SessionLocal
    from app.services.metadata_builder import MetadataBuilder
    
    async def _run():
        db = SessionLocal()
        try:
            builder = MetadataBuilder()
            logger.info(f"Starting metadata build (user_id={user_id}, force={force})")
            await builder.build_trakt_ids(db, user_id=user_id, force=force)
            logger.info("Metadata build completed successfully")
        except Exception as e:
            logger.error(f"Metadata build failed: {e}", exc_info=True)
            raise
        finally:
            db.close()
    
    # Execute metadata build coroutine
    return asyncio.run(_run())


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def sync_single_list_async(self, list_id: int, force_full: bool = False, watched_only: bool = False):
    """
    Async task to sync a single list with progress tracking.
    
    Args:
        list_id: ID of the list to sync
        force_full: Force full sync even if incremental would suffice
        watched_only: Only update watched status without changing list
    """
    async def _run_sync():
        from app.core.database import SessionLocal
        from app.models import UserList
        from app.services.list_sync import ListSyncService
        from app.core.redis_client import get_redis_sync
        
        # Use sync Redis client for status tracking to avoid event loop issues
        redis = get_redis_sync()
        status_key = f"list_sync:{list_id}:status"
        
        try:
            # Set initial status
            redis.set(status_key, json.dumps({
                "status": "running",
                "list_id": list_id,
                "started_at": time.time(),
                "progress": 0
            }), ex=3600)
            
            db = SessionLocal()
            try:
                user_list = db.query(UserList).filter(UserList.id == list_id).first()
                if not user_list:
                    redis.set(status_key, json.dumps({
                        "status": "error",
                        "error": "List not found"
                    }), ex=300)
                    raise ValueError(f"List {list_id} not found")
                
                sync_service = ListSyncService(user_list.user_id)
                
                if watched_only:
                    result = await sync_service.sync_watched_status_only(list_id)
                    redis.set(status_key, json.dumps({
                        "status": "complete",
                        "list_id": list_id,
                        "result": result,
                        "completed_at": time.time()
                    }), ex=300)
                else:
                    result = await sync_service._sync_single_list(user_list, force_full=force_full)
                    redis.set(status_key, json.dumps({
                        "status": "complete",
                        "list_id": list_id,
                        "result": result,
                        "completed_at": time.time()
                    }), ex=300)
                
                logger.info(f"List sync completed: {list_id}, result: {result}")
                
            finally:
                db.close()
                
        except Exception as exc:
            msg = extract_error_message(exc)
            logger.error(f"List sync failed for {list_id}: {msg}")
            
            # Store error status
            redis.set(status_key, json.dumps({
                "status": "error",
                "list_id": list_id,
                "error": msg,
                "failed_at": time.time()
            }), ex=300)
            
            # Retry logic
            if self.request.retries < self.max_retries:
                raise self.retry(countdown=60 * (2 ** self.request.retries))
            raise
    
    # FIXED: Always create a fresh event loop in Celery workers to avoid reuse issues
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_run_sync())
        finally:
            try:
                loop.close()
            except:
                pass
    except RuntimeError as e:
        logger.error(f"Event loop error in sync_single_list_async: {e}")
        raise


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def populate_new_list_async(
    self,
    list_id: int,
    user_id: int,
    discovery: str = "balanced",
    media_types: list = None,
    items_per_list: int = 20,
    fusion_mode: bool = False,
    list_type: str = "smartlist"
):
    """
    Async task to populate a newly created list with scored candidates.
    
    Args:
        list_id: ID of the list to populate
        user_id: User ID for scoring
        discovery: Discovery strategy (obscure/popular/balanced)
        media_types: List of media types to include
        items_per_list: Number of items to add
        fusion_mode: Whether to use fusion scoring
        list_type: Type of list being populated
    """
    from app.core.database import SessionLocal
    from app.models import UserList, ListItem, PersistentCandidate
    from app.services.bulk_candidate_provider import BulkCandidateProvider
    from app.services.scoring_engine import ScoringEngine
    from app.services.fusion import FusionEngine
    from app.core.redis_client import get_redis_sync
    from app.services.trakt_client import TraktClient
    
    r = get_redis_sync()  # Use sync Redis client
    status_key = f"list_populate:{list_id}:status"
    db = None
    
    async def _enrich_item_metadata(item: dict, trakt_client: TraktClient, db_session) -> dict:
        """
        Enrich item with trakt_id and title if missing.
        Updates persistent_candidates table if we find new data.
        """
        trakt_id = item.get("ids", {}).get("trakt")
        tmdb_id = item.get("ids", {}).get("tmdb")
        title = item.get("title", "")
        
        # If we already have trakt_id and title, we're good
        if trakt_id and title:
            return item
        
        # If we have tmdb_id but missing trakt_id, fetch from Trakt
        if tmdb_id and not trakt_id:
            try:
                media_type = "movie" if item.get("media_type") == "movie" else "show"
                results = await trakt_client.search_by_tmdb_id(tmdb_id, media_type=media_type)
                
                if results:
                    result = results[0]  # Take first match
                    if media_type == "movie" and "movie" in result:
                        trakt_data = result["movie"]
                        trakt_id = trakt_data.get("ids", {}).get("trakt")
                        title = trakt_data.get("title", title or "Unknown")
                    elif media_type == "show" and "show" in result:
                        trakt_data = result["show"]
                        trakt_id = trakt_data.get("ids", {}).get("trakt")
                        title = trakt_data.get("title", title or "Unknown")
                    
                    # Update persistent_candidates if we found trakt_id
                    if trakt_id:
                        try:
                            pc = db_session.query(PersistentCandidate).filter(
                                PersistentCandidate.tmdb_id == tmdb_id
                            ).first()
                            if pc and not pc.trakt_id:
                                pc.trakt_id = trakt_id
                                if not pc.title or pc.title.strip() == "":
                                    pc.title = title
                                db_session.commit()
                                logger.info(f"Updated persistent_candidate tmdb={tmdb_id} with trakt_id={trakt_id}")
                        except Exception as e:
                            logger.error(f"Failed to update persistent_candidate: {e}")
                            db_session.rollback()
                    
                    # Update item dict
                    if "ids" not in item:
                        item["ids"] = {}
                    item["ids"]["trakt"] = trakt_id
                    item["title"] = title
                    
            except Exception as e:
                logger.warning(f"Failed to enrich item with tmdb_id={tmdb_id}: {e}")
        
        return item
    
    try:
        # Set initial status
        r.set(status_key, json.dumps({
            "status": "running",
            "list_id": list_id,
            "started_at": time.time(),
            "progress": 10,
            "message": "Fetching candidates..."
        }), ex=3600)
        
        # Prepare engines
        provider = BulkCandidateProvider(user_id)
        se = ScoringEngine()
        fusion = FusionEngine(user_id=user_id) if fusion_mode else None
        
        # Fetch candidates
        if media_types is None:
            media_types = ["movies", "shows"]
        
        r.set(status_key, json.dumps({
            "status": "running",
            "list_id": list_id,
            "progress": 30,
            "message": f"Sourcing {', '.join(media_types)}..."
        }), ex=3600)
        
        candidates = []
        
        # Get candidates using async method in new event loop
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            for mt in media_types:
                try:
                    enhanced_discovery = discovery
                    enhanced_limit = max(50, items_per_list * 3)
                    
                    if list_type == "smartlist" or enhanced_limit >= 500:
                        enhanced_discovery = "ultra_discovery"
                        enhanced_limit = min(enhanced_limit * 4, 3000)
                    
                    batch = loop.run_until_complete(provider.get_candidates(
                        media_type=mt,
                        limit=enhanced_limit,
                        discovery=enhanced_discovery,
                        include_watched=False,
                        enrich_with_tmdb=False  # Skip TMDB to avoid extra async complexity
                    ))
                    candidates.extend(batch)
                except Exception as e:
                    logger.error(f"Failed to get candidates for {mt}: {e}")
        finally:
            loop.close()
        
        if hasattr(provider, 'db') and provider.db:
            provider.db.close()
        
        # Enrich candidates with trakt_id if missing
        r.set(status_key, json.dumps({
            "status": "running",
            "list_id": list_id,
            "progress": 45,
            "message": "Enriching metadata..."
        }), ex=3600)
        
        # Create new DB session and Trakt client for enrichment
        db = SessionLocal()
        enrichment_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(enrichment_loop)
        try:
            trakt_client = TraktClient(user_id=user_id)
            
            # Enrich items missing trakt_id
            enriched_candidates = []
            for candidate in candidates:
                trakt_id = candidate.get('ids', {}).get('trakt')
                tmdb_id = candidate.get('ids', {}).get('tmdb')
                
                # Only enrich if we have tmdb_id but missing trakt_id
                if tmdb_id and not trakt_id:
                    try:
                        enriched = enrichment_loop.run_until_complete(
                            _enrich_item_metadata(candidate, trakt_client, db)
                        )
                        enriched_candidates.append(enriched)
                    except Exception as e:
                        logger.warning(f"Failed to enrich candidate tmdb={tmdb_id}: {e}")
                        enriched_candidates.append(candidate)  # Keep original
                else:
                    enriched_candidates.append(candidate)
            
            candidates = enriched_candidates
            
        finally:
            enrichment_loop.close()
        
        # Score candidates
        r.set(status_key, json.dumps({
            "status": "running",
            "list_id": list_id,
            "progress": 60,
            "message": "Scoring candidates..."
        }), ex=3600)
        
        scored = []
        if fusion:
            # Fusion requires async, run in new event loop
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                fused = loop.run_until_complete(fusion.fuse(
                    user={"id": user_id},
                    candidates=candidates,
                    list_type=list_type,
                    media_type=(media_types[0] if media_types else "movies"),
                    limit=items_per_list
                ))
                # Build candidate map with both trakt_id and tmdb_id as keys
                candidate_map = {}
                for c in candidates:
                    trakt = c.get('ids', {}).get('trakt')
                    tmdb = c.get('ids', {}).get('tmdb')
                    if trakt:
                        candidate_map[f"trakt_{trakt}"] = c
                    if tmdb:
                        candidate_map[f"tmdb_{tmdb}"] = c
                
                for it in fused:
                    trakt_id = it.get("trakt_id")
                    tmdb_id = it.get("tmdb_id")
                    
                    # Try to find original candidate by trakt_id first, then tmdb_id
                    orig = None
                    if trakt_id:
                        orig = candidate_map.get(f"trakt_{trakt_id}")
                    if not orig and tmdb_id:
                        orig = candidate_map.get(f"tmdb_{tmdb_id}")
                    
                    if not orig:
                        orig = {}
                    
                    scored.append({
                        "trakt_id": trakt_id,
                        "tmdb_id": tmdb_id,
                        "media_type": it.get("media_type"),
                        "title": orig.get("title", "Unknown"),
                        "year": orig.get("year"),
                        "score": it.get("fusion_score", it.get("final_score", 0)),
                        "explanation": f"Fusion score: {it.get('fusion_score', 0):.2f}"
                    })
            finally:
                loop.close()
        else:
            ranked = se.score_candidates(
                user={"id": user_id},
                candidates=candidates,
                list_type=list_type,
                item_limit=items_per_list
            )
            # Build candidate map with both trakt_id and tmdb_id as keys
            candidate_map = {}
            for c in candidates:
                trakt = c.get('ids', {}).get('trakt')
                tmdb = c.get('ids', {}).get('tmdb')
                if trakt:
                    candidate_map[f"trakt_{trakt}"] = c
                if tmdb:
                    candidate_map[f"tmdb_{tmdb}"] = c
            
            for it in ranked:
                trakt_id = it.get("trakt_id")
                tmdb_id = it.get("tmdb_id")
                
                # Try to find original candidate by trakt_id first, then tmdb_id
                orig = None
                if trakt_id:
                    orig = candidate_map.get(f"trakt_{trakt_id}")
                if not orig and tmdb_id:
                    orig = candidate_map.get(f"tmdb_{tmdb_id}")
                
                if not orig:
                    orig = {}
                
                scored.append({
                    "trakt_id": trakt_id,
                    "tmdb_id": tmdb_id,
                    "media_type": it.get("media_type"),
                    "title": orig.get("title", "Unknown"),
                    "year": orig.get("year"),
                    "score": it.get("final_score", 0),
                    "explanation": it.get("explanation_text", "")
                })
        
        # Persist to database
        r.set(status_key, json.dumps({
            "status": "running",
            "list_id": list_id,
            "progress": 90,
            "message": "Saving items..."
        }), ex=3600)
        
        db = SessionLocal()
        to_write = scored[:items_per_list]
        for it in to_write:
            # Ensure we have valid trakt_id or tmdb_id
            trakt_id = it.get("trakt_id")
            tmdb_id = it.get("tmdb_id")
            
            if not trakt_id and not tmdb_id:
                logger.warning(f"Skipping item without trakt_id or tmdb_id: {it.get('title')}")
                continue
            
            # Use trakt_id as primary item_id, fall back to tmdb_id
            item_id = str(trakt_id) if trakt_id else f"tmdb_{tmdb_id}"
            
            li = ListItem(
                smartlist_id=list_id,
                item_id=item_id,
                trakt_id=trakt_id,
                title=it.get("title", "Unknown"),
                media_type=it.get("media_type") or "movie",
                score=it.get("score", 0.0),
                explanation=it.get("explanation", "") or f"Score: {it.get('score', 0.0):.2f}"
            )
            db.add(li)
        
        db.commit()
        
        # Update list status
        user_list = db.query(UserList).filter(UserList.id == list_id).first()
        if user_list:
            user_list.last_sync_at = datetime.utcnow()
            user_list.sync_status = "complete"
            db.commit()
            
            # Push initial items to Trakt if list exists there
            try:
                if user_list.trakt_list_id:
                    trakt_items = []
                    for it in to_write:
                        if it.get("trakt_id"):
                            trakt_items.append({
                                "trakt_id": it.get("trakt_id"),
                                "media_type": it.get("media_type") or "movie"
                            })
                    if trakt_items:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            trakt_client = TraktClient(user_id=user_id)
                            loop.run_until_complete(trakt_client.add_items_to_list(user_list.trakt_list_id, trakt_items))
                            logger.info(f"Pushed {len(trakt_items)} items to Trakt list {user_list.trakt_list_id}")
                            # Notify success
                            from app.api.notifications import send_notification
                            try:
                                loop.run_until_complete(send_notification(user_id, f"Pushed {len(trakt_items)} items to Trakt for '{user_list.title}'", "success"))
                            except Exception:
                                pass
                        finally:
                            loop.close()
            except Exception as e:
                logger.warning(f"Failed to push initial items to Trakt for list {list_id}: {e}")
                from app.api.notifications import send_notification
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(send_notification(user_id, f"Failed to push items to Trakt for '{user_list.title}'", "warning"))
                except Exception:
                    pass
                finally:
                    loop.close()
        
        # Set completion status
        r.set(status_key, json.dumps({
            "status": "complete",
            "list_id": list_id,
            "items_added": len(to_write),
            "completed_at": time.time(),
            "progress": 100,
            "message": f"Added {len(to_write)} items"
        }), ex=600)
        
        logger.info(f"List population completed: {list_id}, added {len(to_write)} items")
        
    except Exception as exc:
        msg = extract_error_message(exc)
        logger.error(f"List population failed for {list_id}: {msg}")
        
        # Store error status
        r.set(status_key, json.dumps({
            "status": "error",
            "list_id": list_id,
            "error": msg,
            "failed_at": time.time(),
            "progress": 0
        }), ex=600)
        
        # Update list status in DB
        try:
            if db is None:
                db = SessionLocal()
            user_list = db.query(UserList).filter(UserList.id == list_id).first()
            if user_list:
                user_list.sync_status = "error"
                db.commit()
        except Exception:
            pass
        
        # Retry logic
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=60 * (2 ** self.request.retries))
        raise
    finally:
        if db:
            db.close()

