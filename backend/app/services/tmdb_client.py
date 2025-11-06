"""
TMDB client for WatchBuddy.
- Async httpx client, no torch, no .env.
- Reads TMDB API key from encrypted DB storage.
- Handles 429 with exponential backoff and Retry-After.
- No in-module caching; results cached by caller.
"""
import logging
import asyncio
import json
from typing import Optional, Dict, List
import httpx
from app.core.redis_client import get_redis

TMDB_BASE = "https://api.themoviedb.org/3"
logger = logging.getLogger(__name__)

async def get_tmdb_api_key() -> Optional[str]:
    """Read TMDB API key from Redis-backed settings."""
    r = get_redis()
    return await r.get("settings:global:tmdb_api_key")

async def fetch_tmdb_metadata(tmdb_id: int, media_type: str = 'movie') -> Optional[Dict]:
    """Fetch comprehensive metadata from TMDB with rate limiting and backoff.
    
    Includes: credits (cast), keywords, production details, and TV-specific info.
    """
    api_key = await get_tmdb_api_key()
    if not api_key:
        logger.warning("TMDB API key not configured")
        return None
    
    url = f"{TMDB_BASE}/{media_type}/{tmdb_id}"
    # Request all enrichments in a single API call
    append_to_response = "credits,keywords" if media_type == 'movie' else "credits,keywords,content_ratings"
    params = {"api_key": api_key, "append_to_response": append_to_response}
    
    from app.services.rate_limit import with_backoff
    
    async def make_request():
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
    
    try:
        return await with_backoff(
            make_request,
            max_retries=4,
            service="tmdb_api",
            user_id="global"
        )
    except Exception as e:
        logger.debug(f"TMDB API failed for {media_type}/{tmdb_id}: {e}")
        return None

async def fetch_tmdb_metadata_with_fallback(item_ids: Dict, media_type: str = 'movie') -> Optional[Dict]:
    """
    Fetch TMDB metadata using multiple ID fallbacks.
    Tries: 1) Direct TMDB ID, 2) Find external ID endpoint with IMDB, 3) Search by title fallback
    
    Args:
        item_ids: Dict with possible keys: tmdb, imdb, trakt, plus title for search fallback
        media_type: 'movie' or 'tv'
    """
    api_key = await get_tmdb_api_key()
    if not api_key:
        logger.warning("TMDB API key not configured")
        return None
    
    # Strategy 1: Direct TMDB ID lookup
    tmdb_id = item_ids.get('tmdb')
    if tmdb_id:
        result = await fetch_tmdb_metadata(tmdb_id, media_type)
        if result:
            return result
        logger.debug(f"Direct TMDB lookup failed for {media_type}/{tmdb_id}")
    
    # Strategy 2: Find by external ID (IMDB)
    imdb_id = item_ids.get('imdb')
    if imdb_id:
        result = await _find_by_external_id(imdb_id, 'imdb_id', media_type)
        if result:
            logger.debug(f"Found TMDB match via IMDB ID {imdb_id}")
            return result
        logger.debug(f"IMDB external ID lookup failed for {imdb_id}")
    
    # Strategy 3: Search by title (if provided)
    title = item_ids.get('title')
    year = item_ids.get('year')
    if title:
        result = await _search_by_title(title, media_type, year)
        if result:
            logger.debug(f"Found TMDB match via title search: {title}")
            return result
        logger.debug(f"Title search failed for: {title}")
    
    logger.debug(f"All TMDB lookup strategies failed for item: {item_ids}")
    return None

async def _find_by_external_id(external_id: str, id_type: str, media_type: str) -> Optional[Dict]:
    """Find TMDB item by external ID (IMDB, etc.)"""
    api_key = await get_tmdb_api_key()
    if not api_key:
        return None
    
    url = f"{TMDB_BASE}/find/{external_id}"
    params = {"api_key": api_key, "external_source": id_type}
    
    from app.services.rate_limit import with_backoff
    
    async def make_request():
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
    
    try:
        result = await with_backoff(
            make_request,
            max_retries=4,
            service="tmdb_api",
            user_id="global"
        )
        
        # Extract the first matching result
        if result:
            items = result.get('movie_results', []) if media_type == 'movie' else result.get('tv_results', [])
            if items:
                # Fetch full details for the first match
                tmdb_id = items[0].get('id')
                if tmdb_id:
                    return await fetch_tmdb_metadata(tmdb_id, media_type)
        
        return None
    except Exception as e:
        logger.debug(f"External ID lookup failed for {external_id}: {e}")
        return None

