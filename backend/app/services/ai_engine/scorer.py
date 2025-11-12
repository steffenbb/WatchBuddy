"""
scorer.py (AI Engine)
- Score candidates with filters, TF-IDF and embedding cosine blend, list-type weights, and explanation meta.
"""
from typing import List, Dict, Any, Optional, Tuple
import math
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from rank_bm25 import BM25Okapi
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


def _compare_numeric(value: Optional[float], cmp_tuple: Optional[Tuple[str, float]], lenient: bool = False) -> bool:
    """
    Compare numeric value against threshold.
    Args:
        value: The candidate's value
        cmp_tuple: (operator, threshold) tuple (e.g., ('>=', 100))
        lenient: If True, allow missing values (return True); if False, reject missing values (return False)
    """
    if cmp_tuple is None:
        return True
    if value is None:
        return lenient  # AI lists: allow missing data; custom/manual: reject missing data
    op, thresh = cmp_tuple
    try:
        v = float(value)
    except Exception:
        return lenient  # AI lists: allow unparseable data; custom/manual: reject
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


def _passes_filters(c: Dict[str, Any], filters: Dict[str, Any], list_type: str = "chat") -> bool:
    def matches_any_field(candidate, values):
        if not values:
            return True
        fields = [candidate.get("genres"), candidate.get("keywords"), candidate.get("overview"), candidate.get("tagline")]
        for val in values:
            val_lower = str(val).lower()
            for field in fields:
                if field and val_lower in str(field).lower():
                    return True
        return False

    is_ai_list = list_type in ("chat", "mood", "theme", "fusion", "custom")

    # Media type constraint (movie/show)
    if "media_type" in filters and filters["media_type"]:
        want = filters["media_type"].lower()
        cand = str(c.get("media_type") or c.get("type") or "").lower()
        cand_norm = "show" if cand in ("show", "tv", "tvshow", "tv series", "series") else cand
        if cand_norm != want:
            return False

    # Genres/mood/keywords/overview/tagline flexible matching for mood/theme/custom
    if list_type in ("mood", "theme", "custom") and "genres" in filters and filters["genres"] and len(filters["genres"]):
        if not matches_any_field(c, filters["genres"]):
            return False
    elif "genres" in filters and filters["genres"] and len(filters["genres"]):
        cand_genres = set()
        try:
            import json
            cand_genres = set(json.loads(c.get("genres") or "[]"))
        except Exception:
            if isinstance(c.get("genres"), str):
                cand_genres = set(g.strip() for g in c.get("genres").split(","))
        if not cand_genres & set(filters["genres"]):
            return False

    # Actors
    if "actors" in filters and filters["actors"] and len(filters["actors"]):
        cand_cast = []
        try:
            import json
            cand_cast = json.loads(c.get("cast") or "[]")
            if isinstance(cand_cast, list):
                cand_cast = [str(name).lower() for name in cand_cast]
        except Exception:
            if isinstance(c.get("cast"), str):
                cand_cast = [name.strip().lower() for name in c.get("cast").split(",")]
        if is_ai_list:
            if cand_cast:
                filter_actors = [str(name).lower() for name in filters["actors"]]
                if not any(actor in " ".join(cand_cast) for actor in filter_actors):
                    return False
        else:
            filter_actors = [str(name).lower() for name in filters["actors"]]
            if not any(actor in " ".join(cand_cast) for actor in filter_actors):
                return False

    # Studios
    if "studios" in filters and filters["studios"] and len(filters["studios"]):
        cand_studios = []
        try:
            import json
            cand_studios = json.loads(c.get("production_companies") or "[]")
            if isinstance(cand_studios, list):
                cand_studios = [str(name).lower() for name in cand_studios]
        except Exception:
            if isinstance(c.get("production_companies"), str):
                cand_studios = [name.strip().lower() for name in c.get("production_companies").split(",")]
        if is_ai_list:
            if cand_studios:
                filter_studios = [str(name).lower() for name in filters["studios"]]
                if not any(studio in " ".join(cand_studios) for studio in filter_studios):
                    return False
        else:
            filter_studios = [str(name).lower() for name in filters["studios"]]
            if not any(studio in " ".join(cand_studios) for studio in filter_studios):
                return False

    # Languages
    if "languages" in filters and filters["languages"] and len(filters["languages"]):
        if (c.get("language") or "").lower() not in [l.lower() for l in filters["languages"]]:
            return False

    # Years
    if "years" in filters and filters["years"] and len(filters["years"]):
        if int(c.get("year") or 0) not in set(filters["years"]):
            return False

    # Year range
    if "year_range" in filters and filters["year_range"] and len(filters["year_range"]):
        lo, hi = filters["year_range"][0], filters["year_range"][1]
        y = int(c.get("year") or 0)
        if not (lo <= y <= hi):
            return False

    # Adult flag
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
    if not _compare_numeric(c.get("vote_average"), filters.get("rating_cmp"), lenient=is_ai_list):
        return False
    if filters.get("votes_cmp") is not None:
        # If an explicit comparator is provided, enforce it (lenient handling for AI lists on missing values)
        if not _compare_numeric(c.get("vote_count"), filters.get("votes_cmp"), lenient=is_ai_list):
            return False
    else:
        # Apply default vote_count floors for ALL list types; thresholds vary by discovery/obscurity intent.
        discovery = (filters.get("discovery") or filters.get("obscurity") or "balanced")
        try:
            vc = float(c.get("vote_count") or 0)
        except Exception:
            vc = 0.0
        if str(discovery) in ("obscure", "obscure_high", "very_obscure"):
            min_votes = 100.0
        elif str(discovery) in ("popular", "mainstream"):
            min_votes = 800.0
        else:
            min_votes = 400.0
        if vc < min_votes:
            return False
    if not _compare_numeric(c.get("revenue"), filters.get("revenue_cmp"), lenient=is_ai_list):
        return False
    if not _compare_numeric(c.get("budget"), filters.get("budget_cmp"), lenient=is_ai_list):
        return False
    if not _compare_numeric(c.get("popularity"), filters.get("popularity_cmp"), lenient=is_ai_list):
        return False
    if not _compare_numeric(c.get("number_of_seasons"), filters.get("seasons_cmp"), lenient=is_ai_list):
        return False
    if not _compare_numeric(c.get("number_of_episodes"), filters.get("episodes_cmp"), lenient=is_ai_list):
        return False
    if not _compare_numeric(c.get("runtime"), filters.get("runtime_cmp"), lenient=is_ai_list):
        return False

    return True
    def matches_any_field(candidate, values):
        if not values:
            return True
        fields = [candidate.get("genres"), candidate.get("keywords"), candidate.get("overview"), candidate.get("tagline")]
        for val in values:
            val_lower = str(val).lower()
            for field in fields:
                if field and val_lower in str(field).lower():
                    return True
        return False

    is_ai_list = list_type in ("chat", "mood", "theme", "fusion", "custom")

    # Media type constraint (movie/show)
    if "media_type" in filters and filters["media_type"]:
        want = filters["media_type"].lower()
        cand = str(c.get("media_type") or c.get("type") or "").lower()

    # Genres/mood/keywords/overview/tagline flexible matching for mood/theme/custom
    if list_type in ("mood", "theme", "custom") and "genres" in filters and filters["genres"] and len(filters["genres"]):
        if not matches_any_field(c, filters["genres"]):
            return False
    elif "genres" in filters and filters["genres"] and len(filters["genres"]):
        cand_genres = set()
        try:
            import json
            cand_genres = set(json.loads(c.get("genres") or "[]"))
        except Exception:
            min_votes = 800.0
        else:
            min_votes = 400.0
        if vc < min_votes:
            return False
    if not _compare_numeric(c.get("revenue"), filters.get("revenue_cmp"), lenient=is_ai_list):
        return False
    if not _compare_numeric(c.get("budget"), filters.get("budget_cmp"), lenient=is_ai_list):
        return False
    if not _compare_numeric(c.get("popularity"), filters.get("popularity_cmp"), lenient=is_ai_list):
        return False
    if not _compare_numeric(c.get("number_of_seasons"), filters.get("seasons_cmp"), lenient=is_ai_list):
        return False
    if not _compare_numeric(c.get("number_of_episodes"), filters.get("episodes_cmp"), lenient=is_ai_list):
        return False
    if not _compare_numeric(c.get("runtime"), filters.get("runtime_cmp"), lenient=is_ai_list):
        return False

    return True
    # For mood/theme/custom lists, allow genre/mood/keywords/overview/tagline to match in any field
    def matches_any_field(candidate, values):
        if not values:
            return True
        fields = [
            candidate.get("genres"), candidate.get("keywords"), candidate.get("overview"), candidate.get("tagline")
        ]
        for val in values:
            val_lower = str(val).lower()
            for field in fields:
                if field and val_lower in str(field).lower():
                    return True
        return False

    # For AI lists (chat/mood/theme/fusion), be LENIENT on metadata filters (actors/studios/etc)
    # since FAISS semantic search already found relevant candidates and persistent candidates have incomplete metadata.
    # Only reject if candidate HAS metadata that DOESN'T match.
    # For custom/manual lists, be STRICT (require matches when filters present).
    # Numeric comparators as strict thresholds
    # For rating, always be strict (vote_average should be in persistent candidates)
    if not _compare_numeric(c.get("vote_average"), filters.get("rating_cmp"), lenient=is_ai_list):
        return False
    # Votes: if explicit comparator provided, enforce it; otherwise apply sensible defaults
    if filters.get("votes_cmp") is not None:
        if not _compare_numeric(c.get("vote_count"), filters.get("votes_cmp"), lenient=is_ai_list):
            return False
    else:
        # Default vote_count floor based on discovery/obscurity intent
        discovery = (filters.get("discovery") or filters.get("obscurity") or "balanced")
        try:
            vc = float(c.get("vote_count") or 0)
        except Exception:
            vc = 0.0
        # Map to thresholds
        if str(discovery) in ("obscure", "obscure_high", "very_obscure"):
            min_votes = 100.0
        elif str(discovery) in ("popular", "mainstream"):
            min_votes = 800.0
        else:
            min_votes = 400.0
        if vc < min_votes:
            return False
    # Budget/revenue/popularity filters: lenient for AI lists (allow missing data)
    if not _compare_numeric(c.get("revenue"), filters.get("revenue_cmp"), lenient=is_ai_list):
        return False
    if not _compare_numeric(c.get("budget"), filters.get("budget_cmp"), lenient=is_ai_list):
        return False
    if not _compare_numeric(c.get("popularity"), filters.get("popularity_cmp"), lenient=is_ai_list):
        return False
    
    # TV-specific numeric filters: lenient for AI lists
    if not _compare_numeric(c.get("number_of_seasons"), filters.get("seasons_cmp"), lenient=is_ai_list):
        return False
    if not _compare_numeric(c.get("number_of_episodes"), filters.get("episodes_cmp"), lenient=is_ai_list):
        return False
    if not _compare_numeric(c.get("runtime"), filters.get("runtime_cmp"), lenient=is_ai_list):
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
        
        # For AI lists: only reject if networks populated but doesn't match
        filter_networks = [str(name).lower() for name in filters["networks"]]
        if is_ai_list:
            if cand_networks:  # Only check if network data exists
                if not any(network in " ".join(cand_networks) for network in filter_networks):
                    return False
        else:
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
        
        # For AI lists: only reject if creators populated but doesn't match
        filter_creators = [str(name).lower() for name in filters["creators"]]
        if is_ai_list:
            if cand_creators:  # Only check if creator data exists
                if not any(creator in " ".join(cand_creators) for creator in filter_creators):
                    return False
        else:
            if not any(creator in " ".join(cand_creators) for creator in filter_creators):
                return False
    
    # Directors (movies/TV)
    if "directors" in filters and filters["directors"]:
        cand_director = (c.get("director") or "").lower()
        filter_directors = [str(name).lower() for name in filters["directors"]]
        # For AI lists: only reject if director populated but doesn't match
        if is_ai_list:
            if cand_director:  # Only check if director data exists
                if not any(director in cand_director for director in filter_directors):
                    return False
        else:
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
        
        # For AI lists: only reject if countries populated but doesn't match
        filter_countries = [str(name).upper() for name in filters["countries"]]
        if is_ai_list:
            if cand_countries:  # Only check if country data exists
                if not any(country in cand_countries for country in filter_countries):
                    return False
        else:
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


