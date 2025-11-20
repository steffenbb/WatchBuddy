"""
FAISS index helpers for HNSW (float32).
- train_build_hnsw: builds HNSW index from embeddings and mapping (no training needed)
- load_index: loads index from disk
- search_index: queries index and returns mapped trakt_ids
- add_to_index: incrementally add new embeddings to existing index
- serialize_embedding/deserialize_embedding: convert embeddings to/from bytes for DB storage

HNSW Optimization:
- IndexHNSWFlat with L2 metric (normalized vectors = cosine similarity)
- M=32 (bidirectional links per layer) - good balance of speed/quality
- efConstruction=200 (build-time quality) - higher = better index quality
- efSearch=200-350 (search-time quality) - tunable per query
- Float32 for best CPU performance
"""
import numpy as np
import faiss
import json
import logging
import os
from pathlib import Path
from typing import List, Tuple, Dict, Optional

logger = logging.getLogger(__name__)

# Spec paths
DATA_DIR = Path("/data/ai")
DATA_DIR.mkdir(exist_ok=True, parents=True)
INDEX_FILE = DATA_DIR / "faiss_index.bin"
MAPPING_FILE = DATA_DIR / "faiss_map.json"

# HNSW hyperparameters
HNSW_M = 32  # Bidirectional links per layer (higher = better recall, slower build)
HNSW_EF_CONSTRUCTION = 200  # Build-time quality (higher = better index)
HNSW_EF_SEARCH = 250  # Search-time quality (200-350 range, tunable)

# Simple in-process cache to avoid re-reading FAISS index on every request
_INDEX_CACHE = None
_MAP_CACHE = None


def _l2_normalize(mat: np.ndarray) -> np.ndarray:
    """Normalize vectors to unit length for cosine similarity via L2 distance."""
    norms = np.linalg.norm(mat, axis=1, keepdims=True) + 1e-8
    return (mat / norms).astype(np.float32)  # Keep float32 for CPU performance


def serialize_embedding(embedding: np.ndarray) -> bytes:
    """Convert numpy embedding array to bytes for database storage."""
    # Keep float32 for CPU performance (faster than float16)
    return embedding.astype(np.float32).tobytes()


def deserialize_embedding(blob: bytes) -> np.ndarray:
    """Convert bytes from database back to numpy float32 array."""
    return np.frombuffer(blob, dtype=np.float32)


def train_build_hnsw(embeddings: np.ndarray, trakt_ids: List[int], dim: int):
    """
    Build HNSW index (no training needed), save to disk, and export mapping.
    
    HNSW is a graph-based ANN algorithm that's much faster than IVF+PQ:
    - No training phase required
    - Better recall at same speed
    - Scales well for up to 10M+ vectors
    
    Args:
        embeddings: Embedding vectors (N x dim) as float32
        trakt_ids: Trakt IDs for each embedding
        dim: Embedding dimension
    """
    # Create HNSW index with L2 metric
    index = faiss.IndexHNSWFlat(dim, HNSW_M)
    index.hnsw.efConstruction = HNSW_EF_CONSTRUCTION
    
    logger.info(f"[FAISS] Building HNSW index with {len(trakt_ids)} vectors (dim={dim}, M={HNSW_M}, efConstruction={HNSW_EF_CONSTRUCTION})...")
    
    # Normalize for cosine similarity via L2 distance
    embeddings_normalized = _l2_normalize(embeddings.astype(np.float32))
    
    # Add all vectors (HNSW builds incrementally, no separate training)
    index.add(embeddings_normalized)
    
    # Save index with atomic write pattern to prevent corruption
    # Write to temp files first, then atomically rename to final location
    import fcntl
    
    LOCK_FILE = DATA_DIR / "faiss_index.lock"
    INDEX_TEMP = DATA_DIR / "faiss_index.bin.tmp"
    MAPPING_TEMP = DATA_DIR / "faiss_map.json.tmp"
    
    with open(LOCK_FILE, 'w') as lock_f:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)  # Exclusive lock - blocks all readers/writers
        try:
            # Write to temp files first
            faiss.write_index(index, str(INDEX_TEMP))
            
            # Save mapping (use trakt_id now instead of tmdb_id for better coverage)
            mapping = {int(i): int(trakt_ids[i]) for i in range(len(trakt_ids))}
            with open(MAPPING_TEMP, "w") as f:
                json.dump(mapping, f)
                f.flush()
                os.fsync(f.fileno())  # Force write to disk
            
            # Atomic rename (overwrites existing files atomically)
            INDEX_TEMP.rename(INDEX_FILE)
            MAPPING_TEMP.rename(MAPPING_FILE)
            
            logger.info(f"[FAISS] ✅ Index files written atomically")
        except Exception as e:
            # Clean up temp files on error
            if INDEX_TEMP.exists():
                INDEX_TEMP.unlink()
            if MAPPING_TEMP.exists():
                MAPPING_TEMP.unlink()
            raise e
        finally:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
    
    logger.info(f"[FAISS] ✅ HNSW index and mapping saved to {INDEX_FILE} and {MAPPING_FILE}")
    logger.info(f"[FAISS] Index stats: {len(trakt_ids)} vectors, M={HNSW_M}, efSearch will be {HNSW_EF_SEARCH}")


