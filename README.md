# 🎬 WatchBuddy

**Your AI-powered movie and TV show recommendation companion**

WatchBuddy is a smart recommendation system that syncs with your Trakt watch history to suggest movies and TV shows tailored to your taste. Create custom SmartLists based on mood, genre, language, and more - all running locally with zero configuration.

---

## ✨ Features

### 🎯 Smart Recommendations
- **AI-Powered Scoring**: Multi-factor algorithm considers your watch history, mood preferences, and content freshness
- **MMR Diversity Algorithm**: No more lists full of sequels - get varied, relevant recommendations
- **Mood-Based Filtering**: Filters for Dark, Cozy, Intense, Quirky, and more
- **Semantic Matching**: TF-IDF-based similarity to your favorite content

### 📋 SmartLists
- **Custom Lists**: Create lists with genre, year, language, and obscurity filters
- **Dynamic Titles**: Netflix-style personalized titles like "Fans of Inception Also Enjoyed"
- **Watched Status Sync**: Automatically marks watched items from your Trakt history
- **Trakt Integration**: Two-way sync with your Trakt lists

### 🌍 Content Discovery
- **20,000+ Pre-Loaded Movies & Shows**: Instant recommendations from TMDB dataset
- **Multi-Language Support**: Discover content in 20+ languages with smart fallback
- **Ultra Discovery Mode**: Find hidden gems and obscure titles
- **Genre Blending**: Mix multiple genres for unique recommendations

### ⚡ Zero-Config Setup
- **Docker-First Architecture**: One command to start everything
- **Auto-Init Database**: Pre-populates 20K+ candidates on first startup
- **Persistent Storage**: All data survives container restarts
- **Background Tasks**: Celery workers handle syncs and updates automatically

---

## 🚀 Installation

