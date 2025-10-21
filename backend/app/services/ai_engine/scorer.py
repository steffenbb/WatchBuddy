"""
scorer.py (AI Engine)
- Score candidates with filters, TF-IDF and embedding cosine blend, list-type weights, and explanation meta.
"""
from typing import List, Dict, Any, Optional, Tuple
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import gc
import json
from .explainability import build_explanation_meta
import re
import logging

logger = logging.getLogger(__name__)

# Lightweight access to user ratings stored locally
def _load_user_ratings(user_id: int | None) -> dict[int, int]:
    if not user_id:
        return {}
    try:
        from app.core.database import SessionLocal
        from app.models import UserRating
        db = SessionLocal()
        try:
            rows = db.query(UserRating).filter(UserRating.user_id == user_id).all()
            return {r.trakt_id: r.rating for r in rows}
        finally:
            db.close()
    except Exception:
        return {}


def _get_user_timezone(user_id: int | None) -> str:
    """Get user timezone preference with Redis fallback."""
    if not user_id:
        return "UTC"
    try:
        from app.core.redis_client import redis_client
        timezone_setting = redis_client.get(f"settings:global:user_timezone")
        if timezone_setting:
            return timezone_setting.decode('utf-8') if isinstance(timezone_setting, bytes) else str(timezone_setting)
    except Exception:
        pass
    return "UTC"


def _to_bool(val: Any) -> Optional[bool]:
    if isinstance(val, bool):
        return val
    if val is None:
        return None
    s = str(val).strip().lower()
    if s in ("1", "true", "yes", "y"): return True
    if s in ("0", "false", "no", "n"): return False
    return None


def _compare_numeric(value: Optional[float], cmp_tuple: Optional[Tuple[str, float]]) -> bool:
    if cmp_tuple is None:
        return True
    if value is None:
        return False
    op, thresh = cmp_tuple
    try:
        v = float(value)
    except Exception:
        return False
    if op == ">":
        return v > thresh
    if op == ">=":
        return v >= thresh
    if op == "<":
        return v < thresh
    if op == "<=":
        return v <= thresh
    # treat '=' as close to threshold
    if op == "=":
        return abs(v - thresh) < 1e-6
    return True


