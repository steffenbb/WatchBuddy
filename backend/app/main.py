from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.gzip import GZipMiddleware
import os
from fastapi.middleware.cors import CORSMiddleware
from app.core.database import init_db
from starlette.requests import Request
from starlette.responses import Response

from app.api import lists, settings, status, trakt_auth, suggested, ratings, metadata, chatlists, ai_lists, individual_lists, maintenance, phases, overview, items, search
from app.api.chat_prompt import router as chat_prompt_router
from app.api.available_genres_languages import router as genres_languages_router
from app.api.recommendations import router as recommendations_router
from app.api.notifications import router as notifications_router
from app.api.metadata_options import router as metadata_options_router
from app.api.pairwise import router as pairwise_router
from app.api.feature_flags import router as feature_flags_router
from app.api.metrics_api import router as metrics_api_router
from app.api.telemetry import router as telemetry_router


app = FastAPI(title="WatchBuddy API", version="1.0.0")
app.include_router(chat_prompt_router, prefix="/api", tags=["Chat Prompt"])

# Add GZip compression middleware for better transfer performance
app.add_middleware(GZipMiddleware, minimum_size=1000)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



app.include_router(genres_languages_router, prefix="/api", tags=["Genres & Languages"])
app.include_router(metadata_options_router, prefix="/api/metadata", tags=["Metadata Options"])

# Core API routers
app.include_router(lists.router, prefix="/api/lists", tags=["Lists"])
app.include_router(settings.router, prefix="/api/settings", tags=["Settings"])
app.include_router(status.router, prefix="/api/status", tags=["Status"])
app.include_router(trakt_auth.router, prefix="/api/trakt", tags=["Trakt"])
app.include_router(suggested.router, prefix="/api/suggested", tags=["Suggested"])
app.include_router(ratings.router, prefix="/api/ratings", tags=["Ratings"])
app.include_router(metadata.router, prefix="/api/metadata", tags=["Metadata"])
app.include_router(chatlists.router, prefix="/api/chatlists", tags=["Chat Lists"])
app.include_router(ai_lists.router, prefix="/api/ai", tags=["AI Lists"])
app.include_router(individual_lists.router, prefix="/api", tags=["Individual Lists"])
app.include_router(items.router, prefix="/api/items", tags=["Items"])
app.include_router(search.router, prefix="/api", tags=["Search"])

# Optional: Recommendations and Notifications if present
app.include_router(recommendations_router, prefix="/api/recommendations", tags=["Recommendations"])
app.include_router(notifications_router, prefix="/api/notifications", tags=["Notifications"])
app.include_router(maintenance.router, prefix="/api/maintenance", tags=["Maintenance"])
app.include_router(phases.router, tags=["Phases"])
app.include_router(overview.router, prefix="/api", tags=["Overview"])
app.include_router(pairwise_router, prefix="/api/pairwise", tags=["Pairwise Training"])
app.include_router(feature_flags_router, prefix="/api", tags=["Feature Flags"])
app.include_router(metrics_api_router, prefix="/api", tags=["Metrics"])
app.include_router(telemetry_router, prefix="/api/telemetry", tags=["Telemetry"])

# Serve generated list posters as static files (ensure directory exists)
POSTERS_DIR = "/app/data/posters"
try:
    os.makedirs(POSTERS_DIR, exist_ok=True)
except Exception:
    pass
if os.path.isdir(POSTERS_DIR):
    app.mount("/posters", StaticFiles(directory=POSTERS_DIR), name="posters")


# Ensure list posters are not cached by clients
@app.middleware("http")
async def no_cache_posters(request: Request, call_next):
    response: Response = await call_next(request)
    try:
        path = request.url.path or ""
        if path.startswith("/posters/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
    except Exception:
        pass
    return response


@app.on_event("startup")
async def startup_event():
    await init_db()

    # Repopulate Trakt client_id and client_secret from DB to Redis if missing
    from app.core.redis_client import get_redis
    from app.models import Secret
    import json
    r = get_redis()
    # Only set if not already present in Redis
    keys = [
        ("trakt_client_id", "trakt_client_id"),
        ("trakt_client_secret", "trakt_client_secret")
    ]
    from app.core.database import SessionLocal
    db = None
    try:
        db = SessionLocal()
        for redis_key, secret_key in keys:
            redis_val = await r.get(f"settings:global:{redis_key}")
            if not redis_val:
                secret = db.query(Secret).filter(Secret.key == secret_key).first()
                if secret:
                    await r.set(f"settings:global:{redis_key}", secret.value_encrypted)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to repopulate Trakt credentials in Redis: {e}")
    finally:
        if db:
            db.close()

    # Background warm-up to reduce first-request latency for search and embeddings
    try:
        import threading

        def _warm_background():
            try:
                # Warm ElasticSearch connection and query caches
                from app.services.elasticsearch_client import get_elasticsearch_client
                es = get_elasticsearch_client()
                if es and es.is_connected():
                    try:
                        es.search("warm", limit=1)
                    except Exception:
                        pass
                # Warm embedding model (load into memory)
                try:
                    from app.services.ai_engine.embeddings import EmbeddingService
                    EmbeddingService().encode_text("warmup")
                except Exception:
                    pass
                # Warm Cross-Encoder reranker (ensures model is available)
                try:
                    from app.services.ai_engine.cross_encoder_reranker import CrossEncoderReranker
                    ce = CrossEncoderReranker()
                    ce.ensure()
                    try:
                        _ = ce.score("warmup", ["warmup"], batch_size=1)
                    except Exception:
                        pass
                except Exception:
                    pass
            except Exception:
                pass

        threading.Thread(target=_warm_background, daemon=True).start()
    except Exception:
        pass

@app.get("/")
def root():
    return {"status": "WatchBuddy API Running"}

@app.get("/health")
async def health_check():
    """Simple health check endpoint for load balancers/monitoring"""
    try:
        from app.core.redis_client import get_redis
        from app.core.database import SessionLocal

        # Quick Redis check
        await get_redis().ping()

        # Quick DB check
        db = SessionLocal()
        try:
            db.execute("SELECT 1")
        finally:
            db.close()

        from app.utils.timezone import utc_now
        return {"status": "healthy", "timestamp": utc_now().isoformat()}
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail=f"Service unhealthy: {str(e)}")
