"""
items.py - Item page API endpoints
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Dict, Any, Optional
import logging
from app.core.database import get_db
from app.models import PersistentCandidate, UserRating
from app.services.trakt_client import TraktClient
from app.services.tmdb_client import get_tmdb_api_key, fetch_tmdb_metadata
from app.services.ai_engine.candidate_enricher import enrich_candidates_async
from app.services.similar_items import SimilarItemsService
import httpx
import json
from app.core.redis_client import get_redis
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/{media_type}/{tmdb_id}")
async def get_item_details(
    media_type: str,
    tmdb_id: int,
    user_id: int = 1,
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Get comprehensive item details for item page.
    
    Returns:
    - Full persistent_candidate metadata
    - Watch status from Trakt history
    - User rating (thumbs up/down)
    - Trailer URL (if available from TMDB, no DB storage)
    
    If metadata is stale/missing, triggers enrichment via candidate_enricher.
    """
    # Normalize media type
    if media_type not in ['movie', 'tv', 'show']:
        raise HTTPException(status_code=400, detail="Invalid media_type")
    
    normalized_type = 'tv' if media_type in ['tv', 'show'] else 'movie'
    
    # Fetch from persistent_candidates - try TMDB ID first, then Trakt ID as fallback
    logger.info(f"Looking for item: {normalized_type}/{tmdb_id}")
    # Query with OR condition to handle both 'tv' and 'show' in database
    item = db.query(PersistentCandidate).filter(
        PersistentCandidate.tmdb_id == tmdb_id,
        PersistentCandidate.media_type.in_(['tv', 'show']) if normalized_type == 'tv' else PersistentCandidate.media_type == 'movie'
    ).first()
    
    # If not found by TMDB ID, try Trakt ID (in case frontend sent Trakt ID by mistake)
    if not item:
        logger.warning(f"Item not found by TMDB ID {tmdb_id}, trying Trakt ID as fallback")
        item = db.query(PersistentCandidate).filter(
            PersistentCandidate.trakt_id == tmdb_id,
            PersistentCandidate.media_type.in_(['tv', 'show']) if normalized_type == 'tv' else PersistentCandidate.media_type == 'movie'
        ).first()
        if item:
            logger.info(f"Found item by Trakt ID {tmdb_id} -> TMDB ID {item.tmdb_id}")
    
    if not item:
        # Try to find ANY item with this ID to debug
        any_item = db.query(PersistentCandidate).filter(
            (PersistentCandidate.tmdb_id == tmdb_id) | (PersistentCandidate.trakt_id == tmdb_id)
        ).first()
        if any_item:
            logger.error(f"Found item with wrong media_type: {any_item.media_type} (requested {normalized_type}), tmdb_id={any_item.tmdb_id}, trakt_id={any_item.trakt_id}")
        else:
            logger.error(f"Item {tmdb_id} NOT IN DATABASE AT ALL (tried TMDB ID and Trakt ID)")
        raise HTTPException(status_code=404, detail=f"Item not found in database: {normalized_type}/{tmdb_id}")
    
    # Check if metadata is stale (older than 90 days) or missing critical fields
    needs_enrichment = False
    if item.last_refreshed:
        age_days = (datetime.utcnow() - item.last_refreshed).days
        if age_days > 90:
            needs_enrichment = True
            logger.info(f"Item {tmdb_id} metadata is stale ({age_days} days old)")
    
    # Check for missing critical fields
    if not item.overview or not item.poster_path or not item.genres:
        needs_enrichment = True
        logger.info(f"Item {tmdb_id} has missing metadata fields")
    
    # Check for planned/unreleased items that might have been released
    if item.status and item.status.lower() in ['planned', 'in production', 'post production', 'rumored']:
        needs_enrichment = True
        logger.info(f"Item {tmdb_id} has status '{item.status}' - checking for updates")
    
    # Check if release date has passed but status is still planned
    if item.release_date:
        try:
            from datetime import datetime as dt
            release_date = dt.strptime(item.release_date, '%Y-%m-%d')
            if release_date < dt.utcnow() and item.status and item.status.lower() in ['planned', 'in production']:
                needs_enrichment = True
                logger.info(f"Item {tmdb_id} release date {item.release_date} has passed but status is '{item.status}' - refreshing")
        except:
            pass
    
    # Trigger enrichment if needed (async, don't wait)
    if needs_enrichment:
        try:
            logger.info(f"Enriching metadata for {normalized_type}/{tmdb_id}")
            # Use ai_engine's on-demand enrichment
            enriched = await enrich_candidates_async(db, [{
                'id': item.id,
                'tmdb_id': item.tmdb_id,
                'media_type': item.media_type
            }], max_age_days=90)
            # Refresh item from DB after enrichment
            db.refresh(item)
        except Exception as e:
            logger.error(f"Failed to enrich item {tmdb_id}: {e}")
            # Continue with stale data rather than failing
    
    # Get watch status from Trakt (check last 5000 items)
    watched = False
    watched_at = None
    try:
        client = TraktClient(user_id, db)
        history = await client.get_watch_history(media_type=normalized_type, limit=5000)
        for entry in history:
            if entry.get('trakt_id') == item.trakt_id:
                watched = True
                watched_at = entry.get('watched_at')
                break
    except Exception as e:
        logger.debug(f"Failed to fetch watch status: {e}")
    
    # Get user rating (thumbs up/down)
    rating = None
    if item.trakt_id:
        user_rating = db.query(UserRating).filter(
            UserRating.user_id == user_id,
            UserRating.trakt_id == item.trakt_id
        ).first()
        if user_rating:
            rating = user_rating.rating  # 1 for thumbs up, -1 for thumbs down
    
    # Fetch trailer URL (no DB storage, just pass-through)
    trailer_url = await get_trailer_url(tmdb_id, normalized_type)
    
    # Parse JSON fields safely
    try:
        genres = json.loads(item.genres) if item.genres else []
    except (json.JSONDecodeError, TypeError, AttributeError):
        genres = item.genres.split(',') if item.genres else []
    
    try:
        cast = json.loads(item.cast) if item.cast else []
    except (json.JSONDecodeError, TypeError):
        cast = []
    
    try:
        keywords = json.loads(item.keywords) if item.keywords else []
    except (json.JSONDecodeError, TypeError):
        keywords = []
    
    # Parse additional JSON fields
    try:
        episode_run_time = json.loads(item.episode_run_time) if item.episode_run_time else []
    except (json.JSONDecodeError, TypeError):
        episode_run_time = []
    
    try:
        production_companies = json.loads(item.production_companies) if item.production_companies else []
    except (json.JSONDecodeError, TypeError):
        production_companies = []
    
    try:
        networks = json.loads(item.networks) if item.networks else []
    except (json.JSONDecodeError, TypeError):
        networks = []
    
    return {
        'tmdb_id': item.tmdb_id,
        'trakt_id': item.trakt_id,
        'media_type': item.media_type,
        'title': item.title,
        'original_title': item.original_title,
        'year': item.year,
        'release_date': item.release_date,
        'overview': item.overview,
        'tagline': item.tagline,
        'poster_path': item.poster_path,
        'backdrop_path': item.backdrop_path,
        'genres': genres,
        'keywords': keywords,
        'cast': cast[:10] if isinstance(cast, list) else [],  # Top 10 cast members
        'vote_average': item.vote_average,
        'vote_count': item.vote_count,
        'popularity': item.popularity,
        'runtime': item.runtime,
        'language': item.language,
        'budget': item.budget,
        'revenue': item.revenue,
        'status': item.status,
        'homepage': item.homepage,
        # TV-specific
        'number_of_seasons': item.number_of_seasons,
        'number_of_episodes': item.number_of_episodes,
        'first_air_date': item.first_air_date,
        'last_air_date': item.last_air_date,
        'episode_run_time': episode_run_time,
        'in_production': item.in_production,
        'networks': networks,
        # Additional metadata
        'production_companies': production_companies,
        'obscurity_score': item.obscurity_score,
        'mainstream_score': item.mainstream_score,
        'freshness_score': item.freshness_score,
        # User-specific data
        'watched': watched,
        'watched_at': watched_at,
        'user_rating': rating,
        'trailer_url': trailer_url,
        # Metadata freshness
        'last_refreshed': item.last_refreshed.isoformat() if item.last_refreshed else None
    }


