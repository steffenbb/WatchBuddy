# 🎬 WatchBuddy

**Your AI-powered movie and TV show recommendation companion**

WatchBuddy is a smart recommendation system that syncs with your Trakt watch history to suggest movies and TV shows tailored to your taste. Create custom SmartLists based on mood, genre, language, and more - all running locally with zero configuration.

---

## ✨ Features

### 🎯 Smart Recommendations
- **1.4 Million+ Candidate Pool**: Instant recommendations from our massive pre-loaded database (~1.3M movies + 165K shows)
- **Trakt History Integration**: Personalized scoring based on your actual watch history and ratings
- **Multi-Factor Scoring**: Combines popularity, rating, votes, freshness, mood matching, and semantic similarity
- **Discovery Modes**: Find mainstream hits, hidden gems, or a balanced mix based on your preferences
- **Semantic Matching**: TF-IDF-based similarity finds content similar to your favorites
- **Mood-Based Filtering**: Choose from Dark, Cozy, Intense, Quirky, Feel-Good and more

### 📋 SmartLists & List Types

WatchBuddy offers six powerful list types to match different discovery needs:

#### **Custom Lists**
Manually curated lists where you have full control. Perfect for watchlists, collections, or sharing with friends.
- Add/remove titles manually
- Set custom filters and ordering
- Optionally sync to Trakt

#### **Suggested Lists**
Pre-configured recommendation lists with optimized filters for popular use cases.
- "Hidden Gems" - Highly rated but lesser-known titles
- "Recent Blockbusters" - Mainstream hits from the last 2 years
- "Cult Classics" - Beloved niche favorites
- One-click setup with proven filter combinations

#### **Mood Lists**
Emotionally-driven recommendations that match your current feeling.
- Select up to 3 moods (Dark, Cozy, Tense, Quirky, Feel-Good, etc.)
- Mood vector scoring weights genres and themes appropriately
- Perfect for "I want something dark and intense tonight"

#### **Theme Lists**
Curated around specific topics, settings, or concepts.
- Time Travel, Space Exploration, Heist Movies, etc.
- Combines genre filters with keyword matching
- Semantic search finds thematically similar content

#### **Fusion Lists**
Blend multiple genres together for unique combinations.
- "Rom-Com Thrillers" (Romance + Comedy + Thriller)
- "Sci-Fi Horror" (Science Fiction + Horror)
- Automatic genre weighting finds the best crossover titles

#### **Chat Lists** ⭐ *Most Powerful*
Natural language prompts powered by smart parsing - just describe what you want!

**Examples:**
- *"Cozy feel-good movies like The Hangover, prefer stuff after 2000, comedies with a bit of action"*
- *"Dark psychological thrillers in Scandinavian languages"*
- *"Hidden gem sci-fi films from the 80s and 90s"*

**How Chat Lists Work:**
1. **Smart Parsing**: Extracts genres, moods, years, languages, and reference titles from your text
2. **Discovery Detection**: Recognizes "obscure", "popular", "mainstream", "under the radar" keywords
3. **Semantic Anchoring**: Uses "like [movie]" or "similar to [show]" for TF-IDF similarity matching
4. **Automatic Defaults**: Assumes English and mainstream content unless you specify otherwise
5. **Flexible Genre Matching**: Allows broader matches while respecting your intent

**Chat Features:**
- Natural language understanding (no complex syntax needed)
- Automatic mainstream bias for quality results
- Smart defaults for missing parameters
- Supports all filters: genre, mood, year, language, obscurity, media type
- Semantic similarity to reference titles

### Other SmartList Features
- **Dynamic Titles**: Netflix-style personalized titles like "Fans of Inception Also Enjoyed"
- **Watched Status Sync**: Automatically marks watched items from your Trakt history
- **Trakt Integration**: Two-way sync with your Trakt lists
- **Cooldown Management**: Smart sync timing prevents API rate limits

### 🌍 Content Discovery
- **~1.47 Million Pre-Loaded Titles**: Instant recommendations from TMDB CSV datasets (~1.3M movies + 165K shows)
- **Multi-Language Support**: Discover content in 20+ languages with smart fallback
- **Ultra Discovery Mode**: Find hidden gems and obscure titles
- **Genre Blending**: Mix multiple genres for unique recommendations

### ⚡ Zero-Config Setup
- **Docker-First Architecture**: One command to start everything
- **Auto-Init Database**: Pre-populates ~1.47M candidates on first startup
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
git clone https://github.com/steffenbb/WatchBuddy.git
cd watchbuddy

# Start all services
docker compose up -d

# Wait ~2 minutes for database initialization (first run only)
# Then open your browser to http://localhost:5173
```

That's it! WatchBuddy will automatically:
- ✅ Create the database
- ✅ Load ~1.47 million titles (~1.3M movies + 165K shows)
- ✅ Start the API, frontend, Redis, and background workers
- ✅ Initialize all required services

### Alternative Installation (no git clone)

Want to run WatchBuddy without cloning the repo? You can use Docker Compose directly:

Steps (Windows PowerShell):

```powershell
# 1) Create a folder (optional) and enter it
New-Item -ItemType Directory -Force .\watchbuddy | Out-Null; Set-Location .\watchbuddy

# 2) Download the compose file into this folder
Invoke-WebRequest -UseBasicParsing \
    -Uri https://raw.githubusercontent.com/steffenbb/WatchBuddy/main/docker-compose.yml \
    -OutFile docker-compose.yml

# 3) Pull the required images
docker compose pull

# 4) Start the stack in the background
docker compose up -d