# Keep old function name for backwards compatibility
def train_build_ivfpq(embeddings: np.ndarray, trakt_ids: List[int], dim: int, nlist: int = 4096, m: int = 64, nbits: int = 8):
    """Legacy wrapper - redirects to HNSW build."""
    logger.warning("[FAISS] train_build_ivfpq() is deprecated, using HNSW instead")
    train_build_hnsw(embeddings, trakt_ids, dim)


def _rebuild_index_from_db() -> bool:
    """
    Failsafe: Rebuild FAISS index from all existing embeddings in persistent_candidates.
    Returns True if successful, False otherwise.
    """
    try:
        logger.warning("[FAISS] Index missing - attempting automatic rebuild from database...")
        from app.core.database import SessionLocal
        from sqlalchemy import text
        
        db = SessionLocal()
        try:
            # Count candidates with embeddings (trakt_id optional)
            total = db.execute(text(
                "SELECT COUNT(*) FROM persistent_candidates WHERE embedding IS NOT NULL"
            )).scalar()
            
            if total == 0:
                logger.error("[FAISS] No candidates with embeddings found for rebuild")
                return False
            
            logger.info(f"[FAISS] Found {total} candidates for index rebuild")
            
            all_embeddings = []
            all_trakt_ids = []
            batch_size = 10000
            offset = 0
            
            while offset < total:
                rows = db.execute(text(
                    """
                    SELECT COALESCE(trakt_id, tmdb_id) AS trakt_id, embedding
                    FROM persistent_candidates
                    WHERE embedding IS NOT NULL
                    ORDER BY id
                    OFFSET :off LIMIT :lim
                    """
                ), {"off": offset, "lim": batch_size}).fetchall()
                
                if not rows:
                    break
                
                for trakt_id, embedding_blob in rows:
                    try:
                        emb = deserialize_embedding(embedding_blob)
                        all_embeddings.append(emb)
                        all_trakt_ids.append(int(trakt_id))
                    except Exception as e:
                        logger.warning(f"[FAISS] Failed to deserialize embedding for trakt_id={trakt_id}: {e}")
                        continue
                
                offset += len(rows)
                logger.info(f"[FAISS] Loaded {len(all_embeddings)} embeddings...")
            
            if not all_embeddings:
                logger.error("[FAISS] No valid embeddings loaded for rebuild")
                return False
            
            # Build index
            embeddings_array = np.array(all_embeddings, dtype=np.float32)
            dim = embeddings_array.shape[1]
            logger.info(f"[FAISS] Building HNSW index with {len(all_trakt_ids)} vectors (dim={dim})...")
            train_build_hnsw(embeddings_array, all_trakt_ids, dim)
            logger.info("[FAISS] ✅ Index rebuild successful!")
            return True
            
        finally:
            db.close()
            
    except Exception as e:
        logger.error(f"[FAISS] Index rebuild failed: {e}", exc_info=True)
        return False


