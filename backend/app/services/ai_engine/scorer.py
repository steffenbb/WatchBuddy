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
from app.core.config import settings
from .rankers import ClassicRanker, LLMRanker
from .pairwise import PairwiseRanker

logger = logging.getLogger(__name__)

# Tunables loader and canary gating
from typing import cast
def _stable_cand_id(c: Dict[str, Any]) -> int:
    """Best-effort stable integer id for caching and LLM judge.
    Prefers explicit id, then tmdb_id, then trakt_id.
    """
    for k in ("id", "tmdb_id", "trakt_id"):
        try:
            if c.get(k) is not None:
                return int(c.get(k))
        except Exception:
            continue
    return 0

def _order_cache_key(user_id: Optional[int], list_type: str, prompt: str, filters: Dict[str, Any], cand_ids: list[int]) -> str:
    try:
        import hashlib, json as _json
        filt = {k: filters.get(k) for k in [
            "genres","mood","tone","seasonal","language","year_range","audience","pacing","runtime"
        ] if filters.get(k) is not None}
        payload = {
            "u": int(user_id or 1),
            "t": list_type,
            "p": prompt,  # Full prompt, not truncated
            "f": filt,
            "ids": cand_ids,
            "strategy": getattr(settings, "ai_ranker_strategy", "classic"),
        }
        raw = _json.dumps(payload, separators=(",",":"), sort_keys=True).encode("utf-8")
        return "ai_order:" + hashlib.sha256(raw).hexdigest()[:32]
    except Exception:
        return ""


def _load_tunables(list_type: str) -> Dict[str, Any]:
    try:
        from app.core.redis_client import get_redis_sync as _get_redis_sync
        r = _get_redis_sync()
        raw = r.get(f"ai:tunables:{list_type}") or r.get("ai:tunables:default")
        if not raw:
            return {}
        import json as _json
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        return cast(Dict[str, Any], _json.loads(raw))
    except Exception:
        return {}


def _is_canary_enabled(tunables: Dict[str, Any], user_id: Optional[int], list_type: str) -> bool:
    try:
        ratio = float(tunables.get("canary_ratio", 1.0))
    except Exception:
        ratio = 1.0
    if ratio >= 1.0:
        return True
    if ratio <= 0.0:
        return False
    import hashlib as _hl
    key = f"{user_id or 0}:{list_type}"
    h = int(_hl.sha1(key.encode("utf-8")).hexdigest()[:8], 16)
    return (h % 10000) / 10000.0 < ratio

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