def _passes_filters(c: Dict[str, Any], filters: Dict[str, Any]) -> bool:
    # Media type constraint (movie/show)
    if "media_type" in filters and filters["media_type"]:
        want = filters["media_type"].lower()
        cand = str(c.get("media_type") or c.get("type") or "").lower()
        if want in ("movie", "show"):
            # Normalize TV shows synonyms
            cand_norm = "show" if cand in ("show", "tv", "tvshow", "tv series", "series") else cand
            if cand_norm != want:
                return False
    # Genres
    if "genres" in filters and filters["genres"]:
        cand_genres = set()
        try:
            import json
            cand_genres = set(json.loads(c.get("genres") or "[]"))
        except Exception:
            if isinstance(c.get("genres"), str):
                cand_genres = set(g.strip() for g in c.get("genres").split(","))
        if not cand_genres & set(filters["genres"]):
            return False
    
    # Actors (from spaCy NER extraction) - check if any extracted actor appears in cast
    if "actors" in filters and filters["actors"]:
        cand_cast = []
        try:
            import json
            cand_cast = json.loads(c.get("cast") or "[]")
            if isinstance(cand_cast, list):
                # Normalize cast names to lowercase for comparison
                cand_cast = [str(name).lower() for name in cand_cast]
        except Exception:
            if isinstance(c.get("cast"), str):
                cand_cast = [name.strip().lower() for name in c.get("cast").split(",")]
        
        # Check if any filtered actor appears in candidate's cast
        filter_actors = [str(name).lower() for name in filters["actors"]]
        if not any(actor in " ".join(cand_cast) for actor in filter_actors):
            return False
    
    # Studios (from spaCy NER extraction) - check if any extracted studio matches production company
    if "studios" in filters and filters["studios"]:
        cand_studios = []
        try:
            import json
            cand_studios = json.loads(c.get("production_companies") or "[]")
            if isinstance(cand_studios, list):
                cand_studios = [str(name).lower() for name in cand_studios]
        except Exception:
            if isinstance(c.get("production_companies"), str):
                cand_studios = [name.strip().lower() for name in c.get("production_companies").split(",")]
        
        # Check if any filtered studio appears in candidate's studios
        filter_studios = [str(name).lower() for name in filters["studios"]]
        if not any(studio in " ".join(cand_studios) for studio in filter_studios):
            return False
    
    # Languages
    if "languages" in filters and filters["languages"]:
        if (c.get("language") or "").lower() not in [l.lower() for l in filters["languages"]]:
            return False
    # Years
    if "years" in filters and filters["years"]:
        if int(c.get("year") or 0) not in set(filters["years"]):
            return False
    # Year range
    if "year_range" in filters and filters["year_range"]:
        lo, hi = filters["year_range"][0], filters["year_range"][1]
        y = int(c.get("year") or 0)
        if not (lo <= y <= hi):
            return False
    # Adult flag (if specified)
    if "adult" in filters:
        want_adult = filters["adult"]
        cand_adult = _to_bool(c.get("adult"))
        if want_adult is True and cand_adult is False:
            return False
        if want_adult is False and cand_adult is True:
            return False
    # Original language
    if "original_language" in filters and filters["original_language"]:
        ol = (c.get("original_language") or c.get("language") or "").lower()
        if ol != str(filters["original_language"]).lower():
            return False
    # Numeric comparators as strict thresholds
    if not _compare_numeric(c.get("vote_average"), filters.get("rating_cmp")):
        return False
    if not _compare_numeric(c.get("vote_count"), filters.get("votes_cmp")):
        return False
    if not _compare_numeric(c.get("revenue"), filters.get("revenue_cmp")):
        return False
    if not _compare_numeric(c.get("budget"), filters.get("budget_cmp")):
        return False
    if not _compare_numeric(c.get("popularity"), filters.get("popularity_cmp")):
        return False
    
    # TV-specific numeric filters
    if not _compare_numeric(c.get("number_of_seasons"), filters.get("seasons_cmp")):
        return False
    if not _compare_numeric(c.get("number_of_episodes"), filters.get("episodes_cmp")):
        return False
    if not _compare_numeric(c.get("runtime"), filters.get("runtime_cmp")):
        return False
    
    # Networks (TV shows)
    if "networks" in filters and filters["networks"]:
        cand_networks = []
        try:
            import json
            cand_networks = json.loads(c.get("networks") or "[]")
            if isinstance(cand_networks, list):
                cand_networks = [str(name).lower() for name in cand_networks]
        except Exception:
            if isinstance(c.get("networks"), str):
                cand_networks = [name.strip().lower() for name in c.get("networks").split(",")]
        
        filter_networks = [str(name).lower() for name in filters["networks"]]
        if not any(network in " ".join(cand_networks) for network in filter_networks):
            return False
    
    # Creators (TV shows)
    if "creators" in filters and filters["creators"]:
        cand_creators = []
        try:
            import json
            cand_creators = json.loads(c.get("created_by") or "[]")
            if isinstance(cand_creators, list):
                cand_creators = [str(name).lower() for name in cand_creators]
        except Exception:
            if isinstance(c.get("created_by"), str):
                cand_creators = [name.strip().lower() for name in c.get("created_by").split(",")]
        
        filter_creators = [str(name).lower() for name in filters["creators"]]
        if not any(creator in " ".join(cand_creators) for creator in filter_creators):
            return False
    
    # Directors (movies/TV)
    if "directors" in filters and filters["directors"]:
        cand_director = (c.get("director") or "").lower()
        filter_directors = [str(name).lower() for name in filters["directors"]]
        if not any(director in cand_director for director in filter_directors):
            return False
    
    # Production countries
    if "countries" in filters and filters["countries"]:
        cand_countries = []
        try:
            import json
            cand_countries = json.loads(c.get("production_countries") or "[]")
            if isinstance(cand_countries, list):
                cand_countries = [str(name).upper() for name in cand_countries]
        except Exception:
            if isinstance(c.get("production_countries"), str):
                cand_countries = [name.strip().upper() for name in c.get("production_countries").split(",")]
        
        filter_countries = [str(name).upper() for name in filters["countries"]]
        if not any(country in cand_countries for country in filter_countries):
            return False
    
    # In production status (TV shows)
    if "in_production" in filters and filters["in_production"] is not None:
        want_in_production = filters["in_production"]
        cand_in_production = _to_bool(c.get("in_production"))
        if want_in_production is True and cand_in_production is False:
            return False
        if want_in_production is False and cand_in_production is True:
            return False
    
    return True


def _normalize(arr: np.ndarray) -> np.ndarray:
    if arr.size == 0:
        return arr
    mn, mx = arr.min(), arr.max()
    if mx - mn < 1e-8:
        return np.zeros_like(arr)
    return (arr - mn) / (mx - mn)