async def _search_by_title(title: str, media_type: str, year: Optional[int] = None) -> Optional[Dict]:
    """Search TMDB by title and return the best match"""
    if media_type == 'movie':
        result = await search_movies(title, year=year)
    else:
        result = await search_tv(title, first_air_date_year=year)
    
    if result and result.get('results'):
        # Return full details for the first (most relevant) match
        first_match = result['results'][0]
        tmdb_id = first_match.get('id')
        if tmdb_id:
            return await fetch_tmdb_metadata(tmdb_id, media_type)
    
    return None

def merge_tmdb_trakt(trakt_item: Dict, tmdb_data: Dict) -> Dict:
    """Merge TMDB metadata with Trakt item."""
    if not tmdb_data:
        return trakt_item
    
    merged = trakt_item.copy()
    
    # Add TMDB-specific fields
    merged['tmdb'] = {
        'poster_path': tmdb_data.get('poster_path'),
        'backdrop_path': tmdb_data.get('backdrop_path'),
        'overview': tmdb_data.get('overview'),
        'genres': [g['name'] for g in tmdb_data.get('genres', [])],
        'keywords': [k['name'] for k in tmdb_data.get('keywords', {}).get('keywords', [])]
    }
    
    return merged

async def discover_movies(original_language: Optional[str] = None, with_genres: Optional[str] = None, page: int = 1) -> Optional[Dict]:
    """Discover movies with optional language and genre filters. Returns raw TMDB payload for the page."""
    api_key = await get_tmdb_api_key()
    if not api_key:
        logger.warning("TMDB API key not configured")
        return None
    url = f"{TMDB_BASE}/discover/movie"
    params = {
        "api_key": api_key,
        "sort_by": "popularity.desc",
        "page": page,
        "include_adult": False,
        "vote_count.gte": 10,
    }
    if original_language:
        params["with_original_language"] = original_language
    if with_genres:
        params["with_genres"] = with_genres
    from app.services.rate_limit import with_backoff
    async def make_request():
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
    try:
        return await with_backoff(make_request, max_retries=4, service="tmdb_api", user_id="global")
    except Exception as e:
        logger.error(f"TMDB discover movies failed: {e}")
        return None

async def discover_tv(original_language: Optional[str] = None, with_genres: Optional[str] = None, page: int = 1) -> Optional[Dict]:
    """Discover TV shows with optional language and genre filters. Returns raw TMDB payload for the page."""
    api_key = await get_tmdb_api_key()
    if not api_key:
        logger.warning("TMDB API key not configured")
        return None
    url = f"{TMDB_BASE}/discover/tv"
    params = {
        "api_key": api_key,
        "sort_by": "popularity.desc",
        "page": page,
        "include_adult": False,
        "vote_count.gte": 10,
    }
    if original_language:
        params["with_original_language"] = original_language
    if with_genres:
        params["with_genres"] = with_genres
    from app.services.rate_limit import with_backoff
    async def make_request():
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
    try:
        return await with_backoff(make_request, max_retries=4, service="tmdb_api", user_id="global")
    except Exception as e:
        logger.error(f"TMDB discover tv failed: {e}")
        return None

async def search_multi(query: str, page: int = 1, language: str = "en-US") -> Optional[Dict]:
    """Search TMDB multi-endpoint for movies and TV shows."""
    api_key = await get_tmdb_api_key()
    if not api_key:
        logger.warning("TMDB API key not configured")
        return None
    
    url = f"{TMDB_BASE}/search/multi"
    params = {
        "api_key": api_key,
        "query": query,
        "page": page,
        "language": language,
        "include_adult": False,
    }
    
    from app.services.rate_limit import with_backoff
    async def make_request():
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
    
    try:
        return await with_backoff(make_request, max_retries=4, service="tmdb_api", user_id="global")
    except Exception as e:
        logger.debug(f"TMDB search failed for '{query}': {e}")
        return None

