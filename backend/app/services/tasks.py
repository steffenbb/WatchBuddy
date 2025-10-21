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
from app.utils.timezone import utc_now
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

@shared_task(bind=True, max_retries=3)
def send_user_notification(self, user_id: int, message: str):
    """Send user notification."""
    async def _send_notification():
        await send_toast_notification(user_id, message, "info")
    
    asyncio.run(_send_notification())

@shared_task(bind=True, max_retries=3)
def run_nightly_maintenance(self):
    """Check timezone-aware local time and, if within 7 hours from local midnight, run metadata builder and AI index prerequisites.
    - Timezone loaded from Redis settings: settings:global:user_timezone
    - Window: [00:00, 07:00) local time
    - Always runs on new installs as well; safe to no-op if already complete
    """
    async def _maybe_run():
        try:
            from app.core.redis_client import get_redis
            from app.utils.timezone import utc_now
            import pytz
            r = get_redis()
            tz_str = await r.get("settings:global:user_timezone")
            tz_name = tz_str.decode("utf-8") if isinstance(tz_str, bytes) else (tz_str or "UTC")
            try:
                tz = pytz.timezone(tz_name)
            except Exception:
                tz = pytz.UTC
            now_local = utc_now().astimezone(tz)
            if 0 <= now_local.hour < 7:
                # Run metadata builder (retry missing trakt_ids)
                try:
                    build_metadata.delay(user_id=1, force=False)
                except Exception as e:
                    logger.warning(f"Nightly: failed to queue metadata build: {e}")
                # Run comprehensive AI optimization for all candidates
                try:
                    await _backfill_ai_segments(max_minutes=420)  # 7 hours max
                except Exception as e:
                    logger.warning(f"Nightly: AI optimization failed: {e}")
        except Exception as e:
            logger.warning(f"Nightly maintenance dispatcher error: {e}")
    asyncio.run(_maybe_run())

