"""
regenerate_embeddings_and_rebuild_faiss.py

One-off maintenance script to:
  1) Regenerate embeddings for ALL active persistent_candidates (overwrite existing)
  2) Rebuild FAISS HNSW index from the database using COALESCE(trakt_id, tmdb_id)

Run inside the backend container:
  docker exec -i watchbuddy-backend-1 sh -c "cd /app && PYTHONPATH=/app python app/scripts/regenerate_embeddings_and_rebuild_faiss.py"

Notes:
- Processes candidates in batches to limit memory usage
- Uses the same compose_text_for_embedding as AI engine
- Overwrites the embedding column
- Rebuilds FAISS mapping with trakt_id if present, otherwise tmdb_id
"""
import json
import logging
from sqlalchemy import text
import numpy as np

from app.core.database import SessionLocal
from app.services.ai_engine.metadata_processing import compose_text_for_embedding
from app.services.ai_engine.embeddings import EmbeddingService
from app.services.ai_engine.faiss_index import (
    serialize_embedding,
    deserialize_embedding,
    DATA_DIR,
    INDEX_FILE,
    MAPPING_FILE,
    HNSW_M,
    HNSW_EF_CONSTRUCTION,
    _l2_normalize,
)
import faiss

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

BATCH_SIZE = 64


def regenerate_all_embeddings():
    db = SessionLocal()
    try:
        # Count total active candidates
        total = db.execute(text(
            "SELECT COUNT(*) FROM persistent_candidates WHERE active=true"
        )).scalar() or 0
        logger.info(f"[EMBED] Regenerating embeddings for {total} candidates (batch={BATCH_SIZE})")

        embedder = EmbeddingService()
        offset = 0
        processed = 0

        while offset < total:
            rows = db.execute(text(
                 
                """
                SELECT id, trakt_id, tmdb_id, media_type, title, original_title, overview, genres, keywords, "cast", 
                       production_companies, vote_average, vote_count, popularity, year, language, runtime, tagline, 
                       homepage, budget, revenue, production_countries, spoken_languages, networks, created_by, 
                       number_of_seasons, number_of_episodes, episode_run_time, first_air_date, last_air_date, 
                       in_production, status
                FROM persistent_candidates
                WHERE active=true
                ORDER BY popularity DESC
                OFFSET :off LIMIT :lim
                """
            ), {"off": int(offset), "lim": int(BATCH_SIZE)}).fetchall()

            if not rows:
                break

            # Compose candidate dicts
            cands = []
            ids = []
            for r in rows:
                (
                    rid, trakt_id, tmdb_id, media_type, title, original_title, overview, genres, keywords, cast,
                    prod_comp, vote_average, vote_count, popularity, year, language, runtime, tagline, homepage,
                    budget, revenue, prod_countries, spoken_languages, networks, created_by, number_of_seasons,
                    number_of_episodes, episode_run_time, first_air_date, last_air_date, in_production, status
                ) = r
                c = {
                    'id': rid,
                    'trakt_id': trakt_id,
                    'tmdb_id': tmdb_id,
                    'media_type': media_type,
                    'title': title or '',
                    'original_title': original_title or '',
                    'overview': overview or '',
                    'genres': genres or '[]',
                    'keywords': keywords or '[]',
                    'cast': cast or '[]',
                    'production_companies': prod_comp or '[]',
                    'vote_average': vote_average or 0,
                    'vote_count': vote_count or 0,
                    'popularity': popularity or 0,
                    'year': year,
                    'language': language or '',
                    'runtime': runtime or 0,
                    'tagline': tagline or '',
                    'homepage': homepage or '',
                    'budget': budget or 0,
                    'revenue': revenue or 0,
                    'production_countries': prod_countries or '[]',
                    'spoken_languages': spoken_languages or '[]',
                    'networks': networks or '[]',
                    'created_by': created_by or '[]',
                    'number_of_seasons': number_of_seasons,
                    'number_of_episodes': number_of_episodes,
                    'episode_run_time': episode_run_time or '[]',
                    'first_air_date': first_air_date or '',
                    'last_air_date': last_air_date or '',
                    'in_production': in_production,
                    'status': status or '',
                }
                ids.append(rid)
                cands.append(c)

            # Generate embeddings for batch
            texts = [compose_text_for_embedding(c) for c in cands]
            embs = embedder.encode_texts(texts, batch_size=BATCH_SIZE).astype(np.float32)

            # Persist embeddings
            for i, rid in enumerate(ids):
                try:
                    blob = serialize_embedding(embs[i])
                    db.execute(text("UPDATE persistent_candidates SET embedding=:e WHERE id=:id"), {"e": blob, "id": rid})
                except Exception as e:
                    logger.warning(f"Failed to persist embedding for id={rid}: {e}")
                    db.rollback()
                    continue

            db.commit()
            processed += len(ids)
            offset += len(ids)
            logger.info(f"[EMBED] {processed}/{total} regenerated")

        logger.info(f"[EMBED] Completed regeneration for {processed} candidates")

    finally:
        db.close()