async def get_trailer_url(tmdb_id: int, media_type: str) -> Optional[str]:
    """
    Fetch trailer URL from TMDB videos endpoint.
    No database storage - just pass-through to frontend.
    
    Returns YouTube URL or None.
    """
    # Check Redis cache first (30 day TTL)
    r = get_redis()
    cache_key = f"trailer:{media_type}:{tmdb_id}"
    cached = await r.get(cache_key)
    if cached:
        return cached if cached != "null" else None
    
    api_key = await get_tmdb_api_key()
    if not api_key:
        return None
    
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}/videos",
                params={"api_key": api_key}
            )
            
            if resp.status_code != 200:
                await r.setex(cache_key, 2592000, "null")  # Cache negative result
                return None
            
            data = resp.json()
            
            # Find official trailer on YouTube
            for video in data.get('results', []):
                if (video.get('type') == 'Trailer' and 
                    video.get('site') == 'YouTube' and
                    video.get('official', False)):
                    url = f"https://www.youtube.com/watch?v={video['key']}"
                    await r.setex(cache_key, 2592000, url)  # Cache for 30 days
                    return url
            
            # Fallback: any trailer
            for video in data.get('results', []):
                if video.get('type') == 'Trailer' and video.get('site') == 'YouTube':
                    url = f"https://www.youtube.com/watch?v={video['key']}"
                    await r.setex(cache_key, 2592000, url)
                    return url
        
        # No trailer found
        await r.setex(cache_key, 2592000, "null")
        return None
    except Exception as e:
        logger.debug(f"Failed to fetch trailer for {media_type}/{tmdb_id}: {e}")
        return None


