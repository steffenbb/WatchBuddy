"""
FAISS index helpers for IVF+PQ (float16).
- train_build_ivfpq: trains and builds index from embeddings and mapping
- load_index: loads index from disk
- search_index: queries index and returns mapped tmdb_ids
- add_to_index: incrementally add new embeddings to existing index
- serialize_embedding/deserialize_embedding: convert embeddings to/from bytes for DB storage
"""
import numpy as np
import faiss
import json
import logging
from pathlib import Path
from typing import List, Tuple, Dict, Optional

logger = logging.getLogger(__name__)

# Spec paths
DATA_DIR = Path("/data/ai")
DATA_DIR.mkdir(exist_ok=True, parents=True)
INDEX_FILE = DATA_DIR / "faiss_index.bin"
MAPPING_FILE = DATA_DIR / "faiss_map.json"


def _l2_normalize(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True) + 1e-8
    return (mat / norms).astype(np.float16)


def serialize_embedding(embedding: np.ndarray) -> bytes:
    """Convert numpy embedding array to bytes for database storage."""
    # Store as float16 to save space
    return embedding.astype(np.float16).tobytes()


def deserialize_embedding(blob: bytes) -> np.ndarray:
    """Convert bytes from database back to numpy float16 array."""
    return np.frombuffer(blob, dtype=np.float16)


def train_build_ivfpq(embeddings: np.ndarray, tmdb_ids: List[int], dim: int, nlist: int = 4096, m: int = 64, nbits: int = 8):
    """
    Train and build IVF+PQ index, save to disk, and export mapping.
    """
    quantizer = faiss.IndexFlatIP(dim)
    index = faiss.IndexIVFPQ(quantizer, dim, nlist, m, nbits)
    print("Training FAISS index...")
    # Cosine sim via inner-product requires normalized vectors
    embeddings = _l2_normalize(embeddings.astype(np.float32))
    index.train(embeddings)
    print("Adding embeddings to index...")
    index.add(embeddings)
    faiss.write_index(index, str(INDEX_FILE))
    # Save mapping
    mapping = {int(i): int(tmdb_ids[i]) for i in range(len(tmdb_ids))}
    with open(MAPPING_FILE, "w") as f:
        json.dump(mapping, f)
    print(f"✅ FAISS index and mapping saved to {INDEX_FILE} and {MAPPING_FILE}")


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
            # Count candidates with embeddings and trakt_id
            total = db.execute(text(
                "SELECT COUNT(*) FROM persistent_candidates WHERE embedding IS NOT NULL AND trakt_id IS NOT NULL"
            )).scalar()
            
            if total == 0:
                logger.error("[FAISS] No candidates with embeddings and trakt_id found for rebuild")
                return False
            
            logger.info(f"[FAISS] Found {total} candidates for index rebuild")
            
            all_embeddings = []
            all_trakt_ids = []
            batch_size = 10000
            offset = 0
            
            while offset < total:
                rows = db.execute(text(
                    """
                    SELECT trakt_id, embedding
                    FROM persistent_candidates
                    WHERE embedding IS NOT NULL AND trakt_id IS NOT NULL
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
            logger.info(f"[FAISS] Building index with {len(all_trakt_ids)} vectors (dim={dim})...")
            train_build_ivfpq(embeddings_array, all_trakt_ids, dim)
            logger.info("[FAISS] ✅ Index rebuild successful!")
            return True
            
        finally:
            db.close()
            
    except Exception as e:
        logger.error(f"[FAISS] Index rebuild failed: {e}", exc_info=True)
        return False


def load_index() -> Tuple[faiss.IndexIVFPQ, Dict[int, int]]:
    """
    Load FAISS index and mapping from disk.
    If index files are missing, attempts to rebuild from database embeddings.
    """
    # Check if index exists, rebuild if missing
    if not INDEX_FILE.exists() or not MAPPING_FILE.exists():
        logger.warning("[FAISS] Index files not found, attempting rebuild...")
        if not _rebuild_index_from_db():
            raise FileNotFoundError(f"FAISS index not found at {INDEX_FILE} and rebuild failed")
    
    index = faiss.read_index(str(INDEX_FILE))
    with open(MAPPING_FILE) as f:
        mapping = json.load(f)
    mapping = {int(k): int(v) for k, v in mapping.items()}
    return index, mapping


def search_index(index: faiss.IndexIVFPQ, query_vec: np.ndarray, top_k: int = 100) -> Tuple[List[int], List[float]]:
    """
    Search FAISS index and return top_k tmdb_ids and scores.
    """
    # Set nprobe to search more clusters for better recall
    # Default nprobe=1 is too low; use ~10% of nlist (nlist=4096 default, so nprobe=400)
    if hasattr(index, 'nprobe'):
        index.nprobe = min(400, int(index.nlist * 0.1)) if hasattr(index, 'nlist') else 400
    
    # normalize for cosine/inner-product
    if query_vec.dtype != np.float32:
        query_vec = query_vec.astype(np.float32)
    query_vec = query_vec / (np.linalg.norm(query_vec) + 1e-8)
    query_vec = query_vec.astype(np.float16)
    query_vec = np.expand_dims(query_vec, axis=0)
    scores, ids = index.search(query_vec, top_k)
    ids = ids[0]
    scores = scores[0]
    return list(ids), list(scores)


def add_to_index(embeddings: np.ndarray, tmdb_ids: List[int], dim: int) -> bool:
    """
    Incrementally add new embeddings to existing FAISS index.
    
    Args:
        embeddings: New embeddings to add (N x dim)
        tmdb_ids: TMDB IDs for new embeddings
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
        
        # Add to index
        logger.info(f"[FAISS] Adding {len(embeddings)} new vectors to index (current size: {current_size})")
        index.add(embeddings_normalized)
        
        # Update mapping
        for i, tmdb_id in enumerate(tmdb_ids):
            mapping[current_size + i] = int(tmdb_id)
        
        # Save updated index and mapping
        faiss.write_index(index, str(INDEX_FILE))
        with open(MAPPING_FILE, "w") as f:
            json.dump(mapping, f)
        
        logger.info(f"[FAISS] Successfully added {len(embeddings)} vectors (new total: {len(mapping)})")
        return True
        
    except Exception as e:
        logger.error(f"[FAISS] Failed to add embeddings incrementally: {e}")
        return False
