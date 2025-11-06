"""
user_profile.py

Service for building and caching user profile vectors based on Trakt watch history and ratings.
Used for Individual Lists fit scoring.

Profile includes:
- Genre preferences from watched/rated content
- Popularity preferences (mainstream vs obscure)
- Recent activity boost (last 90 days weighted higher)
- Highly-rated content similarity
"""
import json
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from collections import Counter
import numpy as np

from app.core.redis_client import get_redis_sync
from app.core.database import SessionLocal
from app.services.trakt_client import TraktClient
from app.services.watch_history_helper import WatchHistoryHelper
from app.models import UserRating, PersistentCandidate
from app.utils.timezone import utc_now

logger = logging.getLogger(__name__)

PROFILE_CACHE_TTL = 3600  # 1 hour
RECENT_DAYS_THRESHOLD = 90  # Last 90 days get higher weight


class UserProfileService:
    """
    Build and cache user profile vectors for fit scoring.
    
    Profile structure:
    {
        "genre_weights": {"action": 0.8, "drama": 0.6, ...},  # Genre affinity 0-1
        "avg_popularity": 45.2,  # Average popularity of watched content
        "avg_rating": 7.8,  # Average rating given by user
        "recent_activity_boost": 1.5,  # Multiplier for recent watches
        "preferred_obscurity": "mainstream" | "balanced" | "obscure",
        "top_genres": ["action", "thriller", "sci-fi"],  # Top 5 genres
        "recent_tmdb_ids": [123, 456, 789],  # Recent watched items for similarity
        "updated_at": "2025-10-21T18:00:00Z"
    }
    """
    
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.redis = get_redis_sync()
        self.cache_key = f"user_profile:{user_id}"
    
    def get_profile(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Get user profile from cache or build if missing/expired.
        
        Args:
            force_refresh: Bypass cache and rebuild profile
            
        Returns:
            User profile dict with genre weights, preferences, etc.
        """
        if not force_refresh:
            cached = self.redis.get(self.cache_key)
            if cached:
                try:
                    profile = json.loads(cached)
                    logger.debug(f"Loaded cached profile for user {self.user_id}")
                    return profile
                except Exception as e:
                    logger.warning(f"Failed to parse cached profile: {e}")
        
        # Build profile from scratch
        profile = self._build_profile()
        
        # Cache for 1 hour
        try:
            self.redis.setex(
                self.cache_key,
                PROFILE_CACHE_TTL,
                json.dumps(profile)
            )
            logger.info(f"Cached profile for user {self.user_id}")
        except Exception as e:
            logger.error(f"Failed to cache profile: {e}")
        
        return profile
    
    def invalidate_cache(self):
        """Clear cached profile to force rebuild on next access."""
        try:
            self.redis.delete(self.cache_key)
            logger.info(f"Invalidated profile cache for user {self.user_id}")
        except Exception as e:
            logger.error(f"Failed to invalidate profile cache: {e}")
    
    def _build_profile(self) -> Dict[str, Any]:
        """
        Build user profile from Trakt watch history and ratings.
        
        Steps:
        1. Fetch Trakt watch history (last 6 months for performance)
        2. Get user ratings from DB
        3. Aggregate genre preferences with recency weighting
        4. Calculate popularity preferences
        5. Identify top genres and recent items
        
        Returns:
            Complete user profile dict
        """
        logger.info(f"Building profile for user {self.user_id}")
        
        db = SessionLocal()
        try:
            # Get user ratings from DB
            ratings = db.query(UserRating).filter(
                UserRating.user_id == self.user_id
            ).all()
            
            # Get watch history from Trakt
            trakt_client = TraktClient(user_id=self.user_id)
            watched_items = self._fetch_trakt_history(trakt_client)
            
            # Combine ratings and watched items
            all_items = self._merge_watched_and_ratings(watched_items, ratings, db)
            
            if not all_items:
                logger.warning(f"No watch history or ratings for user {self.user_id}, returning default profile")
                return self._default_profile()
            
            # Calculate profile metrics
            genre_weights = self._calculate_genre_weights(all_items)
            avg_popularity = np.mean([item['popularity'] for item in all_items if item['popularity'] is not None])
            avg_rating = np.mean([item['rating'] for item in all_items if item['rating'] is not None])
            
            # Determine obscurity preference
            obscurity_pref = self._determine_obscurity_preference(avg_popularity)
            
            # Get top genres
            top_genres = sorted(genre_weights.items(), key=lambda x: x[1], reverse=True)[:5]
            top_genres = [g[0] for g in top_genres]
            
            # Get recent items for similarity
            recent_cutoff = utc_now() - timedelta(days=RECENT_DAYS_THRESHOLD)
            recent_items = [
                item['tmdb_id'] for item in all_items 
                if item['watched_at'] and item['watched_at'] > recent_cutoff
            ][:20]  # Limit to 20 most recent
            
            profile = {
                "genre_weights": genre_weights,
                "avg_popularity": float(avg_popularity),
                "avg_rating": float(avg_rating),
                "recent_activity_boost": 1.5,  # Fixed multiplier for now
                "preferred_obscurity": obscurity_pref,
                "top_genres": top_genres,
                "recent_tmdb_ids": recent_items,
                "total_watched": len(all_items),
                "updated_at": utc_now().isoformat()
            }
            
            logger.info(f"Built profile for user {self.user_id}: {len(all_items)} items, top genres: {top_genres[:3]}")
            return profile
            
        except Exception as e:
            logger.error(f"Failed to build profile for user {self.user_id}: {e}")
            return self._default_profile()
        finally:
            db.close()
    
    def _fetch_trakt_history(self, trakt_client: TraktClient) -> List[Dict[str, Any]]:
        """Fetch watch history preferring DB cache; fallback to Trakt API.

        Returns items with fields: trakt_id, tmdb_id, media_type, watched_at (datetime)
        """
        import asyncio

        # 1) Try DB-backed helper first (fast path)
        try:
            helper = WatchHistoryHelper(user_id=self.user_id)
            # Fetch a generous number; downstream will weight by recency (90 days)
            recent = helper.get_recent_watches(limit=1000)
            items: List[Dict[str, Any]] = []
            for r in recent:
                try:
                    # watched_at from helper is ISO string; convert to datetime
                    wa = r.get('watched_at')
                    watched_at = (
                        datetime.fromisoformat(wa.replace('Z', '+00:00')) if isinstance(wa, str) and wa else utc_now()
                    )
                except Exception:
                    watched_at = utc_now()
                items.append({
                    'trakt_id': r.get('trakt_id'),
                    'tmdb_id': r.get('tmdb_id'),
                    'media_type': r.get('media_type'),
                    'watched_at': watched_at,
                })
            logger.info(f"[UserProfile] Loaded {len(items)} items from DB watch history for user {self.user_id}")
            return items
        except Exception as e:
            logger.warning(f"[UserProfile] DB watch history unavailable, falling back to Trakt API: {e}")

        # 2) Fallback to Trakt API (slower path)
        try:
            movies_response = asyncio.run(trakt_client.get_watched_movies())
            shows_response = asyncio.run(trakt_client.get_watched_shows())

            items: List[Dict[str, Any]] = []

            # Process movies
            if movies_response and isinstance(movies_response, list):
                for entry in movies_response:
                    if entry.get('movie'):
                        wa = entry.get('watched_at')
                        items.append({
                            'trakt_id': entry['movie']['ids'].get('trakt'),
                            'tmdb_id': entry['movie']['ids'].get('tmdb'),
                            'media_type': 'movie',
                            'watched_at': datetime.fromisoformat(wa.replace('Z', '+00:00')) if wa else utc_now(),
                        })

            # Process shows
            if shows_response and isinstance(shows_response, list):
                for entry in shows_response:
                    if entry.get('show'):
                        wa = entry.get('watched_at')
                        items.append({
                            'trakt_id': entry['show']['ids'].get('trakt'),
                            'tmdb_id': entry['show']['ids'].get('tmdb'),
                            'media_type': 'show',
                            'watched_at': datetime.fromisoformat(wa.replace('Z', '+00:00')) if wa else utc_now(),
                        })

            logger.info(f"[UserProfile] Fetched {len(items)} watched items from Trakt API for user {self.user_id}")
            return items

        except Exception as e:
            logger.error(f"Failed to fetch Trakt history via API: {e}")
            return []
    
    def _merge_watched_and_ratings(
        self, 
        watched_items: List[Dict[str, Any]], 
        ratings: List[UserRating],
        db
    ) -> List[Dict[str, Any]]:
        """
        Merge watched items and ratings, enrich with metadata from persistent_candidates.
        
        Returns list of items with: tmdb_id, genres, popularity, rating, watched_at, is_recent
        """
        # Create lookup for ratings
        rating_map = {
            (r.trakt_id, r.media_type): r.rating 
            for r in ratings
        }
        
        # Get unique tmdb_ids to fetch metadata
        tmdb_ids = list(set([item['tmdb_id'] for item in watched_items if item['tmdb_id']]))
        
        # Fetch metadata in batch
        metadata_map = {}
        if tmdb_ids:
            candidates = db.query(PersistentCandidate).filter(
                PersistentCandidate.tmdb_id.in_(tmdb_ids)
            ).all()
            
            for candidate in candidates:
                key = (candidate.tmdb_id, candidate.media_type)
                metadata_map[key] = {
                    'genres': json.loads(candidate.genres) if candidate.genres else [],
                    'popularity': candidate.popularity,
                    'vote_average': candidate.vote_average
                }
        
        # Merge everything
        recent_cutoff = utc_now() - timedelta(days=RECENT_DAYS_THRESHOLD)
        merged = []
        
        for item in watched_items:
            if not item['tmdb_id']:
                continue
                
            key = (item['tmdb_id'], item['media_type'])
            metadata = metadata_map.get(key, {})
            
            user_rating = rating_map.get((item['trakt_id'], item['media_type']))
            
            merged.append({
                'tmdb_id': item['tmdb_id'],
                'media_type': item['media_type'],
                'genres': metadata.get('genres', []),
                'popularity': metadata.get('popularity'),
                'vote_average': metadata.get('vote_average'),
                'rating': user_rating,
                'watched_at': item['watched_at'],
                'is_recent': item['watched_at'] > recent_cutoff if item['watched_at'] else False
            })
        
        return merged
    
    def _calculate_genre_weights(self, items: List[Dict[str, Any]]) -> Dict[str, float]:
        """
        Calculate genre preference weights (0-1) with recency boost.
        
        Recent items (last 90 days) get 2x weight.
        """
        genre_counts = Counter()
        recent_genre_counts = Counter()
        
        for item in items:
            genres = item.get('genres', [])
            weight = 1.0
            
            # Boost recent items
            if item.get('is_recent'):
                weight = 2.0
            
            for genre in genres:
                genre_lower = genre.lower()
                genre_counts[genre_lower] += weight
                if item.get('is_recent'):
                    recent_genre_counts[genre_lower] += 1
        
        if not genre_counts:
            return {}
        
        # Normalize to 0-1
        max_count = max(genre_counts.values())
        genre_weights = {
            genre: count / max_count 
            for genre, count in genre_counts.items()
        }
        
        return genre_weights
    
    def _determine_obscurity_preference(self, avg_popularity: float) -> str:
        """
        Determine if user prefers mainstream, balanced, or obscure content.
        
        Based on average popularity of watched items:
        - < 20: obscure
        - 20-60: balanced  
        - > 60: mainstream
        """
        if avg_popularity < 20:
            return "obscure"
        elif avg_popularity < 60:
            return "balanced"
        else:
            return "mainstream"
    
    def _default_profile(self) -> Dict[str, Any]:
        """Return default profile for users with no history."""
        return {
            "genre_weights": {},
            "avg_popularity": 50.0,  # Neutral
            "avg_rating": 7.0,  # Neutral
            "recent_activity_boost": 1.0,
            "preferred_obscurity": "balanced",
            "top_genres": [],
            "recent_tmdb_ids": [],
            "total_watched": 0,
            "updated_at": utc_now().isoformat()
        }
