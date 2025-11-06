"""
Lightweight mood pipeline for WatchBuddy.
- Computes and caches user mood vectors based on recent history and TMDB metadata.
- Uses Redis for caching (key: user:{user_id}:mood).
- No torch or sentence-transformers; only TF-IDF, numpy, and static mappings.

Unit test stub:
    def test_compute_mood_vector_from_items():
        fake_history = [{"ids": {"tmdb": 123}, "watched_at": "2023-01-01T00:00:00Z"}]
        # ...mock MediaMetadata...
        vec = compute_mood_vector_from_items(fake_history)
        assert abs(sum(vec.values()) - 1.0) < 1e-3
"""
from typing import List, Dict, Optional, Any
from collections import defaultdict
import numpy as np
import logging
import json
import datetime
from app.core.redis_client import redis_client
from app.core.database import SessionLocal
from app.models import MediaMetadata
from app.utils.timezone import utc_now, ensure_utc, get_user_hour, get_user_weekday, safe_datetime_diff_days
from app.services.trakt_client import TraktClient

# Enhanced mood mappings with better coverage
GENRE_TO_MOOD = {
    # Happy/Uplifting
    "comedy": {"happy": 0.8, "excited": 0.2},
    "family": {"happy": 0.6, "thoughtful": 0.4},
    "animation": {"happy": 0.5, "curious": 0.3, "excited": 0.2},
    "musical": {"happy": 0.7, "excited": 0.3},
    "adventure": {"excited": 0.6, "happy": 0.4},
    
    # Sad/Emotional
    "drama": {"sad": 0.5, "thoughtful": 0.5},
    "tragedy": {"sad": 0.8, "thoughtful": 0.2},
    "war": {"sad": 0.4, "tense": 0.6},
    "history": {"thoughtful": 0.6, "sad": 0.4},
    
    # Exciting/Action
    "action": {"excited": 0.8, "tense": 0.2},
    "thriller": {"tense": 0.7, "excited": 0.3},
    "crime": {"tense": 0.6, "excited": 0.4},
    "mystery": {"curious": 0.6, "tense": 0.4},
    
    # Scary/Intense
    "horror": {"scared": 0.9, "tense": 0.1},
    "suspense": {"tense": 0.8, "scared": 0.2},
    
    # Romantic
    "romance": {"romantic": 0.8, "happy": 0.2},
    
    # Intellectual/Thoughtful
    "sci-fi": {"curious": 0.6, "thoughtful": 0.4},
    "science fiction": {"curious": 0.6, "thoughtful": 0.4},
    "documentary": {"thoughtful": 0.8, "curious": 0.2},
    "biography": {"thoughtful": 0.7, "sad": 0.3},
    "fantasy": {"curious": 0.5, "excited": 0.3, "happy": 0.2},
    
    # Neutral/Mixed
    "western": {"excited": 0.4, "tense": 0.6},
    "noir": {"tense": 0.6, "thoughtful": 0.4},
    "sport": {"excited": 0.6, "happy": 0.4},
}