def score_candidates(
    prompt_text: str,
    candidates: List[Dict[str, Any]],
    candidate_texts: List[str],
    candidate_embeddings: Optional[np.ndarray],
    query_embedding: Optional[np.ndarray],
    filters: Dict[str, Any],
    list_type: str = "chat",
    topk_reduce: int = 200,
    user_id: Optional[int] = 1,
    watch_history: Optional[Dict[int, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    # 1) Strict filtering
    filtered: List[Tuple[int, Dict[str, Any]]] = [
        (i, c) for i, c in enumerate(candidates) if _passes_filters(c, filters)
    ]
    if not filtered:
        return []

    idxs = [i for i, _ in filtered]
    cand_subset = [candidates[i] for i in idxs]
    texts_subset = [candidate_texts[i] for i in idxs]
    # Popularity and rating normalization
    popularity = np.array([float(c.get("popularity") or 0) for c in cand_subset])
    rating = np.array([float(c.get("vote_average") or 0) for c in cand_subset])
    pop_norm = _normalize(popularity)
    rating_norm = _normalize(rating)
    novelty = 1.0 - pop_norm

    # 2) Top-K reduction with quick composite
    # quick_score = 0.6*genre_overlap (omitted here) + 0.3*pop + 0.1*rating
    quick_score = 0.3 * pop_norm + 0.1 * rating_norm
    order = np.argsort(-quick_score)
    keep = order[: min(topk_reduce, len(order))]
    cand_subset = [cand_subset[i] for i in keep]
    texts_subset = [texts_subset[i] for i in keep]
    pop_norm = pop_norm[keep]
    rating_norm = rating_norm[keep]
    novelty = novelty[keep]

    # 3) TF-IDF similarity
    vectorizer = TfidfVectorizer(max_features=5000)
    tfidf = vectorizer.fit_transform([prompt_text] + texts_subset)
    tfidf_sim = cosine_similarity(tfidf[0:1], tfidf[1:]).flatten()
    del vectorizer
    gc.collect()

    # 4) Embedding cosine similarity
    semantic_sim = np.zeros_like(tfidf_sim)
    if candidate_embeddings is not None and query_embedding is not None:
        embs = candidate_embeddings[idxs][keep]
        q = query_embedding / (np.linalg.norm(query_embedding) + 1e-8)
        embs_norm = embs / (np.linalg.norm(embs, axis=1, keepdims=True) + 1e-8)
        semantic_sim = embs_norm.dot(q)

    # 4.5) Genre overlap (Jaccard)
    def parse_genres(s: Any) -> List[str]:
        try:
            if isinstance(s, str):
                return json.loads(s) if s.startswith("[") else [g.strip() for g in s.split(",") if g.strip()]
            if isinstance(s, list):
                return s
        except Exception:
            return []
        return []

    user_genres = set(filters.get("genres", []) or [])
    genre_overlap = np.zeros(len(cand_subset), dtype=np.float32)
    if user_genres:
        for i, c in enumerate(cand_subset):
            c_genres = set(parse_genres(c.get("genres")))
            inter = len(user_genres & c_genres)
            union = len(user_genres | c_genres) or 1
            genre_overlap[i] = inter / union

    # 4.5b) Preferred genres removed (use embeddings instead)
    preferred_genre_bonus = np.zeros(len(cand_subset), dtype=np.float32)

    # 4.6) Phrase/tokens bonus: presence of quoted phrases gets a small boost
    phrases = filters.get("phrases") or []
    phrase_bonus = np.zeros(len(cand_subset), dtype=np.float32)
    if phrases:
        for i, txt in enumerate(texts_subset):
            hits = 0
            for ph in phrases:
                if ph and ph.lower() in (txt or "").lower():
                    hits += 1
            if hits:
                # normalized by number of phrases
                phrase_bonus[i] = min(1.0, hits / max(1, len(phrases)))

    # 4.6b) Preferred keywords removed (use embeddings instead)
    keyword_bonus = np.zeros(len(cand_subset), dtype=np.float32)
    
    # 4.7) Actor/Studio bonus: if actors or studios from filters match candidate metadata
    actor_studio_bonus = np.zeros(len(cand_subset), dtype=np.float32)
    filter_actors = [str(name).lower() for name in (filters.get("actors") or [])]
    filter_studios = [str(name).lower() for name in (filters.get("studios") or [])]
    
    if filter_actors or filter_studios:
        for i, c in enumerate(cand_subset):
            matches = 0
            total_filters = len(filter_actors) + len(filter_studios)
            
            # Check actor matches
            if filter_actors:
                try:
                    import json
                    cand_cast = json.loads(c.get("cast") or "[]")
                    if isinstance(cand_cast, list):
                        cand_cast = [str(name).lower() for name in cand_cast]
                        matches += sum(1 for actor in filter_actors if any(actor in cast_name for cast_name in cand_cast))
                except Exception:
                    pass
            
            # Check studio matches
            if filter_studios:
                try:
                    import json
                    cand_studios = json.loads(c.get("production_companies") or "[]")
                    if isinstance(cand_studios, list):
                        cand_studios = [str(name).lower() for name in cand_studios]
                        matches += sum(1 for studio in filter_studios if any(studio in studio_name for studio_name in cand_studios))
                except Exception:
                    pass
            
            if total_filters > 0:
                actor_studio_bonus[i] = matches / total_filters

    # 4.8) Recency bias: for dynamic lists or chat lists without explicit years, prefer post-1970 content
    # Apply if: (1) list_type is mood/theme/fusion OR (2) chat without explicit years/year_range in filters
    recency_bonus = np.zeros(len(cand_subset), dtype=np.float32)
    has_explicit_years = bool(filters.get("years") or filters.get("year_range"))
    apply_recency_bias = (list_type in ["mood", "theme", "fusion"]) or (list_type == "chat" and not has_explicit_years)
    
    if apply_recency_bias:
        for i, c in enumerate(cand_subset):
            year = int(c.get("year") or 0)
            if year >= 1970:
                # Linear bonus: 1970 = 0.0, 2025 = 1.0
                # This ensures ~70% of results are post-1970 when combined with other scores
                recency_bonus[i] = min(1.0, (year - 1970) / (2025 - 1970))
            else:
                # Penalize pre-1970 content slightly
                recency_bonus[i] = -0.3
    
    # 4.9) Watch history similarity: boost items similar to what user has watched on Trakt
    # This personalizes recommendations based on viewing history
    watch_history_bonus = np.zeros(len(cand_subset), dtype=np.float32)
    if watch_history:
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Applying watch history personalization with {len(watch_history)} watched items")
        
        for i, c in enumerate(cand_subset):
            cand_trakt_id = int(c.get("trakt_id") or 0)
            if not cand_trakt_id:
                continue
            
            # Check if this exact item was watched (avoid recommending already seen content)
            if cand_trakt_id in watch_history:
                watch_history_bonus[i] = -0.5  # Penalize already watched items
                continue
            
            # Genre similarity with watched content
            cand_genres = set()
            try:
                cand_genres = set(json.loads(c.get("genres") or "[]"))
            except Exception:
                if isinstance(c.get("genres"), str):
                    cand_genres = set(g.strip() for g in c.get("genres").split(","))
            
            if not cand_genres:
                continue
            
            # Calculate average genre overlap with watched items
            genre_overlaps = []
            for watched_id, watched_meta in watch_history.items():
                # For now, we don't have genres in watch_history metadata
                # This is a placeholder for future enhancement when we enrich watch history
                pass
            
            # Simple boost if same type (movie/show) as recent watches
            cand_type = c.get("media_type", "movie")
            watched_types = [meta.get("type") for meta in watch_history.values() if meta.get("type")]
            if watched_types:
                type_match_ratio = sum(1 for t in watched_types if t == cand_type) / len(watched_types)
                if type_match_ratio > 0.6:  # User prefers this media type
                    watch_history_bonus[i] = 0.1

    # 5) Weighting by list type
    # For dynamic lists (mood/theme/fusion), use NEGATIVE novelty weight to prefer mainstream content
    # novelty = 1.0 - popularity, so negative weight means we BOOST popular items
    # watch_history weight: boost items similar to user's viewing preferences
    weights = {
        "chat": {"sim": 0.25, "semantic": 0.25, "genre": 0.08, "rating": 0.10, "novelty": 0.05, "phrase": 0.05, "actor_studio": 0.08, "recency": 0.05, "watch_history": 0.09, "tone": 0.00},
        "mood": {"sim": 0.15, "semantic": 0.20, "genre": 0.10, "rating": 0.10, "novelty": -0.15, "phrase": 0.08, "actor_studio": 0.08, "recency": 0.15, "watch_history": 0.09, "tone": 0.01},
        "theme": {"sim": 0.15, "semantic": 0.20, "genre": 0.10, "rating": 0.10, "novelty": -0.15, "phrase": 0.08, "actor_studio": 0.08, "recency": 0.15, "watch_history": 0.09, "tone": 0.01},
        "fusion": {"sim": 0.10, "semantic": 0.25, "genre": 0.10, "rating": 0.10, "novelty": -0.15, "phrase": 0.05, "actor_studio": 0.08, "recency": 0.15, "watch_history": 0.12, "tone": 0.01},
    }.get(list_type, {"sim": 0.25, "semantic": 0.25, "genre": 0.0, "rating": 0.10, "novelty": 0.10, "phrase": 0.10, "actor_studio": 0.08, "recency": 0.05, "watch_history": 0.09, "tone": 0.00})

    # Include local user ratings influence
    user_ratings = _load_user_ratings(user_id)
    ratings_boost = np.zeros(len(cand_subset), dtype=np.float32)
    if user_ratings:
        for i, c in enumerate(cand_subset):
            try:
                tid = int(c.get("trakt_id") or c.get("ids", {}).get("trakt") or 0)
            except Exception:
                tid = 0
            if tid and tid in user_ratings:
                ratings_boost[i] = 0.3 if user_ratings[tid] == 1 else (-0.7 if user_ratings[tid] == -1 else 0.0)

    # Tone boost: if user tone from filters contains 'light' or 'cozy', prefer higher rating
    tone = [t.lower() for t in (filters.get("tone") or [])]
    tone_bonus = np.zeros(len(cand_subset), dtype=np.float32)
    if any(k in tone for k in ["light", "cozy", "wholesome", "warm"]):
        # Reward higher ratings slightly as a proxy for feel-good
        tone_bonus = rating_norm * 0.5

    # Contextual mood adjustments based on time of day (for mood/theme/fusion lists)
    mood_time_bonus = np.zeros(len(cand_subset), dtype=np.float32)
    if list_type in ["mood", "theme", "fusion"]:
        try:
            from app.services.mood import get_contextual_mood_adjustment
            user_timezone = _get_user_timezone(user_id)
            contextual_moods = get_contextual_mood_adjustment(user_timezone)
            
            logger.debug(f"Applying contextual mood adjustments for timezone {user_timezone}: {contextual_moods}")
            
            # Map mood keywords to genres/tones that should be boosted based on time of day
            mood_genre_map = {
                "happy": ["comedy", "family", "animation", "musical"],
                "excited": ["action", "adventure", "thriller"],
                "romantic": ["romance", "drama"],
                "thoughtful": ["documentary", "drama", "history"],
                "curious": ["science fiction", "mystery", "documentary"],
                "scared": ["horror", "thriller"]
            }
            
            # Apply mood boosts to candidates that match the contextual mood
            for i, c in enumerate(cand_subset):
                c_genres = set(parse_genres(c.get("genres")))
                mood_boost = 0.0
                
                for mood, adjustment in contextual_moods.items():
                    if mood in mood_genre_map:
                        target_genres = set(mood_genre_map[mood])
                        if c_genres & target_genres:
                            # Boost/penalize based on genre match and mood adjustment
                            mood_boost += adjustment * 0.5
                
                mood_time_bonus[i] = mood_boost
        except Exception as e:
            logger.warning(f"Failed to apply contextual mood adjustments: {e}")

    final = (
        weights["sim"] * tfidf_sim
        + weights["semantic"] * semantic_sim
        + weights["genre"] * genre_overlap
        + weights["rating"] * rating_norm
        + weights["novelty"] * novelty
        + weights["phrase"] * phrase_bonus
        + weights["actor_studio"] * actor_studio_bonus
        + weights["recency"] * recency_bonus
        + weights["watch_history"] * watch_history_bonus
        + weights.get("tone", 0.0) * tone_bonus
        + mood_time_bonus  # Add time-of-day contextual mood adjustment
    )
    # Apply per-item multiplicative adjustment for explicit user ratings
    if user_ratings:
        final = final * (1.0 + ratings_boost)

    results = []
    for i, c in enumerate(cand_subset):
        item = {
            **c,
            "tfidf_sim": float(tfidf_sim[i]),
            "semantic_sim": float(semantic_sim[i]),
            "genre_overlap": float(genre_overlap[i]),
            "phrase_bonus": float(phrase_bonus[i]),
            "actor_studio_bonus": float(actor_studio_bonus[i]),
            "recency_bonus": float(recency_bonus[i]),
            "tone_bonus": float(tone_bonus[i]),
            "watch_history_bonus": float(watch_history_bonus[i]),
            "mood_time_bonus": float(mood_time_bonus[i]),
            "popularity_norm": float(pop_norm[i]),
            "rating_norm": float(rating_norm[i]),
            "novelty": float(novelty[i]),
            "final_score": float(final[i]),
        }
        item["explanation_meta"] = build_explanation_meta(item)
        results.append(item)

    results.sort(key=lambda x: x["final_score"], reverse=True)
    return results