@router.get("/{media_type}/{tmdb_id}/collection")
async def get_item_collection(
    media_type: str,
    tmdb_id: int,
    user_id: int = 1,
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Get franchise/collection items for this title.
    Uses TMDB's belongs_to_collection field for movies.
    Cached in Redis for 30 days.
    """
    # Normalize media type
    normalized_type = 'tv' if media_type in ['tv', 'show'] else 'movie'
    
    # Only movies have collections in TMDB
    if normalized_type != 'movie':
        return {"collection_name": None, "items": []}
    
    # Check Redis cache
    r = get_redis()
    cache_key = f"collection:{normalized_type}:{tmdb_id}"
    cached = await r.get(cache_key)
    if cached:
        return json.loads(cached)
    
    api_key = await get_tmdb_api_key()
    if not api_key:
        return {"collection_name": None, "items": []}
    
    try:
        # Fetch movie details to get collection info
        movie_data = await fetch_tmdb_metadata(tmdb_id, normalized_type)
        
        if not movie_data or not movie_data.get('belongs_to_collection'):
            result = {"collection_name": None, "items": []}
            await r.setex(cache_key, 2592000, json.dumps(result))
            return result
        
        collection_id = movie_data['belongs_to_collection']['id']
        collection_name = movie_data['belongs_to_collection']['name']
        
        # Fetch collection details
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://api.themoviedb.org/3/collection/{collection_id}",
                params={"api_key": api_key}
            )
            
            if resp.status_code != 200:
                result = {"collection_name": collection_name, "items": []}
                await r.setex(cache_key, 2592000, json.dumps(result))
                return result
            
            collection_data = resp.json()
        
        # Extract all items in collection and check if they exist in our DB
        items = []
        for part in collection_data.get('parts', []):
            candidate = db.query(PersistentCandidate).filter(
                PersistentCandidate.tmdb_id == part['id'],
                PersistentCandidate.media_type == 'movie'
            ).first()
            
            if candidate:
                items.append({
                    'tmdb_id': candidate.tmdb_id,
                    'title': candidate.title,
                    'year': candidate.year,
                    'poster_path': candidate.poster_path,
                    'release_date': candidate.release_date,
                    'vote_average': candidate.vote_average
                })
        
        # Sort by release date
        items.sort(key=lambda x: x.get('release_date') or '9999')
        
        # Enrich with watch status
        try:
            client = TraktClient(user_id, db)
            watched_tmdb_ids = set()
            history = await client.get_watch_history(media_type='movie', limit=5000)
            
            for entry in history:
                # Map trakt_id to tmdb_id
                candidate = db.query(PersistentCandidate).filter(
                    PersistentCandidate.trakt_id == entry.get('trakt_id')
                ).first()
                if candidate:
                    watched_tmdb_ids.add(candidate.tmdb_id)
            
            for item in items:
                item['is_watched'] = item['tmdb_id'] in watched_tmdb_ids
        except Exception as e:
            logger.debug(f"Failed to fetch watch status for collection: {e}")
            for item in items:
                item['is_watched'] = False
        
        result = {
            'collection_name': collection_name,
            'items': items
        }
        
        # Cache for 30 days
        await r.setex(cache_key, 2592000, json.dumps(result))
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to fetch collection for {media_type}/{tmdb_id}: {e}")
        return {"collection_name": None, "items": []}


@router.get("/{media_type}/{tmdb_id}/similar")
async def get_similar_items(
    media_type: str,
    tmdb_id: int,
    top_k: int = 20,
    same_type_only: bool = True,
    user_id: int = 1,
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Get similar items using dual-index hybrid search (BGE multi-vector + FAISS fallback).
    
    Returns up to top_k similar items based on semantic similarity.
    Uses Redis caching (7 days) to avoid repeated queries.
    Enriches with watch status from Trakt and user ratings.
    """
    # Normalize media type
    if media_type not in ['movie', 'tv', 'show']:
        raise HTTPException(status_code=400, detail="Invalid media_type")
    
    normalized_type = 'tv' if media_type in ['tv', 'show'] else 'movie'
    
    # Check Redis cache
    r = get_redis()
    cache_key = f"similar_items:{normalized_type}:{tmdb_id}:{top_k}:{same_type_only}:v2"
    
    try:
        cached = await r.get(cache_key)
        if cached:
            logger.debug(f"Returning cached similar items for {normalized_type}/{tmdb_id}")
            return json.loads(cached)
    except Exception as e:
        logger.warning(f"Redis get failed for similar items: {e}")
    
    # Get source item
    source_item = db.query(PersistentCandidate).filter(
        PersistentCandidate.tmdb_id == tmdb_id,
        PersistentCandidate.media_type.in_(['tv', 'show']) if normalized_type == 'tv' else PersistentCandidate.media_type == 'movie'
    ).first()
    
    if not source_item:
        raise HTTPException(status_code=404, detail="Item not found")
    
    # Build candidate pool (exclude source item and adult content)
    query = db.query(PersistentCandidate).filter(
        PersistentCandidate.tmdb_id != tmdb_id,
        PersistentCandidate.is_adult == False
    )
    
    if same_type_only:
        query = query.filter(
            PersistentCandidate.media_type.in_(['tv', 'show']) if normalized_type == 'tv' else PersistentCandidate.media_type == 'movie'
        )
    
    # Get larger pool for better results
    candidate_pool = query.limit(2000).all()
    
    if not candidate_pool:
        result = {"items": []}
        try:
            await r.setex(cache_key, 3600, json.dumps(result))
        except Exception:
            pass
        return result
    
    # Use dual-index hybrid search
    from app.services.ai_engine.dual_index_search import hybrid_search
    
    try:
        scored_results = hybrid_search(
            db=db,
            user_id=user_id,
            candidate_pool=candidate_pool,
            top_k=top_k,
            bge_weight=0.7,
            faiss_weight=0.3
        )
    except Exception as e:
        logger.warning(f"Hybrid search failed, using fallback: {e}")
        # Fallback to simple random selection if hybrid search fails
        import random
        scored_results = [
            {'candidate': c, 'score': random.random(), 'source': 'fallback'}
            for c in random.sample(candidate_pool, min(top_k, len(candidate_pool)))
        ]
    
    # Convert to API format
    similar_items = []
    for result in scored_results:
        candidate = result['candidate']
        similar_items.append({
            'tmdb_id': candidate.tmdb_id,
            'media_type': candidate.media_type,
            'title': candidate.title,
            'year': candidate.year,
            'poster_path': candidate.poster_path,
            'vote_average': candidate.vote_average,
            'similarity_score': result['score'],
            'is_watched': False
        })
    
    # Enrich with watch status (check last 5000 items)
    try:
        client = TraktClient(user_id, db)
        watched_tmdb_ids = set()
        
        # Get watch history for appropriate media type
        history_type = 'tv' if normalized_type == 'tv' else 'movie'
        history = await client.get_watch_history(media_type=history_type, limit=5000)
        
        for entry in history:
            # Map trakt_id to tmdb_id
            candidate = db.query(PersistentCandidate).filter(
                PersistentCandidate.trakt_id == entry.get('trakt_id')
            ).first()
            if candidate:
                watched_tmdb_ids.add(candidate.tmdb_id)
        
        for item in similar_items:
            item['is_watched'] = item['tmdb_id'] in watched_tmdb_ids
    except Exception as e:
        logger.debug(f"Failed to fetch watch status for similar items: {e}")
    
    # Enrich with user ratings
    try:
        ratings = db.query(UserRating).filter(
            UserRating.user_id == user_id,
            UserRating.tmdb_id.in_([item['tmdb_id'] for item in similar_items])
        ).all()
        
        ratings_by_tmdb = {r.tmdb_id: r.rating for r in ratings}
        
        for item in similar_items:
            item['user_rating'] = ratings_by_tmdb.get(item['tmdb_id'])
    except Exception as e:
        logger.debug(f"Failed to fetch user ratings for similar items: {e}")
        for item in similar_items:
            item['user_rating'] = None
    
    result = {"items": similar_items}
    
    # Cache for 7 days
    try:
        await r.setex(cache_key, 604800, json.dumps(result))
    except Exception as e:
        logger.warning(f"Failed to cache similar items: {e}")
    
    logger.info(f"Returning {len(similar_items)} similar items for {normalized_type}/{tmdb_id}")
    return result


@router.get("/{media_type}/{tmdb_id}/rationale")
async def get_item_rationale(
    media_type: str,
    tmdb_id: int,
    user_id: int = 1,
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Generate AI-powered personalized rationale for why this item fits the user's profile.
    
    Uses existing UserTextProfile (LLM-generated persona from watch history & ratings)
    combined with Trakt watch history to explain why this specific title appeals to the user.
    """
    # Normalize media type
    if media_type not in ['movie', 'tv', 'show']:
        raise HTTPException(status_code=400, detail="Invalid media_type")
    
    normalized_type = 'tv' if media_type in ['tv', 'show'] else 'movie'
    
    # Check Redis cache (24 hour TTL)
    r = get_redis()
    cache_key = f"item_rationale:{normalized_type}:{tmdb_id}:{user_id}"
    
    try:
        cached = await r.get(cache_key)
        if cached:
            logger.debug(f"Returning cached rationale for {normalized_type}/{tmdb_id}")
            return json.loads(cached)
    except Exception as e:
        logger.warning(f"Redis get failed for rationale: {e}")
    
    # Get item details
    item = db.query(PersistentCandidate).filter(
        PersistentCandidate.tmdb_id == tmdb_id,
        PersistentCandidate.media_type.in_(['tv', 'show']) if normalized_type == 'tv' else PersistentCandidate.media_type == 'movie'
    ).first()
    
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    
    # Get user profile from database (LLM-generated persona)
    from app.models import UserTextProfile
    user_profile = db.query(UserTextProfile).filter_by(user_id=user_id).first()
    
    # Also get compressed persona from Redis (history compression)
    persona_text = ""
    history_summary = ""
    try:
        from app.core.redis_client import get_redis_sync
        redis_sync = get_redis_sync()
        compression_key = f"history_compression:{user_id}"
        compression_data = redis_sync.get(compression_key)
        
        if compression_data:
            compression = json.loads(compression_data if isinstance(compression_data, str) else compression_data.decode("utf-8"))
            persona_text = compression.get("persona_text", "")[:300]
            
            # Get top genres/themes from history
            top_genres = compression.get("top_genres", [])[:5]
            if top_genres:
                history_summary = f"Favorite genres: {', '.join(top_genres)}"
    except Exception as e:
        logger.debug(f"Failed to get Redis persona: {e}")
    
    # Fallback to database profile if Redis not available
    if not persona_text and user_profile:
        persona_text = user_profile.summary_text[:300]
    
    # Get recent Trakt watch history for additional context
    watch_context = ""
    try:
        client = TraktClient(user_id, db)
        history = await client.get_watch_history(limit=10)
        recent_titles = [h.get('title', '') for h in history[:5] if h.get('title')]
        if recent_titles:
            watch_context = f"Recently watched: {', '.join(recent_titles[:3])}"
    except Exception as e:
        logger.debug(f"Failed to get watch history: {e}")
    
    # Parse genres
    try:
        genres = json.loads(item.genres) if item.genres else []
    except (json.JSONDecodeError, TypeError):
        genres = []
    
    # Generate rationale using Ollama with user profile
    rationale = ""
    try:
        # Build comprehensive user context
        user_context = persona_text if persona_text else "User enjoys diverse content"
        if history_summary:
            user_context += f". {history_summary}"
        if watch_context:
            user_context += f". {watch_context}"
        
        prompt = f"""You are explaining why a movie/show recommendation fits a user's unique viewing profile.

USER PROFILE:
{user_context}

RECOMMENDED ITEM:
- Title: {item.title}
- Type: {item.media_type}
- Genres: {', '.join(genres[:3])}
- Overview: {item.overview[:200] if item.overview else 'N/A'}

Task: Write ONE compelling sentence explaining the connection between this title and the user's preferences/viewing patterns.

**CRITICAL**: Output ONLY the rationale sentence. No preamble - just the direct explanation.

Example: "Aligns with your appreciation for slow-burn Scandinavian thrillers featuring morally ambiguous protagonists."
"""
        
        logger.info(f"Requesting Ollama rationale for {normalized_type}/{tmdb_id}, user {user_id}, prompt length: {len(prompt)}")
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                "http://ollama:11434/api/generate",
                json={
                    "model": "phi3.5:3.8b-mini-instruct-q4_K_M",
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.7,
                        "num_predict": 150,
                        "num_ctx": 4096
                    },
                    "keep_alive": "24h"
                }
            )
            
            if response.status_code == 200:
                result = response.json()
                rationale = result.get('response', '').strip()
                logger.info(f"Ollama rationale generated for {normalized_type}/{tmdb_id}: length={len(rationale)}, preview={rationale[:100]}")
                
                # Clean up common prefixes
                prefixes = [
                    "You would enjoy this because ",
                    "You might like this because ",
                    "This fits your preferences because ",
                    "Based on your history, ",
                    "Rationale: ",
                    "This matches because ",
                    "This aligns because ",
                    "Recommended because "
                ]
                for prefix in prefixes:
                    if rationale.lower().startswith(prefix.lower()):
                        rationale = rationale[len(prefix):]
                
                # Capitalize first letter
                if rationale:
                    rationale = rationale[0].upper() + rationale[1:] if len(rationale) > 1 else rationale.upper()
                
                # No artificial length cap - let frontend handle display truncation
            else:
                logger.warning(f"Ollama returned non-200 status {response.status_code} for {normalized_type}/{tmdb_id}")
                rationale = "This title aligns with your viewing preferences based on genre overlap and thematic patterns."
                
    except httpx.TimeoutException as e:
        logger.error(f"Ollama request timeout (60s) for {normalized_type}/{tmdb_id}: {e}")
        # Fallback rationale
        if persona_text:
            rationale = "Matches your established viewing patterns and genre preferences."
        elif genres:
            rationale = f"Features {', '.join(genres[:2])} themes that complement your watch history."
        else:
            rationale = "Recommended based on your viewing profile."
    except Exception as e:
        logger.error(f"Failed to generate rationale via Ollama for {normalized_type}/{tmdb_id}: {type(e).__name__}: {e}")
        # Fallback rationale
        if persona_text:
            rationale = "Matches your established viewing patterns and genre preferences."
        elif genres:
            rationale = f"Features {', '.join(genres[:2])} themes that complement your watch history."
        else:
            rationale = "Recommended based on your viewing profile."
    
    result = {
        "rationale": rationale
    }
    
    # Cache for 24 hours
    try:
        await r.setex(cache_key, 86400, json.dumps(result))
    except Exception as e:
        logger.warning(f"Failed to cache rationale: {e}")
    
    logger.info(f"Generated rationale for {normalized_type}/{tmdb_id} for user {user_id}")
    return result


@router.get("/{media_type}/{tmdb_id}/trailers")
async def get_item_trailers(
    media_type: str,
    tmdb_id: int,
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Get trailers and videos for a movie or TV show from TMDB.
    
    Returns list of videos (trailers, teasers, clips) with YouTube keys.
    """
    # Normalize media type
    if media_type not in ['movie', 'tv', 'show']:
        raise HTTPException(status_code=400, detail="Invalid media_type")
    
    normalized_type = 'tv' if media_type in ['tv', 'show'] else 'movie'
    
    # Check Redis cache (7 day TTL)
    r = get_redis()
    cache_key = f"item_trailers:{normalized_type}:{tmdb_id}"
    
    try:
        cached = await r.get(cache_key)
        if cached:
            logger.debug(f"Returning cached trailers for {normalized_type}/{tmdb_id}")
            return json.loads(cached)
    except Exception as e:
        logger.warning(f"Redis get failed for trailers: {e}")
    
    # Fetch from TMDB
    from app.core.redis_client import get_redis_sync
    redis_sync = get_redis_sync()
    tmdb_api_key = redis_sync.get("settings:global:tmdb_api_key")
    
    if not tmdb_api_key:
        logger.warning("TMDB API key not configured")
        return {"videos": []}
    
    if isinstance(tmdb_api_key, bytes):
        tmdb_api_key = tmdb_api_key.decode('utf-8')
    
    videos = []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            tmdb_type = 'tv' if normalized_type == 'tv' else 'movie'
            url = f"https://api.themoviedb.org/3/{tmdb_type}/{tmdb_id}/videos"
            
            response = await client.get(url, params={"api_key": tmdb_api_key})
            
            if response.status_code == 200:
                data = response.json()
                results = data.get('results', [])
                
                # Filter and prioritize trailers
                for video in results:
                    if video.get('site') == 'YouTube':
                        videos.append({
                            'key': video.get('key'),
                            'name': video.get('name'),
                            'type': video.get('type'),  # Trailer, Teaser, Clip, etc.
                            'official': video.get('official', False),
                            'published_at': video.get('published_at'),
                            'size': video.get('size', 1080)
                        })
                
                # Sort: Official trailers first, then by type priority
                type_priority = {'Trailer': 0, 'Teaser': 1, 'Clip': 2, 'Featurette': 3}
                videos.sort(key=lambda v: (
                    not v['official'],
                    type_priority.get(v['type'], 99),
                    -v['size']
                ))
            else:
                logger.debug(f"TMDB videos API returned {response.status_code} for {normalized_type}/{tmdb_id}")
    except Exception as e:
        logger.warning(f"Failed to fetch trailers from TMDB: {e}")
    
    result = {"videos": videos}
    
    # Cache for 7 days
    try:
        await r.setex(cache_key, 604800, json.dumps(result))
    except Exception as e:
        logger.warning(f"Failed to cache trailers: {e}")
    
    return result
