from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine, text
import asyncio
import os
import logging

logger = logging.getLogger(__name__)

# Read DB credentials from environment variables (set in docker-compose.yml)
user = os.getenv("POSTGRES_USER", "watchbuddy")
password = os.getenv("POSTGRES_PASSWORD", "watchbuddy")
db = os.getenv("POSTGRES_DB", "watchbuddy")
if os.getenv("POSTGRES_HOST_AUTH_METHOD") == "trust":
    # Allow empty password in libpq when server trusts host
    DATABASE_URL = f"postgresql://{user}@db:5432/{db}"
else:
    DATABASE_URL = f"postgresql://{user}:{password}@db:5432/{db}"


# Increase connection pool to handle concurrent operations
# pool_size: base connections (default 5 -> 20)
# max_overflow: additional connections allowed (default 10 -> 30)
# pool_timeout: seconds to wait for connection (default 30)
# pool_recycle: recycle connections after N seconds to prevent stale connections
# pool_pre_ping: verify connections before using them
engine = create_engine(
    DATABASE_URL,
    pool_size=20,
    max_overflow=30,
    pool_timeout=30,
    pool_recycle=3600,
    pool_pre_ping=True
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- ASYNC SESSION SUPPORT FOR CELERY TASKS ---
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker as async_sessionmaker

ASYNC_DATABASE_URL = DATABASE_URL.replace('postgresql://', 'postgresql+asyncpg://')
async_engine = create_async_engine(
    ASYNC_DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
    pool_recycle=3600,
    pool_pre_ping=True
)
AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False, class_=AsyncSession)

async def get_async_session():
    async with AsyncSessionLocal() as session:
        yield session

