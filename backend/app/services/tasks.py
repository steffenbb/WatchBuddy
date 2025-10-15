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
from celery import shared_task
from celery.exceptions import Retry
from app.core.redis_client import get_redis
from app.services.scoring_engine import ScoringEngine
from app.services.mood import get_user_mood
from app.api.notifications import send_notification as persistent_send_notification

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
    await persistent_send_notification(user_id, message, notification_type)

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
        from app.services.candidate_ingestion import ingest_new_content
        try:
            await ingest_new_content('movies')
        except Exception as e:
            logger.error(f"ingest_new_movies failed: {e}")
    asyncio.run(_run())

@shared_task
def ingest_new_shows():
    """Celery task to ingest new show candidates incrementally."""
    async def _run():
        from app.services.candidate_ingestion import ingest_new_content
        try:
            await ingest_new_content('shows')
        except Exception as e:
            logger.error(f"ingest_new_shows failed: {e}")
    asyncio.run(_run())

@shared_task
def refresh_recent_votes_movies():
    """Refresh vote stats for recent movies."""
    async def _run():
        from app.services.candidate_ingestion import refresh_recent_votes
        try:
            await refresh_recent_votes('movies')
        except Exception as e:
            logger.error(f"refresh_recent_votes_movies failed: {e}")
    asyncio.run(_run())

@shared_task
def refresh_recent_votes_shows():
    """Refresh vote stats for recent shows."""
    async def _run():
        from app.services.candidate_ingestion import refresh_recent_votes
        try:
            await refresh_recent_votes('shows')
        except Exception as e:
            logger.error(f"refresh_recent_votes_shows failed: {e}")
    asyncio.run(_run())

@shared_task(bind=True)
def build_metadata(self, user_id: int = 1, force: bool = False):
    """
    Build Trakt IDs for persistent candidates with progress tracking.
    
    Args:
        user_id: User ID for Trakt authentication
        force: Force rebuild even if already complete
    """
    async def _run():
        from app.core.database import SessionLocal
        from app.services.metadata_builder import MetadataBuilder
        
        db = SessionLocal()
        try:
            builder = MetadataBuilder()
            logger.info(f"Starting metadata build (user_id={user_id}, force={force})")
            await builder.build_trakt_ids(db, user_id=user_id, force=force)
            logger.info("Metadata build completed successfully")
        except Exception as e:
            logger.error(f"Metadata build failed: {e}")
            raise
        finally:
            db.close()
    
    asyncio.run(_run())
