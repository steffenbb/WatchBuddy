"""
import_bootstrap_data.py

Import bootstrap bundle on first run. This module is called by database.py init_db()
to quickly populate the database from a pre-built bundle instead of CSV files.

Expected bundle structure (watchbuddy_bootstrap.tar.gz):
  bootstrap/
    - persistent_candidates.pgdump
    - faiss_index.bin
    - faiss_map.json
    - elasticsearch_mapping.json (optional)
    - metadata.json

Returns True if import succeeded, False otherwise.
"""

import logging
import json
import subprocess
import shutil
import tarfile
from pathlib import Path
from sqlalchemy import text

from app.core.database import SessionLocal

logger = logging.getLogger(__name__)

BUNDLE_PATH = Path("/app/data/watchbuddy_bootstrap.tar.gz")
EXTRACT_DIR = Path("/tmp/bootstrap_extract")
FAISS_DIR = Path("/data/ai")


def check_bundle_exists():
    """Check if bootstrap bundle exists."""
    return BUNDLE_PATH.exists()


def extract_bundle():
    """Extract bootstrap bundle to temporary directory."""
    logger.warning(f"Extracting bootstrap bundle from {BUNDLE_PATH}...")
    
    # Clean extract directory
    if EXTRACT_DIR.exists():
        shutil.rmtree(EXTRACT_DIR)
    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
    
    try:
        with tarfile.open(BUNDLE_PATH, 'r:gz') as tar:
            tar.extractall(path=EXTRACT_DIR)
        
        # Find the bootstrap directory (should be bootstrap/)
        bootstrap_dirs = list(EXTRACT_DIR.glob("bootstrap"))
        if not bootstrap_dirs:
            raise RuntimeError("Bundle does not contain 'bootstrap/' directory")
        
        bootstrap_dir = bootstrap_dirs[0]
        logger.warning(f"✅ Bundle extracted to {bootstrap_dir}")
        return bootstrap_dir
        
    except Exception as e:
        logger.error(f"Failed to extract bundle: {e}")
        raise


def load_metadata(bootstrap_dir):
    """Load and validate metadata.json."""
    metadata_file = bootstrap_dir / "metadata.json"
    
    if not metadata_file.exists():
        logger.warning("metadata.json not found in bundle")
        return {}
    
    with open(metadata_file) as f:
        metadata = json.load(f)
    
    logger.warning(f"Bundle metadata: {metadata.get('counts', {}).get('total_candidates', 0)} candidates")
    return metadata


def import_database(bootstrap_dir):
    """Import persistent_candidates table using COPY from binary format."""
    logger.warning("Importing database from COPY file...")
    
    copy_file_gz = bootstrap_dir / "persistent_candidates.copy.gz"
    
    if not copy_file_gz.exists():
        raise FileNotFoundError(f"Database dump not found: {copy_file_gz}")
    
    db = SessionLocal()
    try:
        # Check if data already exists
        existing_count = db.execute(text("SELECT COUNT(*) FROM persistent_candidates")).scalar()
        if existing_count > 100000:  # Threshold to detect if bootstrap already loaded
            logger.warning(f"⚠️ Database already contains {existing_count:,} candidates - skipping import")
            
            # Set completion flag since bootstrap data exists
            try:
                from app.core.redis_client import get_redis
                import asyncio
                async def _set_flag():
                    r = get_redis()
                    await r.set("metadata_build:scan_completed", "true")
                asyncio.run(_set_flag())
                logger.warning("Metadata scan marked as completed (bootstrap data detected)")
            except Exception:
                pass
            
            return True  # Return success since data exists
        
        # Increase statement timeout for large import (30 minutes)
        logger.warning("Setting statement timeout to 30 minutes...")
        db.execute(text("SET statement_timeout = '1800000'"))  # 30 minutes in ms
        db.commit()
        
        # Truncate existing table
        logger.warning("Clearing existing data...")
        db.execute(text("TRUNCATE TABLE persistent_candidates CASCADE"))
        db.commit()
        
        # Decompress and import using COPY
        import gzip
        logger.warning("Importing compressed data (this may take 2-3 minutes)...")
        
        raw_conn = db.connection().connection  # Get raw psycopg2 connection
        cursor = raw_conn.cursor()
        
        # Set TCP keepalive to prevent connection drops
        cursor.execute("SET tcp_keepalives_idle = 60")
        cursor.execute("SET tcp_keepalives_interval = 10")
        cursor.execute("SET tcp_keepalives_count = 10")
        
        with gzip.open(copy_file_gz, 'rb') as f:
            cursor.copy_expert(
                "COPY persistent_candidates FROM STDIN WITH (FORMAT binary)",
                f
            )
        
        raw_conn.commit()
        logger.warning("Database import committed, resetting sequences and verifying...")

        # Reset sequence for primary key to max(id) to avoid duplicate key errors on next inserts
        try:
            db.execute(text(
                "SELECT setval("
                "  pg_get_serial_sequence('persistent_candidates','id'),"
                "  GREATEST((SELECT COALESCE(MAX(id), 1) FROM persistent_candidates), 1),"
                "  true"
                ")"
            ))
            db.commit()
            logger.warning("✅ Sequence persistent_candidates.id reset to MAX(id)")
        except Exception as seq_err:
            logger.warning(f"Failed to reset sequence for persistent_candidates.id: {seq_err}")
        
        # Verify import
        count = db.execute(text("SELECT COUNT(*) FROM persistent_candidates")).scalar()
        logger.warning(f"✅ Database imported: {count:,} candidates")
        
        # Set completion flag immediately after successful import
        if count > 0:
            try:
                from app.core.redis_client import get_redis
                import asyncio
                async def _set_flag():
                    r = get_redis()
                    await r.set("metadata_build:scan_completed", "true")
                asyncio.run(_set_flag())
                logger.warning("Metadata scan marked as completed (bootstrap uses TMDB IDs)")
            except Exception:
                pass
        
        return count > 0
        
    except Exception as e:
        logger.error(f"Import failed: {e}", exc_info=True)
        db.rollback()
        raise
    finally:
        db.close()


