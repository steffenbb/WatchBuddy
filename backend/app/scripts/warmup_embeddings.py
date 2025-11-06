"""
Warm-up embeddings and FAISS index.

Generates embeddings for a small batch of top candidates missing embeddings,
persists them in DB, and initializes or updates the FAISS index.

Usage (inside backend container, always set PYTHONPATH=/app):
  python -m app.scripts.warmup_embeddings --count 5000
"""
from __future__ import annotations

import argparse
import gc
import logging
import numpy as np
from sqlalchemy import text

from app.core.database import SessionLocal
from app.services.ai_engine.metadata_processing import compose_text_for_embedding
from app.services.ai_engine.embeddings import EmbeddingService
from app.services.ai_engine.faiss_index import serialize_embedding, add_to_index, train_build_hnsw


logger = logging.getLogger(__name__)


def warmup(count: int = 5000, batch_size: int = 64) -> int:
    db = SessionLocal()
    try:
        logger.warning("Querying top %d candidates missing embeddings...", count)
        rows = db.execute(text(
            """
            SELECT id, tmdb_id, trakt_id, media_type, title, original_title, overview, genres, keywords, "cast",
                   production_companies, vote_average, vote_count, popularity, year, language, runtime,
                   tagline, homepage, budget, revenue, production_countries, spoken_languages, networks,
                   created_by, number_of_seasons, number_of_episodes, episode_run_time, first_air_date,
                   last_air_date, in_production, status
            FROM persistent_candidates
            WHERE active=true AND embedding IS NULL AND trakt_id IS NOT NULL
            ORDER BY popularity DESC
            LIMIT :lim
            """
        ), {"lim": int(count)}).fetchall()
        logger.warning("Found %d candidates to embed.", len(rows))
        if not rows:
            logger.warning("No rows to warm up; exiting")
            return 0

        # Build candidate dicts
        cands = []
        for r in rows:
            try:
                (rid, tmdb_id, trakt_id, media_type, title, original_title, overview, genres, keywords, cast, prod_comp,
                 vote_average, vote_count, popularity, year, language, runtime, tagline, homepage, budget, revenue,
                 prod_countries, spoken_languages, networks, created_by, number_of_seasons, number_of_episodes,
                 episode_run_time, first_air_date, last_air_date, in_production, status) = r
                c = {
                    'id': rid,
                    'tmdb_id': tmdb_id,
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

        logger.warning("Composing texts for embedding...")
        texts = [compose_text_for_embedding(c) for c in cands]
        logger.warning("Encoding %d texts into embeddings...", len(texts))
        embedder = EmbeddingService()
        try:
            embs = embedder.encode_texts(texts, batch_size=batch_size).astype(np.float32)
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            return 0

        logger.warning("Persisting embeddings to DB...")
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
        logger.warning("Persisted %d embeddings to DB.", embedded)

        # Update or create FAISS
        trakt_ids = [int(c['trakt_id']) for c in cands]  # Use trakt_id for better coverage
        logger.warning("Updating FAISS HNSW index with %d vectors...", len(trakt_ids))
        try:
            ok = add_to_index(embs, trakt_ids, embs.shape[1])
            if ok:
                logger.warning("Successfully added vectors to FAISS HNSW index.")
            else:
                logger.warning("FAISS index not found, building new HNSW index from batch.")
                train_build_hnsw(embs, trakt_ids, embs.shape[1])
                logger.info("Built new FAISS HNSW index from batch.")
        except Exception as e:
            logger.error(f"FAISS index update/build failed: {e}")

        logger.info("Warm-up done: embedded=%d", len(cands))
        return len(cands)
    finally:
        db.close()
        gc.collect()


def main():
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=5000)
    args = ap.parse_args()
    warmup(count=args.count)


if __name__ == "__main__":
    main()
