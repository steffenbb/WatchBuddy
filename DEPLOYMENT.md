Note: This document has moved.

Please see docs/DEPLOYMENT.md for the latest deployment guide.

## System Requirements

### Backend Requirements
- Python 3.9 or higher
- PostgreSQL 12 or higher
- Redis 6 or higher
- Celery 5.x

### Frontend Requirements
- Node.js 18 or higher
- npm or yarn

## Installation & Setup

### 1. Clone and Setup Repository

```powershell
# Clone the repository
git clone <repository-url>

# WatchBuddy: Zero-Config Deployment

## Overview
This deployment is fully automated. The only required user action is:

```
docker-compose up --build
```

All secrets, database credentials, and app keys are generated and managed automatically. No .env files or backend setup is required. All configuration and onboarding is handled in the frontend UI.

## What Happens Automatically
- **Database**: Postgres is started with a random, secure password (never user-supplied). Credentials are stored in a Docker volume and never exposed to the user.
- **Redis**: Starts automatically, no config needed.
- **App Key**: A secure app key is generated and stored in a Docker volume for encryption.
- **Backend/Frontend**: Both are built and started automatically. No manual config or .env files are needed.
- **Secrets**: All secrets are generated at first startup and persisted in Docker volumes.

## First-Time Setup
1. Run `docker-compose up --build`.
2. Wait for all containers to start (see logs for progress).
3. Open the frontend in your browser (http://localhost:5173).
4. Complete any onboarding or admin setup in the UI (e.g., connect Trakt, set admin email/password).

## No Backend Setup Required
- No .env files, no manual secret generation, no database/redis config.
- All credentials are generated and managed by the containers.
- If you ever need to reset secrets, remove the `secrets_data` and `db_data` Docker volumes.

## Advanced: Resetting All Data
To reset all secrets and database data (danger: this deletes everything!):

```
docker-compose down -v
```

Then run `docker-compose up --build` again for a fresh start.

## Security Notes
- All secrets are stored in Docker volumes and never committed to git.
- Database and app keys are never exposed to the user or frontend except via secure onboarding flows.
- For production, use Docker secrets or a secret manager for even stronger isolation.

## Troubleshooting
- If a service fails to start, check logs with `docker-compose logs <service>`.
- If you see database connection errors, try `docker-compose down -v` and restart.
- For advanced debugging, inspect the contents of the `secrets_data` volume.

## Persistent Candidate Pool Management

WatchBuddy uses a persistent database pool of pre-fetched candidates for fast recommendations.

### Initial Setup
1. **Place CSV datasets** in `backend/data/` before first build:
   ```
   backend/data/TMDB_movies_dataset.csv
   backend/data/TMDB_shows_dataset.csv
   ```
2. On first startup, `init_db()` auto-imports these CSVs into `persistent_candidates` table.
3. Historical content (pre-2024) is populated from CSVs; new content comes from scheduled ingestion tasks.

### Celery Task Configuration
Background tasks keep the candidate pool current. Configure via Celery Beat or cron:

**Environment Variables (Optional):**
```bash
CANDIDATE_INGEST_ENABLED=true
CANDIDATE_INGEST_INTERVAL_HOURS=24
VOTE_REFRESH_ENABLED=true
VOTE_REFRESH_INTERVAL_HOURS=48
```

**Celery Beat Schedule Example:**
Add to `backend/app/core/celery_app.py`:
```python
from celery.schedules import crontab

app.conf.beat_schedule = {
    'ingest-new-movies-daily': {
        'task': 'app.services.tasks.ingest_new_movies',
        'schedule': crontab(hour=2, minute=0),
    },
    'ingest-new-shows-daily': {
        'task': 'app.services.tasks.ingest_new_shows',
        'schedule': crontab(hour=3, minute=0),
    },
    'refresh-votes-movies-weekly': {
        'task': 'app.services.tasks.refresh_recent_votes_movies',
        'schedule': crontab(hour=4, minute=0, day_of_week=0),
    },
    'refresh-votes-shows-weekly': {
        'task': 'app.services.tasks.refresh_recent_votes_shows',
        'schedule': crontab(hour=5, minute=0, day_of_week=0),
    },
}
```

**Docker Compose Beat Service (Recommended):**
Add a Celery Beat container:
```yaml
celery-beat:
  build: ./backend
  command: celery -A app.core.celery_app beat --loglevel=info
  depends_on:
    - redis
    - db
  environment:
    - POSTGRES_USER=${POSTGRES_USER}
    - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
    - POSTGRES_DB=${POSTGRES_DB}
