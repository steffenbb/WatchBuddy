from celery import Celery
from app.core.config import settings
import os
from app.core.redis_client import get_redis_sync

celery_app = Celery(
    "watchbuddy",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.services.tasks", "app.tasks_ai"]
)

def _get_celery_timezone() -> str:
    """Resolve timezone for Celery from settings storage with safe fallbacks.

    Order of precedence:
    1) Redis key settings:global:user_timezone (set from Settings UI)
    2) Environment variables WATCHBUDDY_TIMEZONE or TZ
    3) Default 'UTC'
    """
    # Default
    tz = "UTC"
    try:
        # Try Redis-backed settings first
        r = get_redis_sync()
        val = r.get("settings:global:user_timezone")
        if val and isinstance(val, str) and len(val) > 0:
            tz = val
        else:
            # Environment fallback
            env_tz = os.getenv("WATCHBUDDY_TIMEZONE") or os.getenv("TZ")
            if env_tz:
                tz = env_tz
    except Exception:
        # Redis not ready or other issue; fall back to env/UTC
        env_tz = os.getenv("WATCHBUDDY_TIMEZONE") or os.getenv("TZ")
        if env_tz:
            tz = env_tz
    return tz

celery_app.conf.update(
    # Memory optimization settings
    result_expires=3600,
    task_acks_late=True,
    worker_max_tasks_per_child=100,  # Restart worker after 100 tasks to prevent memory leaks
    worker_prefetch_multiplier=1,    # Process one task at a time
    task_compression='gzip',         # Compress task payloads
    result_compression='gzip',       # Compress results
    
    # Connection settings
    broker_connection_retry_on_startup=True,
    broker_pool_limit=10,
    
    # RedBeat scheduler configuration
    beat_scheduler='redbeat.schedulers:RedBeatScheduler',
    redbeat_redis_url=settings.redis_url,
    redbeat_key_prefix='celery:beat:',
    
    # Task routing - comprehensive mapping for 3-worker architecture
    task_routes={
        # Worker 1: Maintenance + Ingestion (long-running background tasks)
        'app.services.tasks.build_metadata': {'queue': 'maintenance'},
        'app.services.tasks.cleanup_orphaned_items': {'queue': 'maintenance'},
        'app.services.tasks.run_nightly_maintenance': {'queue': 'maintenance'},
        'rebuild_faiss_index': {'queue': 'maintenance'},
        'app.services.tasks.ingest_new_movies': {'queue': 'ingestion'},
        'app.services.tasks.ingest_new_shows': {'queue': 'ingestion'},
        'app.services.tasks.refresh_recent_votes_movies': {'queue': 'ingestion'},
        'app.services.tasks.refresh_recent_votes_shows': {'queue': 'ingestion'},

        # Worker 2: List Creation + Scoring (AI list generation)
        'generate_chat_list': {'queue': 'creation'},
        'generate_dynamic_lists': {'queue': 'creation'},
    'refresh_dynamic_lists': {'queue': 'creation'},
        'app.services.tasks.populate_new_list_async': {'queue': 'creation'},

        # Worker 3: List Updates + Sync (refreshes, watched status updates)
        'refresh_ai_list': {'queue': 'sync'},
        'app.services.tasks.sync_user_lists': {'queue': 'sync'},
        'app.services.tasks.sync_single_list_async': {'queue': 'sync'},
        'app.services.tasks.send_user_notification': {'queue': 'sync'},  # Low priority utility
        
        # Phase detection and watch history (maintenance queue - low priority)
        'app.services.tasks.compute_user_phases_task': {'queue': 'maintenance'},
        'app.services.tasks.sync_user_watch_history_task': {'queue': 'maintenance'},
        'compress_user_history': {'queue': 'maintenance'},  # History compression via LLM
    },

    # Memory management
    worker_disable_rate_limits=True,
    worker_max_memory_per_child=200000,  # 200MB limit per worker
    
    # Scheduled tasks
    beat_schedule={
        "cleanup-metadata": {
            "task": "app.services.tasks.cleanup_orphaned_items", 
            "schedule": 60 * 60 * 6,  # every 6 hours
        },
        "ingest-new-movies": {
            "task": "app.services.tasks.ingest_new_movies",
            "schedule": 60 * 60 * 2,  # every 2 hours
        },
        "ingest-new-shows": {
            "task": "app.services.tasks.ingest_new_shows",
            "schedule": 60 * 60 * 2,  # every 2 hours
        },
        "refresh-recent-votes-movies": {
            "task": "app.services.tasks.refresh_recent_votes_movies",
            "schedule": 60 * 60 * 24,  # daily
        },
        "refresh-recent-votes-shows": {
            "task": "app.services.tasks.refresh_recent_votes_shows",
            "schedule": 60 * 60 * 24,  # daily
        },
            # DISABLED: Using on-demand Trakt ID resolution with TraktIdResolver instead
            # "retry-metadata-mapping": {
            #     "task": "app.services.tasks.build_metadata",
            #     "schedule": 60 * 60 * 12,  # every 12 hours (retry failed Trakt ID mappings)
            #     "kwargs": {"user_id": 1, "force": False}
            # },
        # Auto-refresh dynamic AI lists (mood/theme/fusion) every 2 hours
        "refresh-dynamic-ai-lists": {
            "task": "refresh_dynamic_lists",
            "schedule": 60 * 60 * 2,  # every 2 hours
            "kwargs": {"user_id": 1}
        },
        # Nightly maintenance window starter (runs every 30 min to check local midnight window)
        "nightly-maintenance-dispatch": {
            "task": "app.services.tasks.run_nightly_maintenance",
            "schedule": 60 * 30,
        },
        # Periodic smart-list sync honoring per-list sync_interval (covers custom lists)
        "sync-user-lists": {
            "task": "app.services.tasks.sync_user_lists",
            "schedule": 60 * 30,  # every 30 minutes checks all lists and skips those not due
            "kwargs": {"user_id": 1, "force_full": False}
        },
        # Daily phase detection for all users
        "compute-phases-daily": {
            "task": "app.services.tasks.compute_user_phases_task",
            "schedule": 60 * 60 * 24,  # daily at midnight (timezone-aware)
            "kwargs": {"user_id": 1}
        },
        # Incremental watch history sync (daily)
        "sync-watch-history-daily": {
            "task": "app.services.tasks.sync_user_watch_history_task",
            "schedule": 60 * 60 * 24,  # daily
            "kwargs": {"user_id": 1, "full_sync": True}
        },
        # Build user profile vectors (2-3 centroids) daily
        "build-user-profile-vectors": {
            "task": "build_user_profile_vectors",
            "schedule": 60 * 60 * 24,  # daily
            "kwargs": {"user_id": 1}
        },
        # Generate user text profiles (LLM-based summaries) daily for users without profiles
        "generate-user-text-profiles": {
            "task": "generate_user_text_profile",
            "schedule": 60 * 60 * 24,  # daily
            "kwargs": {"user_id": 1}
        },
        # Nightly BGE secondary index builder (additive, safe when disabled)
        "build-bge-index-nightly": {
            "task": "build_bge_index_topN",
            "schedule": 60 * 60 * 24,  # daily
            "kwargs": {"top_n": getattr(settings, "ai_bge_topn_nightly", 50000)}
        },
        # Daily history compression (persona generation via phi3:mini)
        "compress-history-daily": {
            "task": "compress_user_history",
            "schedule": 60 * 60 * 24,  # daily at midnight (timezone-aware)
            "kwargs": {"user_id": 1, "force_rebuild": False}
        },
    },
    timezone=_get_celery_timezone(),
)

@celery_app.task(bind=True)
def debug_task(self):
    print(f"Request: {self.request!r}")

# Configure memory monitoring
celery_app.conf.worker_send_task_events = True
celery_app.conf.task_send_sent_event = True
