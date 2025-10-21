#!/usr/bin/env python
"""
Build FAISS index from all existing embeddings in persistent_candidates.
This should be run once to initialize the index from pre-existing embeddings.
"""
import gc
import logging
import numpy as np
from sqlalchemy import text

from app.core.database import SessionLocal
from app.services.ai_engine.faiss_index import deserialize_embedding, train_build_ivfpq

logger = logging.getLogger("build_faiss_from_existing")
BATCH_SIZE = 10000

def main():
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    db = SessionLocal()
    try:
        # Count total items with embeddings and trakt_id
        total = db.execute(text(
            "SELECT COUNT(*) FROM persistent_candidates WHERE embedding IS NOT NULL AND trakt_id IS NOT NULL"
        )).scalar()
        logger.info(f"Found {total} candidates with both embeddings and trakt_id")
        
        if total == 0:
            logger.warning("No candidates with embeddings and trakt_id found. Nothing to build.")
            return
        
        all_embeddings = []
        all_trakt_ids = []
        offset = 0
        
        while offset < total:
            logger.info(f"Fetching batch at offset {offset}...")
            rows = db.execute(text(
                """
                SELECT trakt_id, embedding
                FROM persistent_candidates
                WHERE embedding IS NOT NULL AND trakt_id IS NOT NULL
                ORDER BY id
                OFFSET :off LIMIT :lim
                """
            ), {"off": offset, "lim": BATCH_SIZE}).fetchall()
            
            if not rows:
                break
            
            for trakt_id, embedding_blob in rows:
                try:
                    emb = deserialize_embedding(embedding_blob)
                    all_embeddings.append(emb)
                    all_trakt_ids.append(int(trakt_id))
                except Exception as e:
                    logger.warning(f"Failed to deserialize embedding for trakt_id={trakt_id}: {e}")
                    continue
            
            offset += len(rows)
            logger.info(f"Loaded {len(all_embeddings)} embeddings so far...")
            
            # Periodically collect garbage to manage memory
            if offset % 50000 == 0:
                gc.collect()
        
        if not all_embeddings:
            logger.error("No valid embeddings loaded!")
            return
        
        logger.info(f"Converting {len(all_embeddings)} embeddings to numpy array...")
        embeddings_array = np.array(all_embeddings, dtype=np.float32)
        logger.info(f"Embeddings shape: {embeddings_array.shape}")
        
        # Build FAISS index
        dim = embeddings_array.shape[1]
        logger.info(f"Building FAISS index with {len(all_trakt_ids)} vectors (dimension={dim})...")
        train_build_ivfpq(embeddings_array, all_trakt_ids, dim)
        logger.info("✅ FAISS index built successfully!")
        
    finally:
        db.close()
        gc.collect()

if __name__ == "__main__":
    main()
