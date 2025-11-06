
"""
Warm up embeddings and FAISS index for all persistent_candidates with a non-null trakt_id.
Mirrors warmup_embeddings.py but uses trakt_id as the mapping key for the index.
Run inside backend container with: PYTHONPATH=/app python app/scripts/warmup_embeddings_trakt.py
"""

import gc
import logging
import numpy as np
from sqlalchemy import text

from app.core.database import SessionLocal
from app.services.ai_engine.metadata_processing import compose_text_for_embedding
from app.services.ai_engine.embeddings import EmbeddingService
from app.services.ai_engine.faiss_index import serialize_embedding, add_to_index, train_build_ivfpq

logger = logging.getLogger("warmup_embeddings_trakt")
BATCH_SIZE = 64

def main():
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    db = SessionLocal()
    try:
        # Use a direct SQL count to avoid ORM model imports
        total = db.execute(text(
            """
            SELECT COUNT(*)
            FROM persistent_candidates
            WHERE active=true AND trakt_id IS NOT NULL AND embedding IS NULL
            """
        )).scalar() or 0
        logger.info(f"Total persistent_candidates with trakt_id: {total}")
        offset = 0
        while offset < total:
            rows = db.execute(text(
                '''
                SELECT id, trakt_id, media_type, title, original_title, overview, genres, keywords, "cast",
                       production_companies, vote_average, vote_count, popularity, year, language, runtime,
                       tagline, homepage, budget, revenue, production_countries, spoken_languages, networks,
                       created_by, number_of_seasons, number_of_episodes, episode_run_time, first_air_date,
                       last_air_date, in_production, status
                FROM persistent_candidates
                WHERE active=true AND embedding IS NULL AND trakt_id IS NOT NULL
                ORDER BY popularity DESC
                OFFSET :off LIMIT :lim
                '''
            ), {"off": int(offset), "lim": BATCH_SIZE}).fetchall()
            if not rows:
                break

            cands = []
            for r in rows:
                try:
                    (rid, trakt_id, media_type, title, original_title, overview, genres, keywords, cast, prod_comp,
                     vote_average, vote_count, popularity, year, language, runtime, tagline, homepage, budget, revenue,
                     prod_countries, spoken_languages, networks, created_by, number_of_seasons, number_of_episodes,
                     episode_run_time, first_air_date, last_air_date, in_production, status) = r
                    c = {
                        'id': rid,
                        'trakt_id': trakt_id,
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
                    cands.append(c)
                except Exception as e:
                    logger.warning(f"Failed to unpack candidate row: {e}")
                    continue

            logger.info(f"Composing texts for {len(cands)} candidates...")
            texts = [compose_text_for_embedding(c) for c in cands]
            logger.info(f"Encoding {len(texts)} texts into embeddings...")
            embedder = EmbeddingService()
            try:
                embs = embedder.encode_texts(texts, batch_size=BATCH_SIZE).astype(np.float32)
            except Exception as e:
                logger.error(f"Embedding generation failed: {e}")
                break

            logger.info("Persisting embeddings to DB...")
            embedded = 0
            for i, c in enumerate(cands):
                try:
                    blob = serialize_embedding(embs[i])
                    db.execute(text("UPDATE persistent_candidates SET embedding=:e WHERE id=:id"), {"e": blob, "id": c['id']})
                    embedded += 1
                except Exception as e:
                    logger.warning(f"Failed to persist embedding for id={c['id']}: {e}")
                    db.rollback()
                    continue
            db.commit()
            logger.info(f"Persisted {embedded} embeddings to DB.")

            # Update or create FAISS index using trakt_id as mapping
            trakt_ids = [int(c['trakt_id']) for c in cands]
            logger.info(f"Updating FAISS index with {len(trakt_ids)} vectors (trakt_id mapping)...")
            try:
                ok = add_to_index(embs, trakt_ids, embs.shape[1])
                if ok:
                    logger.info("Successfully added vectors to FAISS index.")
                else:
                    logger.warning("FAISS index not found, building new index from batch.")
                    train_build_ivfpq(embs, trakt_ids, embs.shape[1])
                    logger.info("Built new FAISS index from batch.")
            except Exception as e:
                logger.error(f"FAISS index update/build failed: {e}")

            offset += len(cands)
            logger.info(f"Processed {min(offset, total)}/{total}")
            gc.collect()
    finally:
        db.close()
        gc.collect()

if __name__ == "__main__":
    main()
