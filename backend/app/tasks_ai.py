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
        seasonal_words = (parsed.get("filters", {}).get("seasonal") or [])
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
        
        # Log ALL extracted filters for visibility and debugging
        extracted_actors = parsed.get("filters", {}).get("actors") or []
        extracted_studios = parsed.get("filters", {}).get("studios") or []
        extracted_years = parsed.get("filters", {}).get("years") or []
        extracted_year_range = parsed.get("filters", {}).get("year_range") or []
        extracted_obscurity = parsed.get("filters", {}).get("obscurity")
        extracted_rating = parsed.get("filters", {}).get("rating_cmp")
        extracted_adult = parsed.get("filters", {}).get("adult")
        
        logger.info(
            f"[{ai_list_id}] Extracted filters from prompt: "
            f"seeds={seeds}, tone={tone_words}, seasonal={seasonal_words}, genres={extracted_genres}, "
            f"languages={extracted_languages}, media_type={media_type_filter}, negative={negative_cues}, "
            f"networks={extracted_networks}, countries={extracted_countries}, "
            f"creators={extracted_creators}, directors={extracted_directors}, "
            f"actors={extracted_actors}, studios={extracted_studios}, "
            f"years={extracted_years}, year_range={extracted_year_range}, "
            f"obscurity={extracted_obscurity}, rating={extracted_rating}, adult={extracted_adult}"
        )
        
        # Build enriched query text incorporating all extracted metadata
        # This makes FAISS return semantically targeted candidates from the start
        # Mirror the rich metadata structure used in compose_text_for_embedding
        
        # For mood/theme/fusion lists, LEAD with the mood for maximum FAISS impact
        if ai_list.type in ['mood', 'theme', 'fusion'] and tone_words:
            tone_text = " ".join(tone_words[:8])
            # Mood-first structure: emphasize the emotional/atmospheric dimension
            query_parts = [
                f"Mood: {tone_text}",
                f"Atmosphere: {tone_text}",
                f"{tone_text} vibe",
                normalized,
            ]
            # Add mood as descriptive phrases for richer semantic matching
            mood_descriptors = " ".join([f"{word} feeling and {word} atmosphere" for word in tone_words[:3]])
            query_parts.append(mood_descriptors)
            logger.info(f"[{ai_list_id}] MOOD/THEME list - Leading FAISS query with mood: {tone_text}")
        elif ai_list.type in ['mood', 'theme', 'fusion']:
            # Even if no explicit tone words, use the prompt itself which should contain mood descriptors
            # For preset mood/theme lists, the prompt_text itself IS the mood (e.g., "dark", "uplifting")
            query_parts = [
                f"Mood: {normalized}",
                f"Atmosphere: {normalized}",
                f"{normalized} feeling",
                f"{normalized} vibe and tone",
                f"{normalized} mood movies and shows",
                normalized,
            ]
            # Also extract mood words directly from the prompt for additional emphasis
            from app.services.ai_engine.classifiers import detect_tone_keywords
            direct_mood_keywords = detect_tone_keywords(normalized)
            if direct_mood_keywords:
                mood_emphasis = " ".join(direct_mood_keywords[:5])
                query_parts.insert(0, f"Emotional tone: {mood_emphasis}")
                logger.info(f"[{ai_list_id}] MOOD/THEME list - Detected direct moods: {direct_mood_keywords}")
            logger.info(f"[{ai_list_id}] MOOD/THEME list - Using prompt as mood: {normalized}")
        else:
            # For chat lists, start with normalized prompt then add mood
            query_parts = [normalized]
            if tone_words:
                tone_text = " ".join(tone_words[:8])
                # Add mood in multiple forms for emphasis in embedding
                query_parts.append(f"Mood: {tone_text}")
                query_parts.append(f"Tone: {tone_text}")
                query_parts.append(f"Atmosphere: {tone_text}")
                # Repeat as descriptive adjectives for semantic richness
                mood_descriptors = " ".join([f"{word} feeling" for word in tone_words[:5]])
                query_parts.append(mood_descriptors)
                logger.info(f"[{ai_list_id}] Enhanced FAISS query with mood: {tone_text}")
            
            # Add seasonal keywords for thematic emphasis
            if seasonal_words:
                seasonal_text = " ".join(seasonal_words)
                query_parts.append(f"Theme: {seasonal_text}")
                query_parts.append(f"Holiday: {seasonal_text}")
                # Repeat for emphasis in embedding space
                seasonal_descriptors = " ".join([f"{word} themed" for word in seasonal_words])
                query_parts.append(seasonal_descriptors)
                logger.info(f"[{ai_list_id}] Enhanced FAISS query with seasonal: {seasonal_text}")
        
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
            f"|seasonal:{','.join(seasonal_words[:3])}"
            f"|neg:{','.join(negative_cues[:6])}"
            f"|genres:{','.join(extracted_genres[:5])}"
            f"|langs:{','.join(extracted_languages[:3])}"
        )
        cached = r.get(cache_key)
        embedder = EmbeddingService()
        index, mapping = load_index()
        small_index = False
        try:
            small_index = len(mapping) < 1000
            if small_index:
                logger.warning(f"[{ai_list_id}] FAISS index appears small (size={len(mapping)}). Will use DB fallback if needed.")
        except Exception:
            small_index = False
        
        # Use enriched query text for embedding instead of just base prompt
        # This incorporates genres, languages, mood, and seed metadata into semantic search
        logger.info(f"[{ai_list_id}] Enriched query for FAISS: {enriched_query[:200]}")
        
        # --- FAISS fallback logic: up to 3 attempts with increasing top_k ---
        cache_key = (
            f"ai:prompt_cache_v2:{normalized}"
            f"|seeds:{','.join(seeds[:5])}"
            f"|tone:{','.join(tone_words[:6])}"
            f"|seasonal:{','.join(seasonal_words[:3])}"
            f"|neg:{','.join(negative_cues[:6])}"
            f"|genres:{','.join(extracted_genres[:5])}"
            f"|langs:{','.join(extracted_languages[:3])}"
        )
        embedder = EmbeddingService()
        index, mapping = load_index()
        logger.info(f"[{ai_list_id}] Enriched query for FAISS: {enriched_query[:200]}")
        try:
            query_emb = embedder.encode_text(enriched_query)
        except Exception as e:
            logger.warning(f"Embedding enriched query failed: {e}, falling back to base prompt")
            query_emb = embedder.encode_text(normalized)
        if negative_cues:
            try:
                neg_text = ", ".join(negative_cues[:6])
                neg_vec = embedder.encode_text(f"avoid: {neg_text}")
                import numpy as _np
                q = query_emb.astype(_np.float32)
                n = neg_vec.astype(_np.float32)
                alpha = float(_np.dot(q, n))
                q_adj = q - 0.25 * alpha * n
                q_adj = q_adj / (float((q_adj ** 2).sum()) ** 0.5 + 1e-8)
                query_emb = q_adj.astype(np.float16)
            except Exception as e:
                logger.debug(f"Negative cue embedding adjustment skipped: {e}")

        # === BGE + FAISS HYBRID SEARCH ===
        # Try BGE index first if enabled, then supplement with FAISS
        bge_ids = []
        bge_scores_dict = {}
        _bge_enabled = False
        try:
            from app.core.config import settings
            _bge_enabled = bool(getattr(settings, 'ai_bge_index_enabled', False))
            if not _bge_enabled:
                _val = r.get('settings:global:ai_bge_index_enabled')
                _bge_enabled = (_val == b'true' or _val == 'true')
        except Exception:
            pass
        
        if _bge_enabled:
            try:
                from app.services.ai_engine.bge_index import BGEIndex, BGEEmbedder
                logger.info(f"[{ai_list_id}] BGE index enabled, attempting BGE search first")
                
                # Encode query with BGE embedder
                bge_embedder = BGEEmbedder()
                bge_query_emb = bge_embedder.embed([enriched_query])
                
                # Search BGE index
                bge_idx = BGEIndex(settings.ai_bge_index_dir)
                indices_list, scores_list = bge_idx.search(bge_query_emb, top_k=60000)  # Get more candidates from BGE
                
                # Extract IDs and scores (first query only since we passed single query)
                if indices_list and scores_list:
                    indices = indices_list[0]  # First (and only) query results
                    scores = scores_list[0]
                    for item_id, score in zip(indices, scores):
                        try:
                            if item_id >= 0:  # FAISS uses -1 for empty slots
                                bge_ids.append(int(item_id))
                                bge_scores_dict[int(item_id)] = float(score)
                        except Exception:
                            continue
                
                logger.info(f"[{ai_list_id}] BGE index returned {len(bge_ids)} candidates")
                del bge_embedder, bge_idx, bge_query_emb
                gc.collect()
            except Exception as bge_err:
                logger.warning(f"[{ai_list_id}] BGE search failed: {bge_err}, falling back to FAISS only")
                _bge_enabled = False

        # Try up to 3 FAISS attempts with increasing top_k (always run as backup/supplement)
        faiss_attempts = [40000, 80000, 120000]
        topk_ids = []
        faiss_scores_dict = {}
        rows = []
        scored = []
        # Iterate FAISS attempts
        for attempt, top_k in enumerate(faiss_attempts, 1):
            ids, faiss_scores = search_index(index, query_emb, top_k=top_k)
            faiss_ids = []
            for idx, internal_id in enumerate(ids):
                if int(internal_id) in mapping:
                    mapped_id = mapping.get(int(internal_id))
                    if mapped_id is None:
                        continue
                    try:
                        mapped_id_int = int(mapped_id)
                    except Exception:
                        continue
                    faiss_ids.append(mapped_id_int)
                    try:
                        faiss_scores_dict[mapped_id_int] = float(faiss_scores[idx])
                    except Exception:
                        faiss_scores_dict[mapped_id_int] = 0.0
            logger.info(f"[{ai_list_id}] FAISS attempt {attempt} (top_k={top_k}) returned {len(faiss_ids)} candidate IDs")
            
            # === MERGE BGE + FAISS RESULTS ===
            # Blend scores with BGE primary (70%), FAISS secondary (30%)
            # This ensures we get the best of both: BGE's multi-vector sophistication + FAISS's broad coverage
            merged_scores = {}
            topk_ids = []
            
            if _bge_enabled and bge_ids:
                # Normalize scores to 0-1 range
                max_bge = max(bge_scores_dict.values()) if bge_scores_dict else 1.0
                max_faiss = max(faiss_scores_dict.values()) if faiss_scores_dict else 1.0
                
                # Add BGE candidates with primary weight
                for item_id in bge_ids:
                    bge_score = bge_scores_dict.get(item_id, 0.0) / max_bge if max_bge > 0 else 0.0
                    faiss_score = faiss_scores_dict.get(item_id, 0.0) / max_faiss if max_faiss > 0 else 0.0
                    
                    # Blended score: BGE 70%, FAISS 30%
                    if faiss_score > 0:
                        merged_scores[item_id] = 0.7 * bge_score + 0.3 * faiss_score
                    else:
                        merged_scores[item_id] = 0.7 * bge_score  # Pure BGE if not in FAISS
                
                # Add FAISS-only candidates with lower weight
                for item_id in faiss_ids:
                    if item_id not in merged_scores:
                        faiss_score = faiss_scores_dict.get(item_id, 0.0) / max_faiss if max_faiss > 0 else 0.0
                        merged_scores[item_id] = 0.3 * faiss_score  # FAISS-only gets 30% weight
                
                # Sort by merged score and take top candidates
                topk_ids = sorted(merged_scores.keys(), key=lambda x: merged_scores[x], reverse=True)
                logger.info(f"[{ai_list_id}] Merged BGE+FAISS: {len(topk_ids)} total candidates (BGE: {len(bge_ids)}, FAISS: {len(faiss_ids)})")
            else:
                # Pure FAISS fallback
                topk_ids = faiss_ids
                merged_scores = faiss_scores_dict
                logger.info(f"[{ai_list_id}] Using FAISS-only: {len(topk_ids)} candidates")
            # DB fetch as before
            filters = parsed.get("filters", {}) or {}
            genres = [g.lower() for g in (filters.get("genres") or [])]
            genre_mode = (filters.get("genre_mode") or filters.get("genres_mode") or "any").lower()
            languages = [l.lower() for l in (filters.get("languages") or [])]
            media_types = filters.get("media_types") or []
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
            where_clauses = [
                "(tmdb_id = ANY(:ids) OR trakt_id = ANY(:ids))",
                "active = true",
                "poster_path IS NOT NULL",  # Only include items with posters
            ]
            params = {}
            if media_types:
                where_clauses.append("media_type = ANY(:media_types)")
                params["media_types"] = media_types
            if languages:
                where_clauses.append("LOWER(COALESCE(language, '')) = ANY(:languages)")
                params["languages"] = languages
            if isinstance(year_from, int):
                where_clauses.append("COALESCE(year, 0) >= :year_from")
                params["year_from"] = int(year_from)
            if isinstance(year_to, int):
                where_clauses.append("COALESCE(year, 9999) <= :year_to")
                params["year_to"] = int(year_to)
            if obscurity in ("very_obscure", "very-obscure"):
                where_clauses.append("COALESCE(obscurity_score, 0) >= 0.8")
            elif obscurity in ("obscure",):
                where_clauses.append("COALESCE(obscurity_score, 0) >= 0.6")
            elif obscurity in ("popular", "mainstream"):
                where_clauses.append("COALESCE(mainstream_score, 0) >= 0.6")
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
            if networks:
                net_like = []
                for i, n in enumerate(networks):
                    key = f"net{i}"
                    net_like.append(f"LOWER(COALESCE(networks, '')) LIKE :{key}")
                    params[key] = f"%{n}%"
                where_clauses.append("(" + " OR ".join(net_like) + ")")
            if countries:
                ctry_like = []
                for i, c in enumerate(countries):
                    key = f"ctry{i}"
                    ctry_like.append(f"LOWER(COALESCE(production_countries, '')) LIKE :{key}")
                    params[key] = f"%{c}%"
                where_clauses.append("(" + " OR ".join(ctry_like) + ")")
            if creators:
                cr_like = []
                for i, c in enumerate(creators):
                    key = f"cr{i}"
                    cr_like.append(f"LOWER(COALESCE(created_by, '')) LIKE :{key}")
                    params[key] = f"%{c}%"
                where_clauses.append("(" + " OR ".join(cr_like) + ")")
            if directors:
                dir_like_or_groups = []
                for i, d in enumerate(directors):
                    key = f"dir{i}"
                    dir_like_or_groups.append(f"LOWER(COALESCE(created_by, '')) LIKE :{key} OR LOWER(COALESCE(\"cast\", '')) LIKE :{key}")
                    params[key] = f"%{d}%"
                where_clauses.append("(" + " OR ".join(dir_like_or_groups) + ")")
            where_sql = " AND ".join(where_clauses)
            base_sql = f"SELECT * FROM persistent_candidates WHERE {where_sql}"
            id_list = topk_ids[:top_k]
            CHUNK = 1000
            try:
                desired = int(ai_list.item_limit or 50)
            except Exception:
                desired = 50
            MAX_PREFILTERED = 6000
            rows = []
            for start in range(0, len(id_list), CHUNK):
                if len(rows) >= MAX_PREFILTERED:
                    break
                chunk_ids = id_list[start:start+CHUNK]
                params_chunk = dict(params)
                params_chunk["ids"] = chunk_ids
                res = db.execute(text(base_sql), params_chunk)
                cols = res.keys()
                for row in res:
                    row_dict = dict(zip(cols, row))
                    try:
                        rid_trakt = row_dict.get("trakt_id")
                        rid_tmdb = row_dict.get("tmdb_id")
                        score = None
                        # Try merged scores first (BGE+FAISS blend), then individual scores
                        if rid_trakt is not None and int(rid_trakt) in merged_scores:
                            score = merged_scores.get(int(rid_trakt))
                        elif rid_tmdb is not None and int(rid_tmdb) in merged_scores:
                            score = merged_scores.get(int(rid_tmdb))
                        elif rid_trakt is not None and int(rid_trakt) in faiss_scores_dict:
                            score = faiss_scores_dict.get(int(rid_trakt))
                        elif rid_tmdb is not None and int(rid_tmdb) in faiss_scores_dict:
                            score = faiss_scores_dict.get(int(rid_tmdb))
                        
                        if score is not None:
                            row_dict["_faiss_score"] = float(score)
                            # Mark if from BGE for transparency
                            if _bge_enabled and (rid_trakt in bge_scores_dict or rid_tmdb in bge_scores_dict):
                                row_dict["_from_bge"] = True
                            row_dict["_from_faiss"] = True
                    except Exception:
                        pass
                    rows.append(row_dict)
                if len(rows) > MAX_PREFILTERED:
                    rows = rows[:MAX_PREFILTERED]
                    break
            # If FAISS-targeted fetch is too small, relax DB-side filters (keep only ID and active/media_type)
            if len(rows) < max(20, int((ai_list.item_limit or 50) * 0.4)):
                try:
                    relaxed_where = [
                        "(tmdb_id = ANY(:ids) OR trakt_id = ANY(:ids))",
                        "active = true",
                        "poster_path IS NOT NULL",  # Only include items with posters
                    ]
                    relaxed_params = {"ids": id_list[:top_k]}
                    # Preserve media_type if explicitly requested
                    if media_types:
                        relaxed_where.append("media_type = ANY(:media_types)")
                        relaxed_params["media_types"] = media_types
                    relaxed_sql = f"SELECT * FROM persistent_candidates WHERE {' AND '.join(relaxed_where)}"
                    res_rel = db.execute(text(relaxed_sql), relaxed_params)
                    cols_rel = res_rel.keys()
                    relaxed_rows = []
                    for r2 in res_rel:
                        d2 = dict(zip(cols_rel, r2))
                        try:
                            rid_trakt = d2.get("trakt_id")
                            rid_tmdb = d2.get("tmdb_id")
                            s = None
                            # Try merged scores first, then individual
                            if rid_trakt is not None and int(rid_trakt) in merged_scores:
                                s = merged_scores.get(int(rid_trakt))
                            elif rid_tmdb is not None and int(rid_tmdb) in merged_scores:
                                s = merged_scores.get(int(rid_tmdb))
                            elif rid_trakt is not None and int(rid_trakt) in faiss_scores_dict:
                                s = faiss_scores_dict.get(int(rid_trakt))
                            elif rid_tmdb is not None and int(rid_tmdb) in faiss_scores_dict:
                                s = faiss_scores_dict.get(int(rid_tmdb))
                            if s is not None:
                                d2["_faiss_score"] = float(s)
                                if _bge_enabled and (rid_trakt in bge_scores_dict or rid_tmdb in bge_scores_dict):
                                    d2["_from_bge"] = True
                                d2["_from_faiss"] = True
                        except Exception:
                            pass
                        relaxed_rows.append(d2)
                    # Sort relaxed rows by FAISS position
                    rank = {val: idx for idx, val in enumerate(id_list)}
                    def _pos2(c):
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
                    relaxed_rows.sort(key=_pos2)
                    # Only replace if it truly expands the pool
                    if len(relaxed_rows) > len(rows):
                        rows = relaxed_rows[:MAX_PREFILTERED]
                        logger.info(f"[{ai_list_id}] FAISS-targeted fallback fetch expanded pool to {len(rows)} candidates")
                except Exception as _rel_err:
                    logger.debug(f"[{ai_list_id}] Relaxed DB fetch skipped: {_rel_err}")
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
            logger.info(f"[{ai_list_id}] DB fetch (FAISS-targeted, attempt {attempt}) returned {len(rows)} candidates for scoring")
            
            # TMDB enrichment disabled - candidates should already have metadata from persistent_candidates table
            # try:
            #     from app.services.ai_engine.candidate_enricher import enrich_candidates_async
            #     rows = await enrich_candidates_async(db, rows, max_age_days=30, max_concurrent=10)
            #     logger.info(f"[{ai_list_id}] Enriched {len(rows)} candidates with TMDB metadata")
            # except Exception as e:
            #     logger.warning(f"[{ai_list_id}] Enrichment failed: {e}")
            
            # Compose texts for scoring
            texts = [compose_text_for_embedding(c) for c in rows]
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
            texts = [compose_text_for_embedding(c) for c in rows]
            # Increase candidate reduction window for dynamic lists to widen choice set
            topk_reduce_val = 400 if ai_list.type in ("mood", "theme", "fusion") else 200
            # Pass item_limit into filters so scorer can enforce hard-match quotas relative to final list size
            _filters = dict(parsed.get("filters", {}))
            try:
                _filters["item_limit"] = int(ai_list.item_limit or 50)
            except Exception:
                _filters["item_limit"] = 50
            scored = score_candidates(
                enriched_query,
                rows,
                texts,
                candidate_embeddings=cand_embs,
                query_embedding=query_emb if cand_embs is not None else None,
                filters=_filters,
                list_type=parsed.get("type", "chat"),
                topk_reduce=topk_reduce_val,
                user_id=user_id,
                watch_history=watch_history,
            )
            logger.info(f"[{ai_list_id}] Scoring (attempt {attempt}) returned {len(scored)} candidates after filtering")
            # If enough candidates, break and use this pool
            if len(scored) >= max(20, int((ai_list.item_limit or 50) * 0.6)):
                break
        # Non-FAISS pool fallback if index tiny or FAISS-targeted pool too small
        if len(scored) < max(20, int((ai_list.item_limit or 50) * 0.6)):
            need_pool_fallback = small_index or len(rows) < max(20, int((ai_list.item_limit or 50) * 0.4))
            if need_pool_fallback:
                try:
                    logger.info(f"[{ai_list_id}] Entering DB pool fallback (small_index={small_index}, faiss_rows={len(rows)})")
                    # Build a broad pool using filters (media_type, languages, genres ANY/ALL, years) and vote_count floor
                    where2 = ["active = true", "poster_path IS NOT NULL"]  # Only include items with posters
                    p2 = {}
                    # Vote floor based on obscurity intent
                    intent = (parsed.get("filters", {}).get("obscurity") or "balanced").lower()
                    if intent in ("obscure", "obscure_high", "very_obscure"):
                        min_votes = 100
                    elif intent in ("popular", "mainstream"):
                        min_votes = 800
                    else:
                        min_votes = 400
                    where2.append("COALESCE(vote_count, 0) >= :min_votes")
                    p2["min_votes"] = int(min_votes)
                    # Media type
                    if media_type_filter:
                        where2.append("media_type = :mt")
                        p2["mt"] = str(media_type_filter)
                    # Languages
                    if extracted_languages:
                        where2.append("LOWER(COALESCE(language, '')) = ANY(:languages)")
                        p2["languages"] = [l.lower() for l in extracted_languages]
                    # Years
                    if extracted_year_range and len(extracted_year_range) == 2:
                        where2.append("COALESCE(year, 0) BETWEEN :ylo AND :yhi")
                        p2["ylo"], p2["yhi"] = int(extracted_year_range[0]), int(extracted_year_range[1])
                    elif extracted_years:
                        where2.append("COALESCE(year, 0) = ANY(:years)")
                        p2["years"] = [int(y) for y in extracted_years if isinstance(y, (int, float, str)) and str(y).isdigit()]
                    # Genres
                    g2 = [str(g).lower() for g in extracted_genres]
                    if g2:
                        like_clauses = []
                        for i, g in enumerate(g2):
                            key = f"gg{i}"
                            like_clauses.append(f"LOWER(COALESCE(genres, '')) LIKE :{key}")
                            p2[key] = f"%{g}%"
                        if (parsed.get("filters", {}).get("genre_mode") or parsed.get("filters", {}).get("genres_mode") or "any").lower() == "all":
                            where2.extend(like_clauses)
                        else:
                            where2.append("(" + " OR ".join(like_clauses) + ")")
                    sql2 = f"SELECT * FROM persistent_candidates WHERE {' AND '.join(where2)} ORDER BY popularity DESC LIMIT 6000"
                    res2 = db.execute(text(sql2), p2)
                    cols2 = res2.keys()
                    pool_rows = [dict(zip(cols2, rr)) for rr in res2]
                    # Use FAISS similarities if available (map by trakt/tmdb id), else rely on TF-IDF
                    rank_map = {}
                    try:
                        rank_map = {val: idx for idx, val in enumerate(topk_ids)}
                    except Exception:
                        rank_map = {}
                    for d in pool_rows:
                        try:
                            tid = d.get("trakt_id")
                            mid = d.get("tmdb_id")
                            pos = None
                            if tid is not None and int(tid) in rank_map:
                                pos = rank_map[int(tid)]
                            elif mid is not None and int(mid) in rank_map:
                                pos = rank_map[int(mid)]
                            if pos is not None:
                                # Convert rank to a similarity proxy
                                d["_faiss_score"] = float(max(0.0, 1.0 - (pos / max(1, len(topk_ids)))))
                                d["_from_faiss"] = True
                        except Exception:
                            pass
                    texts2 = [compose_text_for_embedding(c) for c in pool_rows]
                    scored_pool = score_candidates(
                        enriched_query,
                        pool_rows,
                        texts2,
                        candidate_embeddings=None,
                        query_embedding=None,
                        filters=parsed.get("filters", {}),
                        list_type=parsed.get("type", "chat"),
                        topk_reduce=topk_reduce_val,
                        user_id=user_id,
                        watch_history=watch_history,
                    )
                    if len(scored_pool) > len(scored):
                        scored = scored_pool
                        rows = pool_rows
                        texts = texts2
                        logger.info(f"[{ai_list_id}] DB pool fallback produced {len(scored_pool)} scored candidates")
                except Exception as pool_err:
                    logger.warning(f"[{ai_list_id}] DB pool fallback failed: {pool_err}")
        # If after all attempts not enough candidates, fallback to relaxed filters
        if len(scored) < max(20, int((ai_list.item_limit or 50) * 0.6)):
            try:
                with managed_memory("ai_scoring_relaxed"):
                    # Keep core vote_count floor by supplying obscurity intent; preserve media_type if provided
                    base_relaxed_filters = {}
                    try:
                        base_relaxed_filters["obscurity"] = parsed.get("filters", {}).get("obscurity") or "balanced"
                        if media_type_filter:
                            base_relaxed_filters["media_type"] = media_type_filter
                    except Exception:
                        pass
                    relaxed = score_candidates(
                        enriched_query,
                        rows,
                        texts,
                        candidate_embeddings=cand_embs,
                        query_embedding=query_emb if cand_embs is not None else None,
                        filters=base_relaxed_filters,
                        list_type=parsed.get("type", "chat"),
                        topk_reduce=topk_reduce_val,
                        user_id=user_id,
                        watch_history=watch_history,
                    )
                if len(relaxed) > len(scored):
                    try:
                        logger.info(f"[{ai_list_id}] Using relaxed scoring pool ({len(relaxed)} > {len(scored)})")
                    except Exception:
                        pass
                    scored = relaxed
            except Exception:
                pass
        
        logger.info(f"[{ai_list_id}] Final scoring returned {len(scored)} candidates after all attempts")
        
        # Build MMR vectors for diversification
        try:
            with managed_memory("ai_mmr_vectors"):
                from sklearn.feature_extraction.text import TfidfVectorizer
                vec = TfidfVectorizer(max_features=2048)
                mat = vec.fit_transform(texts)
                mmr_vecs = mat.toarray().astype(np.float32)
        except Exception:
            mmr_vecs = np.zeros((len(texts), 32), dtype=np.float32)

        diversified = maximal_marginal_relevance(scored, mmr_vecs, top_k=min(ai_list.item_limit or 50, 50))

        # Cross-list de-duplication: for dynamic AI lists (mood/theme/fusion), avoid items
        # that already appear in the user's other AI lists of these types to increase variety.
        try:
            desired_k = min(ai_list.item_limit or 50, 50)
            if ai_list.type in ("mood", "theme", "fusion"):
                # Gather items from other AI lists of the same user (excluding this list)
                other_lists_q = db.query(AiList.id).filter(
                    AiList.user_id == user_id,
                    AiList.id != ai_list.id,
                    AiList.type.in_(["mood", "theme", "fusion"]),
                )
                other_list_ids = [row[0] for row in other_lists_q.all()]
                exclude_trakt: set[int] = set()
                exclude_tmdb: set[int] = set()
                if other_list_ids:
                    other_items = db.query(AiListItem).filter(AiListItem.ai_list_id.in_(other_list_ids)).all()
                    for it in other_items:
                        try:
                            if it.trakt_id:
                                exclude_trakt.add(int(it.trakt_id))
                            if it.tmdb_id:
                                exclude_tmdb.add(int(it.tmdb_id))
                        except Exception:
                            continue
                # Build a de-duplicated selection prioritizing non-overlapping items
                deduped: list[dict] = []
                seen_trakt: set[int] = set()
                seen_tmdb: set[int] = set()
                removed_for_dedup = 0
                for cand in diversified:
                    try:
                        tid = cand.get("trakt_id")
                        mid = cand.get("tmdb_id")
                        if tid is not None:
                            tid = int(tid)
                            if tid in exclude_trakt:
                                removed_for_dedup += 1
                                continue
                            if tid in seen_trakt:
                                # Avoid duplicates within the same list
                                removed_for_dedup += 1
                                continue
                        if mid is not None:
                            mid = int(mid)
                            if mid in exclude_tmdb:
                                removed_for_dedup += 1
                                continue
                            if mid in seen_tmdb:
                                removed_for_dedup += 1
                                continue
                        deduped.append(cand)
                        if tid is not None:
                            seen_trakt.add(tid)
                        if mid is not None:
                            seen_tmdb.add(mid)
                        if len(deduped) >= desired_k:
                            break
                    except Exception:
                        # If malformed IDs, keep candidate to avoid over-filtering
                        deduped.append(cand)
                        if len(deduped) >= desired_k:
                            break

                # If we removed many and have room, backfill from the remaining scored pool
                if len(deduped) < desired_k:
                    try:
                        # Build a fast lookup of IDs already chosen
                        chosen_trakt = set(seen_trakt)
                        chosen_tmdb = set(seen_tmdb)
                        for cand in scored:
                            if len(deduped) >= desired_k:
                                break
                            try:
                                tid = cand.get("trakt_id")
                                mid = cand.get("tmdb_id")
                                if tid is not None:
                                    tid = int(tid)
                                    if tid in chosen_trakt or tid in exclude_trakt:
                                        continue
                                if mid is not None:
                                    mid = int(mid)
                                    if mid in chosen_tmdb or mid in exclude_tmdb:
                                        continue
                                # Also avoid duplicating within the same filled list
                                deduped.append(cand)
                                if tid is not None:
                                    chosen_trakt.add(tid)
                                if mid is not None:
                                    chosen_tmdb.add(mid)
                            except Exception:
                                continue
                    except Exception:
                        pass

                # Only replace diversified if dedup achieved some effect and we still have a list
                if deduped and (removed_for_dedup > 0):
                    logger.info(f"[{ai_list_id}] Cross-list de-dup removed {removed_for_dedup} items; filled {len(deduped)}/{desired_k} after backfill")
                    diversified = deduped
                # If deduped is too small (e.g., tiny pool), keep original diversified to avoid empty lists
        except Exception as _dedup_err:
            logger.debug(f"[{ai_list_id}] Cross-list de-dup skipped: {_dedup_err}")

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
                    # Prepare top candidates (limit)
                    top_candidates = diversified[: ai_list.item_limit or 50]

                    # Attempt batch resolution of missing trakt_ids for these candidates
                    unresolved = [c for c in top_candidates if not c.get("trakt_id") and c.get("tmdb_id") and c.get("media_type") in ("movie", "show")]
                    if unresolved:
                        try:
                            from app.services.trakt_id_resolver import TraktIdResolver
                            resolver = TraktIdResolver(user_id=user_id)
                            batch_pairs = [(c.get("tmdb_id"), c.get("media_type")) for c in unresolved if c.get("tmdb_id") and c.get("media_type")]
                            mappings = await resolver.get_trakt_ids_batch(batch_pairs)
                            resolved_count = 0
                            for c in unresolved:
                                key = (c.get("tmdb_id"), c.get("media_type"))
                                tid_val = mappings.get(key)
                                if tid_val:
                                    c["trakt_id"] = tid_val
                                    resolved_count += 1
                            logger.info(f"AI list {ai_list.id}: batch-resolved {resolved_count}/{len(unresolved)} Trakt IDs for publish")
                        except Exception as batch_err:
                            logger.debug(f"AI list {ai_list.id}: batch Trakt ID resolve skipped: {batch_err}")

                    # Build desired items (include tmdb_id fallback if trakt unresolved)
                    desired_items: list[dict] = []
                    trakt_present = 0
                    for cand in top_candidates:
                        mtype = cand.get("media_type") or ("movie" if str(cand.get("type", "")).lower().startswith("movie") else None)
                        if mtype not in ("movie", "show"):
                            continue
                        tid = cand.get("trakt_id")
                        tmdb_id = cand.get("tmdb_id") or (cand.get("ids", {}) if isinstance(cand.get("ids"), dict) else {}).get("tmdb")
                        if tid:
                            trakt_present += 1
                            desired_items.append({"trakt_id": int(tid), "media_type": mtype})
                        elif tmdb_id:
                            # Provide tmdb fallback when trakt missing (will allow Trakt to map and return trakt id on next sync)
                            try:
                                desired_items.append({"tmdb_id": int(tmdb_id), "media_type": mtype})
                            except Exception:
                                pass

                    if not desired_items:
                        logger.info(f"AI list {ai_list.id}: no resolvable items for Trakt sync (missing IDs) - skipping")
                    else:
                        t = TraktClient(user_id=user_id)
                        # Strategy: If we have at least 5 trakt IDs use full sync (add/remove). Otherwise additive push only.
                        use_full_sync = trakt_present >= 5
                        try:
                            if use_full_sync:
                                # Only include items with trakt IDs for sync diff logic
                                diff_items = [d for d in desired_items if "trakt_id" in d]
                                stats = await t.sync_list_items(ai_list.trakt_list_id, diff_items)
                                logger.info(f"Synced AI list {ai_list.id} to Trakt {ai_list.trakt_list_id}: {stats}")
                                # After sync, attempt additive push of any tmdb-only items
                                tmdb_only = [d for d in desired_items if "tmdb_id" in d and "trakt_id" not in d]
                                if tmdb_only:
                                    add_stats = await t.add_items_to_list(ai_list.trakt_list_id, tmdb_only)
                                    logger.info(f"Added {len(tmdb_only)} tmdb-only items to AI list {ai_list.trakt_list_id}: {add_stats}")
                            else:
                                # Low trakt coverage: push everything additively so Trakt assigns IDs
                                add_stats = await t.add_items_to_list(ai_list.trakt_list_id, desired_items)
                                logger.info(f"Added {len(desired_items)} items (mixed IDs) to AI list {ai_list.trakt_list_id}: {add_stats}")
                        except Exception as sync_err:
                            logger.warning(f"AI list {ai_list.id}: Trakt sync/add failed, attempting recreate: {sync_err}")
                            try:
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
                                    # Retry additive publish (safer when list recreated)
                                    add_stats = await t.add_items_to_list(ai_list.trakt_list_id, desired_items)
                                    logger.info(f"AI list {ai_list.id}: publish after recreate added {len(desired_items)} items: {add_stats}")
                            except Exception as recreate_err:
                                logger.warning(f"AI list {ai_list.id}: Trakt recreate+publish failed: {recreate_err}")
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
            # CRITICAL: Wrap label in quotes so parser extracts it as a phrase for hard-inclusion
            # This ensures "political satire" or "buddy cop action" are enforced as must-have cues
            label = p["label"].lower()
            if p["type"] == "mood":
                # Add descriptive mood context
                rich_prompt = f'"{label}" mood atmosphere tone vibe feeling emotional'
            elif p["type"] == "theme":
                # Add thematic story context
                rich_prompt = f'"{label}" theme story narrative about exploring dealing with'
            elif p["type"] == "fusion":
                # Add genre fusion context
                rich_prompt = f'"{label}" genre blend combination mix hybrid'
            else:
                rich_prompt = f'"{label}"'
            
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
        # Optional faiss import (wrapped) - environment may not have native module during lint/static analysis
        try:
            import faiss  # type: ignore
        except Exception:
            faiss = None
        
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