def import_faiss_index(bootstrap_dir):
    """Copy FAISS index files to /data/ai."""
    logger.warning("Importing FAISS index...")
    
    index_file = bootstrap_dir / "faiss_index.bin"
    map_file = bootstrap_dir / "faiss_map.json"
    
    if not index_file.exists() or not map_file.exists():
        logger.warning("FAISS index files not found in bundle, skipping")
        return False
    
    # Ensure target directory exists
    FAISS_DIR.mkdir(parents=True, exist_ok=True)
    
    # Check if FAISS files already exist
    target_index = FAISS_DIR / "faiss_index.bin"
    target_map = FAISS_DIR / "faiss_map.json"
    
    if target_index.exists() and target_map.exists():
        # Check if existing files are valid (non-empty)
        if target_index.stat().st_size > 1000 and target_map.stat().st_size > 10:
            logger.warning(f"⚠️ FAISS index already exists at {FAISS_DIR} - skipping import")
            return True
    
    try:
        # Copy files
        shutil.copy2(index_file, target_index)
        shutil.copy2(map_file, target_map)
        
        logger.warning(f"✅ FAISS index imported to {FAISS_DIR}")
        return True
    except Exception as e:
        logger.error(f"Failed to copy FAISS files: {e}")
        return False


def import_elasticsearch_data(bootstrap_dir, rebuild_now=True):
    """
    Import Elasticsearch data. Since ES is fast to rebuild from DB,
    we just log that it needs to be rebuilt unless rebuild_now=True.
    """
    if rebuild_now:
        logger.warning("Rebuilding Elasticsearch index from persistent_candidates...")
        try:
            from app.scripts.index_elasticsearch import main as rebuild_es
            rebuild_es()
            logger.warning("✅ Elasticsearch index rebuilt")
            return True
        except Exception as e:
            logger.warning(f"Failed to rebuild Elasticsearch: {e}")
            logger.warning("Elasticsearch will be indexed on first use")
            return True
    else:
        logger.warning("Elasticsearch will be indexed on first use from persistent_candidates")
        return True


def cleanup_extract_dir():
    """Clean up temporary extraction directory."""
    if EXTRACT_DIR.exists():
        shutil.rmtree(EXTRACT_DIR)
        logger.warning("✅ Cleaned up temporary files")


