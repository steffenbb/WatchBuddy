# WatchBuddy AI Agent Instructions

## Architecture Overview

WatchBuddy is a **zero-config Docker-first** movie/TV recommendation system with persistent candidate pooling architecture. Core workflow: Persistent DB pool → SmartList filtering → Mood/semantic scoring → User recommendations.

**Key Services (docker-compose.yml):**
- `backend`: FastAPI (port 8000) with auto-init DB migrations
- `frontend`: React + Vite + Tailwind (port 5173 dev, nginx prod)
- `db`: PostgreSQL 15 with auto-generated credentials, persistent volumes
- `redis`: Caching, settings storage, and Celery broker
- `celery`: Background ingestion tasks (new content, vote refresh)
- `celery-beat`: Task scheduler for periodic updates

**Persistent Candidate Pool:** Database-first architecture pre-populates `persistent_candidates` table with 20,000+ movies/shows from TMDB CSVs on first startup, enabling fast (<320ms) list syncs without external API calls.

## Critical Development Workflows

### Container Development Cycle
```powershell
# Rebuild backend after code changes (use VS Code task or manual)
docker compose build backend; docker compose up -d backend

# Execute Python inside container (ALWAYS set PYTHONPATH=/app)
docker exec -i watchbuddy-backend-1 sh -c "cd /app && PYTHONPATH=/app python app/scripts/your_script.py"

# Check logs
docker logs --tail 50 watchbuddy-backend-1

# Database inspection
docker exec -it watchbuddy-db-1 psql -U watchbuddy -d watchbuddy -c "SELECT COUNT(*) FROM persistent_candidates;"
```

### SmartList Development & Testing
```powershell
# Trigger manual list sync with force_full
docker exec -i watchbuddy-backend-1 python /app/tests/manual/trigger_sync.py <list_id>

# Analyze sync performance
docker exec -i watchbuddy-backend-1 sh -c "cd /app && PYTHONPATH=/app python app/scripts/analyze_sync_simple.py"

# Check list items
docker exec -i watchbuddy-backend-1 python /app/tests/debug/check_lists.py
```

## Service Architecture Patterns

### Recommendation Pipeline (Critical Path)
1. **BulkCandidateProvider** (`services/bulk_candidate_provider.py`)
   - Primary: Queries `persistent_candidates` table with filters (language, genre, year, obscurity)
   - Fallback: External APIs only for missing content
   - Returns enriched candidates with pre-computed scores (`obscurity_score`, `mainstream_score`, `freshness_score`)

2. **ListSyncService** (`services/list_sync.py`)
   - Orchestrates SmartList syncs (incremental/full logic)
   - Updates `last_sync_at`, `sync_status` in `user_lists` table
   - Marks watched status via Trakt history integration

3. **ScoringEngine** (`services/scoring_engine.py`)
   - Blends persistent scores with mood vectors and TF-IDF semantic matching
   - Detects `_from_persistent_store` flag to use pre-computed values
   - Uses scikit-learn (TF-IDF, cosine similarity), NO torch/transformers

4. **TMDB Enrichment** (`services/tmdb_client.py`)
   - Rate-limited async httpx client with exponential backoff
   - API key stored in Redis (`settings:global:tmdb_api_key`)
   - **404s are normal** - log at debug level, preserve items without metadata

### Data Models (backend/app/models.py)
- `UserList`: SmartList config with JSON filters, sync timestamps
- `ListItem`: Individual recommendations with scores, watched status, Trakt IDs
- `PersistentCandidate`: Pre-enriched pool (TMDB metadata + computed scores)
- `CandidateIngestionState`: Checkpoints for incremental TMDB fetches
- `MediaMetadata`: TMDB cache for on-demand lookups

### API Integration Pattern
**External APIs:** Trakt (user watch history) + TMDB (metadata enrichment)
- `TraktClient` (`services/trakt_client.py`): OAuth tokens in Redis, user-specific
- `tmdb_client.py`: Global API key, shared rate limiter
- **Nordic content**: Language fallbacks (`da` → `sv`, `no`)

## Project-Specific Conventions