KEYWORD_TO_MOOD = {
    # Happy/Positive keywords
    "friendship": {"happy": 0.8, "thoughtful": 0.2},
    "love": {"romantic": 0.8, "happy": 0.2},
    "wedding": {"happy": 0.9, "romantic": 0.1},
    "celebration": {"happy": 1.0},
    "success": {"happy": 0.7, "excited": 0.3},
    "victory": {"happy": 0.6, "excited": 0.4},
    "family": {"happy": 0.6, "thoughtful": 0.4},
    "children": {"happy": 0.7, "thoughtful": 0.3},
    "hope": {"happy": 0.5, "thoughtful": 0.5},
    
    # Sad/Emotional keywords
    "death": {"sad": 0.9, "thoughtful": 0.1},
    "loss": {"sad": 0.8, "thoughtful": 0.2},
    "betrayal": {"sad": 0.6, "tense": 0.4},
    "sacrifice": {"sad": 0.5, "thoughtful": 0.5},
    "tragedy": {"sad": 0.8, "thoughtful": 0.2},
    "depression": {"sad": 1.0},
    "grief": {"sad": 0.9, "thoughtful": 0.1},
    
    # Exciting/Action keywords
    "chase": {"excited": 0.8, "tense": 0.2},
    "fight": {"excited": 0.7, "tense": 0.3},
    "battle": {"excited": 0.6, "tense": 0.4},
    "explosion": {"excited": 0.9, "tense": 0.1},
    "car chase": {"excited": 0.9, "tense": 0.1},
    "heist": {"excited": 0.7, "tense": 0.3},
    "escape": {"excited": 0.6, "tense": 0.4},
    
    # Scary/Tense keywords
    "murder": {"scared": 0.5, "tense": 0.5},
    "killer": {"scared": 0.6, "tense": 0.4},
    "ghost": {"scared": 0.8, "curious": 0.2},
    "monster": {"scared": 0.9, "excited": 0.1},
    "haunted": {"scared": 0.8, "tense": 0.2},
    "zombie": {"scared": 0.7, "excited": 0.3},
    "conspiracy": {"tense": 0.7, "curious": 0.3},
    "kidnapping": {"scared": 0.4, "tense": 0.6},
    
    # Romantic keywords
    "marriage": {"romantic": 0.8, "happy": 0.2},
    "passion": {"romantic": 0.9, "excited": 0.1},
    "heartbreak": {"sad": 0.6, "romantic": 0.4},
    "dating": {"romantic": 0.7, "happy": 0.3},
    "relationship": {"romantic": 0.6, "thoughtful": 0.4},
    
    # Intellectual/Curious keywords
    "space": {"curious": 0.7, "excited": 0.3},
    "science": {"curious": 0.8, "thoughtful": 0.2},
    "discovery": {"curious": 0.7, "excited": 0.3},
    "mystery": {"curious": 0.6, "tense": 0.4},
    "investigation": {"curious": 0.7, "tense": 0.3},
    "time travel": {"curious": 0.8, "excited": 0.2},
    "alien": {"curious": 0.6, "excited": 0.4},
    "future": {"curious": 0.7, "thoughtful": 0.3},
    "technology": {"curious": 0.8, "thoughtful": 0.2},
    "artificial intelligence": {"curious": 0.6, "thoughtful": 0.4},
    
    # War/Conflict keywords
    "war": {"tense": 0.6, "sad": 0.4},
    "soldier": {"tense": 0.5, "thoughtful": 0.3, "sad": 0.2},
    "bomb": {"tense": 0.8, "scared": 0.2},
    "prison": {"tense": 0.7, "sad": 0.3},
    "rescue": {"excited": 0.6, "tense": 0.4},
}
MOOD_AXES = ["happy", "sad", "excited", "scared", "romantic", "tense", "curious", "thoughtful"]

logger = logging.getLogger(__name__)