@celery_app.task(name="compress_user_history", bind=True, max_retries=3)
def compress_user_history_task(self, user_id: int = 1, force_rebuild: bool = False):
    """Celery task to compress user watch history into persona vectors.
    
    Generates:
    - Persona text summary (2-5 sentences via phi3:mini)
    - Watch vector (genre/keyword weights with recency decay)
    - Version hash for cache invalidation
    
    Args:
        user_id: User ID to process
        force_rebuild: Force rebuild even if recent cache exists
        
    Returns:
        Dict with compression results
    """
    try:
        from app.services.history_compression import compress_user_history_task as compress_func
        
        logger.info(f"[HISTORY_COMPRESSION] Starting history compression for user {user_id}")
        
        # Run async function in sync context
        result = asyncio.run(compress_func(user_id=user_id, force_rebuild=force_rebuild))
        
        logger.info(f"[HISTORY_COMPRESSION] Completed for user {user_id}: {result.get('item_count', 0)} items compressed")
        
        return {
            "status": "success",
            "user_id": user_id,
            "item_count": result.get("item_count", 0),
            "persona_length": len(result.get("persona_text", "")),
            "vector_size": len(result.get("watch_vector", {})),
            "version": result.get("version")
        }
        
    except Exception as e:
        logger.exception(f"[HISTORY_COMPRESSION] Failed for user {user_id}: {e}")
        raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))
