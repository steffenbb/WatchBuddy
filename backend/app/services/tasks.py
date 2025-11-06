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
from app.core.redis_client import get_redis_sync
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

def send_toast_notification_sync(user_id: int, message: str, notification_type: str = "info"):
    """Synchronous version for Celery workers to avoid event loop issues."""
    try:
        r = get_redis_sync()
        notification = {
            "user_id": user_id,
            "message": message,
            "type": notification_type,
            "timestamp": int(time.time())
        }
        r.publish(f"notifications:{user_id}", json.dumps(notification))
    except Exception as e:
        logger.warning(f"Failed to publish sync notification: {e}")

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
    """Send user notification via synchronous Redis to avoid event loop conflicts."""
    try:
        send_toast_notification_sync(user_id, message, "info")
    except Exception as e:
        logger.error(f"send_user_notification failed: {e}")

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
                # Run metadata builder (retry missing trakt_ids) - 4 hour limit
                try:
                    build_metadata.apply_async(
                        args=[1, False],  # user_id, force
                        soft_time_limit=14400,  # 4 hours
                        time_limit=14400 + 300  # 4h + 5min grace period
                    )
                except Exception as e:
                    logger.warning(f"Nightly: failed to queue metadata build: {e}")
                # Run comprehensive AI optimization for all candidates - 2 hour limit
                try:
                    await _backfill_ai_segments(max_minutes=120)  # 2 hours max
                except Exception as e:
                    logger.warning(f"Nightly: AI optimization failed: {e}")
                # Rebuild ElasticSearch index after embeddings and FAISS updates - 1 hour limit
                try:
                    await asyncio.wait_for(
                        _rebuild_elasticsearch_index(),
                        timeout=3600  # 1 hour timeout
                    )
                except asyncio.TimeoutError:
                    logger.warning("Nightly: ElasticSearch index rebuild timed out after 1 hour")
                except Exception as e:
                    logger.warning(f"Nightly: ElasticSearch index rebuild failed: {e}")
        except Exception as e:
            logger.warning(f"Nightly maintenance dispatcher error: {e}")
    # Use a fresh event loop to avoid "Event loop is closed" in forked workers
    loop = None
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_maybe_run())
    finally:
        if loop:
            try:
                loop.close()
            except Exception:
                pass

@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def sync_user_lists(self, user_id: int = 1, force_full: bool = False):
    """Periodically sync all user lists (including custom lists) honoring each list's sync_interval.

    This task scans the user's lists and, for each one, uses ListSyncService's scheduling logic
    to decide full/incremental/skip. It ensures custom lists with a non-null sync_interval are
    picked up automatically without requiring manual syncs.

    Args:
        user_id: The user to sync (default single-user mode: 1)
        force_full: If True, force full sync for all lists regardless of interval
    """
    async def _run():
        try:
            from app.services.list_sync import ListSyncService
            svc = ListSyncService(user_id=user_id)
            result = await svc.sync_all_lists(force_full=force_full)
            logger.info(f"sync_user_lists completed for user {user_id}: {result.get('synced',0)} synced, {result.get('errors',0)} errors")
            return result
        except Exception as e:
            logger.error(f"sync_user_lists failed: {e}", exc_info=True)
            raise

    # Ensure a fresh event loop inside Celery worker
    loop = None
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(_run())
    finally:
        if loop:
            try:
                loop.close()
            except Exception:
                pass