def compute_mood_vector_from_items(history_items: List[Dict]) -> Dict[str, float]:
    """
    Computes a normalized mood vector from recent history items with enhanced time-based weighting.
    Each item should have 'ids' with 'tmdb', a 'watched_at' timestamp, and optionally 'watched_date'.
    Fetches MediaMetadata for tmdb_id, applies time-based recency decay, and aggregates mood axes.
    Returns a dict {mood_axis: weight} summing to 1.0 (rounded to 3 decimals).
    
    Enhanced weighting:
    - Recent watches (last 7 days): weight = 1.0
    - Medium recent (8-30 days): weight = 0.7  
    - Older watches (31-90 days): weight = 0.4
    - Position-based decay within each time group
    """
    mood_vec = defaultdict(float)
    session = SessionLocal()
    try:
        # Sort by recency (most recent first) and limit to reasonable number for performance
        items = history_items[:100]  # Process up to 100 items for richer analysis
        
        now = utc_now()
        
        for idx, item in enumerate(items):
            tmdb_id = item.get("ids", {}).get("tmdb")
            if not tmdb_id:
                continue
                
            # Calculate time-based weight
            time_weight = 0.4  # Default weight for items without date
            watched_at = item.get("watched_at")
            
            if watched_at:
                try:
                    # Use pre-parsed date if available, otherwise parse
                    watched_date = item.get("watched_date")
                    if not watched_date:
                        watched_date = ensure_utc(watched_at)
                    
                    days_ago = safe_datetime_diff_days(now, watched_date)
                    
                    if days_ago <= 7:
                        time_weight = 1.0  # Recent watches get full weight
                    elif days_ago <= 30:
                        time_weight = 0.7  # Medium recent
                    else:
                        time_weight = 0.4  # Older watches
                        
                except Exception:
                    time_weight = 0.4  # Fallback for unparseable dates
            
            # Position-based decay within time group (less aggressive than before)
            position_decay = 1.0 / (1 + 0.05 * idx)  # Gentler decay to preserve signal
            
            # Combined weight emphasizes both recency and time
            final_weight = time_weight * position_decay
            
            meta = session.query(MediaMetadata).filter(
                MediaMetadata.tmdb_id == tmdb_id,
                MediaMetadata.media_type == item.get('media_type', 'movie')
            ).first()
            if not meta:
                continue
            
            # Parse JSON strings to lists
            try:
                genres = json.loads(meta.genres or "[]") if meta.genres else []
                keywords = json.loads(meta.keywords or "[]") if meta.keywords else []
            except (json.JSONDecodeError, TypeError):
                genres = []
                keywords = []
            
            # Aggregate genre moods with enhanced weighting
            for genre in genres:
                genre_lower = genre.lower() if isinstance(genre, str) else str(genre).lower()
                for mood, w in GENRE_TO_MOOD.get(genre_lower, {}).items():
                    mood_vec[mood] += w * final_weight
            
            # Aggregate keyword moods with enhanced weighting
            for kw in keywords:
                kw_lower = kw.lower() if isinstance(kw, str) else str(kw).lower()
                for mood, w in KEYWORD_TO_MOOD.get(kw_lower, {}).items():
                    mood_vec[mood] += w * final_weight
                    
        # Normalize
        total = sum(mood_vec.values()) or 1.0
        norm = {m: round(v / total, 3) for m, v in mood_vec.items()}
        # Fill missing axes with 0
        for m in MOOD_AXES:
            if m not in norm:
                norm[m] = 0.0
        return norm
    finally:
        session.close()


async def _compute_fallback_mood(user_id: int) -> Dict[str, float]:
    """
    Compute fallback mood for users without viewing history.
    Uses SmartList genre preferences, list creation patterns, and other activity signals.
    """
    mood_vec = defaultdict(float)
    session = SessionLocal()
    
    try:
        from app.models import UserList, ListItem
        
        # Analyze user's SmartList genre preferences
        user_lists = session.query(UserList).filter_by(user_id=user_id).all()
        
        for user_list in user_lists:
            if not user_list.filters:
                continue
                
            try:
                filters = json.loads(user_list.filters) if isinstance(user_list.filters, str) else user_list.filters
                list_genres = filters.get("genres", [])
                
                # Weight based on list creation recency (more recent = higher weight)
                if user_list.created_at:
                    days_old = safe_datetime_diff_days(utc_now(), user_list.created_at)
                    recency_weight = 1.0 / (1 + 0.1 * days_old)  # Decays slower than viewing history
                else:
                    recency_weight = 0.5
                
                # Convert genres to mood signals
                for genre in list_genres:
                    genre_lower = genre.lower() if isinstance(genre, str) else str(genre).lower()
                    for mood, weight in GENRE_TO_MOOD.get(genre_lower, {}).items():
                        mood_vec[mood] += weight * recency_weight
                        
                # Also consider explicit mood/theme keywords in list titles and descriptions
                list_text = f"{user_list.title or ''} {getattr(user_list, 'description', '') or ''}".lower()
                _extract_mood_from_text(list_text, mood_vec, weight=0.3)
                        
            except (json.JSONDecodeError, TypeError, AttributeError):
                continue
        
        # Analyze user's item interactions (thumbs up/down if available)
        list_items = session.query(ListItem).join(UserList).filter(UserList.user_id == user_id).limit(50).all()
        
        for item in list_items:
            if not item.explanation:
                continue
                
            try:
                meta = json.loads(item.explanation) if isinstance(item.explanation, str) else item.explanation
                
                # If user liked/saved items with certain moods, that's a signal
                item_mood_score = meta.get('mood_score', 0)
                if item_mood_score > 0.3:  # User seems to like items with similar mood to candidate
                    # For now, just use the score as a general positive signal
                    # We could enhance this by storing TMDB data or fetching it
                    mood_vec["happy"] += item_mood_score * 0.3
                    mood_vec["excited"] += item_mood_score * 0.2
                            
            except (json.JSONDecodeError, TypeError, AttributeError):
                continue
        
        # Normalize result
        total = sum(mood_vec.values())
        if total > 0:
            norm = {m: round(v / total, 3) for m, v in mood_vec.items()}
            # Fill missing axes
            for m in MOOD_AXES:
                if m not in norm:
                    norm[m] = 0.0
            return norm
        else:
            # No signals found, return neutral
            return {m: 0.0 for m in MOOD_AXES}
            
    finally:
        session.close()


