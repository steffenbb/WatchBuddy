from celery import Celery
from app.core.config import settings

celery_app = Celery(
    "watchbuddy",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.services.tasks"]
)

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
    
    # Task routing and concurrency
    task_routes={
        'app.services.tasks.score_smartlist': {'queue': 'scoring'},
        'app.services.tasks.sync_user_lists': {'queue': 'sync'},
        'app.services.tasks.sync_single_list_async': {'queue': 'sync'},
        'app.services.tasks.populate_new_list_async': {'queue': 'sync'},
        'app.services.tasks.cleanup_orphaned_items': {'queue': 'maintenance'},
        'app.services.tasks.ingest_new_movies': {'queue': 'ingestion'},
        'app.services.tasks.ingest_new_shows': {'queue': 'ingestion'},
        'app.services.tasks.refresh_recent_votes_movies': {'queue': 'ingestion'},
        'app.services.tasks.refresh_recent_votes_shows': {'queue': 'ingestion'},
        'app.services.tasks.build_metadata': {'queue': 'maintenance'},
    },
    
    # Memory management
    worker_disable_rate_limits=True,
    worker_max_memory_per_child=200000,  # 200MB limit per worker
    
    # Scheduled tasks
    beat_schedule={
        "refresh-smartlists": {
            "task": "app.services.tasks.refresh_smartlists",
            "schedule": 60 * 60,  # every hour
        },
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
        "retry-metadata-mapping": {
            "task": "app.services.tasks.build_metadata",
            "schedule": 60 * 60 * 12,  # every 12 hours (retry failed Trakt ID mappings)
            "kwargs": {"user_id": 1, "force": False}
        }
    },
    timezone='UTC',
)

@celery_app.task(bind=True)
def debug_task(self):
    print(f"Request: {self.request!r}")

# Configure memory monitoring
celery_app.conf.worker_send_task_events = True
celery_app.conf.task_send_sent_event = True