def load_index() -> Tuple[faiss.IndexHNSWFlat, Dict[int, int]]:
    """
    Load FAISS HNSW index and mapping from disk.
    If index files are missing, attempts to rebuild from database embeddings.
    Uses a lock file to prevent concurrent reads that corrupt FAISS's internal file operations.
    """
    global _INDEX_CACHE, _MAP_CACHE
    if _INDEX_CACHE is not None and _MAP_CACHE is not None:
        return _INDEX_CACHE, _MAP_CACHE
    
    # Check if index exists, rebuild if missing
    if not INDEX_FILE.exists() or not MAPPING_FILE.exists():
        logger.warning("[FAISS] Index files not found, attempting rebuild...")
        if not _rebuild_index_from_db():
            raise FileNotFoundError(f"FAISS index not found at {INDEX_FILE} and rebuild failed")
    
    # Use a separate lock file to coordinate access across processes
    # FAISS's C++ code does its own file I/O, so we need a dedicated lock file
    import fcntl
    import time
    
    LOCK_FILE = DATA_DIR / "faiss_index.lock"
    max_retries = 5
    retry_delay = 0.3
    
    index = None
    for attempt in range(max_retries):
        try:
            # Acquire shared lock on lock file (multiple readers OK, blocks writers)
            with open(LOCK_FILE, 'w') as lock_f:
                fcntl.flock(lock_f.fileno(), fcntl.LOCK_SH)
                try:
                    # Now safe to read - FAISS will do its own file operations
                    index = faiss.read_index(str(INDEX_FILE))
                    logger.debug(f"[FAISS] Successfully loaded index with {index.ntotal} vectors")
                    break
                finally:
                    fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
        except (OSError, RuntimeError) as e:
            if attempt < max_retries - 1:
                logger.warning(f"[FAISS] Read attempt {attempt+1}/{max_retries} failed: {e}, retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                retry_delay *= 1.5  # Exponential backoff
            else:
                logger.error(f"[FAISS] Failed to read index after {max_retries} attempts: {e}")
                raise RuntimeError(f"FAISS index load failed after {max_retries} attempts") from e
    
    if index is None:
        raise RuntimeError("FAISS index load failed - index is None after all attempts")
    
    # Set efSearch for query-time quality/speed tradeoff
    if hasattr(index, 'hnsw'):
        index.hnsw.efSearch = HNSW_EF_SEARCH
        logger.debug(f"[FAISS] Set efSearch={HNSW_EF_SEARCH} for queries")
    
    # Load mapping with corruption fallback (also protected by lock)
    try:
        with open(MAPPING_FILE) as f:
            mapping = json.load(f)
    except json.JSONDecodeError as e:
        # Mapping file appears corrupted (partial write or concurrent write)
        logger.warning(f"[FAISS] Mapping file {MAPPING_FILE} is corrupted ({e}); attempting automatic rebuild...")
        # Attempt a full rebuild from DB embeddings as a safe recovery
        if not _rebuild_index_from_db():
            logger.error("[FAISS] Failed to rebuild FAISS index after mapping corruption; cannot proceed")
            raise
        # Retry loading freshly rebuilt files
        with open(MAPPING_FILE) as f:
            mapping = json.load(f)
    
    mapping = {int(k): int(v) for k, v in mapping.items()}
    _INDEX_CACHE = index
    _MAP_CACHE = mapping
    return _INDEX_CACHE, _MAP_CACHE


def search_index(index: faiss.IndexHNSWFlat, query_vec: np.ndarray, top_k: int = 100, ef_search: Optional[int] = None) -> Tuple[List[int], List[float]]:
    """
    Search FAISS HNSW index and return top_k trakt_ids and scores.
    
    Args:
        index: FAISS HNSW index
        query_vec: Query embedding vector
        top_k: Number of results to return
        ef_search: Override efSearch for this query (None = use default HNSW_EF_SEARCH)
                   Higher = better quality but slower (200-350 recommended range)
    
    Returns:
        (trakt_ids, scores) tuples
    """
    # Set efSearch dynamically if provided
    if ef_search is not None and hasattr(index, 'hnsw'):
        index.hnsw.efSearch = ef_search
    elif hasattr(index, 'hnsw'):
        index.hnsw.efSearch = HNSW_EF_SEARCH
    
    # Normalize for cosine similarity via L2 distance
    if query_vec.dtype != np.float32:
        query_vec = query_vec.astype(np.float32)
    query_vec = query_vec / (np.linalg.norm(query_vec) + 1e-8)
    query_vec = np.expand_dims(query_vec, axis=0)
    
    # Search
    distances, ids = index.search(query_vec, top_k)
    ids = ids[0]
    
    # Convert L2 distances to similarity scores (smaller distance = higher similarity)
    # For normalized vectors: L2_dist = 2 * (1 - cosine_sim)
    # So: cosine_sim = 1 - (L2_dist / 2)
    similarities = 1.0 - (distances[0] / 2.0)
    
    return list(ids), list(similarities)


def add_to_index(embeddings: np.ndarray, trakt_ids: List[int], dim: int) -> bool:
    """
    Incrementally add new embeddings to existing FAISS HNSW index.
    
    Args:
        embeddings: New embeddings to add (N x dim) as float32
        trakt_ids: Trakt IDs for new embeddings
        dim: Embedding dimension (should match existing index)
    
    Returns:
        True if successful, False if index doesn't exist (need full rebuild)
    """
    try:
        if not INDEX_FILE.exists() or not MAPPING_FILE.exists():
            logger.warning("[FAISS] Index files not found, need full rebuild")
            return False
        
        # Load existing index and mapping
        index = faiss.read_index(str(INDEX_FILE))
        with open(MAPPING_FILE) as f:
            mapping = json.load(f)
        mapping = {int(k): int(v) for k, v in mapping.items()}
        
        # Normalize new embeddings
        embeddings_normalized = _l2_normalize(embeddings.astype(np.float32))
        
        # Get current index size (for new mapping keys)
        current_size = len(mapping)
        
        # Add to index (HNSW supports incremental adds)
        logger.info(f"[FAISS] Adding {len(embeddings)} new vectors to HNSW index (current size: {current_size})")
        index.add(embeddings_normalized)
        
        # Update mapping
        for i, trakt_id in enumerate(trakt_ids):
            mapping[current_size + i] = int(trakt_id)
        
        # Save updated index and mapping with atomic write pattern
        import fcntl
        import os
        LOCK_FILE = DATA_DIR / "faiss_index.lock"
        INDEX_TEMP = DATA_DIR / "faiss_index.bin.tmp"
        MAPPING_TEMP = DATA_DIR / "faiss_map.json.tmp"
        
        with open(LOCK_FILE, 'w') as lock_f:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)  # Exclusive lock - blocks all readers/writers
            try:
                # Write to temp files first
                faiss.write_index(index, str(INDEX_TEMP))
                with open(MAPPING_TEMP, "w") as f:
                    json.dump(mapping, f)
                    f.flush()
                    os.fsync(f.fileno())  # Force write to disk
                
                # Atomic rename
                INDEX_TEMP.rename(INDEX_FILE)
                MAPPING_TEMP.rename(MAPPING_FILE)
            except Exception as e:
                # Clean up temp files on error
                if INDEX_TEMP.exists():
                    INDEX_TEMP.unlink()
                if MAPPING_TEMP.exists():
                    MAPPING_TEMP.unlink()
                raise e
            finally:
                fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
        
        # Clear cache to force reload with new data
        global _INDEX_CACHE, _MAP_CACHE
        _INDEX_CACHE = None
        _MAP_CACHE = None
        
        logger.info(f"[FAISS] Successfully added {len(embeddings)} vectors (new total: {len(mapping)})")
        return True
        
    except Exception as e:
        logger.error(f"[FAISS] Failed to add embeddings incrementally: {e}")
        return False


def get_embedding_from_index(tmdb_id: int, media_type: str) -> Optional[np.ndarray]:
    """
    Retrieve embedding for a specific tmdb_id from persistent_candidates.
    
    Args:
        tmdb_id: TMDB ID of the item
        media_type: 'movie' or 'show'
        
    Returns:
        Numpy array embedding (float16) or None if not found
    """
    try:
        from app.core.database import SessionLocal
        from app.models import PersistentCandidate
        
        db = SessionLocal()
        try:
            candidate = db.query(PersistentCandidate).filter(
                PersistentCandidate.tmdb_id == tmdb_id,
                PersistentCandidate.media_type == media_type
            ).first()
            
            if candidate and candidate.embedding:
                return deserialize_embedding(candidate.embedding)
            
            return None
            
        finally:
            db.close()
            
    except Exception as e:
        logger.error(f"Failed to get embedding for tmdb_id={tmdb_id}: {e}")
        return None