async def _backfill_ai_segments(max_minutes: int = 420):
    """Comprehensive AI optimization for persistent candidates during nightly maintenance window.
    
    This is NOT lightweight - it's designed to run during the 7-hour nightly window (00:00-07:00)
    to prepare the AI index for fast daytime queries. Processes ALL candidates that need optimization:
    
    1. Generates embeddings for items missing them (prioritizing high-quality content)
    2. Builds rich text segments from metadata (title, overview, genres, keywords, cast, studios)
    3. Updates FAISS index incrementally
    4. Prioritizes candidates with:
       - Good ratings (vote_average >= 6.0)
       - Sufficient vote count (vote_count >= 50)
       - Complete metadata (cast, genres, keywords populated)
    
    Args:
        max_minutes: Maximum runtime in minutes (default 420 = 7 hours)
    
    Returns:
        Number of candidates processed
    """
    from app.core.database import SessionLocal
    from sqlalchemy import text, and_, or_
    from app.utils.timezone import utc_now
    from app.services.ai_engine.embeddings import EmbeddingService
    from app.services.ai_engine.metadata_processing import compose_text_for_embedding
    from app.models import PersistentCandidate
    import time
    import json
    
    start_time = time.time()
    max_seconds = max_minutes * 60
    db = SessionLocal()
    
    try:
        logger.info(f"[AI Optimization] Starting comprehensive nightly backfill (max {max_minutes} minutes)")
        
        # Step 1: Identify candidates needing AI optimization
        # Priority: Active items with trakt_id, ordered by quality and completeness
        sql = text("""
            SELECT 
                id, tmdb_id, trakt_id, media_type, title, original_title, 
                overview, genres, keywords, "cast", production_companies,
                vote_average, vote_count, popularity, year, language, runtime,
                tagline, homepage, budget, revenue,
                production_countries, spoken_languages,
                networks, created_by, number_of_seasons, number_of_episodes,
                episode_run_time, first_air_date, last_air_date, in_production,
                status
            FROM persistent_candidates
            WHERE 
                active = true 
                AND trakt_id IS NOT NULL
                AND tmdb_id IS NOT NULL
                AND embedding IS NULL
            ORDER BY 
                -- Prioritize high-quality content
                CASE 
                    WHEN vote_average >= 7.0 AND vote_count >= 100 THEN 1
                    WHEN vote_average >= 6.0 AND vote_count >= 50 THEN 2
                    WHEN vote_count >= 20 THEN 3
                    ELSE 4
                END,
                -- Then by completeness (more metadata = better)
                CASE 
                    WHEN "cast" IS NOT NULL AND genres IS NOT NULL AND keywords IS NOT NULL THEN 1
                    WHEN genres IS NOT NULL AND keywords IS NOT NULL THEN 2
                    WHEN overview IS NOT NULL AND overview != '' THEN 3
                    ELSE 4
                END,
                -- Finally by popularity and recency
                popularity DESC,
                year DESC NULLS LAST,
                inserted_at DESC
        """)
        
        rows = db.execute(sql).fetchall()
        total_candidates = len(rows)
        logger.info(f"[AI Optimization] Found {total_candidates} candidates to process")
        
        if not rows:
            logger.info("[AI Optimization] No candidates to process")
            return 0
        
        # Step 2: Build rich text segments and generate embeddings in batches
        embedding_service = EmbeddingService()
        batch_size = 64
        processed = 0
        embeddings_generated = 0
        segments_updated = 0
        
        # Process in batches to manage memory
        for batch_start in range(0, total_candidates, batch_size):
            # Check time limit
            elapsed = time.time() - start_time
            if elapsed > max_seconds:
                logger.info(f"[AI Optimization] Time limit reached ({elapsed/60:.1f} minutes), stopping gracefully")
                break
            
            batch_end = min(batch_start + batch_size, total_candidates)
            batch_rows = rows[batch_start:batch_end]
            
            # Build candidate dicts with all metadata
            candidates = []
            candidate_ids = []
            texts_for_embedding = []
            
            for row in batch_rows:
                (rid, tmdb_id, trakt_id, media_type, title, original_title,
                 overview, genres, keywords, cast, production_companies,
                 vote_average, vote_count, popularity, year, language, runtime,
                 tagline, homepage, budget, revenue,
                 production_countries, spoken_languages,
                 networks, created_by, number_of_seasons, number_of_episodes,
                 episode_run_time, first_air_date, last_air_date, in_production,
                 status) = row
                
                candidate = {
                    'id': rid,
                    'tmdb_id': tmdb_id,
                    'trakt_id': trakt_id,
                    'media_type': media_type,
                    'title': title or '',
                    'original_title': original_title or '',
                    'overview': overview or '',
                    'genres': genres or '[]',
                    'keywords': keywords or '[]',
                    'cast': cast or '[]',
                    'production_companies': production_companies or '[]',
                    'vote_average': vote_average or 0,
                    'vote_count': vote_count or 0,
                    'popularity': popularity or 0,
                    'year': year,
                    'language': language or '',
                    'runtime': runtime or 0,
                    'tagline': tagline or '',
                    'homepage': homepage or '',
                    'budget': budget or 0,
                    'revenue': revenue or 0,
                    'production_countries': production_countries or '[]',
                    'spoken_languages': spoken_languages or '[]',
                    'networks': networks or '[]',
                    'created_by': created_by or '[]',
                    'number_of_seasons': number_of_seasons,
                    'number_of_episodes': number_of_episodes,
                    'episode_run_time': episode_run_time or '[]',
                    'first_air_date': first_air_date or '',
                    'last_air_date': last_air_date or '',
                    'in_production': in_production,
                    'status': status or '',
                }
                
                candidates.append(candidate)
                candidate_ids.append(rid)
                
                # Compose rich text for embedding
                text_segment = compose_text_for_embedding(candidate)
                texts_for_embedding.append(text_segment)
            
            # Generate embeddings for entire batch
            try:
                embeddings = embedding_service.encode_texts(texts_for_embedding, batch_size=batch_size)
                embeddings_generated += len(embeddings)
                
                # Import serialization helper
                from app.services.ai_engine.faiss_index import serialize_embedding
                
                # Store embeddings and update segments
                for i, candidate in enumerate(candidates):
                    try:
                        embedding_blob = serialize_embedding(embeddings[i])
                        
                        # Update with rich composed text segment AND store embedding
                        db.execute(text("""
                            UPDATE persistent_candidates 
                            SET 
                                overview = :segment,
                                embedding = :embedding_blob,
                                last_refreshed = :ts
                            WHERE id = :id
                        """), {
                            'segment': texts_for_embedding[i][:5000],  # Store full segment (truncate if needed)
                            'embedding_blob': embedding_blob,
                            'ts': utc_now(),
                            'id': candidate_ids[i]
                        })
                        segments_updated += 1
                    except Exception as e:
                        logger.warning(f"[AI Optimization] Failed to update candidate {candidate_ids[i]}: {e}")
                        db.rollback()
                        continue
                
                # Commit batch
                db.commit()
                processed += len(candidates)
                
                # Add embeddings to FAISS index incrementally
                try:
                    from app.services.ai_engine.faiss_index import add_to_index
                    success = add_to_index(embeddings, [c['tmdb_id'] for c in candidates], embeddings.shape[1])
                    if not success:
                        logger.warning("[AI Optimization] FAISS index not found - will be built on first AI list generation")
                except Exception as e:
                    logger.warning(f"[AI Optimization] FAISS index update failed: {e}")
                
                # Log progress
                if processed % 500 == 0 or processed == total_candidates:
                    elapsed_min = (time.time() - start_time) / 60
                    rate = processed / elapsed_min if elapsed_min > 0 else 0
                    remaining = total_candidates - processed
                    eta_min = remaining / rate if rate > 0 else 0
                    logger.info(
                        f"[AI Optimization] Progress: {processed}/{total_candidates} "
                        f"({processed/total_candidates*100:.1f}%) | "
                        f"Embeddings: {embeddings_generated} | "
                        f"Rate: {rate:.1f}/min | "
                        f"ETA: {eta_min:.1f}min"
                    )
                
            except Exception as e:
                logger.error(f"[AI Optimization] Batch embedding failed: {e}", exc_info=True)
                db.rollback()
                continue
        
        # Step 3: Log completion (FAISS updates happen incrementally above)
        logger.info(
            f"[AI Optimization] Completed: "
            f"{processed} candidates processed, "
            f"{embeddings_generated} embeddings generated, "
            f"{segments_updated} segments updated | "
            f"Runtime: {(time.time() - start_time)/60:.1f} minutes"
        )
        
        return processed
        
    except Exception as e:
        logger.error(f"[AI Optimization] Fatal error: {e}", exc_info=True)
        db.rollback()
        return 0
    finally:
        db.close()

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
                                PersistentCandidate.tmdb_id == tmdb_id,
                                PersistentCandidate.media_type == media_type
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