def _generate_query_variations(prompt: str, filters: Dict[str, Any]) -> List[str]:
    """Generate prompt variations for multi-query expansion.
    
    Creates 2-3 variations by combining:
    - Original prompt
    - Mood/tone descriptors + genres
    - Seasonal keywords + themes
    """
    variations = [prompt]
    
    # Variation 1: Add mood/tone descriptors
    if filters.get("tone"):
        tone_terms = " ".join(str(t) for t in filters["tone"][:3])
        genres = filters.get("genres", [])
        if genres:
            genre_str = " ".join(str(g) for g in genres[:2])
            variations.append(f"{tone_terms} {genre_str} {prompt}")
        else:
            variations.append(f"{tone_terms} {prompt}")
    
    # Variation 2: Add seasonal + themes
    if filters.get("seasonal") or filters.get("phrases"):
        parts = []
        if filters.get("seasonal"):
            parts.extend([str(s) for s in filters["seasonal"][:2]])
        if filters.get("phrases"):
            parts.extend([str(p) for p in filters["phrases"][:2]])
        if parts:
            variations.append(f"{' '.join(parts)} {prompt}")
    
    # Limit to 3 variations to avoid over-expansion
    return variations[:3]


def _compute_genre_embedding_similarity(prompt_embedding: np.ndarray, candidate_genres: List[str]) -> float:
    """Compute fuzzy genre matching using embeddings.
    
    Handles cases like:
    - "neo-noir" → matches "thriller" + "mystery" + "crime"
    - "space opera" → matches "sci-fi" + "adventure"
    """
    if not candidate_genres or prompt_embedding is None:
        return 0.0
    
    try:
        # Lazy load embedder
        from .embeddings import EmbeddingService
        embedder = EmbeddingService()
        
        # Encode candidate genres
        genre_embs = embedder.encode_texts(candidate_genres)
        
        # Normalize embeddings
        prompt_norm = prompt_embedding / (np.linalg.norm(prompt_embedding) + 1e-8)
        genre_norms = genre_embs / (np.linalg.norm(genre_embs, axis=1, keepdims=True) + 1e-8)
        
        # Max similarity across all genres
        similarities = genre_norms.dot(prompt_norm)
        return float(np.max(similarities))
    except Exception as e:
        logger.warning(f"[GENRE_EMB] Failed to compute genre embedding similarity: {e}")
        return 0.0


