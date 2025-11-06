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
    
    # Copy files
    shutil.copy2(index_file, FAISS_DIR / "faiss_index.bin")
    shutil.copy2(map_file, FAISS_DIR / "faiss_map.json")
    
    logger.warning(f"✅ FAISS index imported to {FAISS_DIR}")
    return True


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
    """
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
        
        # Import components
        db_success = import_database(bootstrap_dir)
        faiss_success = import_faiss_index(bootstrap_dir)
        es_success = import_elasticsearch_data(bootstrap_dir)
        
        # Cleanup
        cleanup_extract_dir()
        
        if db_success:
            # Mark metadata scan as completed to prevent auto-trigger of metadata builder
            # Bootstrap data is complete without Trakt IDs (we use TMDB IDs instead)
            try:
                from app.core.redis_client import get_redis
                import asyncio
                async def _set_completion_flag():
                    r = get_redis()
                    await r.set("metadata_build:scan_completed", "true")
                    logger.warning("Metadata scan marked as completed (bootstrap uses TMDB IDs)")
                asyncio.run(_set_completion_flag())
            except Exception as flag_err:
                logger.warning(f"Failed to set metadata completion flag: {flag_err}")
            
            logger.warning("=" * 60)
            logger.warning("✅ Bootstrap import complete!")
            logger.warning(f"Imported {metadata.get('counts', {}).get('total_candidates', 'unknown')} candidates")
            logger.warning("=" * 60)
            return True
        else:
            logger.error("Bootstrap import failed")
            return False
            
    except Exception as e:
        logger.error(f"Bootstrap import failed: {e}", exc_info=True)
        cleanup_extract_dir()
        return False


if __name__ == "__main__":
    # For testing: run standalone
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    success = import_bootstrap_bundle()
    exit(0 if success else 1)
