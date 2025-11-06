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
            # Posters for generated list artwork
            "ALTER TABLE IF EXISTS user_lists ADD COLUMN IF NOT EXISTS poster_path varchar(500) NULL",
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
            "ALTER TABLE IF EXISTS persistent_candidates ADD COLUMN IF NOT EXISTS last_air_date VARCHAR(50)",
            # Overview feature: add rating column to trakt_watch_history
            "ALTER TABLE IF EXISTS trakt_watch_history ADD COLUMN IF NOT EXISTS user_trakt_rating INTEGER"
        ]
        try:
            with engine.begin() as conn:
                for stmt in stmts:
                    try:
                        conn.execute(text(stmt))
                    except Exception:
                        # Ignore individual failures to keep startup resilient
                        pass
            # Late-add columns for Individual Lists (separate to survive partial failures)
            with engine.begin() as conn:
                try:
                    conn.execute(text("ALTER TABLE IF EXISTS individual_lists ADD COLUMN IF NOT EXISTS poster_path varchar(500) NULL"))
                except Exception:
                    pass
            # Late-add columns for AI Lists
            with engine.begin() as conn:
                try:
                    conn.execute(text("ALTER TABLE IF EXISTS ai_lists ADD COLUMN IF NOT EXISTS poster_path varchar(500) NULL"))
                except Exception:
                    pass
        except Exception:
            # Don't block startup if migrations fail; logs are available in container
            pass

    # Quick schema check to skip migrations on already-initialized DBs
    def _schema_is_up_to_date() -> bool:
        try:
            with engine.connect() as conn:
                # Check a few sentinel artifacts that represent our latest schema
                sentinels = [
                    # Extension
                    ("SELECT 1 FROM pg_extension WHERE extname='pg_trgm'", 1),
                    # Columns on list_items
                    ("SELECT COUNT(*) FROM information_schema.columns WHERE table_name='list_items' AND column_name IN ('is_watched','watched_at','trakt_id','media_type')", 4),
                    # Columns on user_lists
                    ("SELECT COUNT(*) FROM information_schema.columns WHERE table_name='user_lists' AND column_name IN ('poster_path','trakt_list_id','list_type')", 3),
                    # Columns on persistent_candidates
                    ("SELECT COUNT(*) FROM information_schema.columns WHERE table_name='persistent_candidates' AND column_name IN ('embedding','production_companies','spoken_languages','number_of_seasons')", 4),
                    # Performance indexes
                    ("SELECT COUNT(*) FROM pg_indexes WHERE tablename='persistent_candidates' AND indexname IN ('idx_persistent_candidates_media_type','idx_persistent_candidates_genres_trgm')", 2)
                ]
                for sql, expected in sentinels:
                    val = conn.execute(text(sql)).scalar() or 0
                    if int(val) < expected:
                        return False
                return True
        except Exception:
            return False

    # Wrap all DDL operations in a Postgres advisory lock to avoid race conditions
    # when multiple Uvicorn workers execute startup concurrently.
    def _perform_migrations_with_lock():
        LOCK_KEY = 748392615  # Arbitrary constant; must be same across workers
        try:
            # If schema already looks current, skip migration phase entirely
            if _schema_is_up_to_date():
                logger.info("Database schema appears up-to-date; skipping migrations")
                return
            with engine.connect() as conn:
                try:
                    logger.warning("Acquiring DB advisory lock for migrations …")
                    conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": LOCK_KEY})
                    logger.warning("DB advisory lock acquired; running migrations …")
                    # Pre-create migrations (drop old indexes, etc.)
                    with engine.begin() as ddl_conn:
                        ddl_conn.execute(text("DROP INDEX IF EXISTS ix_persistent_candidates_trakt_id"))

                    # Create tables
                    Base.metadata.create_all(bind=engine)

                    # Safe, idempotent column/index migrations
                    _run_safe_migrations()
                finally:
                    try:
                        conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY})
                        logger.warning("DB advisory lock released")
                    except Exception:
                        # If unlock fails, the connection closing will release the lock
                        pass
        except Exception as e:
            # Never fail startup due to migration errors
            logger.warning(f"Migration phase failed or partially applied: {e}", exc_info=True)

    # Execute the migration block synchronously (in executor) so startup awaits completion
    await loop.run_in_executor(None, _perform_migrations_with_lock)

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
    
    def _import_supplementary_data(db, data_dir):
        """
        Import missing field data from supplementary CSV files (e.g., cast data).
        Only updates fields that are NULL in the database.
        
        Looks for files matching: *_supplementary.csv, *_enrichment.csv, *_cast.csv
        Expected columns: id (tmdb_id), and any field names matching PersistentCandidate columns
        """
        from app.models import PersistentCandidate
        from sqlalchemy import inspect
        import csv, json
        from pathlib import Path
        
        # Find supplementary CSV files (avoid main dataset files and trakt mappings)
        supplementary_patterns = ['*_supplementary.csv', '*_enrichment.csv', '*_cast.csv', '*cast*.csv']
        supplementary_files = []
        for pattern in supplementary_patterns:
            found = [f for f in data_dir.glob(pattern) 
                    if 'tmdb_movie_dataset' not in f.name.lower() 
                    and 'tmdb_tv_dataset' not in f.name.lower()
                    and 'trakt_mappings' not in f.name.lower()]
            supplementary_files.extend(found)
        
        if not supplementary_files:
            logger.debug("No supplementary CSV files found for enrichment")
            return 0
        
        # Get valid column names from PersistentCandidate model
        mapper = inspect(PersistentCandidate)
        valid_columns = {col.key for col in mapper.columns}
        
        total_updated = 0
        
        for csv_path in supplementary_files:
            logger.info(f"Processing supplementary CSV: {csv_path.name}")
            file_updated = 0
            
            try:
                with csv_path.open('r', encoding='utf-8', newline='') as f:
                    reader = csv.DictReader(f)
                    
                    # Validate that 'id' column exists (this is tmdb_id)
                    if 'id' not in reader.fieldnames:
                        logger.warning(f"Skipping {csv_path.name}: missing 'id' column")
                        continue
                    
                    # Map CSV column names to DB column names
                    # Filter to only valid columns that exist in the model
                    csv_to_db_mapping = {}
                    for csv_col in reader.fieldnames:
                        if csv_col == 'id':
                            continue  # Skip id, we use it for lookup
                        
                        # Convert CSV column name to snake_case if needed
                        db_col = csv_col.lower().replace(' ', '_')
                        
                        if db_col in valid_columns:
                            csv_to_db_mapping[csv_col] = db_col
                        else:
                            logger.debug(f"Skipping unknown column: {csv_col}")
                    
                    if not csv_to_db_mapping:
                        logger.warning(f"No valid columns found in {csv_path.name}")
                        continue
                    
                    logger.info(f"Mapped columns: {list(csv_to_db_mapping.values())}")
                    
                    batch_updates = []
                    batch_size = 500
                    
                    for row in reader:
                        tmdb_id = row.get('id', '').strip()
                        if not tmdb_id:
                            continue
                        
                        try:
                            tmdb_id = int(tmdb_id)
                        except ValueError:
                            continue
                        
                        # Prepare update data for fields that have values in CSV
                        update_data = {'tmdb_id': tmdb_id}
                        has_data = False
                        
                        for csv_col, db_col in csv_to_db_mapping.items():
                            csv_value = row.get(csv_col, '').strip()
                            if not csv_value or csv_value.lower() in ('null', 'none', ''):
                                continue
                            
                            # Parse value based on column type
                            try:
                                # Get column type from model
                                col_obj = mapper.columns[db_col]
                                col_type = str(col_obj.type)
                                
                                if 'TEXT' in col_type or 'VARCHAR' in col_type:
                                    # Text columns - check if it's a list format
                                    if csv_value.startswith('[') or ',' in csv_value:
                                        # Parse as list and store as JSON
                                        if csv_value.startswith('['):
                                            try:
                                                parsed = json.loads(csv_value)
                                                update_data[db_col] = json.dumps(parsed)
                                            except json.JSONDecodeError:
                                                # Clean brackets and split
                                                cleaned = csv_value.strip('[]').replace('"', '').replace("'", "")
                                                items = [x.strip() for x in cleaned.split(',') if x.strip()]
                                                update_data[db_col] = json.dumps(items)
                                        else:
                                            # Comma-separated list
                                            items = [x.strip() for x in csv_value.split(',') if x.strip()]
                                            update_data[db_col] = json.dumps(items)
                                    else:
                                        # Regular text value
                                        update_data[db_col] = csv_value
                                    has_data = True
                                    
                                elif 'INTEGER' in col_type:
                                    try:
                                        update_data[db_col] = int(float(csv_value))
                                        has_data = True
                                    except ValueError:
                                        pass
                                        
                                elif 'NUMERIC' in col_type or 'FLOAT' in col_type or 'REAL' in col_type:
                                    try:
                                        update_data[db_col] = float(csv_value)
                                        has_data = True
                                    except ValueError:
                                        pass
                                        
                                elif 'BOOLEAN' in col_type:
                                    update_data[db_col] = csv_value.lower() in ('1', 'true', 't', 'yes', 'y')
                                    has_data = True
                                    
                                else:
                                    # Default: store as-is
                                    update_data[db_col] = csv_value
                                    has_data = True
                                    
                            except Exception as e:
                                logger.debug(f"Error parsing column {db_col}: {e}")
                                continue
                        
                        if has_data:
                            batch_updates.append(update_data)
                        
                        # Process batch
                        if len(batch_updates) >= batch_size:
                            updated = _update_missing_fields_batch(db, batch_updates, csv_to_db_mapping)
                            file_updated += updated
                            batch_updates = []
                    
                    # Process remaining batch
                    if batch_updates:
                        updated = _update_missing_fields_batch(db, batch_updates, csv_to_db_mapping)
                        file_updated += updated
                
                total_updated += file_updated
                logger.info(f"Updated {file_updated} records from {csv_path.name}")
                
            except Exception as e:
                logger.warning(f"Failed to process {csv_path.name}: {str(e)}", exc_info=True)
                continue
        
        return total_updated
    
    def _update_missing_fields_batch(db, batch_updates, csv_to_db_mapping):
        """Update only fields that are NULL in database."""
        updated_count = 0
        
        try:
            for update_data in batch_updates:
                tmdb_id = update_data['tmdb_id']
                
                # Build dynamic UPDATE query that only sets NULL fields
                set_clauses = []
                params = {'tmdb_id': tmdb_id}
                
                for db_col in csv_to_db_mapping.values():
                    if db_col in update_data:
                        set_clauses.append(f"{db_col} = COALESCE({db_col}, :{db_col})")
                        params[db_col] = update_data[db_col]
                
                if not set_clauses:
                    continue
                
                sql = f"""
                    UPDATE persistent_candidates 
                    SET {', '.join(set_clauses)}
                    WHERE tmdb_id = :tmdb_id
                """
                
                result = db.execute(text(sql), params)
                if result.rowcount > 0:
                    updated_count += result.rowcount
            
            db.commit()
            
        except Exception as e:
            db.rollback()
            logger.error(f"Error updating batch: {str(e)}")
            raise
        
        return updated_count
    
    def _bootstrap_candidates_if_needed():
        """Bootstrap persistent_candidates from bundle or CSV files if table is empty."""
        if _bootstrap_lock['done']:
            return

        from app.models import PersistentCandidate
        from pathlib import Path
        import csv, json

        # Check if already populated (in separate session to avoid lock issues)
        db = SessionLocal()
        try:
            if db.query(PersistentCandidate).limit(1).count() > 0:
                logger.info("Persistent candidates already bootstrapped, skipping import")
                _bootstrap_lock['done'] = True
                return
        finally:
            db.close()
        
        # Try bootstrap bundle first (much faster than CSV)
        # This runs OUTSIDE any session context to avoid transaction locks
        try:
            from app.scripts.import_bootstrap_data import import_bootstrap_bundle
            logger.info("Attempting to import from bootstrap bundle...")
            if import_bootstrap_bundle():
                logger.info("✅ Bootstrap bundle imported successfully")
                _bootstrap_lock['done'] = True
                return
            else:
                logger.info("Bootstrap bundle import failed or not available, falling back to CSV import")
        except Exception as e:
            logger.warning(f"Bootstrap bundle import error: {e}, falling back to CSV import")
        
        # CSV import uses its own session with advisory lock
        with SessionLocal() as db:
            lock_id = 999999
            try:
                # Acquire advisory lock so only one worker bootstraps CSV import
                has_lock = db.execute(text("SELECT pg_try_advisory_lock(:id)"), {"id": lock_id}).scalar()
                if not has_lock:
                    logger.info("Another worker is already bootstrapping CSV, skipping")
                    return

                # Fallback to CSV import
                data_dir = Path('/app/data')
                if not data_dir.exists():
                    logger.warning("Data directory /app/data does not exist, skipping CSV import")
                    return

                csv_files = [f for f in data_dir.glob('*.csv') if 'trakt_mappings' not in f.name.lower()]
                if not csv_files:
                    logger.warning("No CSV files found in /app/data, skipping bootstrap")
                    return

                logger.info(f"Starting CSV bootstrap from {len(csv_files)} file(s)")
                total_imported = 0

                # Helper parsers (file-scope)
                def parse_list(raw_val):
                    if not raw_val:
                        return []
                    raw_val = str(raw_val).strip()
                    if (raw_val.startswith('[') and raw_val.endswith(']')) or (raw_val.startswith('{') and raw_val.endswith('}')):
                        try:
                            data = json.loads(raw_val)
                            if isinstance(data, list):
                                return [str(x).strip() for x in data if str(x).strip()]
                        except Exception:
                            pass
                    cleaned = raw_val.strip('[]').replace('"', '').replace("'", "")
                    delimiter = '|' if '|' in cleaned else (';' if ';' in cleaned else ',')
                    return [p.strip() for p in cleaned.split(delimiter) if p.strip()]

                def get_first_present(row_dict, names):
                    for n in names:
                        v = row_dict.get(n)
                        if v not in (None, ''):
                            return v
                    return ''

                for csv_path in csv_files:
                    logger.warning(f"Processing CSV file: {csv_path.name}")
                    file_imported = 0
                    seen_ids = set()
                    try:
                        with csv_path.open('r', encoding='utf-8', newline='') as f:
                            reader = csv.DictReader(f)
                            batch = []
                            for row in reader:
                                # Required fields
                                tmdb_id = row.get('id')
                                title = row.get('title') or row.get('name')
                                language = (row.get('original_language') or '').lower()[:5]
                                if not tmdb_id or not title or not language:
                                    continue
                                try:
                                    tmdb_id_int = int(tmdb_id)
                                except Exception:
                                    continue

                                # Media type inference
                                if 'name' in row and 'first_air_date' in row:
                                    media_type = 'show'
                                elif 'movie' in csv_path.name.lower():
                                    media_type = 'movie'
                                elif 'tv' in csv_path.name.lower() or 'show' in csv_path.name.lower():
                                    media_type = 'show'
                                else:
                                    media_type = 'movie'

                                # Deduplicate within file
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

                                genres_list = parse_list(row.get('genres') or '')
                                kw_raw = get_first_present(row, ['keywords', 'keyword', 'keywords_names', 'keyword_names', 'tmdb_keywords', 'tmdb_keyword_names', 'tags'])
                                keywords_list = parse_list(kw_raw)
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

                                try:
                                    number_of_seasons = int(float(row.get('number_of_seasons') or 0)) or None
                                except Exception:
                                    number_of_seasons = None
                                try:
                                    number_of_episodes = int(float(row.get('number_of_episodes') or 0)) or None
                                except Exception:
                                    number_of_episodes = None

                                in_production_val = row.get('in_production', '')
                                in_production = str(in_production_val).lower() in ('1', 'true', 't', 'yes', 'y', 'true') if in_production_val else None

                                created_by_list = parse_list(row.get('created_by') or '')
                                networks_list = parse_list(row.get('networks') or '')
                                episode_run_time_list = parse_list(row.get('episode_run_time') or '')
                                adult_val = row.get('adult', 'False')
                                is_adult = str(adult_val).lower() in ('1', 'true', 't', 'yes', 'y', 'true')

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
                                    try:
                                        db.bulk_save_objects(batch)
                                        db.commit()
                                        file_imported += len(batch)
                                    except Exception:
                                        db.rollback()
                                        for item in batch:
                                            try:
                                                db.merge(item)
                                                file_imported += 1
                                            except Exception:
                                                pass
                                        db.commit()
                                    batch = []

                            # flush remaining
                            if batch:
                                try:
                                    db.bulk_save_objects(batch)
                                    db.commit()
                                    file_imported += len(batch)
                                except Exception:
                                    db.rollback()
                                    for item in batch:
                                        try:
                                            db.merge(item)
                                            file_imported += 1
                                        except Exception:
                                            pass
                                    db.commit()

                        total_imported += file_imported
                        logger.warning(f"Successfully imported {file_imported} items from {csv_path.name}")

                    except Exception as e:
                        logger.warning(f"Failed to import {csv_path.name}: {e}", exc_info=True)
                        db.rollback()
                        continue

                logger.warning(f"CSV bootstrap complete. Total items imported: {total_imported}")
                _bootstrap_lock['done'] = True

                # Supplementary enrichment
                try:
                    updated = _import_supplementary_data(db, data_dir)
                    if updated > 0:
                        logger.info(f"Supplementary data import: updated {updated} records with missing fields")
                except Exception as e:
                    logger.debug(f"Supplementary data import skipped or failed: {e}")

                # Skip Trakt CSV auto-import
                logger.info("Skipping trakt_mappings_export.csv import: using on-demand TMDB→Trakt resolution with cache")

                # Mark metadata scan as completed
                try:
                    import asyncio
                    from app.core.redis_client import get_redis
                    async def _set_completion_flag():
                        r = get_redis()
                        await r.set("metadata_build:scan_completed", "true")
                        logger.info("Metadata scan marked as completed (bootstrap finished; nightly task will populate trakt_ids)")
                    asyncio.run(_set_completion_flag())
                except Exception as flag_err:
                    logger.warning(f"Failed to set metadata completion flag: {flag_err}")

            except Exception as e:
                logger.warning(f"Critical error during CSV bootstrap: {e}", exc_info=True)
                _bootstrap_lock['done'] = True
            finally:
                try:
                    db.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": lock_id})
                    logger.debug("Released CSV bootstrap advisory lock")
                except Exception:
                    pass

    # CSV bootstrap of persistent candidates can be heavy. If the database already
    # contains candidates, skip bootstrap entirely. This both speeds up startup and
    # avoids unnecessary work on persistent environments.
    def _has_persistent_candidates() -> bool:
        try:
            with engine.connect() as conn:
                row = conn.execute(text("SELECT 1 FROM persistent_candidates LIMIT 1")).first()
                return row is not None
        except Exception as e:
            logger.warning(f"Failed to check persistent_candidates existence: {e}")
            return False

    try:
        if _has_persistent_candidates():
            logger.info("persistent_candidates already populated; skipping CSV bootstrap scheduling")
        else:
            # When needed, schedule bootstrap in background (non-blocking). Force synchronous
            # via WB_BOOTSTRAP_SYNC=1 if desired for one-time setups.
            if os.getenv("WB_BOOTSTRAP_SYNC", "0") == "1":
                logger.info("WB_BOOTSTRAP_SYNC=1 detected; running CSV bootstrap synchronously during startup")
                await loop.run_in_executor(None, _bootstrap_candidates_if_needed)
            else:
                logger.info("Scheduling CSV bootstrap in background (only because table is empty)")
                loop.run_in_executor(None, _bootstrap_candidates_if_needed)
    except Exception as e:
        logger.warning(f"Failed to schedule or skip CSV bootstrap: {e}")


