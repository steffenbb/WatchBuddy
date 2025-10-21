"""
tasks_ai.py
- Celery tasks and orchestration for AI-powered lists: chat list generation, dynamic list refresh, FAISS index management.
- Uses ai_engine modules, Redis locks, prompt cache, and notification publishing.
"""
import logging
import json
import gc
import numpy as np
from app.core.celery_app import celery_app
from app.core.database import SessionLocal
from app.core.redis_client import get_redis_sync
from app.models_ai import AiList, AiListItem
from app.services.ai_engine.embeddings import EmbeddingService
from app.services.ai_engine.faiss_index import load_index, search_index
from app.services.ai_engine.parser import parse_prompt
from app.services.ai_engine.metadata_processing import compose_text_for_embedding
from app.services.ai_engine.scorer import score_candidates
from app.services.ai_engine.diversifier import maximal_marginal_relevance
from app.services.ai_engine.explainability import build_explanation_meta, generate_explanation
from app.services.trakt_client import TraktClient, TraktAuthError
from sqlalchemy import text

logger = logging.getLogger(__name__)

async def _get_user_watch_history(user_id: int, limit: int = 500) -> dict:
    """Fetch user's Trakt watch history and return as dict of {trakt_id: metadata}."""
    try:
        trakt = TraktClient(user_id=user_id)
        
        # Fetch recent watch history for both movies and shows
        movie_history = await trakt.get_my_history(media_type="movies", limit=limit // 2)
        show_history = await trakt.get_my_history(media_type="shows", limit=limit // 2)
        
        watched_items = {}
        
        # Process movies
        for entry in movie_history:
            movie = entry.get("movie", {})
            trakt_id = movie.get("ids", {}).get("trakt")
            if trakt_id:
                watched_items[trakt_id] = {
                    "type": "movie",
                    "title": movie.get("title"),
                    "year": movie.get("year"),
                    "watched_at": entry.get("watched_at")
                }
        
        # Process shows
        for entry in show_history:
            show = entry.get("show", {})
            trakt_id = show.get("ids", {}).get("trakt")
            if trakt_id:
                watched_items[trakt_id] = {
                    "type": "show",
                    "title": show.get("title"),
                    "year": show.get("year"),
                    "watched_at": entry.get("watched_at")
                }
        
        logger.info(f"Fetched {len(watched_items)} watch history items for user {user_id}")
        return watched_items
    except TraktAuthError:
        logger.warning(f"User {user_id} has not authorized Trakt, skipping watch history")
        return {}
    except Exception as e:
        logger.warning(f"Failed to fetch Trakt watch history: {e}")
        return {}

@celery_app.task(name="generate_chat_list", bind=True, max_retries=3)
def generate_chat_list(self, ai_list_id: str, user_id: int = 1):
    """Generate a chat-based AI list for a user with FAISS prefilter, scoring, MMR, and explanations."""
    # Wrap async logic
    import asyncio
    return asyncio.run(_generate_chat_list_async(ai_list_id, user_id))

async def _generate_chat_list_async(ai_list_id: str, user_id: int = 1):
    """Async implementation of chat list generation with Trakt history integration."""
    r = get_redis_sync()
    lock_key = f"lock:ai_list:{ai_list_id}"
    import time
    lock_value = json.dumps({"started_at": time.time(), "user_id": user_id})
    if not r.set(lock_key, lock_value, nx=True, ex=3600):
        logger.info(f"AI list {ai_list_id} is already being processed")
        return
    db = SessionLocal()
    try:
        ai_list = db.query(AiList).filter_by(id=ai_list_id, user_id=user_id).first()
        if not ai_list:
            logger.error(f"AiList {ai_list_id} not found for user {user_id}")
            return

        # Fetch user's Trakt watch history for personalization
        watch_history = await _get_user_watch_history(user_id, limit=500)

        parsed = parse_prompt(ai_list.prompt_text or ai_list.normalized_prompt or "")
        normalized = parsed["normalized_prompt"]
        # Persist parsed context on the list
        ai_list.normalized_prompt = normalized
        ai_list.filters = parsed.get("filters")
        ai_list.tone_vector = parsed.get("tone_vector")
        db.commit()
        # Prompt cache (include seeds/tone/negatives to reflect blended query)
        seeds = (parsed.get("seed_titles") or [])
        tone_words = (parsed.get("filters", {}).get("tone") or [])
        negative_cues = (parsed.get("filters", {}).get("negative_cues") or [])
        cache_key = (
            f"ai:prompt_cache:{normalized}"
            f"|seeds:{','.join(seeds[:5])}"
            f"|tone:{','.join(tone_words[:6])}"
            f"|neg:{','.join(negative_cues[:6])}"
        )
        cached = r.get(cache_key)
        embedder = EmbeddingService()
        index, mapping = load_index()
        # Dynamic multi-vector blending: average embeddings of [base prompt, each seed, explicit mood phrase]
        base = normalized
        mood_phrase = None
        if tone_words:
            # Compact, explicit mood phrase to sharpen the vector
            mood_phrase = f"mood: {' '.join(tone_words[:6])}"

        vectors = []
        try:
            vectors.append(embedder.encode_text(base))
        except Exception as e:
            logger.warning(f"Embedding base prompt failed: {e}")
        # Encode each distinct seed (limit 5)
        for s in seeds[:5]:
            try:
                vectors.append(embedder.encode_text(f"like: {s}"))
            except Exception:
                pass
        if mood_phrase:
            try:
                vectors.append(embedder.encode_text(mood_phrase))
            except Exception:
                pass
        # Blend by simple average (already normalized embeddings)
        if vectors:
            import numpy as _np
            query_emb = _np.mean(_np.vstack(vectors).astype(_np.float32), axis=0)
            # Normalize after averaging
            query_emb = query_emb / (float((query_emb ** 2).sum()) ** 0.5 + 1e-8)
            query_emb = query_emb.astype(np.float16)
        else:
            query_emb = embedder.encode_text(base)

        # Negative cue handling: subtract vector component for negatives like "without horror/gore"
        if negative_cues:
            try:
                # Build a single negative descriptor to keep calls minimal
                neg_text = ", ".join(negative_cues[:6])
                neg_vec = embedder.encode_text(f"avoid: {neg_text}")
                # Project and subtract a scaled component
                import numpy as _np
                q = query_emb.astype(_np.float32)
                n = neg_vec.astype(_np.float32)
                # Remove up to 25% of the negative direction
                alpha = float(_np.dot(q, n))
                q_adj = q - 0.25 * alpha * n
                # Renormalize
                q_adj = q_adj / (float((q_adj ** 2).sum()) ** 0.5 + 1e-8)
                query_emb = q_adj.astype(np.float16)
            except Exception as e:
                logger.debug(f"Negative cue embedding adjustment skipped: {e}")
        # We don't know if the FAISS mapping stores tmdb_id or trakt_id; support both.
        topk_ids = []
        if cached:
            try:
                data = json.loads(cached)
                topk_ids = data.get("topk_ids") or data.get("topk_tmdb_ids") or []
            except Exception:
                topk_ids = []
        if not topk_ids:
            ids, scores = search_index(index, query_emb, top_k=1500)
            topk_ids = [mapping.get(int(i)) for i in ids if int(i) in mapping]
            # Back-compat: store under both keys
            r.set(cache_key, json.dumps({"topk_ids": topk_ids, "topk_tmdb_ids": topk_ids}), ex=86400)
        
        logger.info(f"[{ai_list_id}] FAISS returned {len(topk_ids)} candidate IDs for query: {normalized[:50]}")

        # Fetch candidate rows from DB using both columns (tmdb_id OR trakt_id)
        rows = []
        id_vals = [int(t) for t in topk_ids if t is not None]
        if id_vals:
            # Determine minimum vote count based on discovery mode from filters
            discovery_mode = parsed.get("filters", {}).get("discovery", "balanced")
            if discovery_mode in ("obscure", "very_obscure"):
                min_votes = 100  # Hidden gems need at least 100 votes for quality
            elif discovery_mode in ("popular", "mainstream"):
                min_votes = 500  # Mainstream content needs 500+ votes
            else:
                min_votes = 200  # Default/balanced requires 200 votes
            
            # Build named params for tmdb and trakt separately
            tmdb_placeholders = ",".join([f":tm{i}" for i in range(len(id_vals))])
            trakt_placeholders = ",".join([f":tr{i}" for i in range(len(id_vals))])
            params = {f"tm{i}": v for i, v in enumerate(id_vals)}
            params.update({f"tr{i}": v for i, v in enumerate(id_vals)})
            params["min_votes"] = min_votes
            
            sql = text(
                f"""
                SELECT * FROM persistent_candidates 
                WHERE (tmdb_id IN ({tmdb_placeholders}) OR trakt_id IN ({trakt_placeholders}))
                  AND active = true
                  AND trakt_id IS NOT NULL
                  AND embedding IS NOT NULL
                  AND vote_count >= :min_votes
                """
            )
            res = db.execute(sql, params)
            cols = res.keys()
            for row in res:
                rows.append(dict(zip(cols, row)))
        
        logger.info(f"[{ai_list_id}] DB fetch returned {len(rows)} candidates with trakt_id+embedding (min_votes={min_votes}, discovery={discovery_mode})")

        # Compose texts
        texts = [compose_text_for_embedding(c) for c in rows]
        # Load candidate embeddings from DB to enable semantic scoring
        cand_embs = None
        try:
            from app.services.ai_engine.faiss_index import deserialize_embedding
            embs = []
            for c in rows:
                try:
                    blob = c.get("embedding")
                    if blob is None:
                        continue
                    embs.append(deserialize_embedding(bytes(blob)))
                except Exception:
                    embs.append(None)
            # Filter out any Nones just in case
            valid = [(i, e) for i, e in enumerate(embs) if e is not None]
            if valid and len(valid) == len(rows):
                import numpy as _np
                cand_embs = _np.vstack(embs).astype(_np.float32)
            else:
                cand_embs = None
        except Exception:
            cand_embs = None
        scored = score_candidates(
            normalized,
            rows,
            texts,
            candidate_embeddings=cand_embs,
            query_embedding=query_emb if cand_embs is not None else None,
            filters=parsed.get("filters", {}),
            list_type=parsed.get("type", "chat"),
            topk_reduce=800,
            user_id=user_id,
            watch_history=watch_history,  # Pass Trakt history for personalization
        )
        logger.info(f"[{ai_list_id}] Scoring returned {len(scored)} candidates after filtering")
        
        # Fallback: if too few results, relax filters but keep trakt_id+embedding constraint
        if len(scored) < max(20, int((ai_list.item_limit or 50) * 0.6)):
            try:
                relaxed = score_candidates(
                    normalized,
                    rows,
                    texts,
                    candidate_embeddings=cand_embs,
                    query_embedding=query_emb if cand_embs is not None else None,
                    filters={},  # relax
                    list_type=parsed.get("type", "chat"),
                    topk_reduce=1200,
                    user_id=user_id,
                    watch_history=watch_history,
                )
                # Prefer relaxed if it meaningfully increases pool
                if len(relaxed) > len(scored):
                    scored = relaxed
            except Exception:
                pass
        # Embedding for MMR via TF-IDF vectors only as fallback
        # Build a simple TF-IDF matrix for MMR vectors
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            vec = TfidfVectorizer(max_features=2048)
            mat = vec.fit_transform(texts)
            mmr_vecs = mat.toarray().astype(np.float32)
        except Exception:
            mmr_vecs = np.zeros((len(texts), 16), dtype=np.float32)

        diversified = maximal_marginal_relevance(scored, mmr_vecs, top_k=min(ai_list.item_limit or 50, 50))
        # Clear previous items for idempotent refresh
        db.query(AiListItem).filter_by(ai_list_id=ai_list.id).delete()
        for rank, cand in enumerate(diversified, start=1):
            meta = build_explanation_meta(cand)
            item = AiListItem(
                ai_list_id=ai_list.id,
                tmdb_id=cand.get("tmdb_id"),
                trakt_id=cand.get("trakt_id"),
                rank=rank,
                score=float(cand.get("final_score", 0)),
                explanation_meta=meta,
                explanation_text=generate_explanation(cand),
            )
            db.add(item)
        # Title handling
        ai_list.generated_title = parsed.get("suggested_title") or (ai_list.prompt_text[:60] if ai_list.prompt_text else "AI Picks")
        from app.utils.timezone import utc_now
        ai_list.last_synced_at = utc_now()
        # Mark FAISS usage timestamp for maintenance planning
        try:
            r.set("ai:last_faiss_usage", str(int(utc_now().timestamp())), ex=86400)
        except Exception:
            pass
        ai_list.status = "ready"
        db.commit()

        # Publish notification with proper title and message for toast
        r.publish(f"notifications:{user_id}", json.dumps({
            "type": "ai_list_ready",
            "ai_list_id": ai_list.id,
            "title": "AI List Ready",
            "message": f"'{ai_list.generated_title}' has been generated with {len(diversified)} recommendations"
        }))
        # Set short cooldown (e.g., 60s) to prevent rapid re-syncs
        try:
            r.set(f"ai:cooldown:{ai_list.id}", "1", ex=60)
        except Exception:
            pass

    except Exception as e:
        logger.exception(f"Failed to generate chat list: {e}")
        try:
            ai_list.status = "error"
            db.commit()
        except Exception:
            pass
        # In async path, avoid self.retry; log and exit. Celery will handle next run.
    finally:
        try:
            r.delete(lock_key)
        except Exception:
            pass
        db.close()
        gc.collect()

@celery_app.task(name="generate_dynamic_lists", bind=True, max_retries=3)
def generate_dynamic_lists(self, user_id: int = 1):
    """Create and schedule generation of 7 dynamic AI lists (mood, fusion, theme, popularity-based).
    Only creates lists to reach a total of 7 dynamic lists, deleting old ones if needed."""
    r = get_redis_sync()
    lock_key = f"lock:ai:dynamic:{user_id}"
    if not r.set(lock_key, "1", nx=True, ex=1800):
        logger.info(f"Dynamic list generation already in progress for user {user_id}")
        return
    db = SessionLocal()
    try:
        from app.services.ai_engine.moods_themes_map import MOODS, THEMES, FUSIONS, suggest_presets_from_history
        
        # Check existing dynamic lists (type in mood, theme, fusion)
        existing_dynamic_lists = db.query(AiList).filter(
            AiList.user_id == user_id,
            AiList.type.in_(['mood', 'theme', 'fusion'])
        ).all()
        
        existing_count = len(existing_dynamic_lists)
        logger.info(f"User {user_id} has {existing_count} existing dynamic AI lists")
        
        # If we already have 7 or more, delete oldest ones and recreate
        if existing_count >= 7:
            # Sort by creation time and keep the 7 most recent
            from datetime import datetime as dt_class
            existing_dynamic_lists.sort(key=lambda x: x.created_at or dt_class.min, reverse=True)
            lists_to_keep = existing_dynamic_lists[:7]
            lists_to_delete = existing_dynamic_lists[7:]
            
            for old_list in lists_to_delete:
                logger.info(f"Deleting old dynamic list: {old_list.id}")
                db.delete(old_list)
            db.commit()
            
            # Refresh the remaining 7 lists
            for lst in lists_to_keep:
                lst.status = "queued"
                db.commit()
                generate_chat_list.delay(lst.id, user_id)
            
            r.publish(f"notifications:{user_id}", json.dumps({
                "type": "ai_dynamic_refreshed",
                "title": "Dynamic Lists Refreshed",
                "message": f"Regenerating 7 AI-powered recommendation lists"
            }))
            return
        
        # Try to suggest presets from recent history
        history_titles = []
        try:
            from app.services.trakt_client import TraktClient
            tc = TraktClient(user_id=user_id)
            # Expect a method to fetch recently watched titles; fallback gracefully
            history_titles = [h.get("title") for h in (tc.get_recent_history(limit=20) or []) if h.get("title")]
        except Exception:
            history_titles = []

        presets = suggest_presets_from_history(history_titles)
        # Ensure we have enough presets to reach 7 total lists
        pool = ([{"type": "mood", "label": m, "generated_title": f"{m.title()} Vibes"} for m in MOODS[:3]] +
                [{"type": "fusion", "label": f, "generated_title": f.title()} for f in FUSIONS[:2]] +
                [{"type": "theme", "label": t, "generated_title": f"{t.title()} Stories"} for t in THEMES[:2]])
        while len(presets) < (7 - existing_count) and pool:
            presets.append(pool.pop(0))
        
        # Only create the number needed to reach 7
        needed = 7 - existing_count
        presets = presets[:needed]
        
        logger.info(f"Creating {len(presets)} new dynamic lists for user {user_id}")

        created_ids = []
        for p in presets:
            ai_list = AiList(
                user_id=user_id,
                type=p["type"],
                prompt_text=p["label"],
                normalized_prompt=p["label"],
                generated_title=p.get("generated_title") or f"{p['label'].title()} Picks",
                status="queued",
                item_limit=50,
            )
            db.add(ai_list)
            db.commit()
            db.refresh(ai_list)
            created_ids.append(ai_list.id)
            generate_chat_list.delay(ai_list.id, user_id)

        r.publish(f"notifications:{user_id}", json.dumps({
            "type": "ai_dynamic_created",
            "ids": created_ids,
            "title": "Dynamic Lists Created",
            "message": f"Generated {len(created_ids)} AI-powered recommendation lists"
        }))
    except Exception as e:
        logger.exception(f"Failed to generate dynamic lists: {e}")
        raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))
    finally:
        try:
            r.delete(lock_key)
        except Exception:
            pass
        db.close()
        gc.collect()