def import_bootstrap_bundle():
    """
    Main import function. Returns True if successful, False otherwise.
    This function is called by database.py init_db() on first startup.
    Uses Redis lock to prevent duplicate imports.
    """
    # Try to acquire a Redis-backed lock to prevent duplicate imports.
    # Use a unique token and atomic release to avoid accidental unlocks from other processes.
    try:
        from app.core.redis_client import get_redis
        import asyncio
        import uuid
        
        async def _try_acquire_lock_with_retries(retries: int = 5, backoff_seconds: float = 1.0):
            r = get_redis()
            token = str(uuid.uuid4())
            for attempt in range(retries):
                try:
                    # NX ensures we only set if not exists; ex sets expiry to avoid stuck locks
                    acquired = await r.set("bootstrap_import_lock", token, nx=True, ex=1800)
                    if acquired:
                        return token
                except Exception:
                    # Redis may not be ready yet; wait and retry
                    await asyncio.sleep(backoff_seconds)
                    continue
            return None

        lock_token = asyncio.run(_try_acquire_lock_with_retries())
        if not lock_token:
            logger.warning("Could not acquire bootstrap import lock (another importer running or Redis unavailable) - skipping import")
            return False
    except Exception as lock_err:
        logger.warning(f"Critical: Redis unavailable while attempting to acquire import lock: {lock_err} - skipping bootstrap import")
        return False
    
    try:
        if not check_bundle_exists():
            logger.warning("No bootstrap bundle found, will use CSV import fallback")
            return False
        
        logger.warning("=" * 60)
        logger.warning("WatchBuddy Bootstrap Import")
        logger.warning("=" * 60)
        
        # Extract bundle
        bootstrap_dir = extract_bundle()
        
        # Load metadata
        metadata = load_metadata(bootstrap_dir)
        
        # Import components (each is independent, failures don't block others)
        db_success = False
        faiss_success = False
        es_success = False
        
        try:
            db_success = import_database(bootstrap_dir)
        except Exception as e:
            logger.error(f"Database import failed: {e}", exc_info=True)
            logger.warning("Continuing with FAISS and ES imports...")
        
        try:
            faiss_success = import_faiss_index(bootstrap_dir)
        except Exception as e:
            logger.error(f"FAISS import failed: {e}", exc_info=True)
        
        try:
            es_success = import_elasticsearch_data(bootstrap_dir)
        except Exception as e:
            logger.error(f"Elasticsearch import failed: {e}", exc_info=True)
        
        # Cleanup
        cleanup_extract_dir()
        
        if db_success:
            logger.warning("=" * 60)
            logger.warning("✅ Bootstrap import complete!")
            logger.warning(f"Database: {'✅' if db_success else '❌'}")
            logger.warning(f"FAISS Index: {'✅' if faiss_success else '❌'}")
            logger.warning(f"Elasticsearch: {'✅' if es_success else '❌'}")
            logger.warning(f"Imported {metadata.get('counts', {}).get('total_candidates', 'unknown')} candidates")
            logger.warning("=" * 60)
            
            # Release lock on success (only if token still matches)
            try:
                from app.core.redis_client import get_redis
                import asyncio

                async def _release_lock(token: str):
                    r = get_redis()
                    # Use a small Lua script to delete the key only if the value matches our token
                    lua = """
                    if redis.call('get', KEYS[1]) == ARGV[1] then
                        return redis.call('del', KEYS[1])
                    else
                        return 0
                    end
                    """
                    try:
                        await r.eval(lua, keys=["bootstrap_import_lock"], args=[token])
                    except Exception:
                        # Fallback: best-effort delete
                        try:
                            await r.delete("bootstrap_import_lock")
                        except Exception:
                            pass

                asyncio.run(_release_lock(lock_token))
            except Exception:
                pass
            
            return True
        else:
            logger.error("Bootstrap import failed")
            
            # Release lock on failure (only if token still matches)
            try:
                from app.core.redis_client import get_redis
                import asyncio

                async def _release_lock(token: str):
                    r = get_redis()
                    lua = """
                    if redis.call('get', KEYS[1]) == ARGV[1] then
                        return redis.call('del', KEYS[1])
                    else
                        return 0
                    end
                    """
                    try:
                        await r.eval(lua, keys=["bootstrap_import_lock"], args=[token])
                    except Exception:
                        try:
                            await r.delete("bootstrap_import_lock")
                        except Exception:
                            pass

                asyncio.run(_release_lock(lock_token))
            except Exception:
                pass
            
            return False
            
    except Exception as e:
        logger.error(f"Bootstrap import failed: {e}", exc_info=True)
        cleanup_extract_dir()
        
        # Release lock on exception (best-effort; only release if token matches)
        try:
            from app.core.redis_client import get_redis
            import asyncio

            async def _release_lock(token: str):
                r = get_redis()
                lua = """
                if redis.call('get', KEYS[1]) == ARGV[1] then
                    return redis.call('del', KEYS[1])
                else
                    return 0
                end
                """
                try:
                    await r.eval(lua, keys=["bootstrap_import_lock"], args=[token])
                except Exception:
                    try:
                        await r.delete("bootstrap_import_lock")
                    except Exception:
                        pass

            # Only try to release if we had acquired a token
            try:
                token = lock_token
            except NameError:
                token = None
            if token:
                asyncio.run(_release_lock(token))
        except Exception:
            pass
        
        return False


if __name__ == "__main__":
    # For testing: run standalone
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    success = import_bootstrap_bundle()
    exit(0 if success else 1)