async def init_db():
    from app.models import Base
    loop = asyncio.get_running_loop()
    
    # Run pre-create migrations first (before create_all) to fix constraints
    def _run_pre_migrations():
        with engine.begin() as conn:
            # Fix trakt_id constraint: drop old single-column unique index if it exists
            # This must run before create_all() which would try to create the new composite index
            conn.execute(text("DROP INDEX IF EXISTS ix_persistent_candidates_trakt_id"))
    
    await loop.run_in_executor(None, _run_pre_migrations)
    
    def _create_all():
        Base.metadata.create_all(bind=engine)
    await loop.run_in_executor(None, _create_all)

    # Safe, idempotent migrations for new columns (PostgreSQL)
    def _run_safe_migrations():
        stmts = [
            # Enable pg_trgm for faster ILIKE queries
            "CREATE EXTENSION IF NOT EXISTS pg_trgm",
            # list_items new columns
            "ALTER TABLE IF EXISTS list_items ADD COLUMN IF NOT EXISTS is_watched boolean DEFAULT false",
            "ALTER TABLE IF EXISTS list_items ADD COLUMN IF NOT EXISTS watched_at timestamp NULL",
            "ALTER TABLE IF EXISTS list_items ADD COLUMN IF NOT EXISTS trakt_id integer NULL",
            "ALTER TABLE IF EXISTS list_items ADD COLUMN IF NOT EXISTS media_type varchar(50) NOT NULL DEFAULT 'movie'",
            # user_lists new columns
            "ALTER TABLE IF EXISTS user_lists ADD COLUMN IF NOT EXISTS last_sync_at timestamp NULL",
            "ALTER TABLE IF EXISTS user_lists ADD COLUMN IF NOT EXISTS last_full_sync_at timestamp NULL",
            "ALTER TABLE IF EXISTS user_lists ADD COLUMN IF NOT EXISTS sync_status varchar(20) DEFAULT 'pending'",
            "ALTER TABLE IF EXISTS user_lists ADD COLUMN IF NOT EXISTS sync_watched_status boolean DEFAULT true",
            "ALTER TABLE IF EXISTS user_lists ADD COLUMN IF NOT EXISTS exclude_watched boolean DEFAULT false",
            "ALTER TABLE IF EXISTS user_lists ADD COLUMN IF NOT EXISTS list_type varchar(50) NOT NULL DEFAULT 'smartlist'",
            "ALTER TABLE IF EXISTS user_lists ADD COLUMN IF NOT EXISTS persistent_id integer NULL",
            "ALTER TABLE IF EXISTS user_lists ADD COLUMN IF NOT EXISTS dynamic_theme varchar(255) NULL",
            "ALTER TABLE IF EXISTS user_lists ADD COLUMN IF NOT EXISTS trakt_list_id varchar(255) NULL",
            "CREATE INDEX IF NOT EXISTS idx_user_lists_trakt_list_id ON user_lists (trakt_list_id)",
            "CREATE INDEX IF NOT EXISTS ix_userlist_type_pid ON user_lists (user_id, list_type, persistent_id)",
            # persistent_candidates index / columns (table created by create_all)
            "CREATE INDEX IF NOT EXISTS idx_persistent_candidates_media_type ON persistent_candidates (media_type)",
            "CREATE INDEX IF NOT EXISTS idx_persistent_candidates_language ON persistent_candidates (language)",
            "CREATE INDEX IF NOT EXISTS idx_persistent_candidates_year ON persistent_candidates (year)",
            "CREATE INDEX IF NOT EXISTS idx_persistent_candidates_popularity ON persistent_candidates (popularity)",
            "CREATE INDEX IF NOT EXISTS idx_persistent_candidates_vote_count ON persistent_candidates (vote_count)",
            "CREATE INDEX IF NOT EXISTS idx_persistent_candidates_obscurity ON persistent_candidates (obscurity_score)",
            "CREATE INDEX IF NOT EXISTS idx_persistent_candidates_mainstream ON persistent_candidates (mainstream_score)",
            "CREATE INDEX IF NOT EXISTS idx_persistent_candidates_freshness ON persistent_candidates (freshness_score)",
            # Trigram index to accelerate ILIKE on genres JSON text
            "CREATE INDEX IF NOT EXISTS idx_persistent_candidates_genres_trgm ON persistent_candidates USING gin (genres gin_trgm_ops)",
            "CREATE INDEX IF NOT EXISTS idx_candidate_ingestion_state_media_type ON candidate_ingestion_state (media_type)",
            # Ensure new AI columns exist on persistent_candidates
            "ALTER TABLE IF EXISTS persistent_candidates ADD COLUMN IF NOT EXISTS embedding BYTEA",
            # Guard columns for entity filtering (if older DBs missing them)
            "ALTER TABLE IF EXISTS persistent_candidates ADD COLUMN IF NOT EXISTS \"cast\" TEXT",
            "ALTER TABLE IF EXISTS persistent_candidates ADD COLUMN IF NOT EXISTS production_companies TEXT",
            # New TMDB dataset fields (budget, tagline, etc.)
            "ALTER TABLE IF EXISTS persistent_candidates ADD COLUMN IF NOT EXISTS production_countries TEXT",
            "ALTER TABLE IF EXISTS persistent_candidates ADD COLUMN IF NOT EXISTS spoken_languages TEXT",
            "ALTER TABLE IF EXISTS persistent_candidates ADD COLUMN IF NOT EXISTS budget INTEGER",
            "ALTER TABLE IF EXISTS persistent_candidates ADD COLUMN IF NOT EXISTS revenue INTEGER",
            "ALTER TABLE IF EXISTS persistent_candidates ADD COLUMN IF NOT EXISTS tagline VARCHAR(500)",
            "ALTER TABLE IF EXISTS persistent_candidates ADD COLUMN IF NOT EXISTS homepage VARCHAR(500)",
            # TV-specific fields
            "ALTER TABLE IF EXISTS persistent_candidates ADD COLUMN IF NOT EXISTS number_of_seasons INTEGER",
            "ALTER TABLE IF EXISTS persistent_candidates ADD COLUMN IF NOT EXISTS number_of_episodes INTEGER",
            "ALTER TABLE IF EXISTS persistent_candidates ADD COLUMN IF NOT EXISTS in_production BOOLEAN",
            "ALTER TABLE IF EXISTS persistent_candidates ADD COLUMN IF NOT EXISTS created_by TEXT",
            "ALTER TABLE IF EXISTS persistent_candidates ADD COLUMN IF NOT EXISTS networks TEXT",
            "ALTER TABLE IF EXISTS persistent_candidates ADD COLUMN IF NOT EXISTS episode_run_time TEXT",
            "ALTER TABLE IF EXISTS persistent_candidates ADD COLUMN IF NOT EXISTS first_air_date VARCHAR(50)",
            "ALTER TABLE IF EXISTS persistent_candidates ADD COLUMN IF NOT EXISTS last_air_date VARCHAR(50)"
        ]
        try:
            with engine.begin() as conn:
                for stmt in stmts:
                    try:
                        conn.execute(text(stmt))
                    except Exception:
                        # Ignore individual failures to keep startup resilient
                        pass
        except Exception:
            # Don't block startup if migrations fail; logs are available in container
            pass

    await loop.run_in_executor(None, _run_safe_migrations)

    # Create default user if none exists
    def _create_default_user():
        from app.models import User
        import hashlib
        try:
            with SessionLocal() as db:
                # Check if any users exist
                user_count = db.query(User).count()
                if user_count == 0:
                    # Create a default user
                    default_user = User(
                        email="default@watchbuddy.local",
                        password_hash=hashlib.sha256("default".encode()).hexdigest()
                    )
                    db.add(default_user)
                    db.commit()
        except Exception:
            # Don't block startup if user creation fails
            pass

    await loop.run_in_executor(None, _create_default_user)

    # Bootstrap persistent candidate pool from bundled CSVs if empty
    _bootstrap_lock = {'done': False}
    
    def _bootstrap_candidates_if_needed():
        """Bootstrap persistent_candidates from CSV files in /app/data if table is empty."""
        # Check lock first to prevent duplicate runs in same process
        if _bootstrap_lock['done']:
            return
            
        try:
            from app.models import PersistentCandidate
            from pathlib import Path
            import csv, json
            with SessionLocal() as db:
                count = db.query(PersistentCandidate).limit(1).count()
                if count > 0:
                    logger.info("Persistent candidates already bootstrapped, skipping CSV import")
                    _bootstrap_lock['done'] = True
                    return  # Already bootstrapped
                data_dir = Path('/app/data')
                if not data_dir.exists():
                    logger.warning("Data directory /app/data does not exist, skipping CSV import")
                    return
                # Accept multiple CSVs (movies/shows). Import using simplified inline parser to avoid circular imports.
                # Exclude trakt_mappings_export.csv as that's processed separately
                csv_files = [f for f in data_dir.glob('*.csv') if 'trakt_mappings' not in f.name.lower()]
                if not csv_files:
                    logger.warning("No CSV files found in /app/data, skipping bootstrap")
                    return
                
                logger.info(f"Starting CSV bootstrap from {len(csv_files)} file(s)")
                total_imported = 0
                
                for csv_path in csv_files:
                    logger.warning(f"Processing CSV file: {csv_path.name}")
                    file_imported = 0
                    seen_ids = set()  # Track (tmdb_id, media_type) to avoid duplicates
                    try:
                        with csv_path.open('r', encoding='utf-8', newline='') as f:
                            reader = csv.DictReader(f)
                            batch = []
                            for row in reader:
                                # Basic required fields: id, title/name, language
                                tmdb_id = row.get('id')
                                title = row.get('title') or row.get('name')
                                language = (row.get('original_language') or '').lower()[:5]
                                
                                # Skip items without required fields
                                if not tmdb_id or not title or not language:
                                    continue
                                try:
                                    tmdb_id_int = int(tmdb_id)
                                except Exception:
                                    continue
                                    
                                # Infer media type from filename or columns
                                if 'name' in row and 'first_air_date' in row:
                                    media_type = 'show'
                                elif 'movie' in csv_path.name.lower():
                                    media_type = 'movie'
                                elif 'tv' in csv_path.name.lower() or 'show' in csv_path.name.lower():
                                    media_type = 'show'
                                else:
                                    media_type = 'movie'
                                
                                # Skip duplicates within CSV
                                dup_key = (tmdb_id_int, media_type)
                                if dup_key in seen_ids:
                                    continue
                                seen_ids.add(dup_key)
                                
                                release_date = row.get('release_date') or row.get('first_air_date')
                                year = None
                                if release_date and len(release_date) >= 4:
                                    try:
                                        year = int(release_date[:4])
                                    except Exception:
                                        year = None
                                def parse_list(raw_val):
                                    if not raw_val or raw_val == '':
                                        return []
                                    raw_val = str(raw_val).strip()
                                    if raw_val.startswith('[') and raw_val.endswith(']'):
                                        try:
                                            data = json.loads(raw_val)
                                            if isinstance(data, list):
                                                return [str(x) for x in data]
                                        except Exception:
                                            pass
                                    return [p.strip() for p in raw_val.split(',') if p.strip()]
                                genres_list = parse_list(row.get('genres') or '')
                                keywords_list = parse_list(row.get('keywords') or '')
                                production_companies_list = parse_list(row.get('production_companies') or '')
                                production_countries_list = parse_list(row.get('production_countries') or '')
                                spoken_languages_list = parse_list(row.get('spoken_languages') or '')
                                
                                try:
                                    popularity = float(row.get('popularity') or 0.0)
                                except Exception:
                                    popularity = 0.0
                                try:
                                    vote_average = float(row.get('vote_average') or 0.0)
                                except Exception:
                                    vote_average = 0.0
                                try:
                                    vote_count = int(float(row.get('vote_count') or 0))
                                except Exception:
                                    vote_count = 0
                                try:
                                    budget = int(float(row.get('budget') or 0))
                                except Exception:
                                    budget = None
                                try:
                                    revenue = int(float(row.get('revenue') or 0))
                                except Exception:
                                    revenue = None
                                try:
                                    runtime = int(float(row.get('runtime') or 0))
                                except Exception:
                                    runtime = None
                                
                                # TV-specific fields
                                try:
                                    number_of_seasons = int(float(row.get('number_of_seasons') or 0)) or None
                                except Exception:
                                    number_of_seasons = None
                                try:
                                    number_of_episodes = int(float(row.get('number_of_episodes') or 0)) or None
                                except Exception:
                                    number_of_episodes = None
                                
                                in_production_val = row.get('in_production', '')
                                in_production = str(in_production_val).lower() in ('1', 'true', 't', 'yes', 'y', 'True') if in_production_val else None
                                
                                created_by_list = parse_list(row.get('created_by') or '')
                                networks_list = parse_list(row.get('networks') or '')
                                episode_run_time_list = parse_list(row.get('episode_run_time') or '')
                                
                                # Handle is_adult properly
                                adult_val = row.get('adult', 'False')
                                is_adult = str(adult_val).lower() in ('1', 'true', 't', 'yes', 'y', 'True')
                                pc = PersistentCandidate(
                                    tmdb_id=tmdb_id_int,
                                    trakt_id=None,
                                    imdb_id=row.get('imdb_id'),
                                    media_type=media_type,
                                    title=title.strip(),
                                    original_title=row.get('original_title') or row.get('original_name'),
                                    year=year,
                                    release_date=release_date,
                                    language=language,
                                    genres=json.dumps(genres_list) if genres_list else None,
                                    keywords=json.dumps(keywords_list) if keywords_list else None,
                                    production_companies=json.dumps(production_companies_list) if production_companies_list else None,
                                    production_countries=json.dumps(production_countries_list) if production_countries_list else None,
                                    spoken_languages=json.dumps(spoken_languages_list) if spoken_languages_list else None,
                                    overview=row.get('overview') or '',
                                    popularity=popularity,
                                    vote_average=vote_average,
                                    vote_count=vote_count,
                                    runtime=runtime,
                                    status=row.get('status'),
                                    poster_path=row.get('poster_path'),
                                    backdrop_path=row.get('backdrop_path'),
                                    budget=budget,
                                    revenue=revenue,
                                    tagline=row.get('tagline'),
                                    homepage=row.get('homepage'),
                                    # TV-specific fields
                                    number_of_seasons=number_of_seasons,
                                    number_of_episodes=number_of_episodes,
                                    in_production=in_production,
                                    created_by=json.dumps(created_by_list) if created_by_list else None,
                                    networks=json.dumps(networks_list) if networks_list else None,
                                    episode_run_time=json.dumps(episode_run_time_list) if episode_run_time_list else None,
                                    first_air_date=row.get('first_air_date'),
                                    last_air_date=row.get('last_air_date'),
                                    is_adult=is_adult,
                                    manual=True
                                )
                                pc.compute_scores()
                                batch.append(pc)
                                if len(batch) >= 500:
                                    db.bulk_save_objects(batch)
                                    db.commit()
                                    file_imported += len(batch)
                                    batch = []
                            if batch:
                                db.bulk_save_objects(batch)
                                db.commit()
                                file_imported += len(batch)
                        
                        total_imported += file_imported
                        logger.warning(f"Successfully imported {file_imported} items from {csv_path.name}")
                        
                    except Exception as e:
                        logger.warning(f"Failed to import {csv_path.name}: {str(e)}", exc_info=True)
                        db.rollback()
                        continue
                
                logger.warning(f"CSV bootstrap complete. Total items imported: {total_imported}")
                _bootstrap_lock['done'] = True
                
                # Auto-import trakt_id mappings if file exists
                trakt_imported = 0
                try:
                    from app.scripts.auto_import_trakt import auto_import_trakt_mappings
                    trakt_imported = auto_import_trakt_mappings(db)
                    if trakt_imported > 0:
                        logger.info(f"Auto-imported {trakt_imported} trakt_id mappings from trakt_mappings_export.csv")
                except Exception as e:
                    logger.debug(f"Trakt ID auto-import skipped or failed: {e}")
                
                # Mark metadata scan as completed to avoid showing setup UI
                # The nightly Celery task will continue to fill in missing trakt_ids
                try:
                    import asyncio
                    from app.core.redis_client import get_redis
                    async def _set_completion_flag():
                        r = get_redis()
                        await r.set("metadata_build:scan_completed", "true")
                        if trakt_imported > 0:
                            logger.info(f"Metadata scan marked as completed (auto-import successful with {trakt_imported} mappings)")
                        else:
                            logger.info("Metadata scan marked as completed (bootstrap finished, nightly task will populate trakt_ids)")
                    asyncio.run(_set_completion_flag())
                except Exception as flag_err:
                    logger.warning(f"Failed to set metadata completion flag: {flag_err}")

                
        except Exception as e:
            logger.warning(f"Critical error during CSV bootstrap: {str(e)}", exc_info=True)
            _bootstrap_lock['done'] = True

    await loop.run_in_executor(None, _bootstrap_candidates_if_needed)