### Prerequisites
- [Docker](https://www.docker.com/get-started) (with Docker Compose)
- [Trakt](https://trakt.tv) account (free)
- [TMDB API Key](https://www.themoviedb.org/settings/api) (free)

### Quick Start

```bash
# Clone the repository
git clone https://github.com/yourusername/watchbuddy.git
cd watchbuddy

# Start all services
docker compose up -d

# Wait ~2 minutes for database initialization (first run only)
# Then open your browser to http://localhost:5173
```

That's it! WatchBuddy will automatically:
- ✅ Create the database
- ✅ Load 20,000+ movies and shows
- ✅ Start the API, frontend, Redis, and background workers
- ✅ Initialize all required services

### First-Time Setup

1. **Open WatchBuddy**: Navigate to http://localhost:5173
2. **Connect Trakt**: 
    - Go to Settings → Trakt Authentication
    - Click "Authorize with Trakt" and complete OAuth flow
3. **Add TMDB API Key**:
    - Get free API key from [TMDB](https://www.themoviedb.org/settings/api)
    - Go to Settings → TMDB API Key and save it
4. **Create Your First List**:
    - Click "Create SmartList"
    - Set filters (genre, mood, year, language)
    - Watch your personalized recommendations appear!

---

## 📖 Usage Guide

### Creating SmartLists

SmartLists are dynamically generated based on your filters:

1. Click **"Create SmartList"** on the dashboard
2. Configure filters:
    - **Genres**: Select multiple (e.g., `Action`, `Sci-Fi`)
    - **Moods**: Choose up to 3 (`Dark`, `Tense`, `Quirky`)
    - **Languages**: Support for 20+ languages (`en`, `da`, `sv`, `no`, etc.)
    - **Year Range**: Filter by decade or specific years
    - **Obscurity**: Discover hidden gems vs mainstream hits
3. Set **Item Limit** (default: 200)
4. Click **"Sync"** to populate your list

### Syncing with Trakt

WatchBuddy automatically syncs with Trakt:

- **Watched Status**: Items you've watched on Trakt are marked automatically
- **Two-Way Sync**: Changes made in WatchBuddy update Trakt lists
- **Background Updates**: Celery Beat refreshes lists every 24 hours

### Managing Lists

- **Custom Lists**: Manually add specific titles
- **Edit Filters**: Modify SmartList criteria anytime and re-sync
- **Delete Lists**: Removes from both WatchBuddy and Trakt
- **Export**: Lists are automatically synced to your Trakt account

---

## 🔧 Configuration

### Environment Variables

WatchBuddy works out-of-the-box, but you can customize via environment variables:

```bash
# docker-compose.override.yml (optional)
services:
  backend:
     environment:
        - LOG_LEVEL=DEBUG  # Default: INFO
  db:
     environment:
        - POSTGRES_PASSWORD=custom_password  # Default: watchbuddy
```

### Data Persistence

All data is stored in Docker volumes:
- `db_data_v2`: PostgreSQL database (lists, items, candidates)
- Redis (settings, cache, Celery queues)

To reset everything:
```bash
docker compose down -v  # WARNING: Deletes all data!
docker compose up -d
```

---

## 🛠️ Troubleshooting

### Lists Not Syncing?
1. Check Trakt connection in Settings
2. Verify TMDB API key is valid
3. Look for errors in `docker logs watchbuddy-backend-1`

### Missing Recommendations?
- First startup takes ~2 minutes to load candidates
- Check logs: `docker logs watchbuddy-backend-1 --tail 100`
- Try forcing a full sync: Click list → "Sync" button

### Container Issues?
```bash
# Restart all services
docker compose restart

# Rebuild after code changes
docker compose build backend
docker compose up -d backend

# Check service health
docker compose ps
docker logs watchbuddy-backend-1
```

---

## 📊 Technical Stack

- **Backend**: Python 3.11 + FastAPI
- **Frontend**: React 18 + TypeScript + Vite + Tailwind CSS
- **Database**: PostgreSQL 15
- **Cache**: Redis 7
- **Task Queue**: Celery + Celery Beat
- **Deployment**: Docker + Docker Compose

---

## 📜 Data Attribution

**Contains information from:**
- **Full TMDB Movies Dataset 2024 (1M Movies)** - made available under the [ODC Attribution License](https://opendatacommons.org/licenses/by/1-0/)
- **Full TMDb TV Shows Dataset 2024 (150K Shows)** - made available under the [ODC Attribution License](https://opendatacommons.org/licenses/by/1-0/)

TMDB data is used for metadata enrichment and recommendation scoring. Movie posters and metadata are fetched via the [TMDB API](https://www.themoviedb.org/documentation/api).

---

## 🤝 Contributing

WatchBuddy is built for personal use but open to contributions:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## 📝 License

This project is licensed under the MIT License - see the LICENSE file for details.

---

## 🙏 Acknowledgments

- [Trakt](https://trakt.tv) for watch history API
- [TMDB](https://www.themoviedb.org) for movie/TV metadata
- [OpenDataCommons](https://opendatacommons.org) for TMDB datasets

---

## 📞 Support

Having issues? Check the [troubleshooting section](#-troubleshooting) or open an issue on GitHub.

**Enjoy your personalized movie nights!** 🍿

Edit `docker-compose.override.yml` if you want different local credentials. That file is added to `.gitignore` so it won't be committed.

IMPORTANT: this repository also includes an intentionally committed set of default DB credentials
to make the project work out-of-the-box for local development. These credentials are insecure
and MUST NOT be used in production. Rotate/change them before deploying or use a secrets manager.

## Persistent Candidate Pool Architecture

WatchBuddy now uses a **persistent database candidate pool** for fast recommendations without excessive API calls.

### Overview
- **`PersistentCandidate` table**: Stores movies/shows with metadata (genres, language, popularity, votes, obscurity/mainstream/freshness scores).
- **CSV Bootstrap**: On first startup, CSVs in `/app/data/*.csv` auto-import into the database (historical content up to 2023).
- **Incremental Ingestion**: Background Celery tasks fetch new content (>=2024) from TMDB/Trakt and insert into the pool.
- **Vote Refresh**: Recent items (<90 days) have their vote_count/vote_average periodically refreshed for accuracy.
- **Fast Queries**: SmartList syncs query the database with filters (language, genre, year, obscurity) instead of hitting external APIs repeatedly.

### CSV Dataset Placement
Place your TMDB CSV datasets (movies and shows) in `backend/data/` before building:
```bash
# Example structure:
# backend/data/TMDB_movies_dataset.csv
# backend/data/TMDB_shows_dataset.csv
```
Expected CSV columns (flexible matching):
- `tmdb_id` or `id` (required)
- `title` or `name` (required)
- `media_type` or `type` (optional, inferred from filename/context)
- `original_language` or `language`
- `popularity`, `vote_average`, `vote_count`
- `release_date` or `first_air_date`
- `genres` (JSON array or comma-separated)
- `keywords`, `overview`, `poster_path`, `backdrop_path` (optional)

On first run, `init_db()` detects an empty `persistent_candidates` table and imports these CSVs automatically.

### Celery Task Scheduling
The following background tasks maintain the candidate pool:

**Ingestion Tasks** (fetch new content >=2024):
- `ingest_new_movies` – Discovers new movies from TMDB
- `ingest_new_shows` – Discovers new shows from TMDB

**Vote Refresh Tasks** (update recent items):
- `refresh_recent_votes_movies` – Refreshes vote stats for recent movies
- `refresh_recent_votes_shows` – Refreshes vote stats for recent shows

#### Example Celery Beat Schedule
Add to your Celery configuration (e.g., `backend/app/core/celery_app.py` or environment):
```python
from celery.schedules import crontab

beat_schedule = {
    'ingest-new-movies': {
        'task': 'app.services.tasks.ingest_new_movies',
        'schedule': crontab(hour=2, minute=0),  # Daily at 2 AM
    },
    'ingest-new-shows': {
        'task': 'app.services.tasks.ingest_new_shows',
        'schedule': crontab(hour=3, minute=0),  # Daily at 3 AM
    },
    'refresh-votes-movies': {
        'task': 'app.services.tasks.refresh_recent_votes_movies',
        'schedule': crontab(hour=4, minute=0, day_of_week='0,3'),  # Twice a week
    },
    'refresh-votes-shows': {
        'task': 'app.services.tasks.refresh_recent_votes_shows',
        'schedule': crontab(hour=5, minute=0, day_of_week='0,3'),
    },
}
```

Or use Docker Compose environment variables to configure intervals (implementation-specific).

### Obscurity & Mainstream Scoring
The persistent pool pre-computes heuristic scores:
- **`obscurity_score`**: High rating with low vote_count/popularity = interesting obscure content
- **`mainstream_score`**: High rating + high popularity + high votes = mainstream hits
- **`freshness_score`**: Recency decay over 3 years for new content boost

These scores accelerate discovery mode filtering (obscure/popular/balanced) without re-computation.

### Manual CSV Import
If you need to manually import or update datasets after initial bootstrap:
```bash
docker exec -it watchbuddy-backend-1 python -m app.scripts.import_tmdb_csv /app/data/your_file.csv movie
```