def _reciprocal_rank_fusion(rankings: List[np.ndarray], k: int = 60) -> np.ndarray:
    """Combine multiple rankings using Reciprocal Rank Fusion (RRF).
    
    RRF formula: score = sum(1 / (k + rank_i)) for each ranking
    
    Args:
        rankings: List of ranking arrays (lower index = better rank)
        k: Constant to prevent division issues (default 60)
    
    Returns:
        Combined RRF scores (higher = better)
    """
    n = len(rankings[0])
    rrf_scores = np.zeros(n)
    
    for ranking in rankings:
        for idx in range(n):
            # ranking[idx] is the original index in the candidate list
            # Its position in the ranking array is its rank
            rank = np.where(ranking == idx)[0]
            if len(rank) > 0:
                rrf_scores[idx] += 1.0 / (k + rank[0])
    
    return rrf_scores


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
    # 1) Strict filtering (lenient for AI lists on metadata fields)
    filtered: List[Tuple[int, Dict[str, Any]]] = [
        (i, c) for i, c in enumerate(candidates) if _passes_filters(c, filters, list_type)
    ]
    try:
        logger.info(
            f"[AI_SCORE][filter] list_type={list_type} passed={len(filtered)}/{len(candidates)} "
            f"discovery={filters.get('discovery') or filters.get('obscurity') or 'balanced'} "
            f"votes_cmp={filters.get('votes_cmp')}"
        )
    except Exception:
        pass
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
    # Include FAISS semantic similarity and watch-history penalty to avoid dropping
    # highly relevant but less popular items, and down-rank already watched items.
    faiss_sim_full = np.array([float((c.get("_faiss_score") or 0.0)) for c in cand_subset], dtype=np.float32)
    # Quick watch penalty: -0.5 for watched items to push them out early
    watch_penalty_full = np.zeros(len(cand_subset), dtype=np.float32)
    if watch_history:
        for i, c in enumerate(cand_subset):
            try:
                tid = int(c.get("trakt_id") or 0)
            except Exception:
                tid = 0
            if tid and tid in watch_history:
                watch_penalty_full[i] = -0.5

    # quick_score balances semantic fit and basic quality
    quick_score_full = 0.5 * faiss_sim_full + 0.3 * pop_norm + 0.2 * rating_norm + watch_penalty_full
    order = np.argsort(-quick_score_full)
    keep = order[: min(topk_reduce, len(order))]
    cand_subset = [cand_subset[i] for i in keep]
    texts_subset = [texts_subset[i] for i in keep]
    pop_norm = pop_norm[keep]
    rating_norm = rating_norm[keep]
    novelty = novelty[keep]
    faiss_sim = faiss_sim_full[keep]
    watch_penalty = watch_penalty_full[keep]

    # 3) BM25 keyword matching (replacing TF-IDF for better relevance)
    try:
        # Synonym expansion: add common synonyms to query for better keyword coverage
        from .classifiers import MOOD_KEYWORDS
        expanded_query_tokens = prompt_text.lower().split()
        
        # Add synonyms from mood keyword map
        for token in prompt_text.lower().split():
            for mood, synonyms in MOOD_KEYWORDS.items():
                if token in synonyms:
                    # Add 2-3 top synonyms (avoid over-expansion)
                    expanded_query_tokens.extend(synonyms[:3])
                    break
        
        # Deduplicate while preserving order
        seen = set()
        tokenized_query = []
        for token in expanded_query_tokens:
            if token not in seen:
                tokenized_query.append(token)
                seen.add(token)
        
        # Tokenize corpus
        tokenized_corpus = [text.lower().split() for text in texts_subset]
        
        # Build BM25 index with tuned parameters for movie overviews
        # k1=1.2 (reduced from 1.5): movie overviews are shorter than web docs
        # b=0.5 (reduced from 0.75): length normalization less aggressive
        bm25 = BM25Okapi(tokenized_corpus, k1=1.2, b=0.5)
        bm25_scores = bm25.get_scores(tokenized_query)
        
        # Query length normalization: boost short queries (1-4 words)
        # Short queries like "dark" or "thriller" need extra weight
        if len(prompt_text.split()) < 5:
            query_boost = 1.15
            bm25_scores = bm25_scores * query_boost
            logger.info(f"[AI_SCORE][BM25] Applied short query boost ({query_boost}x) for {len(prompt_text.split())} word query")
        
        # Normalize BM25 scores
        bm25_sim = _normalize(np.array(bm25_scores))
        
        # Get BM25 ranking (for RRF later)
        bm25_ranking = np.argsort(-bm25_scores)  # Higher score = better rank
        
        del bm25
        gc.collect()
        
        logger.info(f"[AI_SCORE][BM25] BM25 complete: {len(tokenized_query)} query tokens (incl. {len(tokenized_query) - len(prompt_text.split())} synonyms)")
    except Exception as e:
        logger.warning(f"[AI_SCORE][BM25] Failed, falling back to simple normalization: {e}")
        bm25_sim = np.ones(len(texts_subset)) * 0.5
        bm25_ranking = np.arange(len(texts_subset))

    # 4) Multi-Query Expansion: Generate query variations and average embeddings
    try:
        if candidate_embeddings is not None and query_embedding is not None:
            from .embeddings import EmbeddingService
            embedder = EmbeddingService()
            
            # Generate prompt variations
            variations = _generate_query_variations(prompt_text, filters)
            logger.info(f"[AI_SCORE][MULTI_QUERY] Generated {len(variations)} query variations")
            
            # Encode all variations
            if len(variations) > 1:
                variation_embeddings = embedder.encode_texts(variations)
                # Average embeddings for better semantic coverage
                enhanced_query = np.mean(variation_embeddings, axis=0)
            else:
                enhanced_query = query_embedding
            
            # Normalize
            q = enhanced_query / (np.linalg.norm(enhanced_query) + 1e-8)
            logger.info("[AI_SCORE][MULTI_QUERY] Multi-query expansion complete")
        else:
            q = query_embedding / (np.linalg.norm(query_embedding) + 1e-8) if query_embedding is not None else None
    except Exception as e:
        logger.warning(f"[AI_SCORE][MULTI_QUERY] Failed: {e}")
        q = query_embedding / (np.linalg.norm(query_embedding) + 1e-8) if query_embedding is not None else None

    # 5) Semantic similarity with enhanced query
    # If FAISS similarity was provided, reuse it to avoid recomputation.
    use_faiss_semantic = np.any(faiss_sim > 0)
    if use_faiss_semantic:
        semantic_sim = faiss_sim.copy()
        try:
            logger.info("[AI_SCORE][semantic] Using FAISS pre-computed similarity for scoring")
        except Exception:
            pass
    else:
        semantic_sim = np.zeros_like(bm25_sim)
        if candidate_embeddings is not None and q is not None:
            embs = candidate_embeddings[idxs][keep]
            embs_norm = embs / (np.linalg.norm(embs, axis=1, keepdims=True) + 1e-8)
            semantic_sim = embs_norm.dot(q)
    
    # Get semantic ranking (for RRF)
    semantic_ranking = np.argsort(-semantic_sim)
    
    # 5.1) Hybrid Retrieval: Reciprocal Rank Fusion (RRF)
    # Combine BM25 (keyword) and semantic (embedding) rankings
    try:
        rrf_scores = _reciprocal_rank_fusion([semantic_ranking, bm25_ranking], k=60)
        # Normalize RRF scores for use in final scoring
        rrf_norm = _normalize(rrf_scores)
        logger.info("[AI_SCORE][RRF] Reciprocal Rank Fusion complete")
    except Exception as e:
        logger.warning(f"[AI_SCORE][RRF] Failed: {e}")
        # Fallback to simple average
        rrf_norm = 0.5 * _normalize(semantic_sim) + 0.5 * bm25_sim

    # 5.2) Genre Embedding Fuzzy Matching
    # Add bonus for candidates whose genres semantically match the prompt
    genre_emb_bonus = np.zeros(len(cand_subset))
    try:
        if q is not None:
            for i, cand in enumerate(cand_subset):
                genres_raw = cand.get("genres", "")
                if genres_raw:
                    # Parse genres (JSON array or comma-separated)
                    try:
                        if isinstance(genres_raw, str):
                            genres_list = json.loads(genres_raw) if genres_raw.startswith("[") else genres_raw.split(",")
                        else:
                            genres_list = genres_raw
                        
                        genres_cleaned = [str(g).strip().lower() for g in genres_list if g]
                        
                        if genres_cleaned:
                            # Compute similarity
                            similarity = _compute_genre_embedding_similarity(q, genres_cleaned)
                            genre_emb_bonus[i] = similarity * 0.10  # 0.10 max bonus for perfect match
                    except Exception:
                        pass
            
            matched_count = np.sum(genre_emb_bonus > 0)
            if matched_count > 0:
                logger.info(f"[AI_SCORE][GENRE_EMB] Genre embedding boost applied to {matched_count}/{len(cand_subset)} candidates")
    except Exception as e:
        logger.warning(f"[AI_SCORE][GENRE_EMB] Failed: {e}")

    # 5.3) Topic coherence floor: drop items far from the prompt context
    # Combine semantic and BM25 for a stable similarity measure
    if candidate_embeddings is not None and query_embedding is not None:
        topic_sim = 0.6 * semantic_sim + 0.4 * bm25_sim
    else:
        topic_sim = bm25_sim.copy()
    
    # DISABLE topic floor for AI lists - rely on FAISS/scoring instead of hard TF-IDF cutoff
    # Topic coherence was dropping all candidates for short/generic prompts like "power", "dark", etc.
    # FAISS semantic search + final scoring weights are sufficient for relevance
    apply_topic_floor = False
    
    if apply_topic_floor:
        # Set conservative floors by list type; relaxed for FAISS-sourced pools
        if all(bool(c.get("_from_faiss")) for c in cand_subset):
            # Trust FAISS filtering more; be lenient on lexical TF-IDF mismatch
            if list_type == "chat":
                topic_floor = 0.40
            elif list_type in ("mood", "theme"):
                topic_floor = 0.35
            elif list_type == "fusion":
                topic_floor = 0.38
            else:
                topic_floor = 0.32
        else:
            if list_type == "chat":
                topic_floor = 0.26  # tighten to drop off-topic items
            elif list_type in ("mood", "theme"):
                topic_floor = 0.22
            elif list_type == "fusion":
                topic_floor = 0.24
            else:
                topic_floor = 0.18
        mask = topic_sim >= topic_floor
        try:
            logger.info(
                f"[AI_SCORE][topic] list_type={list_type} before={len(topic_sim)} floor={topic_floor:.2f} kept={int(mask.sum())}"
            )
        except Exception:
            pass
        # If too few remain, relax floor slightly to avoid empty results
        if mask.sum() < max(20, int(0.25 * len(topic_sim))):
            topic_floor *= 0.90
            mask = topic_sim >= topic_floor
            try:
                logger.info(
                    f"[AI_SCORE][topic-relaxed] list_type={list_type} floor={topic_floor:.2f} kept={int(mask.sum())}"
                )
            except Exception:
                pass
        # Apply mask consistently across arrays
        if mask.sum() > 0 and mask.sum() < len(topic_sim):
            # Reduce candidate set to only coherent items
            cand_subset = [c for i, c in enumerate(cand_subset) if mask[i]]
            texts_subset = [t for i, t in enumerate(texts_subset) if mask[i]]
            pop_norm = pop_norm[mask]
            rating_norm = rating_norm[mask]
            novelty = novelty[mask]
            bm25_sim = bm25_sim[mask]
            semantic_sim = semantic_sim[mask]

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
        # Use module-level logger; avoid redefining `logger` locally which
        # would shadow the module variable and cause UnboundLocalError elsewhere.
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
    # Adjusted to reduce literal keyword bias (e.g., titles containing "dark") and improve semantic/genre alignment
    # For dynamic lists (mood/theme/fusion), keep negative novelty to prefer mainstream content a bit
    weights = {
        # Chat: emphasize semantic similarity more; reduce literal phrase weight
        "chat": {"sim": 0.22, "semantic": 0.35, "genre": 0.12, "rating": 0.10, "novelty": 0.03, "phrase": 0.03, "actor_studio": 0.06, "recency": 0.04, "watch_history": 0.05, "tone": 0.00},
        # Mood/Theme/Fusion: reduce phrase reliance; increase semantic and genre alignment
        "mood": {"sim": 0.12, "semantic": 0.28, "genre": 0.14, "rating": 0.10, "novelty": -0.12, "phrase": 0.03, "actor_studio": 0.06, "recency": 0.16, "watch_history": 0.08, "tone": 0.01},
        "theme": {"sim": 0.12, "semantic": 0.28, "genre": 0.14, "rating": 0.10, "novelty": -0.12, "phrase": 0.03, "actor_studio": 0.06, "recency": 0.16, "watch_history": 0.08, "tone": 0.01},
        "fusion": {"sim": 0.10, "semantic": 0.30, "genre": 0.14, "rating": 0.10, "novelty": -0.12, "phrase": 0.03, "actor_studio": 0.06, "recency": 0.16, "watch_history": 0.09, "tone": 0.01},
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

    # 5.1) Mitigate mood keyword bias: penalize items that only match mood words in title but don't align by genres
    mood_keywords = [str(k).lower() for k in (filters.get("mood") or [])]
    mood_penalty = np.zeros(len(cand_subset), dtype=np.float32)
    if list_type in ["mood", "theme", "fusion"] and mood_keywords:
        # Map moods to target genres to validate alignment
        mood_genre_map = {
            "dark": ["thriller", "crime", "horror", "mystery", "drama"],
            "cozy": ["comedy", "family", "animation", "romance"],
            "uplifting": ["comedy", "family", "romance"],
            "serious": ["drama", "history", "biography"],
            "gritty": ["crime", "drama", "thriller"],
        }
        # Build a fast parser for candidate genres
        def _cand_genres(c):
            try:
                import json
                if isinstance(c.get("genres"), str):
                    return set(g.strip().lower() for g in (json.loads(c["genres"]) if c["genres"].startswith("[") else c["genres"].split(",")))
                if isinstance(c.get("genres"), list):
                    return set(str(g).lower() for g in c["genres"])
            except Exception:
                pass
            return set()
        for i, c in enumerate(cand_subset):
            title = (c.get("title") or c.get("original_title") or c.get("name") or "").lower()
            cg = _cand_genres(c)
            # If title contains mood word but candidate genres don't align, apply small penalty
            for mk in mood_keywords:
                if mk in title:
                    targets = set(mood_genre_map.get(mk, []))
                    if targets and not (cg & set(t.lower() for t in targets)):
                        mood_penalty[i] -= 0.05  # small penalty per offending mood word
                        break

    # 5.1b) Mood alignment bonus & conflicting genre penalty (stronger relevance control)
    mood_alignment = np.zeros(len(cand_subset), dtype=np.float32)
    conflicting_penalty = np.zeros(len(cand_subset), dtype=np.float32)
    tone_words = [t.lower() for t in (filters.get("tone") or [])]

    # Define expansions and conflicting genre maps (applies across mood/theme/fusion)
    tone_expansions = {
        "dark": ["dark", "grim", "bleak", "brooding", "ominous"],
        "gritty": ["gritty", "raw", "harsh"],
        "uplifting": ["uplifting", "inspiring", "heartwarming", "feel-good"],
        "romantic": ["romantic", "love", "heartfelt"],
        "cozy": ["cozy", "warm", "comforting"],
        "thrilling": ["thrilling", "suspense", "edge-of-your-seat"],
        "serious": ["serious", "somber", "weighty"],
        "scary": ["scary", "spooky", "chilling"],
    }
    conflicting_genres_map = {
        # Moods mapped to genres that usually clash with intent
        "dark": ["family", "animation", "kids", "children", "musical"],
        "gritty": ["family", "animation", "kids"],
        "serious": ["family", "animation", "kids"],
        "thrilling": ["family", "animation", "kids"],
        "scary": ["family", "animation", "kids"],
        "uplifting": ["crime", "horror", "slasher", "gore"],
        "cozy": ["crime", "horror", "slasher", "gore"],
        "romantic": ["horror", "slasher", "gore"],
    }

    # Build mood tags to drive penalties even when tone words are absent (themes/fusions)
    mood_tags = set(tone_words)
    mood_tags.update([m.lower() for m in (filters.get("mood") or [])])
    mood_tags.update([tok.lower() for tok in (filters.get("tokens") or [])])
    # Derive tags from explicit genres when helpful
    gen_tags = [str(g).lower() for g in (filters.get("genres") or [])]
    if any(g in gen_tags for g in ["action", "thriller"]):
        mood_tags.add("thrilling")
    if any(g in gen_tags for g in ["romance", "comedy"]):
        mood_tags.add("cozy")
        mood_tags.add("romantic")
    if any(g in gen_tags for g in ["horror"]):
        mood_tags.add("scary")

    # Normalize common variants to canonical keys
    synonyms_map = {
        "feel good": "uplifting",
        "feel-good": "uplifting",
        "heartwarming": "uplifting",
        "edge of your seat": "thrilling",
        "edge-of-your-seat": "thrilling",
        "spooky": "scary",
        "frightening": "scary",
        "bleak": "dark",
        "grim": "dark",
    }
    norm_tags = set()
    for t in mood_tags:
        norm_tags.add(synonyms_map.get(t, t))

    def _cand_text_blob(cand):
        txt_parts = [cand.get("title"), cand.get("overview"), cand.get("tagline"), cand.get("keywords")]
        return " ".join([str(p).lower() for p in txt_parts if p])
    def _cand_genre_set(cand):
        try:
            import json
            g = cand.get("genres")
            if isinstance(g, str):
                return set(gg.strip().lower() for gg in (json.loads(g) if g.startswith("[") else g.split(",")) if gg.strip())
            if isinstance(g, list):
                return set(str(gg).lower() for gg in g)
        except Exception:
            return set()
        return set()

    if list_type in ["mood", "theme", "fusion"]:
        # Only compute mood alignment when we actually have tone words
        if tone_words:
            for i, c in enumerate(cand_subset):
                blob = _cand_text_blob(c)
                tone_hits = 0
                total_targets = 0
                for tw in tone_words:
                    expanded = tone_expansions.get(tw, [tw])
                    total_targets += len(expanded)
                    for w in expanded:
                        if w in blob:
                            tone_hits += 1
                if total_targets > 0:
                    mood_alignment[i] = min(1.0, tone_hits / total_targets)
        # Always apply conflicting genre penalties based on normalized tags
        for i, c in enumerate(cand_subset):
            cg = _cand_genre_set(c)
            if not cg:
                continue
            applied = False
            for tag in norm_tags:
                conflicts = conflicting_genres_map.get(tag, [])
                if conflicts and any(conf in cg for conf in conflicts):
                    conflicting_penalty[i] -= 0.12  # push mismatched genres down
                    applied = True
                    break
            # If we had no tags, but list is clearly horror or family by candidate genres, skip

    # 5.1c) Seasonal bonus (e.g., christmas/halloween) if seasonal words present
    seasonal_bonus = np.zeros(len(cand_subset), dtype=np.float32)
    seasonal_words = [s.lower() for s in (filters.get("seasonal") or [])]
    if seasonal_words:
        for i, c in enumerate(cand_subset):
            text_blob = " ".join([str(c.get(k) or "").lower() for k in ["title", "overview", "tagline", "keywords"]])
            hits = sum(1 for w in seasonal_words if w in text_blob)
            if hits:
                seasonal_bonus[i] = min(1.0, hits / max(1, len(seasonal_words))) * 0.6  # cap seasonal influence

    # Integrate new mood alignment & penalties; seasonal bonus scaled separately
    # 4.9) Negative cues penalty - penalize candidates matching "without X", "no X", "avoid X"
    negative_penalty = np.zeros(len(cand_subset))
    if filters.get("negative_cues"):
        neg_cues = [str(nc).lower() for nc in filters["negative_cues"]]
        for i, cand in enumerate(cand_subset):
            txt_blob = " ".join([str(cand.get(k) or "").lower() for k in 
                                ["title", "overview", "keywords", "genres"]])
            # Count how many negative cues appear in this candidate
            neg_count = sum(1 for cue in neg_cues if cue in txt_blob)
            if neg_count > 0:
                # Penalty scales with number of matches: -0.15 per negative cue match
                negative_penalty[i] = -0.15 * neg_count
    
    # 4.10) Rating qualifiers boost - boost candidates matching quality cues
    rating_qualifier_boost = np.zeros(len(cand_subset))
    if filters.get("rating_qualifiers"):
        qualifiers = filters["rating_qualifiers"]
        for i, cand in enumerate(cand_subset):
            rating = cand.get("vote_average", 0) or 0
            votes = cand.get("vote_count", 0) or 0
            popularity = cand.get("popularity", 0) or 0
            
            # "highly_rated" / "critically_acclaimed" - boost high ratings with sufficient votes
            if any(q in qualifiers for q in ["highly_rated", "critically_acclaimed", "award_winning"]):
                if rating >= 7.5 and votes >= 500:
                    rating_qualifier_boost[i] += 0.10
            
            # "cult_classic" - decent rating, lower popularity (niche appeal)
            if "cult_classic" in qualifiers:
                if 6.5 <= rating <= 8.5 and votes >= 100 and popularity < 50:
                    rating_qualifier_boost[i] += 0.12
            
            # "underrated" / "hidden_gem" - good rating but low popularity
            if any(q in qualifiers for q in ["underrated", "hidden_gem"]):
                if rating >= 7.0 and votes >= 100 and popularity < 30:
                    rating_qualifier_boost[i] += 0.10
            
            # "popular" / "mainstream" - high popularity
            if any(q in qualifiers for q in ["popular", "mainstream"]):
                if popularity > 100:
                    rating_qualifier_boost[i] += 0.08
            
            # "obscure" - very low popularity
            if "obscure" in qualifiers:
                if popularity < 20 and votes < 500:
                    rating_qualifier_boost[i] += 0.08
    
    final = (
        weights["sim"] * bm25_sim           # BM25 keyword matching (replaced TF-IDF)
        + weights["semantic"] * semantic_sim # Multi-query expanded semantic similarity
        + 0.15 * rrf_norm                    # Hybrid retrieval (RRF fusion of BM25 + semantic)
        + weights["genre"] * genre_overlap
        + weights["rating"] * rating_norm
        + weights["novelty"] * novelty
        + weights["phrase"] * phrase_bonus
        + weights["actor_studio"] * actor_studio_bonus
        + weights["recency"] * recency_bonus
        + weights["watch_history"] * watch_history_bonus
        + weights.get("tone", 0.0) * tone_bonus
        + mood_time_bonus
        + mood_penalty
        + 0.22 * mood_alignment  # substantial boost for mood thematic fit
        + conflicting_penalty    # push obviously mismatched genres down
        + seasonal_bonus         # holiday/seasonal relevance
        + negative_penalty       # penalize "without X", "no X" matches
        + rating_qualifier_boost # boost quality-specific requests
        + genre_emb_bonus        # fuzzy genre matching via embeddings
    )

    # --- HARD INCLUSION LOGIC FOR PROMPT-SPECIFIC CUES ---
    # If prompt contains seasonal, phrase, seed title, or subgenre cues, ensure at least a few candidates match
    required_cues = []
    cue_context = []  # Track what kind of cues we're enforcing for logging
    # Ensure these are always defined to avoid UnboundLocalError when referenced later
    seasonal_cues = []
    seed_cues = []
    
    # Seasonal (highest priority - very specific)
    if filters.get("seasonal"):
        seasonal_cues = [str(s).lower() for s in filters["seasonal"]]
        required_cues.extend(seasonal_cues)
        cue_context.append(f"seasonal:{','.join(seasonal_cues[:3])}")
    
    # Seed titles (high priority - user wants similarity)
    if filters.get("seed_titles"):
        seed_cues = [str(t).lower() for t in filters["seed_titles"]]
        required_cues.extend(seed_cues)
        cue_context.append(f"seeds:{','.join(seed_cues[:2])}")
    
    # Phrases (medium-high priority - explicit quoted text)
    if filters.get("phrases"):
        phrase_cues = [str(p).lower() for p in filters["phrases"]]
        required_cues.extend(phrase_cues)
        cue_context.append(f"phrases:{','.join(phrase_cues[:2])}")
    
    # Subgenres and multi-word descriptors (medium priority)
    if filters.get("tokens"):
        tokens = [str(tok).lower() for tok in filters["tokens"]]
        # Multi-word tokens likely describe specific subgenres/themes
        subgenre_tokens = [tok for tok in tokens if (" " in tok or "-" in tok) and len(tok) > 4]
        if subgenre_tokens:
            required_cues.extend(subgenre_tokens)
            cue_context.append(f"subgenres:{','.join(subgenre_tokens[:2])}")
    
    # Actors (medium priority - "movies with Tom Hanks", "starring X")
    if filters.get("actors"):
        actor_cues = [str(a).lower() for a in filters["actors"]]
        required_cues.extend(actor_cues)
        cue_context.append(f"actors:{','.join(actor_cues[:2])}")
    
    # Directors (medium priority - "directed by X", "films by X")
    if filters.get("directors"):
        director_cues = [str(d).lower() for d in filters["directors"]]
        required_cues.extend(director_cues)
        cue_context.append(f"directors:{','.join(director_cues[:2])}")
    
    # Creators (medium priority - "shows by X", "created by X")
    if filters.get("creators"):
        creator_cues = [str(c).lower() for c in filters["creators"]]
        required_cues.extend(creator_cues)
        cue_context.append(f"creators:{','.join(creator_cues[:2])}")
    
    # Studios (medium priority - "A24 films", "Marvel movies")
    if filters.get("studios"):
        studio_cues = [str(s).lower() for s in filters["studios"]]
        required_cues.extend(studio_cues)
        cue_context.append(f"studios:{','.join(studio_cues[:2])}")
    
    # Networks (medium priority - "HBO shows", "Netflix series")
    if filters.get("networks"):
        network_cues = [str(n).lower() for n in filters["networks"]]
        required_cues.extend(network_cues)
        cue_context.append(f"networks:{','.join(network_cues[:2])}")

    # Find candidates matching any required cue in title, overview, tagline, keywords, genres, cast, created_by, networks, production_companies
    def _matches_cue(cand, cues):
        # Standard text fields
        blob = " ".join([str(cand.get(k) or "").lower() for k in 
                        ["title", "overview", "tagline", "keywords", "genres", 
                         "cast", "created_by", "networks", "production_companies"]])
        return any(cue in blob for cue in cues)

    # Enforce minimum hard matches based on list type, cue specificity, and final list size
    # Baseline thresholds by type/cues
    if list_type in ["chat"]:
        baseline = 5
    elif seasonal_cues or seed_cues:
        baseline = 4
    else:
        baseline = 3

    # Target top-k equals the final list size constrained to 50
    target_top_k = min(int(filters.get("item_limit") or 50), 50)
    quota = max(baseline, math.ceil(0.5 * target_top_k))  # Require at least 50% specific matches
    min_hard_matches = min(quota, len(cand_subset))
    
    hard_match_idxs = [i for i, c in enumerate(cand_subset) if required_cues and _matches_cue(c, required_cues)]
    
    # If not enough hard matches, forcibly boost top candidates that match
    if required_cues and len(hard_match_idxs) < min_hard_matches:
        # Sort existing matches by current score
        top_hard = sorted(hard_match_idxs, key=lambda i: -final[i])[:min_hard_matches]
        
        # If still not enough, find additional matching candidates
        if len(top_hard) < min_hard_matches:
            extra_needed = min_hard_matches - len(top_hard)
            extra_idxs = []
            # Search in score order for best additional matches
            score_order = np.argsort(-final)
            for idx in score_order:
                if idx not in top_hard and _matches_cue(cand_subset[idx], required_cues):
                    extra_idxs.append(idx)
                    if len(extra_idxs) >= extra_needed:
                        break
            top_hard.extend(extra_idxs)
        
        # Force hard matches to top of results with substantial boost
        if top_hard:
            max_score = float(final.max())
            for i in top_hard:
                final[i] = max_score + 0.15  # Large boost to ensure prominence
            
            try:
                logger.info(f"[AI_SCORE][hard-inclusion] Enforced {len(top_hard)}/{min_hard_matches} hard matches for {cue_context}")
            except Exception:
                pass
    elif required_cues and hard_match_idxs:
        # We have enough matches - log for visibility
        try:
            logger.info(f"[AI_SCORE][hard-inclusion] Found {len(hard_match_idxs)} natural matches for {cue_context}")
        except Exception:
            pass
    
    # 5.2) Add slight jitter for dynamic lists to ensure small variation between syncs
    if list_type in ["mood", "theme", "fusion", "chat"]:
        rng = np.random.default_rng()
        final = final + rng.uniform(0.0, 0.01, size=final.shape)

    # 5.3) Normalize final scores to [0.05, 0.98] range for nicer UI scaling while preserving order
    try:
        fmin, fmax = float(final.min()), float(final.max())
        if fmax - fmin > 1e-8:
            final = 0.05 + 0.93 * ((final - fmin) / (fmax - fmin))
        else:
            final = np.full_like(final, 0.5)
    except Exception:
        pass
    # Apply per-item multiplicative adjustment for explicit user ratings
    if user_ratings:
        final = final * (1.0 + ratings_boost)

    results = []
    for i, c in enumerate(cand_subset):
        item = {
            **c,
            "bm25_sim": float(bm25_sim[i]),
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