```

### Manual Operations
**Import or update CSV datasets manually:**
```bash
docker exec -it watchbuddy-backend-1 python -m app.scripts.import_tmdb_csv /app/data/new_movies.csv movie
```

**Trigger ingestion tasks manually (via docker exec or Django shell):**
```bash
docker exec -it watchbuddy-backend-1 python -c "from app.services.tasks import ingest_new_movies; ingest_new_movies()"
```

**Monitor candidate pool size:**
```bash
docker exec -it watchbuddy-db-1 psql -U watchbuddy -d watchbuddy -c "SELECT media_type, COUNT(*) FROM persistent_candidates GROUP BY media_type;"
```

### Performance Notes
- Persistent pool bypasses API rate limits for bulk queries
- Obscurity/mainstream/freshness scores are pre-computed for instant filtering
- Ingestion tasks respect TMDB rate limits (0.25s between page requests)
- Vote refresh limited to ~400 recent items per run to avoid API strain

## Next Steps
- Complete onboarding in the frontend UI.
- Configure any integrations (Trakt, TMDB) via the UI.
- Enjoy fully automated, zero-backend-setup WatchBuddy!

```powershell
# From backend directory
cd backend
python -c "from app.core.database import init_db; import asyncio; asyncio.run(init_db())"
```

### 5. Start Services

#### Development Mode

**Terminal 1 - Redis:**
```powershell
redis-server
```

**Terminal 2 - Backend API:**
```powershell
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Terminal 3 - Celery Worker:**
```powershell
cd backend
celery -A app.core.celery_app.celery_app worker --loglevel=info --pool=threads
```

**Terminal 4 - Frontend:**
```powershell
cd frontend
npm run dev
```

#### Production Mode with Docker

```powershell
# Build and start all services
docker-compose up -d

# Or build from scratch
docker-compose build
docker-compose up -d
```

### 6. Initial Configuration

1. **Access the application:** http://localhost:3000
2. **Configure Trakt API:**
   - Go to Settings → Trakt Integration
   - Enter your Trakt access token
3. **Configure TMDB API (Optional):**
   - Go to Settings → TMDB Key
   - Enter your TMDB API key for enhanced metadata
4. **Configure Fusion Mode:**
   - Go to Settings → Fusion Mode
   - Enable and adjust weights as desired

## API Endpoints

### Core Endpoints
- `GET /` - API status
- `GET /health` - Simple health check
- `GET /api/status/health` - Detailed system health
- `GET /api/status/sync` - Active sync status
- `GET /api/status/metrics` - System metrics

### Lists & SmartLists
- `GET /api/lists` - Get all lists
- `POST /api/lists` - Create new list
- `DELETE /api/lists/{id}` - Delete list
- `POST /api/lists/{id}/sync` - Trigger sync

- `POST /api/smartlists/create` - Create SmartLists
- `GET /api/recommendations/fusion` - Fusion recommendations

### Settings & Configuration
- `GET /api/settings` - Get user settings
- `POST /api/settings/fusion` - Configure fusion mode
- `POST /api/settings/tmdb-key` - Set TMDB API key
- `GET /api/settings/tmdb-key/status` - Check TMDB configuration

### Notifications
- `GET /api/notifications/stream` - SSE notification stream
- `GET /api/notifications` - Get notification history
- `POST /api/notifications/{id}/read` - Mark notification as read

## Key Features

### Traditional Lists
- **Scoring:** Genre similarity, popularity, ratings
- **Performance:** Fast, reliable scoring
- **Use Case:** Standard recommendation lists