async def search_movies(query: str, page: int = 1, language: str = "en-US", year: Optional[int] = None) -> Optional[Dict]:
    """Search TMDB for movies with optional year filter."""
    api_key = await get_tmdb_api_key()
    if not api_key:
        logger.warning("TMDB API key not configured")
        return None
    
    url = f"{TMDB_BASE}/search/movie"
    params = {
        "api_key": api_key,
        "query": query,
        "page": page,
        "language": language,
        "include_adult": False,
    }
    if year:
        params["year"] = year
    
    from app.services.rate_limit import with_backoff
    async def make_request():
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
    
    try:
        return await with_backoff(make_request, max_retries=4, service="tmdb_api", user_id="global")
    except Exception as e:
        logger.debug(f"TMDB movie search failed for '{query}': {e}")
        return None

async def search_tv(query: str, page: int = 1, language: str = "en-US", first_air_date_year: Optional[int] = None) -> Optional[Dict]:
    """Search TMDB for TV shows with optional year filter."""
    api_key = await get_tmdb_api_key()
    if not api_key:
        logger.warning("TMDB API key not configured")
        return None
    
    url = f"{TMDB_BASE}/search/tv"
    params = {
        "api_key": api_key,
        "query": query,
        "page": page,
        "language": language,
        "include_adult": False,
    }
    if first_air_date_year:
        params["first_air_date_year"] = first_air_date_year
    
    from app.services.rate_limit import with_backoff
    async def make_request():
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
    
    try:
        return await with_backoff(make_request, max_retries=4, service="tmdb_api", user_id="global")
    except Exception as e:
        logger.debug(f"TMDB TV search failed for '{query}': {e}")
        return None


def extract_enriched_fields(tmdb_data: Dict, media_type: str) -> Dict:
    """Extract all enriched fields from TMDB API response for persistent storage.
    
    Returns a dict with all fields needed for PersistentCandidate model including:
    - Core metadata (title, overview, tagline, etc.)
    - Credits (cast, directors, creators)
    - Production details (companies, countries, languages)
    - Financial data (budget, revenue)
    - TV-specific fields (networks, seasons, episodes, air dates)
    - Keywords and homepage
    
    Args:
        tmdb_data: Full TMDB API response (with credits, keywords appended)
        media_type: 'movie' or 'show'/'tv'
    
    Returns:
        Dict with extracted and normalized fields
    """
    fields = {}
    
    # Core fields
    fields['tagline'] = tmdb_data.get('tagline', '')
    fields['homepage'] = tmdb_data.get('homepage', '')
    fields['status'] = tmdb_data.get('status', '')
    fields['runtime'] = tmdb_data.get('runtime') if media_type == 'movie' else None
    
    # Extract genres as JSON array of names
    genres = tmdb_data.get('genres', [])
    fields['genres'] = json.dumps([g.get('name', '') for g in genres if isinstance(g, dict)])
    
    # Extract keywords as JSON array of names
    keywords_obj = tmdb_data.get('keywords', {})
    if media_type == 'movie':
        keywords_list = keywords_obj.get('keywords', [])
    else:  # TV
        keywords_list = keywords_obj.get('results', [])
    fields['keywords'] = json.dumps([k.get('name', '') for k in keywords_list if isinstance(k, dict)])
    
    # Extract cast (top 20 actors)
    credits = tmdb_data.get('credits', {})
    cast_list = credits.get('cast', [])[:20]
    fields['cast'] = json.dumps([c.get('name', '') for c in cast_list if isinstance(c, dict)])
    
    # Extract production companies as JSON array
    prod_companies = tmdb_data.get('production_companies', [])
    fields['production_companies'] = json.dumps([pc.get('name', '') for pc in prod_companies if isinstance(pc, dict)])
    
    # Extract production countries as JSON array of ISO codes
    prod_countries = tmdb_data.get('production_countries', [])
    fields['production_countries'] = json.dumps([pc.get('iso_3166_1', '') for pc in prod_countries if isinstance(pc, dict)])
    
    # Extract spoken languages as JSON array
    spoken_langs = tmdb_data.get('spoken_languages', [])
    fields['spoken_languages'] = json.dumps([sl.get('iso_639_1', '') for sl in spoken_langs if isinstance(sl, dict)])
    
    # Financial data (movies only)
    if media_type == 'movie':
        fields['budget'] = tmdb_data.get('budget', 0)
        fields['revenue'] = tmdb_data.get('revenue', 0)
        
        # Note: director and writers fields removed - not in PersistentCandidate model
        # Director info can be extracted from created_by (TV) or cast (movies) if needed
    else:
        fields['budget'] = None
        fields['revenue'] = None
    
    # TV-specific fields
    if media_type in ('tv', 'show'):
        fields['number_of_seasons'] = tmdb_data.get('number_of_seasons')
        fields['number_of_episodes'] = tmdb_data.get('number_of_episodes')
        fields['in_production'] = tmdb_data.get('in_production', False)
        fields['first_air_date'] = tmdb_data.get('first_air_date', '')
        fields['last_air_date'] = tmdb_data.get('last_air_date', '')
        
        # Extract episode runtime as JSON array
        episode_run_time = tmdb_data.get('episode_run_time', [])
        fields['episode_run_time'] = json.dumps(episode_run_time) if episode_run_time else '[]'
        
        # Extract creators as JSON array
        created_by = tmdb_data.get('created_by', [])
        fields['created_by'] = json.dumps([c.get('name', '') for c in created_by if isinstance(c, dict)])
        
        # Extract networks as JSON array
        networks = tmdb_data.get('networks', [])
        fields['networks'] = json.dumps([n.get('name', '') for n in networks if isinstance(n, dict)])
    else:
        # Movies don't have these fields
        fields['number_of_seasons'] = None
        fields['number_of_episodes'] = None
        fields['in_production'] = None
        fields['first_air_date'] = ''
        fields['last_air_date'] = ''
        fields['episode_run_time'] = '[]'
        fields['created_by'] = '[]'
        fields['networks'] = '[]'
    
    return fields