def rebuild_faiss_from_db_incremental():
    """Rebuild FAISS HNSW index incrementally to avoid memory issues.
    
    Builds index in chunks and saves incrementally instead of loading all vectors at once.
    """
    db = SessionLocal()
    try:
        total = db.execute(text(
            "SELECT COUNT(*) FROM persistent_candidates WHERE active=true AND embedding IS NOT NULL"
        )).scalar() or 0
        if total == 0:
            logger.error("[FAISS] No embeddings found after regeneration; aborting rebuild")
            return

        logger.info(f"[FAISS] Rebuilding HNSW index incrementally from {total} embeddings")
        
        # Initialize index with first batch to get dimension
        first_batch_size = 10000
        rows = db.execute(text(
            """
            SELECT COALESCE(trakt_id, tmdb_id) AS any_id, embedding
            FROM persistent_candidates
            WHERE active=true AND embedding IS NOT NULL
            ORDER BY id
            LIMIT :lim
            """
        ), {"lim": first_batch_size}).fetchall()
        
        if not rows:
            logger.error("[FAISS] No embeddings found")
            return
        
        # Build initial index with first batch
        vecs = []
        ids = []
        for any_id, blob in rows:
            try:
                vecs.append(deserialize_embedding(bytes(blob)))
                ids.append(int(any_id))
            except Exception:
                continue
        
        if not vecs:
            logger.error("[FAISS] No valid embeddings in first batch")
            return
        
        embs = np.array(vecs, dtype=np.float32)
        dim = embs.shape[1]
        logger.info(f"[FAISS] Creating HNSW index (dim={dim}, M={HNSW_M}, efConstruction={HNSW_EF_CONSTRUCTION})")
        
        # Create HNSW index
        index = faiss.IndexHNSWFlat(dim, HNSW_M)
        index.hnsw.efConstruction = HNSW_EF_CONSTRUCTION
        
        # Add first batch
        embs_normalized = _l2_normalize(embs)
        index.add(embs_normalized)
        mapping = {i: ids[i] for i in range(len(ids))}
        logger.info(f"[FAISS] Added first {len(ids)} vectors")
        
        # Process remaining batches incrementally
        offset = first_batch_size
        batch_size = 50000
        current_idx = len(ids)
        
        while offset < total:
            rows = db.execute(text(
                """
                SELECT COALESCE(trakt_id, tmdb_id) AS any_id, embedding
                FROM persistent_candidates
                WHERE active=true AND embedding IS NOT NULL
                ORDER BY id
                OFFSET :off LIMIT :lim
                """
            ), {"off": int(offset), "lim": int(batch_size)}).fetchall()
            
            if not rows:
                break
            
            vecs = []
            ids = []
            for any_id, blob in rows:
                try:
                    vecs.append(deserialize_embedding(bytes(blob)))
                    ids.append(int(any_id))
                except Exception:
                    continue
            
            if vecs:
                embs = np.array(vecs, dtype=np.float32)
                embs_normalized = _l2_normalize(embs)
                index.add(embs_normalized)
                
                # Update mapping
                for i, vid in enumerate(ids):
                    mapping[current_idx + i] = vid
                
                current_idx += len(ids)
                logger.info(f"[FAISS] Progress: {current_idx}/{total} vectors added")
            
            offset += len(rows)
            
            # Free memory
            del vecs, ids, embs, embs_normalized
        
        # Save index and mapping
        logger.info(f"[FAISS] Saving index with {current_idx} vectors...")
        faiss.write_index(index, str(INDEX_FILE))
        
        with open(MAPPING_FILE, "w") as f:
            json.dump(mapping, f)
        
        logger.info(f"[FAISS] ✅ HNSW index rebuilt successfully with {current_idx} vectors")
        logger.info(f"[FAISS] Saved to {INDEX_FILE} and {MAPPING_FILE}")

    finally:
        db.close()


if __name__ == "__main__":
    logger.info("[MAIN] Starting FAISS index rebuild (skipping embedding regeneration)...")
    logger.info("[MAIN] Embeddings already regenerated - building FAISS index only")
    rebuild_faiss_from_db_incremental()
    logger.info("[MAIN] ✓ FAISS rebuild complete!")