async def _backfill_ai_segments(max_minutes: int = 420):
    """Comprehensive AI optimization for persistent candidates during nightly maintenance window.
    
    This is NOT lightweight - it's designed to run during the 7-hour nightly window (00:00-07:00)
    to prepare the AI index for fast daytime queries. Processes ALL candidates that need optimization:
    
    1. Generates embeddings for items missing them (prioritizing high-quality content)
    2. Updates FAISS index with FULL rebuild (prevents corruption from incremental adds)
    3. Uses trakt_id for FAISS mapping (better coverage than tmdb_id)
    4. Preserves original overview field (doesn't overwrite with embedding text)
    5. Prioritizes candidates with:
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
        
        # Collect ALL embeddings and trakt_ids for full FAISS rebuild at end
        all_embeddings = []
        all_trakt_ids = []
        
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
                
                # Store embeddings in DB (preserve original overview, don't overwrite!)
                for i, candidate in enumerate(candidates):
                    try:
                        embedding_blob = serialize_embedding(embeddings[i])
                        
                        # Update embedding only, keep original overview intact
                        db.execute(text("""
                            UPDATE persistent_candidates 
                            SET 
                                embedding = :embedding_blob,
                                last_refreshed = :ts
                            WHERE id = :id
                        """), {
                            'embedding_blob': embedding_blob,
                            'ts': utc_now(),
                            'id': candidate_ids[i]
                        })
                    except Exception as e:
                        logger.warning(f"[AI Optimization] Failed to update candidate {candidate_ids[i]}: {e}")
                        db.rollback()
                        continue
                
                # Commit batch
                db.commit()
                processed += len(candidates)
                
                # Collect embeddings and trakt_ids for full FAISS rebuild at end
                all_embeddings.extend(embeddings)
                all_trakt_ids.extend([c['trakt_id'] for c in candidates])
                
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
        
        # Step 3: Full FAISS rebuild with all collected embeddings (atomic, prevents corruption)
        if all_embeddings:
            logger.info(f"[FAISS] Rebuilding entire HNSW index with {len(all_embeddings)} vectors...")
            try:
                from app.services.ai_engine.faiss_index import train_build_hnsw
                import numpy as np
                
                embeddings_array = np.array(all_embeddings, dtype=np.float32)
                train_build_hnsw(embeddings_array, all_trakt_ids, embeddings_array.shape[1])
                logger.info("[FAISS] ✅ Full HNSW index rebuilt successfully")
            except Exception as e:
                logger.error(f"[FAISS] Index rebuild failed: {e}", exc_info=True)
        
        # Step 4: Log completion
        logger.info(
            f"[AI Optimization] Completed: "
            f"{processed} candidates processed, "
            f"{embeddings_generated} embeddings generated, "
            f"FAISS index rebuilt with {len(all_trakt_ids)} vectors | "
            f"Runtime: {(time.time() - start_time)/60:.1f} minutes"
        )
        
        return processed
        
    except Exception as e:
        logger.error(f"[AI Optimization] Fatal error: {e}", exc_info=True)
        db.rollback()
        return 0
    finally:
        db.close()

    def _prepare_es_candidates_batch(rows):
        """
        Prepare a batch of rows for ElasticSearch indexing.
        Converts DB rows to ES document format with JSON field parsing.
        Memory-efficient: processes in-place without accumulating.
        """
        import json
    
        def parse_json_field(field_value):
            """Parse JSON field to searchable text."""
            if not field_value:
                return ""
            try:
                data = json.loads(field_value)
                if isinstance(data, list):
                    return " ".join(str(item) for item in data)
                return str(data)
            except:
                return ""
    
        candidates = []
        for row in rows:
            candidate = {
                "tmdb_id": row.tmdb_id,
                "media_type": row.media_type,
                "title": row.title or "",
                "original_title": row.original_title or "",
                "year": row.year,
                "overview": row.overview or "",
                "tagline": row.tagline or "",
                "genres": parse_json_field(row.genres),
                "keywords": parse_json_field(row.keywords),
                "cast": parse_json_field(row.cast_json),
                "created_by": parse_json_field(row.created_by),
                "networks": parse_json_field(row.networks),
                "production_companies": parse_json_field(row.production_companies),
                "production_countries": parse_json_field(row.production_countries),
                "spoken_languages": parse_json_field(row.spoken_languages),
                "popularity": row.popularity,
                "vote_average": row.vote_average,
                "vote_count": row.vote_count
            }
            candidates.append(candidate)
        return candidates

async def _rebuild_elasticsearch_index():
    """Rebuild ElasticSearch index from persistent candidates (incremental or full).
    
    This indexes all active candidates with trakt_id and embeddings into ElasticSearch
    for fast literal/fuzzy text search (complementing FAISS semantic search).
    
    Strategy:
    - If index exists with reasonable count, only add missing items (incremental)
    - Otherwise, recreate index and bulk load all candidates (full rebuild)
    """
    from app.core.database import SessionLocal
    from sqlalchemy import text
    from app.services.elasticsearch_client import get_elasticsearch_client
    import json
    import time
    
    start_time = time.time()
    db = SessionLocal()
    
    try:
        logger.info("[ElasticSearch] Starting index update")
        
        # Get ElasticSearch client
        es_client = get_elasticsearch_client()
        
        if not es_client.is_connected():
            logger.error("[ElasticSearch] Failed to connect. Ensure service is running.")
            return 0
        
        # Count total candidates (no longer requiring trakt_id + embedding)
        total = db.execute(text(
            "SELECT COUNT(*) FROM persistent_candidates WHERE active=true"
        )).scalar()
        
        logger.info(f"[ElasticSearch] {total} candidates in database")
        
        # Temporarily disable automatic refresh to speed up bulk indexing
        try:
            es_client.es.indices.put_settings(index="watchbuddy_candidates", body={"index": {"refresh_interval": "-1"}})
        except Exception:
            pass

        # Check if index exists and has reasonable count
        index_exists = es_client.es.indices.exists(index="watchbuddy_candidates")
        if index_exists:
            stats = es_client.get_index_stats()
            current_count = stats.get('count', 0)
            logger.info(f"[ElasticSearch] Existing index has {current_count} documents")
            
            # If index has reasonable count (>80% of total), do incremental update
            if current_count > total * 0.8:
                logger.info("[ElasticSearch] Index is mostly up-to-date, will add only new items")
                
                # Use memory-efficient streaming approach with smaller batches
                from app.core.memory_manager import managed_memory, batch_query_iterator
                
                indexed = 0
                batch_size = 250  # Reduced from 500 for better memory efficiency
                
                with managed_memory("elasticsearch_incremental_index"):
                    # Use batch iterator to avoid loading all rows into memory
                    query = text(
                        """
                        SELECT 
                            tmdb_id, media_type, title, original_title, year, overview, tagline,
                            genres, keywords, "cast" AS cast_json, created_by, networks,
                            production_companies, production_countries, spoken_languages,
                            popularity, vote_average, vote_count
                        FROM persistent_candidates
                        WHERE active=true
                        ORDER BY id
                        """
                    )
                    
                    offset = 0
                    while offset < total:
                        # Fetch batch (streaming approach)
                        rows = db.execute(text(
                            """
                            SELECT 
                                tmdb_id, media_type, title, original_title, year, overview, tagline,
                                genres, keywords, "cast" AS cast_json, created_by, networks,
                                production_companies, production_countries, spoken_languages,
                                popularity, vote_average, vote_count
                            FROM persistent_candidates
                            WHERE active=true
                            ORDER BY id
                            OFFSET :off LIMIT :lim
                            """
                        ), {"off": offset, "lim": batch_size}).fetchall()
                        
                        if not rows:
                            break
                        
                        # Prepare candidates for indexing (process immediately, don't accumulate)
                            candidates = _prepare_es_candidates_batch(rows)
                        
                        # Index batch immediately (stream to ES, don't accumulate)
                        count = es_client.index_candidates(candidates)
                        indexed += count
                        offset += len(rows)
                        
                        # Clear batch data to free memory
                        candidates.clear()
                        del rows
                        
                        # Log progress every 10k items
                        if indexed % 10000 == 0 or indexed == total:
                            elapsed_min = (time.time() - start_time) / 60
                            logger.info(f"[ElasticSearch] Progress: {indexed}/{total} processed ({indexed/total*100:.1f}%)")
                
                elapsed_min = (time.time() - start_time) / 60
                logger.info(f"[ElasticSearch] ✅ Incremental update complete! {indexed} items processed in {elapsed_min:.1f} minutes")
                
                return indexed
        
        # Full rebuild: Create/recreate index with mapping
        logger.info("[ElasticSearch] Performing full rebuild (creating fresh index)...")
        if not es_client.create_index():
            logger.error("[ElasticSearch] Failed to create index")
            return 0
        
            from app.core.memory_manager import managed_memory
        
        indexed = 0
        offset = 0
            batch_size = 250  # Reduced for better memory efficiency
        
            with managed_memory("elasticsearch_full_rebuild"):
                while offset < total:
                    # Fetch batch
                    rows = db.execute(text(
                        """
                        SELECT 
                            tmdb_id, media_type, title, original_title, year, overview, tagline,
                            genres, keywords, "cast" AS cast_json, created_by, networks,
                            production_companies, production_countries, spoken_languages,
                            popularity, vote_average, vote_count
                        FROM persistent_candidates
                        WHERE active=true
                        ORDER BY id
                        OFFSET :off LIMIT :lim
                        """
                    ), {"off": offset, "lim": batch_size}).fetchall()
                
                    if not rows:
                        break
                
                    # Prepare candidates using helper function
                    candidates = _prepare_es_candidates_batch(rows)
                
                    # Index batch immediately
                    count = es_client.index_candidates(candidates)
                    indexed += count
                    offset += len(rows)
                
                    # Clear to free memory
                    candidates.clear()
                    del rows
                
                    # Log progress every 5k items
                    if indexed % 5000 == 0 or indexed == total:
                        elapsed_min = (time.time() - start_time) / 60
                        logger.info(f"[ElasticSearch] Progress: {indexed}/{total} indexed ({indexed/total*100:.1f}%)")
        
        elapsed_min = (time.time() - start_time) / 60
        logger.info(f"[ElasticSearch] ✅ Full rebuild complete! {indexed} items indexed in {elapsed_min:.1f} minutes")
        
        # Show stats
        stats = es_client.get_index_stats()
        logger.info(f"[ElasticSearch] Index stats: {stats}")
        
        return indexed
        
    except Exception as e:
        logger.error(f"[ElasticSearch] Fatal error during index rebuild: {e}", exc_info=True)
        return 0
    finally:
        # Restore refresh interval and refresh index
        try:
            es_client = None
            try:
                from app.services.elasticsearch_client import get_elasticsearch_client
                es_client = get_elasticsearch_client()
            except Exception:
                es_client = None
            if es_client and es_client.es:
                es_client.es.indices.put_settings(index="watchbuddy_candidates", body={"index": {"refresh_interval": "30s"}})
                es_client.es.indices.refresh(index="watchbuddy_candidates")
        except Exception:
            pass
        db.close()

@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def rebuild_elasticsearch_task(self):
    """Celery task to rebuild Elasticsearch index."""
    import asyncio
    try:
        indexed = asyncio.run(_rebuild_elasticsearch_index())
        logger.info(f"Elasticsearch rebuild task completed: {indexed} items indexed")
        return indexed
    except Exception as e:
        logger.exception(f"Elasticsearch rebuild task failed: {e}")
        raise self.retry(exc=e)

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


# ========================================
# Phase Detection Tasks
# ========================================

@shared_task(bind=True, max_retries=2, default_retry_delay=120)
def compute_user_phases_task(self, user_id: int):
    """
    Compute viewing phases for user from watch history.
    Runs daily or on manual refresh.
    """
    logger.info(f"[PhaseTask] Starting phase computation for user {user_id}")
    
    try:
        from app.services.phase_detector import PhaseDetector
        
        detector = PhaseDetector(user_id)
        phases = detector.detect_all_phases()
        
        logger.info(f"[PhaseTask] ✅ Computed {len(phases)} phases for user {user_id}")
        
        # Clear phase caches (all related keys)
        r = get_redis_sync()
        try:
            r.delete(f"phase:current:{user_id}")
            r.delete(f"phase:timeline:{user_id}")
            # Delete history/detail patterns
            for key in r.scan_iter(f"phase:history:{user_id}:*"):
                r.delete(key)
            for key in r.scan_iter(f"phase:detail:{user_id}:*"):
                r.delete(key)
        except Exception:
            pass
        
        # Send notification
        asyncio.run(send_toast_notification(
            user_id,
            f"Phase detection complete - found {len(phases)} viewing phases",
            "success"
        ))
        
        return {"user_id": user_id, "phases_detected": len(phases)}
        
    except Exception as exc:
        msg = extract_error_message(exc)
        logger.error(f"[PhaseTask] Failed for user {user_id}: {msg}")
        
        asyncio.run(send_toast_notification(
            user_id,
            f"Phase detection failed: {msg[:100]}",
            "error"
        ))
        
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=120 * (2 ** self.request.retries))
        raise


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def sync_user_watch_history_task(self, user_id: int, full_sync: bool = True):
    """
    Sync user's Trakt watch history to database.
    Runs after Trakt OAuth and daily for incremental updates.
    """
    logger.info(f"[WatchHistoryTask] Starting {'full' if full_sync else 'incremental'} sync for user {user_id}")
    
    try:
        from app.services.watch_history_sync import sync_user_watch_history
        
        stats = asyncio.run(sync_user_watch_history(user_id, full_sync=full_sync))
        
        logger.info(f"[WatchHistoryTask] ✅ Synced {stats['total']} watch events for user {user_id}")
        
        # Trigger phase detection after history sync
        if stats['new'] > 0:
            compute_user_phases_task.delay(user_id)
        
        asyncio.run(send_toast_notification(
            user_id,
            f"Watch history synced - {stats['new']} new items",
            "success"
        ))
        
        return stats
        
    except Exception as exc:
        msg = extract_error_message(exc)
        logger.error(f"[WatchHistoryTask] Failed for user {user_id}: {msg}")
        
        asyncio.run(send_toast_notification(
            user_id,
            f"Watch history sync failed: {msg[:100]}",
            "error"
        ))
        
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=60 * (2 ** self.request.retries))
        raise


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def compute_user_overview_task(self, user_id: int = 1, skip_recent_days: int = 7):
    """
    Nightly task: Compute all Overview modules and cache results.
    
    Pipeline:
    1. Fetch TMDB trending/popular/upcoming
    2. Queue targeted ingestion for missing items
    3. Wait for ingestion to complete
    4. Sync user ratings from Trakt
    5. Compute all 4 overview modules
    6. Cache results in OverviewCache table
    
    Runs overnight (scheduled via Celery Beat)
    """
    logger.info(f"[OverviewTask] Starting overview computation for user {user_id}")
    
    try:
        from app.services.tmdb_client import fetch_tmdb_trending, fetch_tmdb_popular, fetch_tmdb_upcoming
        from app.services.candidate_ingestion import ingest_specific_tmdb_ids
        from app.services.watch_history_sync import sync_user_ratings
        from app.services.overview_service import OverviewService
        from app.models import TrendingIngestionQueue, PersistentCandidate
        from app.core.database import SessionLocal
        
        db = SessionLocal()
        
        try:
            # Step 1: Fetch trending/popular/upcoming from TMDB
            logger.info("[OverviewTask] Fetching TMDB lists...")
            
            trending_movies_data = asyncio.run(fetch_tmdb_trending('movie', 'week', page=1))
            trending_tv_data = asyncio.run(fetch_tmdb_trending('tv', 'week', page=1))
            popular_movies_data = asyncio.run(fetch_tmdb_popular('movie', page=1))
            upcoming_data = asyncio.run(fetch_tmdb_upcoming(page=1))
            
            # Extract TMDB IDs
            trending_movie_ids = [item['id'] for item in trending_movies_data.get('results', [])] if trending_movies_data else []
            trending_tv_ids = [item['id'] for item in trending_tv_data.get('results', [])] if trending_tv_data else []
            popular_movie_ids = [item['id'] for item in popular_movies_data.get('results', [])] if popular_movies_data else []
            upcoming_ids = [item['id'] for item in upcoming_data.get('results', [])] if upcoming_data else []
            
            logger.info(f"[OverviewTask] Fetched: {len(trending_movie_ids)} trending movies, {len(trending_tv_ids)} trending shows, {len(upcoming_ids)} upcoming")
            
            # Step 2: Add to ingestion queue
            logger.info("[OverviewTask] Adding to ingestion queue...")
            
            for tmdb_id in trending_movie_ids:
                # Check if already exists
                existing = db.query(TrendingIngestionQueue).filter_by(
                    tmdb_id=tmdb_id,
                    media_type='movie',
                    source_list='trending'
                ).first()
                
                if not existing:
                    queue_item = TrendingIngestionQueue(
                        tmdb_id=tmdb_id,
                        media_type='movie',
                        source_list='trending',
                        priority=100 - trending_movie_ids.index(tmdb_id),  # Higher priority for top items
                        status='pending'
                    )
                    db.add(queue_item)
            
            for tmdb_id in trending_tv_ids:
                existing = db.query(TrendingIngestionQueue).filter_by(
                    tmdb_id=tmdb_id,
                    media_type='show',
                    source_list='trending'
                ).first()
                
                if not existing:
                    queue_item = TrendingIngestionQueue(
                        tmdb_id=tmdb_id,
                        media_type='show',
                        source_list='trending',
                        priority=100 - trending_tv_ids.index(tmdb_id),
                        status='pending'
                    )
                    db.add(queue_item)
            
            for tmdb_id in upcoming_ids:
                existing = db.query(TrendingIngestionQueue).filter_by(
                    tmdb_id=tmdb_id,
                    media_type='movie',
                    source_list='upcoming'
                ).first()
                
                if not existing:
                    queue_item = TrendingIngestionQueue(
                        tmdb_id=tmdb_id,
                        media_type='movie',
                        source_list='upcoming',
                        priority=50 - upcoming_ids.index(tmdb_id),
                        status='pending'
                    )
                    db.add(queue_item)
            
            db.commit()
            
            # Step 3: Trigger ingestion for pending items
            logger.info("[OverviewTask] Running targeted ingestion...")
            
            pending_queue = db.query(TrendingIngestionQueue).filter_by(status='pending').all()
            
            movie_ids_to_ingest = [q.tmdb_id for q in pending_queue if q.media_type == 'movie']
            show_ids_to_ingest = [q.tmdb_id for q in pending_queue if q.media_type == 'show']
            
            if movie_ids_to_ingest:
                stats_movies = asyncio.run(ingest_specific_tmdb_ids(movie_ids_to_ingest, 'movies', 'trending/upcoming', skip_recent_days=skip_recent_days))
                logger.info(f"[OverviewTask] Movie ingestion: {stats_movies}")
                
                # Mark as completed and populate trakt_ids from persistent_candidates
                for tmdb_id in movie_ids_to_ingest:
                    pc = db.query(PersistentCandidate).filter_by(tmdb_id=tmdb_id, media_type='movie').first()
                    if pc and pc.trakt_id:
                        db.query(TrendingIngestionQueue).filter(
                            TrendingIngestionQueue.tmdb_id == tmdb_id,
                            TrendingIngestionQueue.media_type == 'movie'
                        ).update({'status': 'completed', 'trakt_id': pc.trakt_id, 'ingested_at': utc_now()}, synchronize_session=False)
                    else:
                        db.query(TrendingIngestionQueue).filter(
                            TrendingIngestionQueue.tmdb_id == tmdb_id,
                            TrendingIngestionQueue.media_type == 'movie'
                        ).update({'status': 'completed', 'ingested_at': utc_now()}, synchronize_session=False)
            
            if show_ids_to_ingest:
                stats_shows = asyncio.run(ingest_specific_tmdb_ids(show_ids_to_ingest, 'shows', 'trending', skip_recent_days=skip_recent_days))
                logger.info(f"[OverviewTask] Show ingestion: {stats_shows}")
                
                # Mark as completed and populate trakt_ids from persistent_candidates
                for tmdb_id in show_ids_to_ingest:
                    pc = db.query(PersistentCandidate).filter_by(tmdb_id=tmdb_id, media_type='show').first()
                    if pc and pc.trakt_id:
                        db.query(TrendingIngestionQueue).filter(
                            TrendingIngestionQueue.tmdb_id == tmdb_id,
                            TrendingIngestionQueue.media_type == 'show'
                        ).update({'status': 'completed', 'trakt_id': pc.trakt_id, 'ingested_at': utc_now()}, synchronize_session=False)
                    else:
                        db.query(TrendingIngestionQueue).filter(
                            TrendingIngestionQueue.tmdb_id == tmdb_id,
                            TrendingIngestionQueue.media_type == 'show'
                        ).update({'status': 'completed', 'ingested_at': utc_now()}, synchronize_session=False)
            
            db.commit()
            
            # Step 4: Sync user ratings from Trakt
            logger.info("[OverviewTask] Syncing user ratings...")
            rating_stats = asyncio.run(sync_user_ratings(user_id))
            logger.info(f"[OverviewTask] Rating sync: {rating_stats}")
            
            # Close old session and create fresh one for overview computation
            db.close()
            db = SessionLocal()
            
            # Step 5: Compute all overview modules
            logger.info("[OverviewTask] Computing overview modules...")
            service = OverviewService(user_id)
            result = asyncio.run(service.compute_all_modules(db))
            
            logger.info(f"[OverviewTask] ✅ Overview computed successfully: {result}")
            
            asyncio.run(send_toast_notification(
                user_id,
                f"Your Overview updated with {result.get('cached_count', 0)} modules",
                "success"
            ))
            
            return result
            
        finally:
            db.close()
        
    except Exception as exc:
        msg = extract_error_message(exc)
        logger.error(f"[OverviewTask] Failed for user {user_id}: {msg}")
        
        asyncio.run(send_toast_notification(
            user_id,
            f"Overview update failed: {msg[:100]}",
            "error"
        ))
        
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=300 * (2 ** self.request.retries))
        raise