# 5) Open the app (after ~2 minutes on first run)
# http://localhost:5173
```

Handy commands:

```powershell
# Update to latest images and restart
docker compose pull; docker compose up -d

# Check status and recent logs
docker compose ps
docker logs --tail 100 watchbuddy-backend-1
docker logs --tail 100 watchbuddy-frontend-1

# Stop the stack (keep data)
docker compose down

# Reset everything (DESTRUCTIVE)
docker compose down -v
```

Notes:
- First startup seeds the database and may take a few minutes depending on your machine.
- Data is persisted in Docker volumes (PostgreSQL, Redis). Removing volumes resets the app.
- Configure Trakt OAuth and TMDB API key from the in-app Settings once the UI is up.

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

## 🎯 How Recommendations Work

### The Scoring System

WatchBuddy uses a sophisticated multi-factor scoring algorithm to rank candidates. Every title gets a score from 0.0 to 1.0 based on:

#### **Base Quality Metrics** (40-50% weight)
- **TMDB Rating** (vote_average): Normalized 0-10 scale → 0-1
- **Vote Count**: More votes = more reliable rating (logarithmic scaling)
- **Popularity**: TMDB popularity score indicates current interest

#### **Discovery Mode** (20-30% weight)
Different modes adjust how popularity affects scoring:
- **Mainstream/Popular**: Boosts high mainstream_score (popularity + votes + rating)
- **Obscure/Hidden Gems**: Boosts high obscurity_score (high rating, low popularity)
- **Balanced**: Equal weight to both mainstream and obscure content
- **Ultra Discovery**: Aggressive exploration of lesser-known titles

#### **Freshness Bonus** (5-15% weight)
Recent releases get a boost:
- Content <1 year old: +15% bonus
- Content 1-2 years old: +10% bonus
- Content 2-3 years old: +5% bonus
- Older content: No bonus

#### **Mood Matching** (10-20% weight for mood lists)
Mood vectors map to genres and themes:
- "Dark" → boosts Thriller, Horror, Crime
- "Cozy" → boosts Comedy, Romance, Family
- "Intense" → boosts Action, Thriller, War
- Multiple moods blend together with configurable weights

#### **Semantic Similarity** (20-55% weight when anchor set)
For "like [movie]" queries:
- TF-IDF vectorization of title, overview, and genres
- Cosine similarity between anchor and candidate
- Higher weight (55%) for chat lists to prioritize good matches
- Lower weight (50%) for other lists to balance with other factors

#### **Trakt History Integration** (Dynamic)
- **Watched Penalty**: Already-watched items scored lower (or excluded entirely)
- **Rating Alignment**: Your Trakt ratings influence similar content scoring (future feature)
- **Genre Preference**: Your watch history informs genre weighting (future feature)

#### **Post-Processing Adjustments**
- **Animation/Family Penalty**: -8-10% when not explicitly requested (reduces kid content noise)
- **English Boost**: +5% for English content when language not specified (mainstream bias)
- **Duplicate Filtering**: Removes duplicates and recently shown items

### Score Calculation Example

For a candidate like "21 Jump Street" (2012):
```python
Base Score = 0.7  # Good rating (7.1/10), 5000+ votes, decent popularity

Discovery Adjustment:
  - Mainstream mode: +0.1 (high mainstream_score = 180)
  
Freshness:
  - Released 2012: 0.0 (>3 years old)

Semantic Similarity (if "like The Hangover"):
  - TF-IDF similarity: 0.65
  - Weight: 0.55 (chat list)
  - Contribution: 0.55 * 0.65 = 0.36

English Boost:
  - +0.05 (English content, no language filter)

Final Score = (0.45 * 0.7) + (0.55 * 0.65) + 0.05 = 0.73
```

This balanced approach ensures:
✅ Quality content rises to the top
✅ Discovery preferences are respected
✅ Semantic matches are prioritized for "like X" queries
✅ Fresh content gets visibility
✅ Your watch history keeps recommendations relevant

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

### How Fetching and Enrichment Works (Plain-English)

- Preloaded pool: WatchBuddy ships with a large, preloaded database of titles so your lists build fast without waiting on external APIs.
- Offline-first syncs: Your SmartLists are created by querying the local database (language, genre, year, mood, etc.).
- Background enrichment: Extra details (artwork, overviews, mappings) are fetched in the background from TMDB/Trakt when available.
- Graceful failures: Some items don’t have perfect cross-service mappings (404s are normal). We keep those items and continue.
- Freshness: Recent titles get vote/popularity refreshed periodically so recommendations stay up to date.

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

---

## 🐳 Docker: Local vs Server and Versioning

- Use `docker-compose.yml` for server/production; it points to published images (`lsdking101/*`).
- Use `docker-compose.override.yml` locally to build images from source while keeping server deploys clean.
- The override file is automatically picked up by `docker compose` when present locally and is ignored by remote builds via `.dockerignore`.

### CI Releases
- On pushes to `main`, GitHub Actions builds and pushes Docker images for backend and frontend with tags:
    - `latest`
    - date-based version (e.g., `v2025.10.15.123`)
    - short commit SHA
- Pushing a git tag like `v1.2.3` will use that semantic version for image tags.

Images include labels `org.opencontainers.image.version` and `org.opencontainers.image.revision`, and both Dockerfiles accept `APP_VERSION` and `GIT_SHA` build args.
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
- **TMDB Movies Dataset (2024)** – made available under the [ODC Attribution License](https://opendatacommons.org/licenses/by/1-0/)
- **TMDB TV Shows Dataset (2024)** – made available under the [ODC Attribution License](https://opendatacommons.org/licenses/by/1-0/)

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