@celery_app.task(name="refresh_ai_list", bind=True, max_retries=3)
def refresh_ai_list(self, ai_list_id: str, user_id: int = 1):
    r = get_redis_sync()
    lock_key = f"lock:ai_list_refresh:{ai_list_id}"
    import time
    lock_value = json.dumps({"started_at": time.time(), "user_id": user_id})
    if not r.set(lock_key, lock_value, nx=True, ex=1800):
        logger.info(f"Refresh already in progress for AI list {ai_list_id}")
        return
    db = SessionLocal()
    try:
        from app.services.ai_engine.moods_themes_map import MOODS, THEMES, FUSIONS
        import random
        
        ai_list = db.query(AiList).filter_by(id=ai_list_id, user_id=user_id).first()
        if not ai_list:
            return
        
        # For mood/theme/fusion lists, rotate to a new random selection
        # Chat lists keep their original prompt
        old_prompt = ai_list.prompt_text
        if ai_list.type == "mood":
            # Pick a different mood than current
            available_moods = [m for m in MOODS if m.lower() != old_prompt.lower()]
            if available_moods:
                new_mood = random.choice(available_moods)
                ai_list.prompt_text = new_mood
                ai_list.normalized_prompt = new_mood
                ai_list.generated_title = f"{new_mood.title()} Vibes"
                logger.info(f"Rotating mood list from '{old_prompt}' to '{new_mood}'")
        elif ai_list.type == "theme":
            # Pick a different theme than current
            available_themes = [t for t in THEMES if t.lower() != old_prompt.lower()]
            if available_themes:
                new_theme = random.choice(available_themes)
                ai_list.prompt_text = new_theme
                ai_list.normalized_prompt = new_theme
                ai_list.generated_title = f"{new_theme.title()} Stories"
                logger.info(f"Rotating theme list from '{old_prompt}' to '{new_theme}'")
        elif ai_list.type == "fusion":
            # Pick a different fusion than current
            available_fusions = [f for f in FUSIONS if f.lower() != old_prompt.lower()]
            if available_fusions:
                new_fusion = random.choice(available_fusions)
                ai_list.prompt_text = new_fusion
                ai_list.normalized_prompt = new_fusion
                ai_list.generated_title = new_fusion.title()
                logger.info(f"Rotating fusion list from '{old_prompt}' to '{new_fusion}'")
        # else: chat lists keep their original prompt_text unchanged
        
        ai_list.status = "queued"
        db.commit()
        generate_chat_list.delay(ai_list.id, user_id)
        r.publish(f"notifications:{user_id}", json.dumps({
            "type": "ai_list_refresh",
            "ai_list_id": ai_list.id,
            "title": "Refreshing AI List",
            "message": f"Regenerating recommendations for '{ai_list.generated_title or ai_list.prompt_text[:50]}'"
        }))
    except Exception as e:
        logger.exception(f"Failed to refresh ai list: {e}")
        raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))
    finally:
        try:
            r.delete(lock_key)
        except Exception:
            pass
        db.close()
        gc.collect()


