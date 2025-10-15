from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine, text
import asyncio
import os

# Read DB credentials from environment variables (set in docker-compose.yml)
user = os.getenv("POSTGRES_USER", "watchbuddy")
password = os.getenv("POSTGRES_PASSWORD", "watchbuddy")
db = os.getenv("POSTGRES_DB", "watchbuddy")
if os.getenv("POSTGRES_HOST_AUTH_METHOD") == "trust":
    # Allow empty password in libpq when server trusts host
    DATABASE_URL = f"postgresql://{user}@db:5432/{db}"
else:
    DATABASE_URL = f"postgresql://{user}:{password}@db:5432/{db}"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

async def init_db():
    from app.models import Base
    loop = asyncio.get_running_loop()
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
            "ALTER TABLE IF EXISTS user_lists ADD COLUMN IF NOT EXISTS trakt_list_id varchar(255) NULL",
            "CREATE INDEX IF NOT EXISTS idx_user_lists_trakt_list_id ON user_lists (trakt_list_id)",
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
            "CREATE INDEX IF NOT EXISTS idx_candidate_ingestion_state_media_type ON candidate_ingestion_state (media_type)"
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
    def _bootstrap_candidates_if_needed():
        try:
            from app.models import PersistentCandidate
            from pathlib import Path
            import csv, json
            with SessionLocal() as db:
                count = db.query(PersistentCandidate).limit(1).count()
                if count > 0:
                    return  # Already bootstrapped
                data_dir = Path('/app/data')
                if not data_dir.exists():
                    return
                # Accept multiple CSVs (movies/shows). Import using simplified inline parser to avoid circular imports.
                csv_files = list(data_dir.glob('*.csv'))
                if not csv_files:
                    return
                for csv_path in csv_files:
                    try:
                        with csv_path.open('r', encoding='utf-8', newline='') as f:
                            reader = csv.DictReader(f)
                            batch = []
                            for row in reader:
                                # Basic required fields: id, title/name, media_type or infer
                                tmdb_id = row.get('id')
                                title = row.get('title') or row.get('name')
                                if not tmdb_id or not title:
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
                                language = (row.get('original_language') or '').lower()[:5]
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
                                    overview=row.get('overview') or '',
                                    popularity=popularity,
                                    vote_average=vote_average,
                                    vote_count=vote_count,
                                    poster_path=row.get('poster_path'),
                                    backdrop_path=row.get('backdrop_path'),
                                    is_adult=is_adult,
                                    manual=True
                                )
                                pc.compute_scores()
                                batch.append(pc)
                                if len(batch) >= 500:
                                    db.bulk_save_objects(batch)
                                    db.commit()
                                    batch = []
                            if batch:
                                db.bulk_save_objects(batch)
                                db.commit()
                        # Continue with next CSV
                    except Exception:
                        db.rollback()
                        continue
        except Exception:
            # Silent failure to avoid blocking startup
            pass

    await loop.run_in_executor(None, _bootstrap_candidates_if_needed)


