"""
tasks_ai.py
- Celery tasks and orchestration for AI-powered lists: chat list generation, dynamic list refresh, FAISS index management.
- Uses ai_engine modules, Redis locks, prompt cache, and notification publishing.
"""
import logging
import json
import gc
import asyncio
import numpy as np
from app.core.celery_app import celery_app
from app.core.database import SessionLocal
from app.core.redis_client import get_redis_sync
from app.models_ai import AiList, AiListItem
from app.services.ai_engine.embeddings import EmbeddingService
from app.services.ai_engine.faiss_index import load_index, search_index
from app.core.memory_manager import managed_memory
from app.services.ai_engine.parser import parse_prompt
from app.services.ai_engine.metadata_processing import compose_text_for_embedding
from app.services.ai_engine.scorer import score_candidates
from app.services.ai_engine.diversifier import maximal_marginal_relevance
from app.services.ai_engine.explainability import build_explanation_meta, generate_explanation
from app.services.trakt_client import TraktClient, TraktAuthError
from app.services.watch_history_helper import WatchHistoryHelper
from sqlalchemy import text

logger = logging.getLogger(__name__)

async def _get_user_watch_history(user_id: int, limit: int = 500) -> dict:
    """Fetch user's watch history as {trakt_id: metadata}, preferring DB cache.

    Falls back to Trakt API only if DB access fails or returns nothing.
    """
    # 1) Try DB helper first
    try:
        helper = WatchHistoryHelper(user_id=user_id)
        # Grab recent items up to requested limit
        recent_movies = helper.get_watched_status_dict("movie")
        recent_shows = helper.get_watched_status_dict("show")
        # Convert to flat dict keyed by trakt_id
        watched_items: dict[int, dict] = {}
        # Combine and sort by watched_at desc, then cap to limit
        combined = []
        for tid, meta in recent_movies.items():
            combined.append((tid, {"type": "movie", **meta}))
        for tid, meta in recent_shows.items():
            combined.append((tid, {"type": "show", **meta}))
        # Sort by watched_at desc if present
        try:
            combined.sort(key=lambda x: (x[1].get("watched_at") or ""), reverse=True)
        except Exception:
            pass
        for tid, meta in combined[: max(1, int(limit))]:
            watched_items[int(tid)] = {
                "type": meta.get("type"),
                "title": meta.get("title"),  # May be None
                "year": meta.get("year"),    # May be None
                "watched_at": meta.get("watched_at"),
            }
        if watched_items:
            logger.info(f"[AI_TASKS] Loaded {len(watched_items)} watch items from DB for user {user_id}")
            return watched_items
    except Exception as e:
        logger.warning(f"[AI_TASKS] DB watch history unavailable, will try Trakt API: {e}")

    # 2) Fallback to Trakt API
    try:
        trakt = TraktClient(user_id=user_id)
        movie_history = await trakt.get_my_history(media_type="movies", limit=limit // 2)
        show_history = await trakt.get_my_history(media_type="shows", limit=limit // 2)

        watched_items: dict[int, dict] = {}
        for entry in movie_history:
            movie = entry.get("movie", {})
            tid = movie.get("ids", {}).get("trakt")
            if tid:
                watched_items[int(tid)] = {
                    "type": "movie",
                    "title": movie.get("title"),
                    "year": movie.get("year"),
                    "watched_at": entry.get("watched_at"),
                }
        for entry in show_history:
            show = entry.get("show", {})
            tid = show.get("ids", {}).get("trakt")
            if tid:
                watched_items[int(tid)] = {
                    "type": "show",
                    "title": show.get("title"),
                    "year": show.get("year"),
                    "watched_at": entry.get("watched_at"),
                }
        logger.info(f"[AI_TASKS] Fetched {len(watched_items)} history items from Trakt for user {user_id}")
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

        # Get obscurity default from settings for mood/theme/fusion lists
        default_obscurity = None
        if ai_list.type in ['mood', 'theme', 'fusion']:
            try:
                obscurity_setting = r.get("settings:global:obscurity_default")
                default_obscurity = obscurity_setting if obscurity_setting else "balanced"
            except Exception:
                default_obscurity = "balanced"
        # For chat lists, don't pass default - only use what's in the prompt
        
        parsed = parse_prompt(ai_list.prompt_text or ai_list.normalized_prompt or "", default_obscurity=default_obscurity)
        normalized = parsed["normalized_prompt"]
        # Enhance prompt for "like <title>" cases by appending seed title's metadata
        enhanced_prompt = normalized
        try:
            seeds = (parsed.get("seed_titles") or [])
            if seeds:
                seed = seeds[0]
                from app.services.tmdb_client import search_movies, search_tv, fetch_tmdb_metadata
                seed_meta = None
                # Try movie then TV for the seed
                try:
                    sm = await search_movies(seed, page=1)
                    if sm and sm.get("results"):
                        tmdb_id = sm["results"][0].get("id")
                        if tmdb_id:
                            seed_meta = await fetch_tmdb_metadata(tmdb_id, media_type='movie')
                except Exception:
                    seed_meta = None
                if not seed_meta:
                    try:
                        st = await search_tv(seed, page=1)
                        if st and st.get("results"):
                            tmdb_id = st["results"][0].get("id")
                            if tmdb_id:
                                seed_meta = await fetch_tmdb_metadata(tmdb_id, media_type='tv')
                    except Exception:
                        seed_meta = None
                # Build anchor text from seed metadata
                if seed_meta:
                    import json as _json
                    title = seed_meta.get("title") or seed_meta.get("name") or seed
                    genres = ", ".join([g.get("name", "") for g in (seed_meta.get("genres") or []) if isinstance(g, dict)])
                    kw = []
                    kws = seed_meta.get("keywords") or {}
                    if isinstance(kws, dict):
                        kw_list = kws.get("keywords") or kws.get("results") or []
                        kw = [k.get("name", "") for k in kw_list if isinstance(k, dict)]
                    overview = seed_meta.get("overview") or ""
                    anchor = f"Anchor: {title}. Genres: {genres}. Keywords: {'; '.join(kw[:12])}. Overview: {overview}"
                    # Append to base normalized prompt for better TF-IDF anchoring
                    enhanced_prompt = normalized + "\n" + anchor
        except Exception as _e:
            # Safe fallback: keep normalized as prompt
            enhanced_prompt = normalized
        # Persist parsed context on the list
        ai_list.normalized_prompt = normalized
        ai_list.filters = parsed.get("filters")
        ai_list.tone_vector = parsed.get("tone_vector")
        db.commit()
        # Prompt cache (include seeds/tone/negatives to reflect blended query)
        seeds = (parsed.get("seed_titles") or [])
        tone_words = (parsed.get("filters", {}).get("tone") or [])
        negative_cues = (parsed.get("filters", {}).get("negative_cues") or [])
        
        # Extract rich filters from parsed prompt for semantic targeting
        extracted_genres = parsed.get("filters", {}).get("genres") or []
        extracted_languages = parsed.get("filters", {}).get("languages") or []
        media_type_filter = parsed.get("filters", {}).get("media_type")
        extracted_networks = parsed.get("filters", {}).get("networks") or []
        extracted_countries = parsed.get("filters", {}).get("countries") or []
        extracted_creators = parsed.get("filters", {}).get("creators") or []
        extracted_directors = parsed.get("filters", {}).get("directors") or []
        extracted_phrases = parsed.get("filters", {}).get("phrases") or []
        
        # Log extracted filters for visibility
        logger.info(
            f"[{ai_list_id}] Extracted filters: "
            f"seeds={len(seeds)}, tone={len(tone_words)}, genres={len(extracted_genres)}, "
            f"languages={len(extracted_languages)}, media_type={media_type_filter}, negative={len(negative_cues)}, "
            f"networks={len(extracted_networks)}, creators={len(extracted_creators)}, directors={len(extracted_directors)}"
        )
        
        # Build enriched query text incorporating all extracted metadata
        # This makes FAISS return semantically targeted candidates from the start
        # Mirror the rich metadata structure used in compose_text_for_embedding
        query_parts = [normalized]
        
        # Add seed metadata context (genres, keywords, overviews from TMDB)
        if seeds and enhanced_prompt and enhanced_prompt != normalized:
            # Extract the anchor text we built earlier
            anchor_parts = enhanced_prompt.split("\n")
            if len(anchor_parts) > 1:
                query_parts.append(anchor_parts[1])  # "Anchor: Title. Genres: X. Keywords: Y. Overview: Z"
        
        # Add explicit genre context to query
        if extracted_genres:
            genre_text = " ".join(extracted_genres[:5])  # Limit to top 5 genres
            query_parts.append(f"Genres: {genre_text}")
        
        # Add language context
        if extracted_languages:
            lang_text = " ".join(extracted_languages[:3])
            query_parts.append(f"Language: {lang_text}")
        
        # Add tone/mood keywords to query text
        if tone_words:
            tone_text = " ".join(tone_words[:8])
            query_parts.append(f"Mood: {tone_text}")
        
        # Add media type for better semantic targeting
        if media_type_filter:
            query_parts.append(f"Type: {media_type_filter}")
        
        # Add networks (for TV shows)
        if extracted_networks:
            networks_text = " ".join(extracted_networks[:3])
            query_parts.append(f"Networks: {networks_text}")
        
        # Add countries
        if extracted_countries:
            countries_text = " ".join(extracted_countries[:3])
            query_parts.append(f"Countries: {countries_text}")
        
        # Add creators/directors (important for personalized searches)
        if extracted_creators:
            creators_text = " ".join(extracted_creators[:3])
            query_parts.append(f"Creators: {creators_text}")
        
        if extracted_directors:
            directors_text = " ".join(extracted_directors[:3])
            query_parts.append(f"Directors: {directors_text}")
        
        # Add quoted phrases (exact match importance)
        if extracted_phrases:
            phrases_text = " ".join(extracted_phrases[:5])
            query_parts.append(f"Key phrases: {phrases_text}")
        
        # Build enriched query text combining seed metadata AND all extracted filters
        enriched_query = ". ".join(query_parts)
        
        cache_key = (
            f"ai:prompt_cache_v2:{normalized}"
            f"|seeds:{','.join(seeds[:5])}"
            f"|tone:{','.join(tone_words[:6])}"
            f"|neg:{','.join(negative_cues[:6])}"
            f"|genres:{','.join(extracted_genres[:5])}"
            f"|langs:{','.join(extracted_languages[:3])}"
        )
        cached = r.get(cache_key)
        embedder = EmbeddingService()
        index, mapping = load_index()
        
        # Use enriched query text for embedding instead of just base prompt
        # This incorporates genres, languages, mood, and seed metadata into semantic search
        logger.info(f"[{ai_list_id}] Enriched query for FAISS: {enriched_query[:200]}")
        
        try:
            query_emb = embedder.encode_text(enriched_query)
        except Exception as e:
            logger.warning(f"Embedding enriched query failed: {e}, falling back to base prompt")
            query_emb = embedder.encode_text(normalized)

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
        faiss_scores_dict = {}
        if not topk_ids:
            # With enriched semantic query (genres, languages, mood, seed metadata),
            # we can use a smaller top_k that returns semantically targeted candidates
            # instead of post-filtering a huge pool. Balance: speed + semantic coverage.
            # Increase initial FAISS pool for better coverage; downstream we will
            # hard-filter by all user filters (genres/languages/media types/years/obscurity)
            ids, faiss_scores = search_index(index, query_emb, top_k=40000)
            # Map FAISS internal IDs to real IDs (trakt_id preferred)
            topk_ids = []
            for idx, internal_id in enumerate(ids):
                if int(internal_id) in mapping:
                    mapped_id = mapping.get(int(internal_id))
                    if mapped_id is None:
                        continue
                    try:
                        mapped_id_int = int(mapped_id)
                    except Exception:
                        continue
                    topk_ids.append(mapped_id_int)
                    try:
                        faiss_scores_dict[mapped_id_int] = float(faiss_scores[idx])
                    except Exception:
                        faiss_scores_dict[mapped_id_int] = 0.0
            # Back-compat: store under both keys
            r.set(cache_key, json.dumps({"topk_ids": topk_ids, "topk_tmdb_ids": topk_ids}), ex=86400)
        
        logger.info(f"[{ai_list_id}] FAISS returned {len(topk_ids)} candidate IDs for enriched query: {enriched_query[:80]}")

        # Fetch candidate rows from DB using FAISS-targeted IDs (semantically enriched query)
        # This is much faster than full-pool and gives semantically relevant candidates from the start
        rows = []
        if topk_ids:
            # Support both tmdb_id and trakt_id in FAISS mapping
            filters = parsed.get("filters", {}) or {}
            genres = [g.lower() for g in (filters.get("genres") or [])]
            genre_mode = (filters.get("genre_mode") or filters.get("genres_mode") or "any").lower()
            languages = [l.lower() for l in (filters.get("languages") or [])]
            media_types = filters.get("media_types") or []
            # Optional extended filters parsed from prompt
            networks = [n.lower() for n in (filters.get("networks") or [])]
            countries = [c.lower() for c in (filters.get("countries") or [])]
            creators = [c.lower() for c in (filters.get("creators") or [])]
            directors = [d.lower() for d in (filters.get("directors") or [])]
            if not media_types:
                mt_single = filters.get("media_type")
                if mt_single:
                    media_types = [mt_single]
            year_from = filters.get("year_from")
            year_to = filters.get("year_to")
            obscurity = (filters.get("obscurity") or "").lower()

            # Build dynamic SQL with safe parameters
            where_clauses = [
                "(tmdb_id = ANY(:ids) OR trakt_id = ANY(:ids))",
                "active = true",
            ]
            params = {}

            if media_types:
                where_clauses.append("media_type = ANY(:media_types)")
                params["media_types"] = media_types
            if languages:
                where_clauses.append("LOWER(COALESCE(original_language, '')) = ANY(:languages)")
                params["languages"] = languages
            if isinstance(year_from, int):
                where_clauses.append("COALESCE(year, 0) >= :year_from")
                params["year_from"] = int(year_from)
            if isinstance(year_to, int):
                where_clauses.append("COALESCE(year, 9999) <= :year_to")
                params["year_to"] = int(year_to)
            # Obscurity buckets (use persistent precomputed scores if available)
            if obscurity in ("very_obscure", "very-obscure"):
                where_clauses.append("COALESCE(obscurity_score, 0) >= 0.8")
            elif obscurity in ("obscure",):
                where_clauses.append("COALESCE(obscurity_score, 0) >= 0.6")
            elif obscurity in ("popular", "mainstream"):
                where_clauses.append("COALESCE(mainstream_score, 0) >= 0.6")

            # Genres matching (stored as free-text comma list). Build LIKE conditions.
            genre_like_clauses = []
            for i, g in enumerate(genres):
                key = f"g{i}"
                genre_like_clauses.append(f"LOWER(COALESCE(genres, '')) LIKE :{key}")
                params[key] = f"%{g}%"
            if genre_like_clauses:
                if genre_mode == "all":
                    where_clauses.extend(genre_like_clauses)
                else:
                    where_clauses.append("(" + " OR ".join(genre_like_clauses) + ")")

            # Networks (TV) any-match OR across provided names
            if networks:
                net_like = []
                for i, n in enumerate(networks):
                    key = f"net{i}"
                    net_like.append(f"LOWER(COALESCE(networks, '')) LIKE :{key}")
                    params[key] = f"%{n}%"
                where_clauses.append("(" + " OR ".join(net_like) + ")")

            # Production countries any-match OR
            if countries:
                ctry_like = []
                for i, c in enumerate(countries):
                    key = f"ctry{i}"
                    ctry_like.append(f"LOWER(COALESCE(production_countries, '')) LIKE :{key}")
                    params[key] = f"%{c}%"
                where_clauses.append("(" + " OR ".join(ctry_like) + ")")

            # Creators (TV) any-match OR
            if creators:
                cr_like = []
                for i, c in enumerate(creators):
                    key = f"cr{i}"
                    cr_like.append(f"LOWER(COALESCE(created_by, '')) LIKE :{key}")
                    params[key] = f"%{c}%"
                where_clauses.append("(" + " OR ".join(cr_like) + ")")

            # Directors (movies/TV) - no dedicated column; match against created_by (TV) OR cast (movies)
            if directors:
                dir_like_or_groups = []
                for i, d in enumerate(directors):
                    key = f"dir{i}"
                    # Note: "cast" needs quoting in SQL
                    dir_like_or_groups.append(f"LOWER(COALESCE(created_by, '')) LIKE :{key} OR LOWER(COALESCE(\"cast\", '')) LIKE :{key}")
                    params[key] = f"%{d}%"
                where_clauses.append("(" + " OR ".join(dir_like_or_groups) + ")")

            where_sql = " AND ".join(where_clauses)
            base_sql = f"SELECT * FROM persistent_candidates WHERE {where_sql}"

            id_list = topk_ids[:40000]
            CHUNK = 1000
            MAX_PREFILTERED = 6000  # cap to avoid excessive memory before scoring
            for start in range(0, len(id_list), CHUNK):
                if len(rows) >= MAX_PREFILTERED:
                    break
                chunk_ids = id_list[start:start+CHUNK]
                params_chunk = dict(params)
                params_chunk["ids"] = chunk_ids
                res = db.execute(text(base_sql), params_chunk)  # Chunked DB fetch with filters applied in SQL
                cols = res.keys()
                for row in res:
                    row_dict = dict(zip(cols, row))
                    # Annotate with FAISS score if available (prefer trakt_id, fallback tmdb_id)
                    try:
                        rid_trakt = row_dict.get("trakt_id")
                        rid_tmdb = row_dict.get("tmdb_id")
                        score = None
                        if rid_trakt is not None and int(rid_trakt) in faiss_scores_dict:
                            score = faiss_scores_dict.get(int(rid_trakt))
                        elif rid_tmdb is not None and int(rid_tmdb) in faiss_scores_dict:
                            score = faiss_scores_dict.get(int(rid_tmdb))
                        if score is not None:
                            row_dict["_faiss_score"] = float(score)
                            row_dict["_from_faiss"] = True
                    except Exception:
                        pass
                    rows.append(row_dict)
                # Safety: trim if we crossed the cap within this chunk
                if len(rows) > MAX_PREFILTERED:
                    rows = rows[:MAX_PREFILTERED]
                    break

            # Preserve FAISS ranking order for downstream steps (stable sort by position in topk_ids)
            rank = {val: idx for idx, val in enumerate(id_list)}
            def _pos(c):
                try:
                    tid = c.get("trakt_id")
                    if tid is not None and int(tid) in rank:
                        return rank[int(tid)]
                    mid = c.get("tmdb_id")
                    if mid is not None and int(mid) in rank:
                        return rank[int(mid)]
                except Exception:
                    return 10**9
                return 10**9
            rows.sort(key=_pos)
        
        logger.info(f"[{ai_list_id}] DB fetch (FAISS-targeted) returned {len(rows)} candidates for scoring")

        # Use a managed memory wrapper for the heavy scoring block
        with managed_memory("ai_scoring_and_mmr"):
            # Compose texts for scoring
            texts = [compose_text_for_embedding(c) for c in rows]
            # Load candidate embeddings from DB to enable semantic scoring
            # If FAISS scores are present, skip loading embeddings (reuse FAISS similarity)
            cand_embs = None
            has_faiss_scores = any(isinstance(c.get("_faiss_score"), (float, int)) for c in rows)
            if not has_faiss_scores:
                try:
                    from app.services.ai_engine.faiss_index import deserialize_embedding
                    filtered_rows = []
                    embs = []
                    skipped = 0
                    for c in rows:
                        try:
                            blob = c.get("embedding")
                            if not blob:
                                skipped += 1
                                continue
                            e = deserialize_embedding(bytes(blob))
                            if e is None:
                                skipped += 1
                                continue
                            filtered_rows.append(c)
                            embs.append(e)
                        except Exception:
                            skipped += 1
                            continue
                    import numpy as _np
                    cand_embs = _np.vstack(embs).astype(_np.float32) if embs else None
                    if skipped:
                        logger.info(f"[{ai_list_id}] Skipped {skipped} candidates due to missing/invalid embeddings; using {len(filtered_rows)} with embeddings")
                    rows = filtered_rows
                except Exception:
                    cand_embs = None
            else:
                logger.info(f"[{ai_list_id}] Using FAISS similarity; skipping candidate embedding load")

            # Compose texts after aligning rows with embeddings
            texts = [compose_text_for_embedding(c) for c in rows]

            # Use enriched_query for scoring to ensure TF-IDF uses same context as FAISS semantic search
            # This includes genres, languages, mood, and seed metadata for better text similarity
            scored = score_candidates(
                enriched_query,
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
                with managed_memory("ai_scoring_relaxed"):
                    relaxed = score_candidates(
                        enriched_query,
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
                    try:
                        logger.info(f"[{ai_list_id}] Using relaxed scoring pool ({len(relaxed)} > {len(scored)})")
                    except Exception:
                        pass
                    scored = relaxed
            except Exception:
                pass
        # Embedding for MMR via TF-IDF vectors only as fallback
        # Build a simple TF-IDF matrix for MMR vectors
        try:
            with managed_memory("ai_mmr_vectors"):
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
        # Title handling - preserve existing generated_title for mood/theme/fusion lists
        # Only use parsed suggested_title for chat lists or if no title exists
        if ai_list.type in ("mood", "theme", "fusion") and ai_list.generated_title:
            # Keep the existing generated_title (e.g., "Dark Vibes", "Fantasy Stories", "Romantic Comedy")
            pass
        else:
            # For chat lists or lists without titles, use parsed suggestion
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

        # Generate poster after list is complete
        logger.info(f"[{ai_list_id}] BEFORE poster generation import")
        try:
            from app.services.poster_generator import generate_list_poster, delete_list_poster
            logger.info(f"[{ai_list_id}] Import successful, generating poster for AI list with {len(diversified)} items")
            old_poster_path = ai_list.poster_path
            poster_filename = generate_list_poster(
                ai_list.id,
                diversified[:5],  # Use top 5 items for poster
                list_type=ai_list.type,  # Use actual AI list type (mood/theme/fusion/chat)
                max_items=5
            )
            logger.info(f"[{ai_list_id}] Poster generation returned: {poster_filename}")
            if poster_filename:
                if old_poster_path and old_poster_path != poster_filename:
                    delete_list_poster(old_poster_path)
                ai_list.poster_path = poster_filename
                db.commit()
                logger.info(f"[{ai_list_id}] Poster generated: {poster_filename}")
            else:
                logger.warning(f"[{ai_list_id}] Poster generation returned None")
        except Exception as e:
            logger.error(f"[{ai_list_id}] Poster generation failed: {e}", exc_info=True)

        # After items are saved, ensure a Trakt list exists and sync items to it
        try:
            from app.services.trakt_client import TraktAuthError
            # Create Trakt list if missing
            if not ai_list.trakt_list_id:
                try:
                    name = ai_list.generated_title or (ai_list.prompt_text[:60] if ai_list.prompt_text else "AI Picks")
                    # We're already inside an async context (_generate_chat_list_async), so just await directly
                    t = TraktClient(user_id=user_id)
                    created = await t.create_list(
                        name=name,
                        description="AI-powered list managed by WatchBuddy",
                        privacy="private"
                    )
                    tlid = (created or {}).get("ids", {}).get("trakt")
                    if tlid:
                        ai_list.trakt_list_id = str(tlid)
                        db.commit()
                        logger.info(f"Created Trakt list {tlid} for AI list {ai_list.id}")
                except TraktAuthError:
                    logger.info("Trakt not authorized; skipping AI list Trakt creation")
                except Exception as e:
                    logger.warning(f"Failed creating Trakt list for AI list {ai_list.id}: {e}")

            # If we have a Trakt list ID, sync top items
            if ai_list.trakt_list_id:
                try:
                    # Build desired items with media_type for add/remove operations
                    desired_items = []
                    for cand in diversified[: ai_list.item_limit or 50]:
                        tid = cand.get("trakt_id")
                        mtype = cand.get("media_type") or ("movie" if str(cand.get("type", "")).lower().startswith("movie") else None)
                        if tid and mtype in ("movie", "show"):
                            try:
                                desired_items.append({"trakt_id": int(tid), "media_type": mtype})
                            except Exception:
                                pass
                    if desired_items:
                        # First attempt - we're in async context, just await
                        try:
                            t = TraktClient(user_id=user_id)
                            stats = await t.sync_list_items(ai_list.trakt_list_id, desired_items)
                            logger.info(f"Synced AI list {ai_list.id} to Trakt {ai_list.trakt_list_id}: {stats}")
                        except Exception as sync_err:
                            logger.warning(f"AI Trakt sync failed, attempting recreate: {sync_err}")
                            try:
                                # Recreate Trakt list and retry once
                                t = TraktClient(user_id=user_id)
                                created = await t.create_list(
                                    name=ai_list.generated_title or (ai_list.prompt_text[:60] if ai_list.prompt_text else "AI Picks"),
                                    description="AI-powered list managed by WatchBuddy",
                                    privacy="private"
                                )
                                tlid = (created or {}).get("ids", {}).get("trakt")
                                if tlid:
                                    ai_list.trakt_list_id = str(tlid)
                                    db.commit()
                                    logger.info(f"Recreated Trakt list {tlid} for AI list {ai_list.id}")
                                    stats = await t.sync_list_items(ai_list.trakt_list_id, desired_items)
                                    logger.info(f"Synced AI list {ai_list.id} to Trakt {ai_list.trakt_list_id} after recreate: {stats}")
                            except Exception as recreate_err:
                                logger.warning(f"AI Trakt recreate+sync failed: {recreate_err}")
                except TraktAuthError:
                    logger.info("Trakt not authorized; skipping AI list Trakt sync")
                except Exception as e:
                    logger.warning(f"Failed syncing AI list {ai_list.id} to Trakt: {e}")
        except Exception as e:
            logger.debug(f"AI list Trakt sync skipped: {e}")

        # Publish notification with proper title and message for toast
        notification_message = f"'{ai_list.generated_title}' has been generated with {len(diversified)} recommendations"
        r.publish(f"notifications:{user_id}", json.dumps({
            "type": "ai_list_ready",
            "ai_list_id": ai_list.id,
            "title": "AI List Ready",
            "message": notification_message
        }))
        
        # Persist notification to database so it shows in notifications UI
        try:
            from app.services.tasks import send_user_notification
            send_user_notification.delay(user_id, notification_message)
        except Exception as e:
            logger.warning(f"Failed to queue AI list ready notification: {e}")
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

        # Get existing titles to avoid duplicates
        existing_titles = {lst.generated_title or lst.prompt_text for lst in existing_dynamic_lists}
        logger.info(f"Existing titles to avoid: {existing_titles}")

        created_ids = []
        for p in presets:
            proposed_title = p.get("generated_title") or f"{p['label'].title()} Picks"
            
            # Check if this title already exists
            if proposed_title in existing_titles:
                logger.warning(f"Title '{proposed_title}' already exists, finding alternative")
                # Try to find an alternative from the same type
                if p["type"] == "mood":
                    alternatives = [m for m in MOODS if f"{m.title()} Vibes" not in existing_titles]
                    if alternatives:
                        p["label"] = alternatives[0]
                        proposed_title = f"{alternatives[0].title()} Vibes"
                elif p["type"] == "theme":
                    alternatives = [t for t in THEMES if f"{t.title()} Stories" not in existing_titles]
                    if alternatives:
                        p["label"] = alternatives[0]
                        proposed_title = f"{alternatives[0].title()} Stories"
                elif p["type"] == "fusion":
                    alternatives = [f for f in FUSIONS if f.title() not in existing_titles]
                    if alternatives:
                        p["label"] = alternatives[0]
                        proposed_title = alternatives[0].title()
                
                logger.info(f"Using alternative: {proposed_title}")
            
            # Expand bare prompts with descriptive context for better semantic matching
            # This helps TF-IDF and embedding similarity find relevant candidates
            label = p["label"].lower()
            if p["type"] == "mood":
                # Add descriptive mood context
                rich_prompt = f"{label} mood atmosphere tone vibe feeling emotional"
            elif p["type"] == "theme":
                # Add thematic story context
                rich_prompt = f"{label} theme story narrative about exploring dealing with"
            elif p["type"] == "fusion":
                # Add genre fusion context
                rich_prompt = f"{label} genre blend combination mix hybrid"
            else:
                rich_prompt = label
            
            ai_list = AiList(
                user_id=user_id,
                type=p["type"],
                prompt_text=rich_prompt,  # Use enriched prompt for better semantic matching
                normalized_prompt=label,   # Keep original label for display/filtering
                generated_title=proposed_title,
                status="queued",
                item_limit=50,
            )
            db.add(ai_list)
            db.commit()
            db.refresh(ai_list)
            created_ids.append(ai_list.id)
            existing_titles.add(proposed_title)  # Track new titles
            generate_chat_list.delay(ai_list.id, user_id)

        notification_message = f"Generated {len(created_ids)} AI-powered recommendation lists"
        r.publish(f"notifications:{user_id}", json.dumps({
            "type": "ai_dynamic_created",
            "ids": created_ids,
            "title": "Dynamic Lists Created",
            "message": notification_message
        }))
        
        # Persist notification to database so it shows in notifications UI
        try:
            from app.services.tasks import send_user_notification
            send_user_notification.delay(user_id, notification_message)
        except Exception as e:
            logger.warning(f"Failed to queue dynamic lists created notification: {e}")
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
            logger.warning(f"AI list {ai_list_id} not found for user {user_id}")
            return
        
        logger.info(f"Refreshing AI list {ai_list_id} - type: {ai_list.type}, prompt_text: {ai_list.prompt_text}")
        
        # Get existing titles to avoid duplicates
        existing_lists = db.query(AiList).filter(
            AiList.user_id == user_id,
            AiList.id != ai_list_id,
            AiList.type.in_(['mood', 'theme', 'fusion'])
        ).all()
        existing_titles = {lst.generated_title or lst.prompt_text for lst in existing_lists}
        logger.info(f"Existing titles to avoid during rotation: {existing_titles}")
        
        # For mood/theme/fusion lists, rotate to a new random selection
        # Chat lists keep their original prompt
        old_prompt = ai_list.prompt_text
        if ai_list.type == "mood":
            logger.info(f"Processing mood list rotation - old_prompt: '{old_prompt}'")
            # Pick a different mood than current and not in existing titles
            available_moods = [
                m for m in MOODS 
                if m.lower() != (old_prompt or "").lower() 
                and f"{m.title()} Vibes" not in existing_titles
            ]
            logger.info(f"Available moods for rotation: {len(available_moods)} options")
            if available_moods:
                new_mood = random.choice(available_moods)
                ai_list.prompt_text = new_mood
                ai_list.normalized_prompt = new_mood
                ai_list.generated_title = f"{new_mood.title()} Vibes"
                logger.info(f"Rotating mood list from '{old_prompt}' to '{new_mood}'")
            else:
                logger.warning(f"No unique mood available for rotation, keeping current")
        elif ai_list.type == "theme":
            logger.info(f"Processing theme list rotation - old_prompt: '{old_prompt}'")
            # Pick a different theme than current and not in existing titles
            available_themes = [
                t for t in THEMES 
                if t.lower() != (old_prompt or "").lower() 
                and f"{t.title()} Stories" not in existing_titles
            ]
            logger.info(f"Available themes for rotation: {len(available_themes)} options")
            if available_themes:
                new_theme = random.choice(available_themes)
                ai_list.prompt_text = new_theme
                ai_list.normalized_prompt = new_theme
                ai_list.generated_title = f"{new_theme.title()} Stories"
                logger.info(f"Rotating theme list from '{old_prompt}' to '{new_theme}'")
            else:
                logger.warning(f"No unique theme available for rotation, keeping current")
        elif ai_list.type == "fusion":
            logger.info(f"Processing fusion list rotation - old_prompt: '{old_prompt}'")
            # Pick a different fusion than current and not in existing titles
            available_fusions = [
                f for f in FUSIONS 
                if f.lower() != (old_prompt or "").lower() 
                and f.title() not in existing_titles
            ]
            logger.info(f"Available fusions for rotation: {len(available_fusions)} options")
            if available_fusions:
                new_fusion = random.choice(available_fusions)
                ai_list.prompt_text = new_fusion
                ai_list.normalized_prompt = new_fusion
                ai_list.generated_title = new_fusion.title()
                logger.info(f"Rotating fusion list from '{old_prompt}' to '{new_fusion}'")
            else:
                logger.warning(f"No unique fusion available for rotation, keeping current")
        # else: chat lists keep their original prompt_text unchanged
        
        # For mood/theme/fusion lists being rotated, delete old Trakt list so a fresh one is created
        if ai_list.type in ['mood', 'theme', 'fusion'] and ai_list.trakt_list_id:
            try:
                from app.services.trakt_list_sync import delete_trakt_list_for_ai_list
                import asyncio
                asyncio.run(delete_trakt_list_for_ai_list(ai_list, user_id))
                ai_list.trakt_list_id = None  # Clear so generate_chat_list creates new
                db.commit()
                logger.info(f"Deleted old Trakt list for rotated {ai_list.type} list {ai_list.id}")
            except Exception as e:
                logger.warning(f"Failed to delete old Trakt list during rotation: {e}")
        
        ai_list.status = "queued"
        db.commit()
        generate_chat_list.delay(ai_list.id, user_id)
        notification_message = f"Regenerating recommendations for '{ai_list.generated_title or ai_list.prompt_text[:50]}'"
        r.publish(f"notifications:{user_id}", json.dumps({
            "type": "ai_list_refresh",
            "ai_list_id": ai_list.id,
            "title": "Refreshing AI List",
            "message": notification_message
        }))
        
        # Persist notification to database so it shows in notifications UI
        try:
            from app.services.tasks import send_user_notification
            send_user_notification.delay(user_id, notification_message)
        except Exception as e:
            logger.warning(f"Failed to queue AI list refresh notification: {e}")
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


@celery_app.task(name="refresh_dynamic_lists", bind=True)
def refresh_dynamic_lists(self, user_id: int = 1):
    """Auto-refresh all mood/theme/fusion lists (not chat lists) for a user.
    Triggered by Celery beat every 2 hours."""
    db = SessionLocal()
    try:
        # Get all dynamic lists (mood, theme, fusion) - exclude chat lists
        dynamic_lists = db.query(AiList).filter(
            AiList.user_id == user_id,
            AiList.type.in_(['mood', 'theme', 'fusion'])
        ).all()
        
        logger.info(f"Auto-refreshing {len(dynamic_lists)} dynamic lists for user {user_id}")
        
        for lst in dynamic_lists:
            logger.info(f"Queueing refresh for dynamic list {lst.id} ({lst.type}): {lst.generated_title or lst.prompt_text}")
            refresh_ai_list.delay(str(lst.id), user_id)
        
        return {"refreshed": len(dynamic_lists)}
    finally:
        db.close()


@celery_app.task(name="rebuild_faiss_index", bind=True, max_retries=3)
def rebuild_faiss_index(self):
    """Rebuild FAISS HNSW index from persisted DB embeddings using trakt_id.
    
    Strategy:
    - Load existing HNSW index if it exists (for incremental updates)
    - Stream all embeddings with trakt_id from DB in chunks
    - Add incrementally to HNSW index (no training needed)
    - Persist mapping (faiss rowId -> trakt_id) and index to /data/ai
    
    Falls back to full HNSW rebuild if index doesn't exist.
    """
    db = SessionLocal()
    try:
        from app.services.ai_engine.faiss_index import (
            deserialize_embedding, train_build_hnsw, add_to_index, 
            DATA_DIR, INDEX_FILE, MAPPING_FILE
        )
        from app.services.ai_engine.metadata_processing import compose_text_for_embedding
        import faiss
        
        embedder = EmbeddingService()

        # 1) Count total embedded rows (trakt_id optional)
        total = db.execute(text(
            "SELECT COUNT(*) FROM persistent_candidates WHERE active=true AND embedding IS NOT NULL"
        )).scalar() or 0

        if total == 0:
            logger.warning("[FAISS] No candidates with embeddings and trakt_id found. Nothing to rebuild.")
            return {"status": "skipped", "reason": "no_embeddings", "count": 0}
        
        logger.info(f"[FAISS] Starting HNSW index rebuild/update with {total} candidates")

        # 2) Check if we can do incremental update
        index_exists = INDEX_FILE.exists() and MAPPING_FILE.exists()
        
        if index_exists:
            # Load existing mapping to find what's already indexed
            with open(MAPPING_FILE) as f:
                existing_mapping = json.load(f)
            existing_trakt_ids = set(int(v) for v in existing_mapping.values())
            logger.info(f"[FAISS] Existing index has {len(existing_trakt_ids)} vectors")
            
            # Find candidates NOT in index yet
            missing_rows = db.execute(text(
                """
                SELECT COALESCE(trakt_id, tmdb_id) AS trakt_id, embedding
                FROM persistent_candidates
                WHERE active=true 
                  AND embedding IS NOT NULL 
                  AND COALESCE(trakt_id, tmdb_id) NOT IN :existing_ids
                ORDER BY popularity DESC
                """
            ), {"existing_ids": tuple(existing_trakt_ids) if existing_trakt_ids else (-1,)}).fetchall()
            
            if not missing_rows:
                logger.info("[FAISS] Index is up-to-date, no new embeddings to add")
                return {"status": "up_to_date", "count": len(existing_trakt_ids)}
            
            logger.info(f"[FAISS] Found {len(missing_rows)} new embeddings to add incrementally")
            
            # Add incrementally in batches
            batch_size = 10000
            added = 0
            for i in range(0, len(missing_rows), batch_size):
                batch = missing_rows[i:i+batch_size]
                vecs = []
                ids = []
                for trakt_id, blob in batch:
                    try:
                        vecs.append(deserialize_embedding(bytes(blob)))
                        ids.append(int(trakt_id))
                    except Exception as e:
                        logger.warning(f"Failed to deserialize embedding for trakt_id={trakt_id}: {e}")
                        continue
                
                if vecs:
                    embeddings_array = np.array(vecs, dtype=np.float32)
                    dim = embeddings_array.shape[1]
                    success = add_to_index(embeddings_array, ids, dim)
                    if success:
                        added += len(ids)
                        logger.info(f"[FAISS] Added {added}/{len(missing_rows)} new vectors...")
                    else:
                        logger.warning("[FAISS] Incremental add failed, will do full rebuild")
                        index_exists = False
                        break
            
            if index_exists:  # Incremental update succeeded
                get_redis_sync().publish("system:ai", json.dumps({
                    "type": "faiss_updated", 
                    "added": added, 
                    "total": len(existing_trakt_ids) + added
                }))
                logger.info(f"[FAISS] ✅ Successfully added {added} new vectors incrementally")
                return {"status": "incremental_update", "added": added, "total": len(existing_trakt_ids) + added}
        
        # 3) Full rebuild if index doesn't exist or incremental failed
        logger.info("[FAISS] Performing full HNSW rebuild...")
        
        # Fetch ALL embeddings in chunks (trakt_id optional)
        all_embeddings = []
        all_trakt_ids = []
        chunk_size = 50000
        offset = 0
        
        while offset < total:
            rows = db.execute(text(
                """
                SELECT COALESCE(trakt_id, tmdb_id) AS trakt_id, embedding
                FROM persistent_candidates
                WHERE active=true 
                  AND embedding IS NOT NULL 
                ORDER BY id
                OFFSET :off LIMIT :lim
                """
            ), {"off": offset, "lim": chunk_size}).fetchall()
            
            if not rows:
                break
            
            for trakt_id, blob in rows:
                try:
                    all_embeddings.append(deserialize_embedding(bytes(blob)))
                    all_trakt_ids.append(int(trakt_id))
                except Exception as e:
                    logger.warning(f"Failed to deserialize embedding for trakt_id={trakt_id}: {e}")
                    continue
            
            offset += len(rows)
            if offset % 100000 == 0:
                logger.info(f"[FAISS] Loaded {offset}/{total} embeddings...")
        
        if not all_embeddings:
            logger.error("[FAISS] No valid embeddings loaded")
            return {"status": "error", "reason": "no_valid_embeddings", "count": 0}
        
        # Build HNSW index from all embeddings
        embeddings_array = np.array(all_embeddings, dtype=np.float32)
        dim = embeddings_array.shape[1]
        
        logger.info(f"[FAISS] Building HNSW index with {len(all_trakt_ids)} vectors (dim={dim})...")
        train_build_hnsw(embeddings_array, all_trakt_ids, dim)
        
        get_redis_sync().publish("system:ai", json.dumps({
            "type": "faiss_rebuilt", 
            "count": len(all_trakt_ids), 
            "algorithm": "HNSW"
        }))
        
        logger.info(f"[FAISS] ✅ HNSW index rebuilt successfully with {len(all_trakt_ids)} vectors!")
        return {"status": "full_rebuild", "count": len(all_trakt_ids), "algorithm": "HNSW"}
        
    except Exception as e:
        logger.exception(f"Failed to rebuild FAISS index: {e}")
        raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))
    finally:
        db.close()
        gc.collect()


@celery_app.task(name="generate_embeddings_for_new_items", bind=True, max_retries=3)
def generate_embeddings_for_new_items(self):
    """Generate embeddings for new candidates that don't have embeddings yet.
    
    This is specifically for newly ingested content from candidate_ingestion.
    Processes items without embeddings (trakt_id optional), then adds them to FAISS index.
    """
    db = SessionLocal()
    BATCH_SIZE = 64
    
    try:
        from app.services.ai_engine.faiss_index import serialize_embedding, add_to_index
        
        # Count candidates needing embeddings
        total = db.execute(text(
            """
            SELECT COUNT(*)
            FROM persistent_candidates
            WHERE active=true AND embedding IS NULL
            """
        )).scalar() or 0
        
        if total == 0:
            logger.info("[EMBEDDINGS] No new candidates need embeddings")
            return
        
        logger.info(f"[EMBEDDINGS] Generating embeddings for {total} new candidates")
        embedder = EmbeddingService()
        offset = 0
        total_embedded = 0
        
        while offset < total:
            # Fetch batch of candidates without embeddings
            rows = db.execute(text(
                '''
                SELECT id, trakt_id, tmdb_id, media_type, title, original_title, overview, genres, keywords, "cast",
                       production_companies, vote_average, vote_count, popularity, year, language, runtime,
                       tagline, homepage, budget, revenue, production_countries, spoken_languages, networks,
                       created_by, number_of_seasons, number_of_episodes, episode_run_time, first_air_date,
                       last_air_date, in_production, status
                FROM persistent_candidates
                WHERE active=true AND embedding IS NULL
                ORDER BY popularity DESC
                OFFSET :off LIMIT :lim
                '''
            ), {"off": int(offset), "lim": BATCH_SIZE}).fetchall()
            
            if not rows:
                break
            
            # Compose candidate dictionaries
            cands = []
            for r in rows:
                try:
                    (rid, trakt_id, tmdb_id, media_type, title, original_title, overview, genres, keywords, cast, prod_comp,
                     vote_average, vote_count, popularity, year, language, runtime, tagline, homepage, budget, revenue,
                     prod_countries, spoken_languages, networks, created_by, number_of_seasons, number_of_episodes,
                     episode_run_time, first_air_date, last_air_date, in_production, status) = r
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
                    cands.append(c)
                except Exception as e:
                    logger.warning(f"Failed to unpack candidate row: {e}")
                    continue
            
            # Generate embeddings for batch
            texts = [compose_text_for_embedding(c) for c in cands]
            try:
                embs = embedder.encode_texts(texts, batch_size=BATCH_SIZE).astype(np.float32)
            except Exception as e:
                logger.error(f"Embedding generation failed: {e}")
                break
            
            # Persist embeddings to database
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
            
            # Add to FAISS index using trakt_id if present, else tmdb_id
            any_ids = []
            for c in cands:
                try:
                    val = c.get('trakt_id') if c.get('trakt_id') is not None else c.get('tmdb_id')
                    any_ids.append(int(val))
                except Exception:
                    continue
            try:
                ok = add_to_index(embs, any_ids, embs.shape[1])
                if ok:
                    logger.info(f"[EMBEDDINGS] Added {len(any_ids)} vectors to FAISS index")
                else:
                    logger.warning("[EMBEDDINGS] FAISS index not found, will need manual rebuild")
            except Exception as e:
                logger.error(f"FAISS index update failed: {e}")
            
            total_embedded += embedded
            offset += len(cands)
            logger.info(f"[EMBEDDINGS] Processed {min(offset, total)}/{total} (embedded: {total_embedded})")
            gc.collect()
        
        logger.info(f"[EMBEDDINGS] Complete! Generated {total_embedded} embeddings for new candidates")
        
    except Exception as e:
        logger.exception(f"Failed to generate embeddings for new items: {e}")
        raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))
    finally:
        db.close()
        gc.collect()