def _static_parse_intent(query: str) -> Dict[str, Any]:
    """Static fallback parser when LLM is unavailable. Extracts genres, actors, studios from prompt."""
    q = (query or "").lower()
    genres = []
    actors = []
    studios = []
    
    # Genre mappings
    genre_map = {
        "sci-fi": "Science Fiction", "science fiction": "Science Fiction", "scifi": "Science Fiction",
        "romcom": ["Romance", "Comedy"], "rom-com": ["Romance", "Comedy"],
        "horror": "Horror", "thriller": "Thriller", "comedy": "Comedy", "drama": "Drama",
        "fantasy": "Fantasy", "animation": "Animation", "anime": "Anime",
        "documentary": "Documentary", "family": "Family", "crime": "Crime",
        "mystery": "Mystery", "action": "Action", "adventure": "Adventure",
        "romance": "Romance", "western": "Western", "war": "War",
    }
    
    for key, val in genre_map.items():
        if key in q:
            if isinstance(val, list):
                genres.extend([v for v in val if v not in genres])
            elif val not in genres:
                genres.append(val)
    
    # Explicit genre list ("genres: action, comedy")
    m = re.search(r"(?:genres?|like)[:\s]+([^\.;\n]+)", q)
    if m:
        raw = [p.strip() for p in re.split(r",|/|\band\b", m.group(1)) if p.strip()]
        for g in raw:
            g_clean = g.replace("sci fi", "science fiction").strip()
            g_title = genre_map.get(g_clean, g_clean.title())
            if isinstance(g_title, list):
                genres.extend([v for v in g_title if v not in genres])
            elif g_title not in genres:
                genres.append(g_title)
    
    # Actor extraction ("with Tom Hanks" or "starring Brad Pitt")
    actor_match = re.findall(r"(?:with|starring|actor[s]?:?)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)", query, re.IGNORECASE)
    if actor_match:
        actors = [a.strip().title() for a in actor_match]
    
    # Studio extraction ("from Disney" or "studio: Warner")
    studio_match = re.findall(r"(?:from|studio[s]?:?)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)", query, re.IGNORECASE)
    if studio_match:
        studios = [s.strip().title() for s in studio_match]
    
    return {
        "genres": genres[:6] if genres else [],
        "actors": actors[:4] if actors else [],
        "studios": studios[:3] if studios else [],
    }


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
    if cmp_tuple is None or value is None:
        return True
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
    import json as json_module  # Avoid shadowing by inner imports
    
    # Log filter details at start (only once per batch to avoid spam)
    if not hasattr(_passes_filters, '_logged_filters'):
        logger.info(f"[FILTER_CONFIG] Active filters: {json_module.dumps({k: v for k, v in filters.items() if v}, indent=2)}")
        _passes_filters._logged_filters = True
    
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
            logger.debug(f"[FILTER_REJECT] Media type mismatch: tmdb_id={c.get('tmdb_id')}, title={c.get('title')}, want={want}, got={cand_norm}")
            return False

    # Genre filtering: For AI lists, check if ANY requested genre is present (flexible OR logic)
    # For manual lists, require exact match (stricter validation)
    if "genres" in filters and filters["genres"] and len(filters["genres"]):
        cand_genres = set()
        try:
            import json
            cand_genres = set(json.loads(c.get("genres") or "[]"))
        except Exception:
            if isinstance(c.get("genres"), str):
                cand_genres = set(g.strip() for g in c.get("genres").split(","))
        
        # Check if ANY of the requested genres is present in candidate
        requested_genres = set(filters["genres"])
        has_match = bool(cand_genres & requested_genres)
        
        if not has_match:
            # For AI lists with mood/theme/seasonal context, also check overview/keywords for genre terms
            # This catches cases where TMDB genre tagging is incomplete
            if is_ai_list:
                if not matches_any_field(c, filters["genres"]):
                    logger.debug(f"[FILTER_REJECT] Genre mismatch (AI): tmdb_id={c.get('tmdb_id')}, title={c.get('title')}, requested={requested_genres}, candidate={cand_genres}")
                    return False
            else:
                # Manual lists: strict genre requirement
                logger.debug(f"[FILTER_REJECT] Genre mismatch (manual): tmdb_id={c.get('tmdb_id')}, title={c.get('title')}, requested={requested_genres}, candidate={cand_genres}")
                return False
    
    # Actors - STRICT: If user specifies actors, candidate MUST have them
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
        
        # Reject if cast is empty OR doesn't contain requested actor
        filter_actors = [str(name).lower() for name in filters["actors"]]
        if not cand_cast or not any(actor in " ".join(cand_cast) for actor in filter_actors):
            logger.debug(f"[FILTER_REJECT] Actor mismatch: tmdb_id={c.get('tmdb_id')}, title={c.get('title')}, requested={filter_actors}, candidate={cand_cast}")
            return False

    # Studios/Production Companies - STRICT: If user specifies studio, candidate MUST have it
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
        
        # Reject if studios is empty OR doesn't contain requested studio
        filter_studios = [str(name).lower() for name in filters["studios"]]
        if not cand_studios or not any(studio in " ".join(cand_studios) for studio in filter_studios):
            logger.debug(f"[FILTER_REJECT] Studio mismatch: tmdb_id={c.get('tmdb_id')}, title={c.get('title')}, requested={filter_studios}, candidate={cand_studios}")
            return False

    # Languages
    if "languages" in filters and filters["languages"] and len(filters["languages"]):
        if (c.get("language") or "").lower() not in [l.lower() for l in filters["languages"]]:
            logger.debug(f"[FILTER_REJECT] Language mismatch: tmdb_id={c.get('tmdb_id')}, title={c.get('title')}, requested={filters['languages']}, candidate={c.get('language')}")
            return False

    # Years
    if "years" in filters and filters["years"] and len(filters["years"]):
        if int(c.get("year") or 0) not in set(filters["years"]):
            logger.debug(f"[FILTER_REJECT] Year mismatch: tmdb_id={c.get('tmdb_id')}, title={c.get('title')}, requested={filters['years']}, candidate={c.get('year')}")
            return False

    # Year range
    if "year_range" in filters and filters["year_range"] and len(filters["year_range"]):
        lo, hi = filters["year_range"][0], filters["year_range"][1]
        y = int(c.get("year") or 0)
        if not (lo <= y <= hi):
            logger.debug(f"[FILTER_REJECT] Year range mismatch: tmdb_id={c.get('tmdb_id')}, title={c.get('title')}, range=[{lo},{hi}], candidate={y}")
            return False

    # Adult flag
    if "adult" in filters:
        want_adult = filters["adult"]
        cand_adult = _to_bool(c.get("adult"))
        if want_adult is True and cand_adult is False:
            logger.debug(f"[FILTER_REJECT] Adult flag mismatch (need adult): tmdb_id={c.get('tmdb_id')}, title={c.get('title')}")
            return False
        if want_adult is False and cand_adult is True:
            logger.debug(f"[FILTER_REJECT] Adult flag mismatch (no adult): tmdb_id={c.get('tmdb_id')}, title={c.get('title')}")
            return False

    # Original language
    if "original_language" in filters and filters["original_language"]:
        ol = (c.get("original_language") or c.get("language") or "").lower()
        if ol != str(filters["original_language"]).lower():
            logger.debug(f"[FILTER_REJECT] Original language mismatch: tmdb_id={c.get('tmdb_id')}, title={c.get('title')}, requested={filters['original_language']}, candidate={ol}")
            return False

    # Numeric comparators as strict thresholds
    if not _compare_numeric(c.get("vote_average"), filters.get("rating_cmp"), lenient=is_ai_list):
        logger.debug(f"[FILTER_REJECT] Rating threshold: tmdb_id={c.get('tmdb_id')}, title={c.get('title')}, rating={c.get('vote_average')}, cmp={filters.get('rating_cmp')}")
        return False
    if filters.get("votes_cmp") is not None:
        # If an explicit comparator is provided, enforce it (lenient handling for AI lists on missing values)
        if not _compare_numeric(c.get("vote_count"), filters.get("votes_cmp"), lenient=is_ai_list):
            logger.debug(f"[FILTER_REJECT] Vote count comparator: tmdb_id={c.get('tmdb_id')}, title={c.get('title')}, votes={c.get('vote_count')}, cmp={filters.get('votes_cmp')}")
            return False
    else:
        # Apply default vote_count floors for ALL list types; thresholds vary by discovery/obscurity intent.
        discovery = (filters.get("discovery") or filters.get("obscurity") or "balanced")
        try:
            vc = float(c.get("vote_count") or 0)
        except Exception:
            vc = 0.0
        
        # For AI lists with seed titles or seasonal context, use lower thresholds to avoid over-filtering
        # This helps with specific queries like "movies like Holidate" where targets may be newer/niche
        has_seeds = filters.get("seed_titles") and len(filters.get("seed_titles", [])) > 0
        has_seasonal = filters.get("seasonal") and len(filters.get("seasonal", [])) > 0
        
        if is_ai_list and (has_seeds or has_seasonal):
            # Relaxed thresholds for specific searches
            # Christmas/seasonal movies tend to be TV-movies/Hallmark with lower votes - use very lenient threshold
            if str(discovery) in ("obscure", "obscure_high", "very_obscure"):
                min_votes = 30.0
            elif str(discovery) in ("popular", "mainstream"):
                min_votes = 300.0
            else:
                min_votes = 80.0  # Drastically reduced from 150 - seasonal queries need access to niche holiday content
        else:
            # Standard thresholds for broad queries
            if str(discovery) in ("obscure", "obscure_high", "very_obscure"):
                min_votes = 100.0
            elif str(discovery) in ("popular", "mainstream"):
                min_votes = 800.0
            else:
                min_votes = 400.0
        if vc < min_votes:
            logger.debug(f"[FILTER_REJECT] Vote count floor: tmdb_id={c.get('tmdb_id')}, title={c.get('title')}, votes={vc}, min_required={min_votes}, discovery={discovery}, is_ai={is_ai_list}, has_seeds={filters.get('seed_titles')}, has_seasonal={filters.get('seasonal')}")
            return False
    if not _compare_numeric(c.get("revenue"), filters.get("revenue_cmp"), lenient=is_ai_list):
        logger.debug(f"[FILTER_REJECT] Revenue threshold: tmdb_id={c.get('tmdb_id')}, title={c.get('title')}, revenue={c.get('revenue')}, cmp={filters.get('revenue_cmp')}")
        return False
    if not _compare_numeric(c.get("budget"), filters.get("budget_cmp"), lenient=is_ai_list):
        logger.debug(f"[FILTER_REJECT] Budget threshold: tmdb_id={c.get('tmdb_id')}, title={c.get('title')}, budget={c.get('budget')}, cmp={filters.get('budget_cmp')}")
        return False
    if not _compare_numeric(c.get("popularity"), filters.get("popularity_cmp"), lenient=is_ai_list):
        logger.debug(f"[FILTER_REJECT] Popularity threshold: tmdb_id={c.get('tmdb_id')}, title={c.get('title')}, popularity={c.get('popularity')}, cmp={filters.get('popularity_cmp')}")
        return False
    if not _compare_numeric(c.get("number_of_seasons"), filters.get("seasons_cmp"), lenient=is_ai_list):
        logger.debug(f"[FILTER_REJECT] Seasons threshold: tmdb_id={c.get('tmdb_id')}, title={c.get('title')}, seasons={c.get('number_of_seasons')}, cmp={filters.get('seasons_cmp')}")
        return False
    if not _compare_numeric(c.get("number_of_episodes"), filters.get("episodes_cmp"), lenient=is_ai_list):
        logger.debug(f"[FILTER_REJECT] Episodes threshold: tmdb_id={c.get('tmdb_id')}, title={c.get('title')}, episodes={c.get('number_of_episodes')}, cmp={filters.get('episodes_cmp')}")
        return False
    if not _compare_numeric(c.get("runtime"), filters.get("runtime_cmp"), lenient=is_ai_list):
        logger.debug(f"[FILTER_REJECT] Runtime threshold: tmdb_id={c.get('tmdb_id')}, title={c.get('title')}, runtime={c.get('runtime')}, cmp={filters.get('runtime_cmp')}")
        return False
    
    # Networks (TV shows) - STRICT: If specified, candidate MUST have the network
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
        if not cand_networks or not any(network in " ".join(cand_networks) for network in filter_networks):
            logger.debug(f"[FILTER_REJECT] Network mismatch: tmdb_id={c.get('tmdb_id')}, title={c.get('title')}, requested={filter_networks}, candidate={cand_networks}")
            return False
    
    # Creators (TV shows) - STRICT: If specified, candidate MUST have the creator
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
        if not cand_creators or not any(creator in " ".join(cand_creators) for creator in filter_creators):
            logger.debug(f"[FILTER_REJECT] Creator mismatch: tmdb_id={c.get('tmdb_id')}, title={c.get('title')}, requested={filter_creators}, candidate={cand_creators}")
            return False
    
    # Directors (movies/TV) - STRICT: If specified, candidate MUST have the director
    if "directors" in filters and filters["directors"]:
        cand_director = (c.get("director") or "").lower()
        filter_directors = [str(name).lower() for name in filters["directors"]]
        if not cand_director or not any(director in cand_director for director in filter_directors):
            logger.debug(f"[FILTER_REJECT] Director mismatch: tmdb_id={c.get('tmdb_id')}, title={c.get('title')}, requested={filter_directors}, candidate={cand_director}")
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
                    logger.debug(f"[FILTER_REJECT] Country mismatch (AI): tmdb_id={c.get('tmdb_id')}, title={c.get('title')}, requested={filter_countries}, candidate={cand_countries}")
                    return False
        else:
            if not any(country in cand_countries for country in filter_countries):
                logger.debug(f"[FILTER_REJECT] Country mismatch (manual): tmdb_id={c.get('tmdb_id')}, title={c.get('title')}, requested={filter_countries}, candidate={cand_countries}")
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
    variations = [prompt]
    # Variation 1: Add mood/tone descriptors (include mood keywords if present)
    tone_terms = []
    if filters.get("tone"):
        tone_terms.extend([str(t) for t in filters["tone"][:3]])
    if filters.get("mood"):
        # Include up to 3 mood keywords to guide embeddings
        tone_terms.extend([str(m) for m in filters["mood"][:3]])
    if tone_terms:
        genres = filters.get("genres", [])
        if genres:
            genre_str = " ".join(str(g) for g in genres[:2])
            variations.append(f"{' '.join(tone_terms)} {genre_str} {prompt}")
        else:
            variations.append(f"{' '.join(tone_terms)} {prompt}")
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
    persona: Optional[str] = None,
    history_summary: Optional[str] = None,
) -> List[Dict[str, Any]]:
    # Use intent extractor for all AI list types with fallback to static parser
    ai_list_types = ("chat", "mood", "theme", "fusion", "custom")
    if list_type in ai_list_types:
        from .intent_extractor import IntentExtractor
        from app.services.persona_helper import PersonaHelper
        
        # Get compressed persona/history if not provided
        if not persona or not history_summary:
            try:
                persona_data = PersonaHelper.format_for_prompt(
                    user_id=user_id or 1,
                    include_history=True,
                    include_pairwise=True
                )
                persona_str = persona or persona_data.get("persona", "")
                history_str = history_summary or persona_data.get("history", "")
            except Exception as e:
                logger.debug(f"[Scorer] Failed to get persona/history: {e}")
                persona_str = persona or ""
                history_str = history_summary or ""
        else:
            persona_str = persona
            history_str = history_summary
        
        # Try intent extractor (LLM-based)
        intent = None
        try:
            intent = IntentExtractor.extract_intent(prompt_text, persona_str, history_str)
        except Exception as e:
            logger.debug(f"[Scorer] IntentExtractor failed: {e}")
        
        # FALLBACK: Static parsing directly in scorer if LLM fails
        if not intent:
            logger.info(f"[Scorer] Using static fallback parser for: {prompt_text[:50]}")
            intent = _static_parse_intent(prompt_text)
        
        if intent:
            # Merge extracted intent into filters
            logger.debug(f"[Scorer] Merged intent keys: {list(intent.keys())}")
            filters = {**filters, **intent}
    # 1) Strict filtering (lenient for AI lists on metadata fields)
    filtered: List[Tuple[int, Dict[str, Any]]] = [
        (i, c) for i, c in enumerate(candidates) if _passes_filters(c, filters, list_type)
    ]
    idxs = [i for i, _ in filtered]
    cand_subset = [candidates[i] for i in idxs]
    texts_subset = [candidate_texts[i] for i in idxs]
    
    # 1.5) On-demand enrichment for candidates with stale/missing metadata
    # This happens BEFORE scoring so enriched data (keywords, cast, overview) can influence results
    try:
        from .candidate_enricher import enrich_candidates_sync
        
        logger.debug(f"[Scorer] Starting candidate enrichment for {len(cand_subset)} candidates (max_age=90 days)")
        
        # Use synchronous wrapper to avoid asyncio.run() issues
        cand_subset = enrich_candidates_sync(cand_subset, max_age_days=90, max_concurrent=10)
        
        logger.debug(f"[Scorer] Candidate enrichment completed for {len(cand_subset)} candidates")
    except Exception as e:
        logger.warning(f"[Scorer] Candidate enrichment failed (continuing with existing data): {e}", exc_info=True)
    
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
    # Quick watch penalty: moderate penalty for watched items (don't eliminate, just deprioritize)
    watch_penalty_full = np.zeros(len(cand_subset), dtype=np.float32)
    if watch_history:
        for i, c in enumerate(cand_subset):
            try:
                tid = int(c.get("trakt_id") or 0)
            except Exception:
                tid = 0
            if tid and tid in watch_history:
                watch_penalty_full[i] = -0.25  # Moderate penalty instead of eliminating

    # quick_score balances semantic fit and basic quality
    quick_score_full = 0.5 * faiss_sim_full + 0.3 * pop_norm + 0.2 * rating_norm + watch_penalty_full
    order = np.argsort(-quick_score_full)
    # Dynamically choose how many to keep for AI lists: use larger of (3x desired) or topk_reduce, capped by BGE/FAISS query cap
    try:
        desired = int(filters.get("item_limit") or 50)
    except Exception:
        desired = 50
    desired = max(1, min(50, desired))
    try:
        from app.core.config import settings as _settings
        cap = int(getattr(_settings, "ai_bge_topk_query", 600) or 600)
    except Exception:
        cap = 600
    dynamic_keep = max(topk_reduce, 3 * desired)
    dynamic_keep = min(dynamic_keep, cap, len(order))
    keep = order[: dynamic_keep]
    cand_subset = [cand_subset[i] for i in keep]
    texts_subset = [texts_subset[i] for i in keep]
    pop_norm = pop_norm[keep]
    rating_norm = rating_norm[keep]
    novelty = novelty[keep]
    faiss_sim = faiss_sim_full[keep]
    watch_penalty = watch_penalty_full[keep]

    # 2.1) Final order cache (order-only) to stabilize output and skip expensive reranking
    cache_key = None
    cached_order_ids = None
    try:
        from app.core.redis_client import get_redis_sync as _get_r
        _r = _get_r()
        cand_ids = [_stable_cand_id(c) for c in cand_subset]
        if all(cid > 0 for cid in cand_ids):
            cache_key = _order_cache_key(user_id, list_type, prompt_text, filters, cand_ids)
            if cache_key:
                raw = _r.get(cache_key)
                if raw:
                    try:
                        import json as _json
                        cached_order_ids = _json.loads(raw if isinstance(raw, str) else raw.decode("utf-8", errors="ignore"))
                    except Exception:
                        cached_order_ids = None
    except Exception:
        cache_key, cached_order_ids = None, None

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
        
        # Handle empty query case to avoid BM25 division by zero
        if not tokenized_query:
            logger.warning(f"[AI_SCORE][BM25] Empty tokenized query from: '{prompt_text}', using fallback scores")
            raise ValueError("Empty tokenized query")
        
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

    # 5.1b) BGE Secondary Index Fusion (additive, flag-gated)
    # If enabled, search BGE index with mood/season-aware query variants and blend ranking signal
    try:
        # Enable BGE retrieval if env flag is on OR nightly set redis flag
        _bge_enabled = bool(getattr(settings, 'ai_bge_index_enabled', False))
        if not _bge_enabled:
            try:
                from app.core.redis_client import get_redis_sync as _get_r
                _r = _get_r()
                _val = _r.get('settings:global:ai_bge_index_enabled')
                _bge_enabled = (_val == b'true' or _val == 'true')
            except Exception:
                _bge_enabled = False
        if _bge_enabled:
            from app.services.ai_engine.bge_index import BGEIndex, BGEEmbedder
            from app.services.ai_engine.query_variants import build_query_variants

            # Build query variants with available facets and mood/season context
            facets = {
                'actors': filters.get('actors') or [],
                'directors': filters.get('directors') or [],
                'creators': filters.get('creators') or [],
                'studios': filters.get('studios') or [],
                'networks': filters.get('networks') or [],
                'brands': filters.get('brands') or [],
                'tones': filters.get('tone') or [],
                'genres': filters.get('genres') or [],
            }
            season = None
            if filters.get('seasonal'):
                try:
                    season = ", ".join([str(s) for s in filters['seasonal'][:2]])
                except Exception:
                    season = None
            if getattr(settings, 'ai_bge_query_context_enabled', True):
                variants = build_query_variants(
                    prompt_text,
                    facets=facets,
                    mood=(filters.get('mood') or [None])[0] if filters.get('mood') else None,
                    season=season,
                    era=(filters.get('era') or [None])[0] if filters.get('era') else None,
                    audience=(filters.get('audience') or [None])[0] if filters.get('audience') else None,
                    language=(filters.get('languages') or [None])[0] if filters.get('languages') else None,
                    pacing=(filters.get('pacing') or [None])[0] if filters.get('pacing') else None,
                    runtime_band=(filters.get('runtime_band') or [None])[0] if filters.get('runtime_band') else None,
                    max_variants=int(getattr(settings, 'ai_multiquery_variants', 4) or 4),
                )
            else:
                variants = [prompt_text]

            # Encode variants with BGE and search the secondary index
            embedder_bge = BGEEmbedder(model_name=settings.ai_bge_model_name)
            v_embs = embedder_bge.embed(variants, batch_size=32)
            idx_bge = BGEIndex(settings.ai_bge_index_dir)
            idx_bge.load()
            topk_bge = int(getattr(settings, 'ai_bge_topk_query', 600) or 600)
            all_indices: list[list[int]] = []
            for ve in v_embs:
                ids_lists, _ = idx_bge.search([ve], topk_bge)
                # ids_lists is a list of lists of FAISS positions; convert to item IDs
                item_ids = idx_bge.positions_to_item_ids(ids_lists[0]) if ids_lists and ids_lists[0] else []
                all_indices.append(item_ids)

            # Include user profile vectors if available (redis-stored BGE centers + compressed watch vector)
            try:
                from app.core.redis_client import get_redis_sync as _get_redis_sync
                rds = _get_redis_sync()
                
                # 1) User profile vectors from BGE clustering
                pv_raw = rds.get(f"profile_vectors:{user_id or 1}")
                if pv_raw:
                    import json as _json
                    centers = _json.loads(pv_raw)
                    if isinstance(centers, list) and centers:
                        for ce_vec in centers[:3]:
                            try:
                                ids_lists, _ = idx_bge.search([ce_vec], topk_bge)
                                item_ids = idx_bge.positions_to_item_ids(ids_lists[0]) if ids_lists and ids_lists[0] else []
                                all_indices.append(item_ids)
                            except Exception:
                                pass
                
                # 2) Compressed watch vector (user-as-query pattern for personalized recall)
                # Convert persona_text from history_compression to embedding and use as query
                compression_raw = rds.get(f"history_compression:{user_id or 1}")
                if compression_raw:
                    import json as _json
                    compression = _json.loads(compression_raw)
                    persona_text = compression.get("persona_text", "")
                    if persona_text and len(persona_text) > 20:
                        try:
                            # Encode persona text to BGE embedding
                            persona_emb = embedder_bge.embed([persona_text], batch_size=1)[0]
                            # Search FAISS with persona embedding as query
                            ids_lists, _ = idx_bge.search([persona_emb], topk_bge)
                            item_ids = idx_bge.positions_to_item_ids(ids_lists[0]) if ids_lists and ids_lists[0] else []
                            all_indices.append(item_ids)
                            logger.debug(f"[Scorer] Added compressed watch persona query (text_len={len(persona_text)})")
                        except Exception as e_cv:
                            logger.warning(f"[Scorer] Failed to use compressed watch persona: {e_cv}")
            except Exception:
                pass

            # Build ranking over our current candidate subset using best rank across variants
            n = len(cand_subset)
            large_rank = max(1, n) + 100000
            # Map candidate persistent id -> local index
            cand_id_to_local = {}
            for i, c in enumerate(cand_subset):
                try:
                    cid = int(c.get('id') or 0)
                except Exception:
                    cid = 0
                if cid:
                    cand_id_to_local[cid] = i

            # Initialize ranks with large default
            ranks = np.full(n, large_rank, dtype=np.int64)
            for variant_ids in all_indices:
                for rank_pos, item_id in enumerate(variant_ids):
                    local = cand_id_to_local.get(item_id)
                    if local is not None:
                        if rank_pos < ranks[local]:
                            ranks[local] = rank_pos

            # Convert ranks to RRF-like scores and normalize
            k_rrf = 60
            bge_scores = np.array([1.0 / (k_rrf + r) if r < large_rank else 0.0 for r in ranks], dtype=np.float32)
            if bge_scores.size:
                bge_norm = _normalize(bge_scores)
                weight = float(getattr(settings, 'ai_bge_weight_in_rrf', 1.1) or 1.1)
                # Blend with existing RRF signal and re-normalize
                rrf_norm = _normalize(rrf_norm + weight * bge_norm)
                try:
                    matched = int(np.sum(bge_norm > 0))
                    logger.info(f"[AI_SCORE][BGE] Blended BGE ranking into RRF; affected={matched}/{len(bge_norm)}")
                except Exception:
                    logger.info("[AI_SCORE][BGE] Blended BGE ranking into RRF")
                # Telemetry: BGE usage
                try:
                    from app.core.redis_client import get_redis_sync as __get_redis_sync
                    _r = __get_redis_sync()
                    _r.incrby("ai_telemetry:bge:invocations", 1)
                    _r.incrby("ai_telemetry:bge:variants", int(len(variants)))
                    _r.incrby("ai_telemetry:bge:affected_items", int(matched if 'matched' in locals() else 0))
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"[AI_SCORE][BGE] Skipped BGE fusion: {e}")

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
    multi_genre_strict = np.zeros(len(cand_subset), dtype=np.float32)
    if user_genres:
        # For multi-genre queries (3+ genres), enforce stricter matching
        is_multi_genre_query = len(user_genres) >= 3
        for i, c in enumerate(cand_subset):
            c_genres = set(parse_genres(c.get("genres")))
            inter = len(user_genres & c_genres)
            union = len(user_genres | c_genres) or 1
            genre_overlap[i] = inter / union
            
            # Strict multi-genre bonus: requires 2+ matches for 3+ genre queries
            if is_multi_genre_query:
                if inter >= 2:
                    # Strong boost for matching 2+ genres (e.g., Action+Comedy for buddy cop)
                    multi_genre_strict[i] = min(1.0, inter / len(user_genres))
                else:
                    # Penalty for matching only 1 genre when 3+ are required
                    multi_genre_strict[i] = -0.3

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
        
        # Pre-compute all watched genres once for performance (O(m) instead of O(n*m))
        all_watched_genres = set()
        watched_types = []
        for watched_id, watched_meta in watch_history.items():
            try:
                if watched_meta.get("genres"):
                    if isinstance(watched_meta["genres"], str):
                        all_watched_genres.update(json.loads(watched_meta["genres"]))
                    elif isinstance(watched_meta["genres"], list):
                        all_watched_genres.update(watched_meta["genres"])
                if watched_meta.get("type"):
                    watched_types.append(watched_meta["type"])
            except Exception:
                continue
        
        # Calculate type preference ratio once
        type_preference = {}
        if watched_types:
            for t in set(watched_types):
                type_preference[t] = sum(1 for wt in watched_types if wt == t) / len(watched_types)
        
        for i, c in enumerate(cand_subset):
            cand_trakt_id = int(c.get("trakt_id") or 0)
            if not cand_trakt_id:
                continue
            
            # Check if this exact item was watched (deprioritize but don't eliminate)
            if cand_trakt_id in watch_history:
                watch_history_bonus[i] = -0.25  # Moderate penalty - still allow if otherwise great match
                continue
            
            # Genre similarity with watched content
            cand_genres = set()
            try:
                cand_genres = set(json.loads(c.get("genres") or "[]"))
            except Exception:
                if isinstance(c.get("genres"), str):
                    cand_genres = set(g.strip() for g in c.get("genres").split(","))
            
            if cand_genres and all_watched_genres:
                # Fast set intersection with pre-computed watched genres
                overlap = len(cand_genres & all_watched_genres) / len(cand_genres)
                if overlap > 0.4:  # Significant genre match
                    watch_history_bonus[i] = min(0.15, overlap * 0.3)
            
            # Media type preference boost (pre-computed)
            cand_type = c.get("media_type", "movie")
            if cand_type in type_preference and type_preference[cand_type] > 0.6:
                watch_history_bonus[i] += 0.1

    # Optional Cross-Encoder reranking (feature-flagged)
    try:
        if settings.ai_reranker_enabled and len(cand_subset) > 0:
            try:
                _tun = _load_tunables(list_type)
                _use_tun = _is_canary_enabled(_tun, user_id, list_type) if _tun else False
            except Exception:
                _tun, _use_tun = {}, False
            topk_ce = min(int(((_tun.get('ce_topk') if (_use_tun and _tun.get('ce_topk')) else settings.ai_reranker_topk) or 300)), len(cand_subset))
            if topk_ce > 0:
                from .cross_encoder_reranker import CrossEncoderReranker
                try:
                    from app.core.redis_client import get_redis_sync as _get_redis_sync
                except Exception:
                    _get_redis_sync = None  # type: ignore
                reranker = CrossEncoderReranker(settings.ai_reranker_model)
                # Build query string with mild context from tone/seasonal
                q_parts = [prompt_text]
                if filters.get("tone"):
                    q_parts.append("tone: " + ", ".join(str(t) for t in filters["tone"][:3]))
                if filters.get("mood"):
                    q_parts.append("mood: " + ", ".join(str(m) for m in filters["mood"][:3]))
                if filters.get("seasonal"):
                    q_parts.append("seasonal: " + ", ".join(str(s) for s in filters["seasonal"][:2]))
                ce_query = " | ".join(q_parts)

                # Prepare texts for top-K by current composite (bm25+semantic) proxy
                proxy = 0.6 * bm25_sim + 0.4 * (faiss_sim if np.any(faiss_sim > 0) else semantic_sim)
                order_ce = np.argsort(-proxy)[:topk_ce]
                ce_texts = [texts_subset[i] for i in order_ce]
                # Try Redis cache per (model, query-hash, candidate-id)
                if _get_redis_sync is not None:
                    try:
                        rds = _get_redis_sync()
                        import hashlib as _hl
                        qhash = _hl.sha1(ce_query.encode("utf-8")).hexdigest()
                        keys = []
                        missing_idx = []
                        cached_vals = {}
                        for j, idx_j in enumerate(order_ce):
                            cand = cand_subset[idx_j]
                            cid = int(cand.get("id") or cand.get("trakt_id") or 0)
                            k = f"ce:{settings.ai_reranker_model}:{qhash}:{cid}"
                            keys.append(k)
                            v = rds.get(k)
                            if v is None:
                                missing_idx.append(j)
                            else:
                                try:
                                    cached_vals[j] = float(v)
                                except Exception:
                                    missing_idx.append(j)
                        scores_arr = np.zeros(len(order_ce), dtype=np.float32)
                        # Score only missing
                        if missing_idx:
                            texts_to_score = [ce_texts[j] for j in missing_idx]
                            fresh_scores = reranker.score(ce_query, texts_to_score, batch_size=64)
                            for pos, sc in zip(missing_idx, fresh_scores):
                                scores_arr[pos] = float(sc)
                                try:
                                    rds.set(keys[pos], str(float(sc)), ex=21600)
                                except Exception:
                                    pass
                        # Fill cached
                        for pos, sc in cached_vals.items():
                            scores_arr[pos] = float(sc)
                        ce_scores = scores_arr
                    except Exception:
                        ce_scores = np.array(reranker.score(ce_query, ce_texts, batch_size=64), dtype=np.float32)
                else:
                    ce_scores = np.array(reranker.score(ce_query, ce_texts, batch_size=64), dtype=np.float32)
                # Normalize CE scores to 0..1 and blend into semantic signal
                if ce_scores.size > 0:
                    ce_min, ce_max = float(ce_scores.min()), float(ce_scores.max())
                    ce_norm = (ce_scores - ce_min) / (ce_max - ce_min + 1e-8)
                    w = float(((_tun.get('ce_weight') if (_use_tun and (_tun.get('ce_weight') is not None)) else settings.ai_reranker_weight) or 0.3))
                    # Blend CE into semantic_sim for the same indices
                    for j, idx_j in enumerate(order_ce):
                        semantic_sim[idx_j] = (1.0 - w) * semantic_sim[idx_j] + w * ce_norm[j]
                    # Telemetry: CE usage
                    try:
                        from app.core.redis_client import get_redis_sync as __get_redis_sync
                        _r = __get_redis_sync()
                        _r.incrby("ai_telemetry:ce:invocations", 1)
                        _r.incrby("ai_telemetry:ce:scored_items", int(len(order_ce)))
                        # accumulate mean proxy; divide offline as needed
                        _r.incrbyfloat("ai_telemetry:ce:avg_norm_sum", float(np.mean(ce_norm)))
                    except Exception:
                        pass
    except Exception as e:
        logger.warning(f"[AI_SCORE][CE] Reranker skipped: {e}")

    # Optional LLM Judge blending (local provider; CPU-friendly)
    # For AI lists we allow running even if nightly prep hasn't completed; degrade gracefully when inputs are missing.
    judge_qhash = None
    judge_map = {}
    # If an order cache exists for this (user,prompt,filters,candidate-ids), skip LLM judge to save cost.
    
    try:
        if getattr(settings, "ai_llm_judge_enabled", False) and len(cand_subset) > 0 and not cached_order_ids:
            # Soft readiness: attempt to get redis, but do not block if flags are absent
            try:
                from app.core.redis_client import get_redis_sync as ____get_redis_sync
                _rdy = ____get_redis_sync()
            except Exception:
                _rdy = None
            # Load tunables if not already present
            try:
                _tun
                _use_tun
            except NameError:
                try:
                    _tun = _load_tunables(list_type)
                    _use_tun = _is_canary_enabled(_tun, user_id, list_type) if _tun else False
                except Exception:
                    _tun, _use_tun = {}, False
            topk_judge = min(int(((_tun.get('judge_topk') if (_use_tun and _tun.get('judge_topk')) else getattr(settings, "ai_llm_judge_topk", 100)) or 100)), len(cand_subset))
            if topk_judge > 0:
                proxy_for_judge = 0.6 * bm25_sim + 0.4 * (faiss_sim if np.any(faiss_sim > 0) else semantic_sim)
                order_judge = np.argsort(-proxy_for_judge)[:topk_judge]

                # Estimate requested list size; fall back to 50
                desired_count = int(filters.get("item_limit") or 50)
                # Compute query hash (to link judge reasons for explanations)
                try:
                    from .llm_judge import _hash_query as __judge_hash
                    judge_qhash = __judge_hash({
                        "prompt": prompt_text,
                        "filters": {k: filters.get(k) for k in [
                            "tone","mood","seasonal","genres","language","era","audience","pacing","runtime"
                        ] if filters.get(k) is not None},
                        "list_type": list_type,
                        "target_size": int(filters.get("item_limit") or 50),
                        "enrichment": {
                            "query_variants": variants[: min(5, len(variants))] if isinstance(variants, list) else [],
                        },
                    })
                except Exception:
                    judge_qhash = None

                # Include compact user profile summary; build lazily if cache is empty
                profile_summary = None
                try:
                    if _rdy is not None:
                        _sum_raw = _rdy.get(f"profile_summary:{user_id or 1}")
                        if _sum_raw:
                            import json as __json
                            profile_summary = __json.loads(_sum_raw)
                except Exception:
                    profile_summary = None
                if profile_summary is None and (user_id or 1):
                    try:
                        from .user_profile_text import UserTextProfileService
                        _prof = UserTextProfileService.get_or_build(int(user_id or 1))
                        profile_summary = {
                            "summary": _prof.get("summary_text", ""),
                            "tags": _prof.get("tags", []),
                        }
                    except Exception:
                        profile_summary = None

                query_summary = {
                    "prompt": prompt_text,
                    "filters": {k: filters.get(k) for k in [
                        "tone","mood","seasonal","genres","language","era","audience","pacing","runtime"
                    ] if filters.get(k) is not None},
                    "list_type": list_type,
                    "target_size": desired_count,
                    "enrichment": {
                        "query_variants": variants[: min(5, len(variants))] if isinstance(variants, list) else [],
                    },
                    "user_profile": profile_summary or {},
                }
                # Build judge candidates and enrich with compact item profile when available (lazy)
                judge_cands = []
                
                for _i in order_judge:
                    _c = dict(cand_subset[int(_i)])
                    # Try to resolve a candidate_id for profile generation
                    try:
                        cid = int(_c.get("_candidate_id") or _c.get("candidate_id") or _c.get("id") or 0)
                    except Exception:
                        cid = 0
                    if cid:
                        try:
                            from .profile_prep import ItemProfileService
                            _p = ItemProfileService.get_or_build(cid)
                            prof = _p.get("profile", {})
                            for k in ["genres","keywords","overview","tagline","popularity","vote_average","vote_count","original_language","runtime"]:
                                if (_c.get(k) in (None, [], "")) and (k in prof):
                                    _c[k] = prof[k]
                            try:
                                _c["id"] = int(_c.get("id") or cid)
                            except Exception:
                                pass
                        except Exception:
                            pass
                    
                    judge_cands.append(_c)
                try:
                    from .llm_judge import judge_scores, JudgeConfig
                    cfg = JudgeConfig(
                        enabled=True,
                        weight=float(getattr(settings, "ai_llm_judge_weight", 0.15) or 0.15),
                        timeout_seconds=int(getattr(settings, "ai_llm_timeout_seconds", 8) or 8),
                        provider=str(getattr(settings, "ai_llm_judge_provider", "ollama")),
                        api_base=str(getattr(settings, "ai_llm_api_base", "http://ollama:11434")),
                        api_key_env=str(getattr(settings, "ai_llm_api_key_env", "")),
                        model=str(getattr(settings, "ai_llm_judge_model", "phi3.5:3.8b-mini-instruct-q4_K_M")),
                        batch_size=20,
                    )
                    judge_map = judge_scores(query_summary, judge_cands, cfg=cfg, persona=persona_str, history=history_str)
                except Exception:
                    judge_map = {}

                if judge_map:
                    id_to_local = {}
                    for idx in range(len(cand_subset)):
                        try:
                            cid = int(cand_subset[idx].get("id"))
                            id_to_local[cid] = idx
                        except Exception:
                            pass
                    judge_bonus = np.zeros(len(cand_subset), dtype=np.float32)
                    for cid, val in judge_map.items():
                        if cid in id_to_local:
                            j = id_to_local[cid]
                            try:
                                judge_bonus[j] = max(0.0, min(1.0, float(val)))
                            except Exception:
                                judge_bonus[j] = 0.0
                    if np.any(judge_bonus > 0):
                        wj = float(((_tun.get('judge_weight') if (_use_tun and (_tun.get('judge_weight') is not None)) else getattr(settings, "ai_llm_judge_weight", 0.15)) or 0.15))
                        semantic_sim = semantic_sim + wj * judge_bonus
                        # Telemetry
                        try:
                            from app.core.redis_client import get_redis_sync as ___get_redis_sync
                            _rt = ___get_redis_sync()
                            _rt.incrby("ai_telemetry:llmjudge:invocations", 1)
                            _rt.incrby("ai_telemetry:llmjudge:scored_items", int(len(judge_map)))
                        except Exception:
                            pass
    except Exception as e:
        logger.debug(f"[AI_SCORE][LLMJ] Judge skipped: {e}")

    # 5) Weighting by list type
    # Adjusted to reduce literal keyword bias (e.g., titles containing "dark") and improve semantic/genre alignment
    # For dynamic lists (mood/theme/fusion), keep negative novelty to prefer mainstream content a bit
    weights_defaults = {
        # Chat: increased phrase weight for subgenre/style matching (buddy cop, heist, etc.)
        # Increased genre weight to prioritize multi-genre alignment over pure semantic similarity
        "chat": {"sim": 0.20, "semantic": 0.30, "genre": 0.20, "rating": 0.10, "novelty": 0.02, "phrase": 0.15, "actor_studio": 0.06, "recency": 0.03, "watch_history": 0.05, "tone": 0.00, "multi_genre_strict": 0.10},
        # Mood/Theme/Fusion: reduce phrase reliance; increase semantic and genre alignment
        "mood": {"sim": 0.12, "semantic": 0.28, "genre": 0.14, "rating": 0.10, "novelty": -0.12, "phrase": 0.03, "actor_studio": 0.06, "recency": 0.16, "watch_history": 0.08, "tone": 0.01},
        "theme": {"sim": 0.12, "semantic": 0.28, "genre": 0.14, "rating": 0.10, "novelty": -0.12, "phrase": 0.03, "actor_studio": 0.06, "recency": 0.16, "watch_history": 0.08, "tone": 0.01},
        "fusion": {"sim": 0.10, "semantic": 0.30, "genre": 0.14, "rating": 0.10, "novelty": -0.12, "phrase": 0.03, "actor_studio": 0.06, "recency": 0.16, "watch_history": 0.09, "tone": 0.01},
    }
    weights = weights_defaults.get(list_type, {"sim": 0.25, "semantic": 0.25, "genre": 0.0, "rating": 0.10, "novelty": 0.10, "phrase": 0.10, "actor_studio": 0.08, "recency": 0.05, "watch_history": 0.09, "tone": 0.00})

    # Load per-list-type tunables and canary-gate them
    _tun = _load_tunables(list_type)
    _use_tun = _is_canary_enabled(_tun, user_id, list_type) if _tun else False
    if _use_tun and isinstance(_tun.get("weights"), dict):
        try:
            for k, v in _tun["weights"].items():
                if k in weights:
                    weights[k] = float(v)
        except Exception:
            pass
    # RRF weight for hybrid signal
    rrf_w = float(_tun.get("rrf_weight", 0.15)) if _use_tun else 0.15
    # CE/LLM topK/weights overrides
    ce_topk_override = int(_tun.get("ce_topk")) if _use_tun and _tun.get("ce_topk") else None
    ce_weight_override = float(_tun.get("ce_weight")) if _use_tun and (_tun.get("ce_weight") is not None) else None
    judge_topk_override = int(_tun.get("judge_topk")) if _use_tun and _tun.get("judge_topk") else None
    judge_weight_override = float(_tun.get("judge_weight")) if _use_tun and (_tun.get("judge_weight") is not None) else None
    # MMR diversification tunables
    apply_mmr = bool(_tun.get("apply_mmr", False)) if _use_tun else False
    mmr_lambda = float(_tun.get("diversity_lambda", 0.6)) if _use_tun else 0.0

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
        "dark": ["family", "animation", "kids", "children", "musical", "anime"],
        "gritty": ["family", "animation", "kids", "anime"],
        "serious": ["family", "animation", "kids", "anime"],
        "thrilling": ["family", "animation", "kids", "anime"],
        "scary": ["family", "animation", "kids", "anime"],
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

    # 5.1b2) Anime/Manga penalty unless explicitly requested
    # Penalize anime/manga content unless user explicitly included "anime" or "manga" in genres
    anime_penalty = np.zeros(len(cand_subset), dtype=np.float32)
    user_genres_lower = [str(g).lower() for g in (filters.get("genres") or [])]
    anime_explicitly_requested = any(g in user_genres_lower for g in ["anime", "manga", "animation"])
    
    if not anime_explicitly_requested:
        for i, c in enumerate(cand_subset):
            cg = _cand_genre_set(c)
            if any(g in cg for g in ["anime", "manga"]):
                anime_penalty[i] = -0.20  # Strong penalty to push anime down unless requested

    # 5.1c) Seasonal bonus (e.g., christmas/halloween) if seasonal words present
    seasonal_bonus = np.zeros(len(cand_subset), dtype=np.float32)
    seasonal_words = [s.lower() for s in (filters.get("seasonal") or [])]
    if seasonal_words:
        for i, c in enumerate(cand_subset):
            text_blob = " ".join([str(c.get(k) or "").lower() for k in ["title", "overview", "tagline", "keywords"]])
            hits = sum(1 for w in seasonal_words if w in text_blob)
            if hits:
                # Doubled from 0.6 to 1.2 - seasonal context is highly specific and should dominate
                seasonal_bonus[i] = min(1.0, hits / max(1, len(seasonal_words))) * 1.2

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
        + rrf_w * rrf_norm                    # Hybrid retrieval (RRF fusion of BM25 + semantic)
        + weights["genre"] * genre_overlap
        + weights.get("multi_genre_strict", 0.0) * multi_genre_strict  # Strict multi-genre requirement
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
        + anime_penalty          # push anime/manga down unless explicitly requested
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
        # For seasonal cues (highest priority), check title/overview/keywords/tagline ONLY to avoid false matches from cast
        if seasonal_cues and any(cue in seasonal_cues for cue in cues):
            seasonal_blob = " ".join([str(cand.get(k) or "").lower() for k in ["title", "overview", "keywords", "tagline"]])
            has_seasonal_match = any(cue in seasonal_blob for cue in seasonal_cues)
            
            if has_seasonal_match:
                return True
            
            # No seasonal match - only fail if ALL cues being checked are seasonal
            if all(cue in seasonal_cues for cue in cues):
                return False
            # Otherwise fall through to check other cues below
        
        # Standard text fields for other cues (actors, directors, studios, seeds, phrases, etc.)
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
    # Reduced from 33% to 25% for seasonal queries - better to have fewer accurate Christmas movies than force non-Christmas content
    quota_pct = 0.25 if seasonal_cues else 0.33
    quota = max(baseline, math.ceil(quota_pct * target_top_k))
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

    # If we have a cached order, apply it to results after computing base signals, then persist the order again.
    results = []
    for i, c in enumerate(cand_subset):
        item = {
            **c,
            "bm25_sim": float(bm25_sim[i]),
            "semantic_sim": float(semantic_sim[i]),
            "genre_overlap": float(genre_overlap[i]),
            "multi_genre_strict": float(multi_genre_strict[i]),
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
        # Surface LLM judge score for ranker strategies if available
        try:
            if judge_map:
                cid = _stable_cand_id(c)
                if cid in judge_map:
                    item["judge_score"] = float(judge_map[cid])
        except Exception:
            pass
        if judge_qhash:
            item["judge_query_hash"] = judge_qhash
        item["explanation_meta"] = build_explanation_meta(item)
        results.append(item)

    # Apply ranker strategy (classic by default; can switch to llm)
    try:
        strategy = str(getattr(settings, "ai_ranker_strategy", "classic") or "classic").lower()
    except Exception:
        strategy = "classic"
    try:
        # Default: classic or simple LLM ranker
        if strategy in ("classic", "llm"):
            ranker = LLMRanker() if strategy == "llm" else ClassicRanker()
            order_idx = ranker.rank(results, context={"user_id": user_id, "list_type": list_type})
            if order_idx and len(order_idx) == len(results):
                results = [results[i] for i in order_idx]
            else:
                results.sort(key=lambda x: x.get("final_score", 0.0), reverse=True)
        else:
            results.sort(key=lambda x: x.get("final_score", 0.0), reverse=True)
    except Exception:
        results.sort(key=lambda x: x.get("final_score", 0.0), reverse=True)

    # Optional pairwise tournament refinement with LLM judge (phi3:mini)
    try:
        if bool(getattr(settings, "ai_llm_pairwise_enabled", False)) or strategy == "llm-pairwise":
            max_pairs = int(getattr(settings, "ai_llm_pairwise_max_pairs", 120) or 120)
            if results and max_pairs > 1:
                # Build user context for LLM judge
                from app.services.persona_helper import PersonaHelper
                persona_data = PersonaHelper.format_for_prompt(
                    user_id=user.get("id", 1),
                    include_history=True,
                    include_pairwise=True
                )
                persona_str = persona_data.get("persona", "")
                history_str = persona_data.get("history", "")
                
                # Create compact intent from prompt and filters
                intent_parts = [prompt_text or "recommendations"]
                if filters:
                    if filters.get("genres"):
                        intent_parts.append(f"genres: {', '.join(filters['genres'][:3])}")
                    if filters.get("moods"):
                        intent_parts.append(f"moods: {', '.join(filters['moods'][:2])}")
                intent_str = " | ".join(intent_parts)
                
                # Call LLM-based pairwise ranker
                pr = PairwiseRanker()
                order_idx, pairs_used = pr.rank(
                    items=results,
                    user_context=user,
                    intent=intent_str,
                    persona=persona_str,
                    history=history_str,
                    max_pairs=max_pairs,
                    batch_size=12
                )
                
                if order_idx and len(order_idx) == len(results):
                    results = [results[i] for i in order_idx]
                    logger.info(f"[Scorer] LLM pairwise reranking applied with {pairs_used} pairs")
                
                # Telemetry: record usage
                try:
                    from app.core.redis_client import get_redis_sync as _get_rpw
                    _rpw = _get_rpw()
                    _rpw.incrby("ai_telemetry:pairwise:applied", 1)
                    _rpw.incrby("ai_telemetry:pairwise:pairs", int(pairs_used))
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"[Scorer] Pairwise LLM reranking failed: {e}")
    try:
        if cached_order_ids:
            id_to_result = {}
            for it in results:
                try:
                    cid = _stable_cand_id(it)
                except Exception:
                    cid = 0
                if cid:
                    id_to_result[cid] = it
            reordered = [id_to_result[cid] for cid in cached_order_ids if cid in id_to_result]
            # Keep original items not in cache order at the end
            seen = set(cached_order_ids)
            tail = [it for it in results if _stable_cand_id(it) not in seen]
            if reordered:
                results = reordered + tail
    except Exception:
        pass
    # Optional MMR diversity re-rank (canary/tunable)
    try:
        if apply_mmr and len(results) > 0:
            K = min(int(filters.get("item_limit") or 50), len(results))
            def _parse_genres_val(v):
                try:
                    if isinstance(v, str):
                        return set(json.loads(v)) if v.startswith("[") else set(g.strip().lower() for g in v.split(",") if g.strip())
                    if isinstance(v, list):
                        return set(str(g).lower() for g in v)
                except Exception:
                    return set()
                return set()
            def _sim(a, b):
                ga = _parse_genres_val(a.get("genres"))
                gb = _parse_genres_val(b.get("genres"))
                inter = len(ga & gb)
                uni = len(ga | gb) or 1
                genre_sim = inter / uni
                media_sim = 1.0 if (a.get("media_type") or a.get("type")) == (b.get("media_type") or b.get("type")) else 0.0
                try:
                    year_a = int(a.get("year") or 0); year_b = int(b.get("year") or 0)
                except Exception:
                    year_a, year_b = 0, 0
                year_sim = max(0.0, 1.0 - abs(year_a - year_b) / 25.0) if (year_a and year_b) else 0.0
                return 0.6*genre_sim + 0.25*media_sim + 0.15*year_sim
            selected = []
            remaining = results.copy()
            selected.append(remaining.pop(0))
            lam = float(mmr_lambda or 0.6)
            while len(selected) < K and remaining:
                best_idx, best_score = 0, -1e9
                for idx, cand in enumerate(remaining):
                    rel = float(cand.get("final_score", 0.0))
                    max_sim = 0.0
                    for s in selected:
                        sim = _sim(cand, s)
                        if sim > max_sim:
                            max_sim = sim
                    mmr = lam*rel - (1.0 - lam)*max_sim
                    if mmr > best_score:
                        best_score, best_idx = mmr, idx
                selected.append(remaining.pop(best_idx))
            results = selected + remaining
    except Exception:
        pass
    # Persist final order in cache for stability next runs
    try:
        if cache_key and results:
            from app.core.redis_client import get_redis_sync as _get_r2
            _r2 = _get_r2()
            out_ids = [_stable_cand_id(it) for it in results]
            import json as _json
            _r2.setex(cache_key, int(getattr(settings, "ai_rank_order_cache_ttl", 21600) or 21600), _json.dumps(out_ids))
    except Exception:
        pass

    # Finally, limit to requested size for AI lists (default 50)
    try:
        K = min(int(filters.get("item_limit") or 50), 50)
    except Exception:
        K = 50
    if K > 0 and len(results) > K:
        results = results[:K]
    return results