### SmartLists
- **Advanced Scoring:** TF-IDF semantic similarity, mood analysis, user history
- **Fusion Mode:** Configurable weight blending across multiple signals
- **Use Case:** Personalized, AI-enhanced recommendations

### Fusion Mode Components
1. **Genre Similarity** (30% default) - Genre overlap with user preferences
2. **Semantic Similarity** (25% default) - Content-based text similarity
3. **Mood Score** (20% default) - Emotional tone matching
4. **Rating Quality** (10% default) - High-rated content preference
5. **Discovery Factor** (5% default) - Novelty and exploration
6. **Trending Boost** (7% default) - Current popularity
7. **Personal History** (3% default) - Based on watch history

## Monitoring & Health Checks

### Health Check Endpoints
- `/health` - Simple UP/DOWN status
- `/api/status/health` - Detailed dependency status
- `/api/status/sync` - Active synchronization status
- `/api/status/metrics` - Performance metrics

### Logs & Debugging
- **Backend logs:** Check uvicorn/gunicorn output
- **Celery logs:** Monitor task execution
- **Frontend logs:** Browser console for client issues
- **Redis logs:** Connection and caching issues

### Performance Monitoring
- **Database:** Monitor connection pool and query performance
- **Redis:** Watch memory usage and connection count
- **Celery:** Monitor task queue depth and execution times
- **API:** Track request response times and error rates

## Security Considerations

### Data Protection
- **Encrypted Storage:** All API keys stored encrypted in database
- **App Key:** Encryption key stored on filesystem (not in database)
- **No .env in Production:** Secure secret management via database

### API Security
- **Rate Limiting:** Built-in rate limiting for external APIs
- **Input Validation:** Pydantic models validate all inputs
- **Error Handling:** Safe error messages, no sensitive data exposure

## Troubleshooting

### Common Issues

**Backend won't start:**
- Check database connection in DATABASE_URL
- Verify Redis is running and accessible
- Ensure all Python dependencies are installed

**Frontend build failures:**
- Clear node_modules: `rm -rf node_modules && npm install`
- Check Node.js version compatibility
- Verify config.json has correct API URL

**Fusion mode not working:**
- Check if user has enabled fusion in settings
- Verify TMDB metadata is available
- Monitor mood calculation in logs

**Sync issues:**
- Check Trakt API token validity
- Monitor Celery worker status
- Review rate limiting logs

### Logs Location
- **Development:** Console output
- **Docker:** `docker-compose logs <service>`
- **Production:** Configure log aggregation (ELK, etc.)

## Performance Optimization

### Database
- **Indexes:** Ensure proper indexing on frequently queried fields
- **Connection Pool:** Configure appropriate pool size
- **Query Optimization:** Monitor slow queries

### Redis
- **Memory:** Set appropriate maxmemory and eviction policies
- **Persistence:** Configure RDB/AOF based on requirements
- **Clustering:** Consider Redis cluster for high availability

### Celery
- **Worker Pools:** Adjust worker count based on load
- **Memory Limits:** Set worker_max_memory_per_child for memory management
- **Queue Monitoring:** Monitor queue depth and processing times

## Scaling Considerations

### Horizontal Scaling
- **API Servers:** Multiple uvicorn/gunicorn instances behind load balancer
- **Celery Workers:** Distribute across multiple machines
- **Database:** Read replicas for query scaling

### Caching Strategy
- **Redis:** Application-level caching for API responses
- **CDN:** Static asset delivery for frontend
- **Database:** Query result caching

### Monitoring at Scale
- **APM Tools:** Application Performance Monitoring
- **Log Aggregation:** Centralized logging
- **Metrics Collection:** Prometheus/Grafana or similar

## Backup & Recovery

### Database Backups
```bash
# Daily backup
pg_dump watchbuddy > backup_$(date +%Y%m%d).sql

# Restore
psql watchbuddy < backup_YYYYMMDD.sql
```

### Redis Persistence
- Configure RDB snapshots for data persistence
- Consider AOF for durability requirements

### Application Data
- Backup encryption keys
- Document configuration settings
- Test recovery procedures regularly