### Database Session Management
```python
# ALWAYS use this pattern (never import db as global)
from app.core.database import SessionLocal

db = SessionLocal()
try:
    # operations here
    db.commit()  # explicit commits
finally:
    db.close()  # ensure cleanup
```

### Error Handling Philosophy
- **TMDB 404s are expected** - many items lack TMDB mappings, log as `logger.debug()` not warnings
- Graceful degradation: preserve items even when enrichment fails
- Try/except around external API calls with fallback values
- Never fail list syncs due to individual item errors

### Async Patterns
```python
# Background sync with Redis-based locking
from app.core.redis_client import get_redis

r = get_redis()
await r.set(f"sync_lock:{list_id}", json.dumps({...}), ex=3600)
```

### User ID Convention (Single-User Mode)
- Default `user_id=1` everywhere in backend
- Frontend API calls always pass `{"user_id": 1}`
- Services accept `user_id` param but default to 1

### Persistent Candidate Scoring
```python
# Provider sets flag to enable score reuse
candidate['_from_persistent_store'] = True

# Engine detects flag and blends persistent scores
if candidate.get('_from_persistent_store'):
    base_score = candidate.get('obscurity_score', 0.5)
```

## Critical File References

### Core Services
- `backend/app/services/bulk_candidate_provider.py` (2557 lines) - DB-first sourcing with discovery modes
- `backend/app/services/list_sync.py` (687 lines) - Sync orchestration with watched tracking
- `backend/app/services/scoring_engine.py` (510 lines) - Multi-factor scoring (mood, semantic, popularity)
- `backend/app/services/candidate_ingestion.py` (180 lines) - Celery tasks for TMDB incremental fetch

### Database & Initialization
- `backend/app/core/database.py` - Auto-migrations, CSV bootstrap on empty DB
- `backend/app/models.py` - SQLAlchemy models with indexes
- `backend/data/` - Place TMDB CSVs here for auto-import (TMDB_movie_dataset_v11.csv, etc.)

### API & Frontend
- `backend/app/api/smartlists.py` - POST `/create`, `/sync/{id}` endpoints
- `frontend/src/api/client.ts` - Simple fetch wrappers (`apiGet`, `apiPost`)

### Testing & Debug
- `tests/manual/trigger_sync.py` - HTTP-based sync trigger (no FastAPI imports)
- `backend/app/scripts/analyze_sync_simple.py` - Performance metrics
- `tests/debug/` - Various inspection scripts (check_lists.py, analyze_enrichment.py)

## Integration Points

### Trakt ↔ TMDB Mapping
- Trakt IDs stored in `ListItem.trakt_id` and `PersistentCandidate.trakt_id`
- TMDB IDs primary for enrichment lookups
- **Many Trakt items will fail TMDB mapping** - normal behavior, don't retry excessively

### Frontend ↔ Backend
- API base: `/api` prefix in Vite proxy config
- Real-time sync status: poll `/api/status/sync` endpoint
- Toast notifications via `send_notification()` stored in Redis, consumed by frontend

### CSV Bootstrap (First Startup)
- On empty `persistent_candidates` table, `init_db()` scans `backend/data/*.csv`
- Expected columns: `tmdb_id`, `title`, `media_type`, `original_language`, `genres`, `vote_average`, etc.
- Bulk insert with score computation (`obscurity_score`, `mainstream_score`, `freshness_score`)

## Development Gotchas

- **PYTHONPATH=/app required** for all `docker exec` Python commands (imports fail otherwise)
- **TMDB rate limits**: Built-in exponential backoff in `rate_limit.py`, don't add manual delays
- **Database migrations**: Automatic via `init_db()` on startup, uses PostgreSQL `CREATE ... IF NOT EXISTS`
- **VS Code tasks**: `.vscode/tasks.json` has rebuild shortcuts (12+ duplicate tasks for convenience)
- **Genre filtering**: Case-insensitive with aliases (`sci-fi` → `science fiction`)
- **Container names**: `watchbuddy-backend-1`, `watchbuddy-db-1` (note the `-1` suffix from Compose v2)
- **Language filtering**: Lenient when metadata missing, uses title/overview validation fallbacks