def _extract_mood_from_text(text: str, mood_vec: defaultdict, weight: float = 1.0):
    """Extract mood signals from text (list names, descriptions, etc.)"""
    text_lower = text.lower()
    
    # Check for direct mood keywords
    mood_keywords = {
        "happy": ["fun", "funny", "comedy", "laugh", "uplifting", "feel good", "cheerful"],
        "sad": ["drama", "tear", "emotional", "cry", "melancholy", "tragic", "heartbreak"],
        "excited": ["action", "adventure", "thrill", "adrenaline", "fast", "intense"],
        "scared": ["horror", "scary", "creepy", "spooky", "fear", "terror"],
        "romantic": ["love", "romance", "date", "romantic", "passion", "relationship"],
        "tense": ["suspense", "thriller", "mystery", "crime", "tension"],
        "curious": ["mystery", "discover", "explore", "science", "documentary", "learn"],
        "thoughtful": ["deep", "thought", "philosophy", "meaning", "reflect", "contemplat"]
    }
    
    for mood, keywords in mood_keywords.items():
        for keyword in keywords:
            if keyword in text_lower:
                mood_vec[mood] += weight * 0.5  # Each keyword match adds some signal

def cache_user_mood(user_id: int, mood_vec: Dict[str, float], ttl_sec: int = 86400) -> None:
    key = f"user:{user_id}:mood"
    redis_client.setex(key, ttl_sec, json.dumps(mood_vec))

def get_cached_user_mood(user_id: int) -> Optional[Dict[str, Any]]:
    key = f"user:{user_id}:mood"
    val = redis_client.get(key)
    if val:
        return json.loads(val)
    return None

def get_user_mood(user_id: int) -> Dict[str, float]:
    """Return cached mood or compute a neutral default."""
    cached = get_cached_user_mood(user_id)
    if cached:
        return cached
    # Neutral default vector
    default = {m: 0.0 for m in MOOD_AXES}
    return default

