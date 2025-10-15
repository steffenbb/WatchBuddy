# WatchBuddy Deployment Guide

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

**Trigger ingestion tasks manually (via docker exec):**
```bash
docker exec -it watchbuddy-backend-1 python -c "from app.services.tasks import ingest_new_movies; ingest_new_movies()"
```

**Monitor candidate pool size:**
```bash
docker exec -it watchbuddy-db-1 psql -U watchbuddy -d watchbuddy -c "SELECT media_type, COUNT(*) FROM persistent_candidates GROUP BY media_type;"
```

### 5. Start Services

#### Development Mode

**Terminal 1 - Backend API:**
```powershell
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Terminal 2 - Celery Worker:**
```powershell
cd backend
celery -A app.core.celery_app.celery_app worker --loglevel=info --pool=threads
```

**Terminal 3 - Frontend:**
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