@celery_app.task(name="rebuild_faiss_index", bind=True, max_retries=3)
def rebuild_faiss_index(self):
    """Rebuild FAISS index from persisted DB embeddings when available, without hard LIMIT.
    Strategy:
    - Count total embedded rows
    - Train IVF+PQ on a representative subset (top by popularity as proxy)
    - Stream all embeddings from DB in ascending tmdb_id and add in chunks
    - Persist mapping (faiss rowId -> tmdb_id) and index to /data/ai
    Falls back to on-the-fly encoding for bootstrap if DB has very few embeddings.
    """
    db = SessionLocal()
    try:
        import faiss
        from app.services.ai_engine.faiss_index import deserialize_embedding, _l2_normalize, DATA_DIR, INDEX_FILE, MAPPING_FILE
        embedder = EmbeddingService()

        # 1) Count total embedded rows
        total = db.execute(text(
            "SELECT COUNT(*) FROM persistent_candidates WHERE active=true AND embedding IS NOT NULL"
        )).scalar() or 0

        if total == 0:
            # Bootstrap: encode a small top slice so we can at least serve something
            bootstrap = db.execute(text(
                """
                SELECT tmdb_id, title, overview
                FROM persistent_candidates
                WHERE active=true
                ORDER BY popularity DESC
                LIMIT 5000
                """
            )).fetchall()
            if not bootstrap:
                logger.warning("[FAISS] No candidates available to bootstrap index.")
                return
            texts = [f"{t or ''} {o or ''}" for _, t, o in bootstrap]
            embs = embedder.encode_texts(texts, batch_size=64).astype(np.float32)
            dim = embs.shape[1]
            quantizer = faiss.IndexFlatIP(dim)
            nlist = max(1024, min(16384, int(np.sqrt(len(embs)))*4))
            index = faiss.IndexIVFPQ(quantizer, dim, nlist, 64, 8)
            index.train(_l2_normalize(embs.astype(np.float32)))
            index.add(_l2_normalize(embs.astype(np.float32)))
            # Persist
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            faiss.write_index(index, str(INDEX_FILE))
            mapping = {i: int(tmdb) for i, (tmdb, _, _) in enumerate(bootstrap)}
            with open(MAPPING_FILE, "w") as f:
                json.dump(mapping, f)
            get_redis_sync().publish("system:ai", json.dumps({"type": "faiss_rebuilt", "count": len(mapping)}))
            return

        # 2) Prepare parameters for full rebuild
        # Training set: take top-K by popularity as representative (up to 100k)
        train_k = int(min(100000, max(10000, total * 0.1)))  # 10% up to 100k, at least 10k
        train_rows = db.execute(text(
            """
            SELECT tmdb_id, embedding
            FROM persistent_candidates
            WHERE active=true AND embedding IS NOT NULL
            ORDER BY popularity DESC
            LIMIT :lim
            """
        ), {"lim": train_k}).fetchall()

        # Deserialize training embeddings
        train_vecs = []
        for _, blob in train_rows:
            try:
                train_vecs.append(deserialize_embedding(bytes(blob)).astype(np.float32))
            except Exception:
                continue
        if not train_vecs:
            logger.warning("[FAISS] No training vectors available; aborting rebuild.")
            return
        dim = train_vecs[0].shape[0]
        train_mat = _l2_normalize(np.vstack(train_vecs).astype(np.float32))

        # Choose nlist proportional to sqrt(N), capped
        nlist = max(2048, min(32768, int(np.sqrt(total)) * 4))
        quantizer = faiss.IndexFlatIP(dim)
        index = faiss.IndexIVFPQ(quantizer, dim, nlist, 64, 8)
        logger.info(f"[FAISS] Training IVF+PQ: total={total}, train={len(train_mat)}, nlist={nlist}, dim={dim}")
        index.train(train_mat)

        # 3) Stream all embeddings and add to index in chunks
        chunk = 50000
        last_tmdb = 0
        added = 0
        mapping = {}
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        while True:
            batch = db.execute(text(
                """
                SELECT tmdb_id, embedding
                FROM persistent_candidates
                WHERE active=true AND embedding IS NOT NULL AND tmdb_id > :last
                ORDER BY tmdb_id ASC
                LIMIT :lim
                """
            ), {"last": last_tmdb, "lim": chunk}).fetchall()
            if not batch:
                break
            vecs = []
            ids = []
            for tmdb_id, blob in batch:
                try:
                    ids.append(int(tmdb_id))
                    vecs.append(deserialize_embedding(bytes(blob)).astype(np.float32))
                except Exception:
                    continue
            if vecs:
                mat = _l2_normalize(np.vstack(vecs).astype(np.float32))
                start_id = index.ntotal
                index.add(mat)
                for i, tm in enumerate(ids):
                    mapping[start_id + i] = tm
                added += len(ids)
            last_tmdb = int(batch[-1][0])
            if added % 200000 == 0:
                logger.info(f"[FAISS] Added {added}/{total} vectors...")

        # 4) Persist artifacts
        faiss.write_index(index, str(INDEX_FILE))
        with open(MAPPING_FILE, "w") as f:
            json.dump(mapping, f)
        get_redis_sync().publish("system:ai", json.dumps({"type": "faiss_rebuilt", "count": added}))
    except Exception as e:
        logger.exception(f"Failed to rebuild FAISS index: {e}")
        raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))
    finally:
        db.close()
        gc.collect()