async def ensure_user_mood(user_id: int, ttl_sec: int = 86400, fallback_strategy: bool = True) -> Dict[str, float]:
    """
    Ensure a user mood vector exists in cache; if not, compute from recent history and cache it.
    - Pulls last ~50 history items from Trakt (movies + shows)
    - Looks up TMDB metadata from DB and computes a mood vector
    - With fallback_strategy=True, tries alternative mood sources if no history
    - Caches JSON in Redis with a 24h TTL by default
    Returns the mood vector (dict of floats per axis).
    """
    try:
        cached = get_cached_user_mood(user_id)
        if cached:
            return cached  # already cached
        
        # Fetch recent user history (last 90 days worth) using DB helper
        trakt = TraktClient(user_id=user_id)
        try:
            # Enhanced history fetching: Get up to 500 items from last 90 days for better mood analysis
            # This provides much richer data for understanding user preferences and recency weighting
            
            # Try DB first, fallback to API
            try:
                from app.services.watch_history_helper import WatchHistoryHelper
                from app.core.database import SessionLocal
                
                db = SessionLocal()
                try:
                    helper = WatchHistoryHelper(db=db, user_id=user_id)
                    movie_status = helper.get_watched_status_dict("movie")
                    show_status = helper.get_watched_status_dict("show")
                    
                    # Convert to API format
                    movies_hist = [{"movie": data, "watched_at": data.get("watched_at")} 
                                  for data in list(movie_status.values())[:250]]
                    shows_hist = [{"show": data, "watched_at": data.get("watched_at")} 
                                 for data in list(show_status.values())[:250]]
                    
                    logger.debug(f"[MOOD] Using WatchHistoryHelper for mood analysis")
                finally:
                    db.close()
            except Exception as e:
                logger.warning(f"Failed to get watch history from DB, falling back to API: {e}")
                movies_hist = await trakt.get_my_history(media_type="movies", limit=250)
                shows_hist = await trakt.get_my_history(media_type="shows", limit=250)
            
            history = []
            ninety_days_ago = utc_now() - datetime.timedelta(days=90)
            
            for item in (movies_hist or []) + (shows_hist or []):
                entity = item.get("movie") or item.get("show") or {}
                ids = entity.get("ids", {})
                watched_at = item.get("watched_at")
                
                # Parse watched_at and check if within 90 days
                if watched_at:
                    try:
                        watched_date = ensure_utc(watched_at)
                        if watched_date >= ninety_days_ago:
                            history.append({
                                "ids": {"tmdb": ids.get("tmdb")},
                                "watched_at": watched_at,
                                "watched_date": watched_date  # Add parsed date for weighting
                            })
                    except Exception:
                        # If date parsing fails, include item without date constraint
                        history.append({
                            "ids": {"tmdb": ids.get("tmdb")},
                            "watched_at": watched_at
                        })
                else:
                    # Include items without watched_at but they get lower weight
                    history.append({
                        "ids": {"tmdb": ids.get("tmdb")},
                        "watched_at": watched_at
                    })
            
            logger.info(f"Fetched {len(history)} history items from last 90 days for mood analysis")
        except Exception as e:
            logger.warning(f"Failed to fetch enhanced history for mood: {e}")
            history = []

        # Primary mood computation from history
        if history and any(item.get("ids", {}).get("tmdb") for item in history):
            vec = compute_mood_vector_from_items(history)
            # Only use history result if it has meaningful data
            if any(v > 0 for v in vec.values()):
                cache_user_mood(user_id, vec, ttl_sec=ttl_sec)
                return vec
        
        # Fallback strategies for new users
        if fallback_strategy:
            fallback_mood = await _compute_fallback_mood(user_id)
            if any(v > 0 for v in fallback_mood.values()):
                cache_user_mood(user_id, fallback_mood, ttl_sec=ttl_sec // 4)  # Shorter cache for fallback
                return fallback_mood
        
        # Final fallback: Cache neutral to avoid recomputation storms
        neutral = {m: 0.0 for m in MOOD_AXES}
        cache_user_mood(user_id, neutral, ttl_sec=ttl_sec // 2)  # Shorter cache for neutral
        return neutral
        
    except Exception:
        # On failure, return neutral but don't crash callers
        return {m: 0.0 for m in MOOD_AXES}

def compute_mood_vector_for_tmdb(tmdb_metadata: Dict) -> Dict[str, float]:
    """
    Computes a mood vector for a single TMDB metadata dict (genres, keywords).
    Returns normalized dict as above.
    """
    mood_vec = defaultdict(float)
    
    # Handle both list and dict formats for genres
    genres = tmdb_metadata.get("genres", [])
    if isinstance(genres, list):
        genre_names = []
        for genre in genres:
            if isinstance(genre, dict):
                genre_names.append(genre.get("name", ""))
            else:
                genre_names.append(str(genre))
    else:
        genre_names = []
    
    # Handle both list and dict formats for keywords
    keywords = tmdb_metadata.get("keywords", [])
    if isinstance(keywords, dict):
        keywords = keywords.get("keywords", [])
    if isinstance(keywords, list):
        keyword_names = []
        for kw in keywords:
            if isinstance(kw, dict):
                keyword_names.append(kw.get("name", ""))
            else:
                keyword_names.append(str(kw))
    else:
        keyword_names = []
    
    # Process genres
    for genre in genre_names:
        genre_lower = genre.lower() if genre else ""
        for mood, w in GENRE_TO_MOOD.get(genre_lower, {}).items():
            mood_vec[mood] += w
    
    # Process keywords
    for kw in keyword_names:
        kw_lower = kw.lower() if kw else ""
        for mood, w in KEYWORD_TO_MOOD.get(kw_lower, {}).items():
            mood_vec[mood] += w
    
    # Add contextual mood boosts based on additional metadata
    _add_contextual_mood_boosts(tmdb_metadata, mood_vec)
            
    total = sum(mood_vec.values()) or 1.0
    norm = {m: round(v / total, 3) for m, v in mood_vec.items()}
    for m in MOOD_AXES:
        if m not in norm:
            norm[m] = 0.0
    return norm


def _add_contextual_mood_boosts(tmdb_metadata: Dict, mood_vec: defaultdict):
    """Add contextual mood adjustments based on runtime, rating, popularity, etc."""
    
    # Runtime-based mood adjustments
    runtime = tmdb_metadata.get("runtime", 0)
    if runtime:
        if runtime > 150:  # Long movies tend to be more thoughtful/dramatic
            mood_vec["thoughtful"] += 0.1
            mood_vec["sad"] += 0.05
        elif runtime < 90:  # Short movies tend to be more action/comedy
            mood_vec["excited"] += 0.1
            mood_vec["happy"] += 0.05
    
    # Rating-based adjustments (higher rated = more thoughtful)
    vote_average = tmdb_metadata.get("vote_average", 0)
    if vote_average > 8.0:
        mood_vec["thoughtful"] += 0.15
    elif vote_average < 5.0:
        mood_vec["excited"] += 0.1  # Lower rated might be action/thriller
    
    # Release date context (older films might be more thoughtful)
    release_date = tmdb_metadata.get("release_date", "") or tmdb_metadata.get("first_air_date", "")
    if release_date:
        try:
            year = int(release_date[:4])
            current_year = utc_now().year
            age = current_year - year
            
            if age > 30:  # Classic films
                mood_vec["thoughtful"] += 0.1
            elif age < 3:  # Very recent
                mood_vec["excited"] += 0.05
        except (ValueError, TypeError):
            pass
    
    # Popularity adjustments (very popular = more mainstream/happy)
    popularity = tmdb_metadata.get("popularity", 0)
    if popularity > 100:
        mood_vec["happy"] += 0.05
        mood_vec["excited"] += 0.05


def get_contextual_mood_adjustment(user_timezone: str = "UTC") -> Dict[str, float]:
    """
    Get time-of-day and situational mood adjustments.
    Returns adjustment weights to apply to mood matching.
    """
    hour = get_user_hour(user_timezone)
    adjustments = defaultdict(float)
    
    # Time-of-day patterns
    if 6 <= hour < 10:  # Morning - prefer uplifting content
        adjustments["happy"] += 0.2
        adjustments["excited"] += 0.1
    elif 10 <= hour < 17:  # Daytime - balanced
        adjustments["curious"] += 0.1
        adjustments["thoughtful"] += 0.1
    elif 17 <= hour < 22:  # Evening - wind down but still engaged
        adjustments["romantic"] += 0.1
        adjustments["thoughtful"] += 0.1
    else:  # Late night - prefer lighter content
        adjustments["happy"] += 0.15
        adjustments["scared"] -= 0.1  # Less horror late at night
    
    # Weekend vs weekday (simplified - could be enhanced)
    weekday = get_user_weekday(user_timezone)
    if weekday >= 5:  # Weekend
        adjustments["excited"] += 0.05
        adjustments["happy"] += 0.05
    else:  # Weekday
        adjustments["thoughtful"] += 0.05
    
    return dict(adjustments)