async def fetch_tmdb_trending(media_type: str = 'movie', time_window: str = 'week', page: int = 1) -> Optional[Dict]:
    """
    Fetch TMDB trending items.
    
    Args:
        media_type: 'movie', 'tv', or 'all'
        time_window: 'day' or 'week'
        page: Page number (1-indexed)
    
    Returns:
        {
            'page': 1,
            'results': [{'id': 123, 'title': '...', ...}],
            'total_pages': 10,
            'total_results': 200
        }
    """
    api_key = await get_tmdb_api_key()
    if not api_key:
        logger.warning("TMDB API key not configured")
        return None
    
    url = f"{TMDB_BASE}/trending/{media_type}/{time_window}"
    params = {"api_key": api_key, "page": page}
    
    from app.services.rate_limit import with_backoff
    
    async def make_request():
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
    
    try:
        return await with_backoff(
            make_request,
            max_retries=4,
            service="tmdb_api",
            user_id="global"
        )
    except Exception as e:
        logger.debug(f"TMDB trending API failed for {media_type}/{time_window}: {e}")
        return None


async def fetch_tmdb_popular(media_type: str = 'movie', page: int = 1) -> Optional[Dict]:
    """
    Fetch TMDB popular items.
    
    Args:
        media_type: 'movie' or 'tv'
        page: Page number (1-indexed)
    
    Returns same structure as fetch_tmdb_trending
    """
    api_key = await get_tmdb_api_key()
    if not api_key:
        logger.warning("TMDB API key not configured")
        return None
    
    media_path = 'movie' if media_type == 'movie' else 'tv'
    url = f"{TMDB_BASE}/{media_path}/popular"
    params = {"api_key": api_key, "page": page}
    
    from app.services.rate_limit import with_backoff
    
    async def make_request():
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
    
    try:
        return await with_backoff(
            make_request,
            max_retries=4,
            service="tmdb_api",
            user_id="global"
        )
    except Exception as e:
        logger.debug(f"TMDB popular API failed for {media_type}: {e}")
        return None


async def fetch_tmdb_upcoming(page: int = 1, region: str = 'US') -> Optional[Dict]:
    """
    Fetch TMDB upcoming movies (next few weeks).
    
    Args:
        page: Page number (1-indexed)
        region: ISO 3166-1 country code (default 'US')
    
    Returns same structure as fetch_tmdb_trending
    """
    api_key = await get_tmdb_api_key()
    if not api_key:
        logger.warning("TMDB API key not configured")
        return None
    
    url = f"{TMDB_BASE}/movie/upcoming"
    params = {"api_key": api_key, "page": page, "region": region}
    
    from app.services.rate_limit import with_backoff
    
    async def make_request():
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
    
    try:
        return await with_backoff(
            make_request,
            max_retries=4,
            service="tmdb_api",
            user_id="global"
        )
    except Exception as e:
        logger.debug(f"TMDB upcoming API failed: {e}")
        return None
