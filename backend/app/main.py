from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.database import init_db
from app.api import lists, smartlists, settings, status, trakt_auth, suggested, ratings
from app.api.recommendations import router as recommendations_router
from app.api.notifications import router as notifications_router

app = FastAPI(title="WatchBuddy API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(lists.router, prefix="/api/lists", tags=["Lists"])
app.include_router(smartlists.router, prefix="/api/smartlists", tags=["SmartLists"])
app.include_router(settings.router, prefix="/api/settings", tags=["Settings"])
app.include_router(status.router, prefix="/api/status", tags=["Status"])
app.include_router(trakt_auth.router, prefix="/api/trakt", tags=["Trakt Auth"])
app.include_router(notifications_router, prefix="/api/notifications", tags=["Notifications"])
app.include_router(recommendations_router, prefix="/api/recommendations", tags=["Recommendations"])
app.include_router(suggested.router, prefix="/api/suggested", tags=["Suggested Lists"])
app.include_router(ratings.router, prefix="/api/ratings", tags=["Ratings"])


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
