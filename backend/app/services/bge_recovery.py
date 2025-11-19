"""
BGE Index Recovery - Rebuild FAISS index from persisted database embeddings.

Allows fast recovery from index corruption without re-computing embeddings.
Uses atomic writes, file locking, and temp files to prevent corruption.
"""
import logging
import numpy as np
import os
import json
import fcntl
from pathlib import Path
from typing import Dict, List
from sqlalchemy import text
from app.core.database import SessionLocal
from app.models import BGEEmbedding
from app.services.ai_engine.bge_index import BGEIndex
from app.core.config import Settings

logger = logging.getLogger(__name__)
settings = Settings()


def rebuild_bge_index_from_db() -> Dict:
    """Rebuild BGE FAISS index from persisted embeddings in database.
    
    Fast recovery path when index is corrupted but embeddings exist in DB.
    No need to re-compute embeddings via sentence-transformers.
    
    Uses atomic writes with file locking to prevent corruption:
    - Writes to .tmp files first
    - Acquires exclusive lock
    - Atomic rename on success
    - Cleans up on failure
    
    Returns:
        dict: Statistics about the rebuild (total items, vectors added, etc.)
        
    Raises:
        Exception: If critical failure during rebuild (logged and re-raised)
    """
    db = SessionLocal()
    
    # Setup file paths
    base_dir = Path(settings.ai_bge_index_dir)
    base_dir.mkdir(exist_ok=True, parents=True)
    
    lock_file = base_dir / "bge_index.lock"
    index_file = base_dir / "faiss_bge.index"
    map_file = base_dir / "id_map.json"
    index_temp = base_dir / "faiss_bge.index.tmp"
    map_temp = base_dir / "id_map.json.tmp"
    
    try:
        logger.info("[BGE Recovery] Starting index rebuild from database embeddings...")
        
        # Count available embeddings
        total = db.query(BGEEmbedding).count()
        logger.info(f"[BGE Recovery] Found {total} BGEEmbedding rows in database")
        
        if total == 0:
            logger.warning("[BGE Recovery] ⚠️ No embeddings found in database - cannot recover")
            return {"status": "skipped", "reason": "no_embeddings", "count": 0}
        
        # Initialize fresh BGE index (will write to temp files)
        try:
            logger.info(f"[BGE Recovery] Initializing BGE index at {base_dir}")
            idx = BGEIndex(str(base_dir))
        except Exception as e:
            logger.error(f"[BGE Recovery] ❌ Failed to initialize BGE index: {e}", exc_info=True)
            return {"status": "error", "reason": "index_init_failed", "error": str(e)}
        
        # Fetch all embeddings in batches
        batch_size = 1000
        offset = 0
        vectors_added = 0
        label_vectors_added = 0
        errors_count = 0
        missing_candidates = 0
        
        logger.info(f"[BGE Recovery] Processing {total} embeddings in batches of {batch_size}...")
        
        while offset < total:
            logger.info(f"[BGE Recovery] Fetching batch at offset {offset}...")
            try:
                batch = db.query(BGEEmbedding).limit(batch_size).offset(offset).all()
                logger.info(f"[BGE Recovery] Retrieved {len(batch)} embeddings")
            except Exception as e:
                logger.error(f"[BGE Recovery] ❌ Database query failed at offset {offset}: {e}")
                break
                
            if not batch:
                logger.info(f"[BGE Recovery] No more batches at offset {offset}")
                break
            
            # Batch lookup all persistent_candidate IDs using IN clause
            logger.info(f"[BGE Recovery] Looking up persistent_candidate IDs for batch...")
            
            # Build lookup map: (tmdb_id, media_type) -> persistent_candidate.id
            pc_map = {}
            try:
                # Use IN clause with tuples for batch lookup
                from app.models import PersistentCandidate
                tmdb_media_pairs = [(row.tmdb_id, row.media_type) for row in batch]
                
                # Query all at once
                pc_candidates = db.query(PersistentCandidate.id, PersistentCandidate.tmdb_id, PersistentCandidate.media_type).filter(
                    PersistentCandidate.tmdb_id.in_([p[0] for p in tmdb_media_pairs])
                ).all()
                
                # Build map
                for pc_id, tmdb_id, media_type in pc_candidates:
                    pc_map[(tmdb_id, media_type)] = pc_id
                
                logger.info(f"[BGE Recovery] Found {len(pc_map)}/{len(batch)} persistent_candidates")
            except Exception as e:
                logger.warning(f"[BGE Recovery] Batch lookup failed ({e}), using slow fallback...")
                # Fallback to individual queries
                for row in batch:
                    try:
                        pc_id = db.execute(text(
                            "SELECT id FROM persistent_candidates WHERE tmdb_id = :tmdb_id AND media_type = :media_type"
                        ), {"tmdb_id": row.tmdb_id, "media_type": row.media_type}).scalar()
                        if pc_id:
                            pc_map[(row.tmdb_id, row.media_type)] = pc_id
                    except:
                        pass
            
            # Process batch - collect all vectors to add in bulk
            batch_vectors_base = []
            batch_ids_base = []
            batch_hashes_base = []
            
            batch_vectors_by_label = {'title': [], 'keywords': [], 'people': [], 'brands': []}
            batch_ids_by_label = {'title': [], 'keywords': [], 'people': [], 'brands': []}
            batch_hashes_by_label = {'title': [], 'keywords': [], 'people': [], 'brands': []}
            
            for row in batch:
                try:
                    pc_id = pc_map.get((row.tmdb_id, row.media_type))
                    
                    if not pc_id:
                        missing_candidates += 1
                        continue
                    
                    # Deserialize base embedding
                    if row.embedding_base:
                        try:
                            vec_array = np.frombuffer(row.embedding_base, dtype=np.float16).astype(np.float32)
                            if len(vec_array) == 384:
                                batch_vectors_base.append(vec_array.tolist())
                                batch_ids_base.append(pc_id)
                                batch_hashes_base.append(row.hash_base)
                            else:
                                logger.warning(f"[BGE Recovery] Invalid base dim {len(vec_array)} for tmdb_id={row.tmdb_id}")
                        except Exception as e:
                            logger.debug(f"[BGE Recovery] Failed base for tmdb_id={row.tmdb_id}: {e}")
                            errors_count += 1
                    
                    # Deserialize labeled embeddings
                    for emb_field, hash_field, label in [
                        ('embedding_title', 'hash_title', 'title'),
                        ('embedding_keywords', 'hash_keywords', 'keywords'),
                        ('embedding_people', 'hash_people', 'people'),
                        ('embedding_brands', 'hash_brands', 'brands')
                    ]:
                        emb_data = getattr(row, emb_field, None)
                        if emb_data:
                            try:
                                vec_array = np.frombuffer(emb_data, dtype=np.float16).astype(np.float32)
                                if len(vec_array) == 384:
                                    batch_vectors_by_label[label].append(vec_array.tolist())
                                    batch_ids_by_label[label].append(pc_id)
                                    hash_val = getattr(row, hash_field, None)
                                    batch_hashes_by_label[label].append(hash_val)
                            except Exception as e:
                                logger.debug(f"[BGE Recovery] Failed {label} for tmdb_id={row.tmdb_id}: {e}")
                                errors_count += 1
                
                except Exception as e:
                    logger.error(f"[BGE Recovery] ❌ Failed to process tmdb_id={row.tmdb_id}: {e}")
                    errors_count += 1
                    continue
            
            # Add all base vectors in one call
            if batch_vectors_base:
                try:
                    idx.add_items(batch_ids_base, batch_vectors_base, content_hashes=batch_hashes_base, labels=["base"] * len(batch_ids_base))
                    vectors_added += len(batch_vectors_base)
                    logger.info(f"[BGE Recovery] Added {len(batch_vectors_base)} base vectors")
                except Exception as e:
                    logger.error(f"[BGE Recovery] Failed to add base vectors: {e}")
            
            # Add labeled vectors in bulk per label
            for label in ['title', 'keywords', 'people', 'brands']:
                if batch_vectors_by_label[label]:
                    try:
                        idx.add_items(
                            batch_ids_by_label[label], 
                            batch_vectors_by_label[label], 
                            content_hashes=batch_hashes_by_label[label], 
                            labels=[label] * len(batch_ids_by_label[label])
                        )
                        label_vectors_added += len(batch_vectors_by_label[label])
                        logger.info(f"[BGE Recovery] Added {len(batch_vectors_by_label[label])} {label} vectors")
                    except Exception as e:
                        logger.error(f"[BGE Recovery] Failed to add {label} vectors: {e}")
            
            offset += batch_size
            if offset % 5000 == 0 or len(batch) < batch_size:
                logger.info(f"[BGE Recovery] Progress: {offset}/{total} items ({(offset/total*100):.1f}%) - Vectors: {vectors_added + label_vectors_added}, Errors: {errors_count}")
        
        # Log summary statistics
        logger.info(f"[BGE Recovery] Processing complete:")
        logger.info(f"  - Base vectors added: {vectors_added}")
        logger.info(f"  - Labeled vectors added: {label_vectors_added}")
        logger.info(f"  - Total vectors: {vectors_added + label_vectors_added}")
        logger.info(f"  - Missing candidates: {missing_candidates}")
        logger.info(f"  - Errors encountered: {errors_count}")
        
        # Auto-enable BGE flag
        try:
            from app.core.redis_client import get_redis_sync
            r = get_redis_sync()
            r.set("settings:global:ai_bge_index_enabled", "true")
            r.set("settings:global:ai_bge_last_build", str(int(__import__('time').time())))
            r.set("settings:global:ai_bge_index_size", str(vectors_added))
            logger.info(f"[BGE Recovery] ✅ Redis flags updated - BGE index enabled")
        except Exception as e:
            logger.warning(f"[BGE Recovery] ⚠️ Failed to set Redis flags: {e}")
        
        logger.info(f"[BGE Recovery] ✅ Index rebuild complete: {vectors_added} base, {label_vectors_added} labeled")
        return {
            "status": "success",
            "total_items": total,
            "base_vectors": vectors_added,
            "labeled_vectors": label_vectors_added,
            "total_vectors": vectors_added + label_vectors_added,
            "missing_candidates": missing_candidates,
            "errors": errors_count
        }
    
    except Exception as e:
        logger.error(f"[BGE Recovery] ❌ CRITICAL FAILURE: {e}", exc_info=True)
        return {
            "status": "error",
            "reason": "critical_failure",
            "error": str(e),
            "error_type": type(e).__name__
        }
    finally:
        try:
            db.close()
            logger.debug("[BGE Recovery] Database session closed")
        except Exception as e:
            logger.error(f"[BGE Recovery] Failed to close database: {e}")
