"""
watch_history_helper.py

Helper service for querying TraktWatchHistory table to avoid repeated Trakt API calls.
Provides fast DB-backed watched status, stats, and top genres computation.
"""
import logging
import json
from typing import Dict, List, Optional, Set, Any
from collections import Counter
from sqlalchemy.orm import Session
from sqlalchemy import func, text

from app.models import TraktWatchHistory
from app.core.database import SessionLocal

logger = logging.getLogger(__name__)


class WatchHistoryHelper:
    """Helper for querying cached watch history from database."""
    
    def __init__(self, user_id: int, db: Optional[Session] = None):
        self.user_id = user_id
        self.db = db or SessionLocal()
        self._owns_db = db is None
    
    def __del__(self):
        if self._owns_db and self.db:
            self.db.close()
    
    def get_watched_trakt_ids(self, media_type: Optional[str] = None) -> Set[int]:
        """
        Get set of all watched Trakt IDs from database.
        Much faster than calling Trakt API.
        
        Args:
            media_type: 'movie' or 'show' to filter, None for all
        
        Returns:
            Set of Trakt IDs that user has watched
        """
        query = self.db.query(TraktWatchHistory.trakt_id).filter(
            TraktWatchHistory.user_id == self.user_id
        )
        
        if media_type:
            query = query.filter(TraktWatchHistory.media_type == media_type)
        
        results = query.distinct().all()
        return {row[0] for row in results if row[0]}
    
    def get_watched_status_dict(self, media_type: str = "movie") -> Dict[int, Dict[str, Any]]:
        """
        Get watched status dictionary matching Trakt API format.
        Returns dict of {trakt_id: {"watched_at": timestamp, "plays": count}}
        
        Args:
            media_type: 'movie' or 'show'
        
        Returns:
            Dictionary mapping Trakt ID to watch info
        """
        query = self.db.query(
            TraktWatchHistory.trakt_id,
            func.max(TraktWatchHistory.watched_at).label('last_watched'),
            func.count(TraktWatchHistory.id).label('plays')
        ).filter(
            TraktWatchHistory.user_id == self.user_id,
            TraktWatchHistory.media_type == media_type
        ).group_by(TraktWatchHistory.trakt_id)
        
        results = query.all()
        
        watched_dict = {}
        for row in results:
            if row[0]:  # trakt_id exists
                watched_dict[row[0]] = {
                    "watched_at": row[1].isoformat() if row[1] else None,
                    "plays": row[2] or 1
                }
        
        return watched_dict
    
    def is_watched(self, trakt_id: int) -> bool:
        """Check if specific item is watched."""
        count = self.db.query(TraktWatchHistory).filter(
            TraktWatchHistory.user_id == self.user_id,
            TraktWatchHistory.trakt_id == trakt_id
        ).count()
        return count > 0
    
    def get_watch_stats(self) -> Dict[str, int]:
        """
        Get watch statistics from database.
        Returns counts matching Trakt API stats format.
        
        Returns:
            {
                "movies_watched": int,
                "shows_watched": int,
                "total_watches": int,
                "earliest_watch": datetime,
                "latest_watch": datetime
            }
        """
        try:
            result = self.db.execute(text("""
                SELECT 
                    COUNT(DISTINCT CASE WHEN media_type = 'movie' THEN trakt_id END) as movies,
                    COUNT(DISTINCT CASE WHEN media_type = 'show' THEN trakt_id END) as shows,
                    COUNT(*) as total,
                    MIN(watched_at) as earliest,
                    MAX(watched_at) as latest
                FROM trakt_watch_history
                WHERE user_id = :user_id
            """), {"user_id": self.user_id}).fetchone()
            
            if result:
                return {
                    "movies_watched": result[0] or 0,
                    "shows_watched": result[1] or 0,
                    "total_watches": result[2] or 0,
                    "earliest_watch": result[3],
                    "latest_watch": result[4]
                }
        except Exception as e:
            logger.warning(f"Failed to get watch stats from DB: {e}")
        
        return {
            "movies_watched": 0,
            "shows_watched": 0,
            "total_watches": 0,
            "earliest_watch": None,
            "latest_watch": None
        }
    
    def get_top_genres(self, limit: int = 10, media_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Calculate top genres from watch history.
        Parses genres JSON from each watched item and counts occurrences.
        
        Args:
            limit: Number of top genres to return
            media_type: Filter by 'movie' or 'show', None for all
        
        Returns:
            List of {"genre": str, "count": int} dicts sorted by count descending
        """
        query = self.db.query(TraktWatchHistory).filter(
            TraktWatchHistory.user_id == self.user_id
        )
        
        if media_type:
            query = query.filter(TraktWatchHistory.media_type == media_type)
        
        history_items = query.all()
        
        # Count genre occurrences
        genre_counter = Counter()
        
        for item in history_items:
            if item.genres:
                try:
                    genres = json.loads(item.genres) if isinstance(item.genres, str) else item.genres
                    if isinstance(genres, list):
                        for genre in genres:
                            if genre and isinstance(genre, str):
                                # Normalize genre name
                                normalized = genre.strip().lower()
                                if normalized and normalized != "n/a":
                                    genre_counter[genre.strip()] += 1
                except Exception as e:
                    logger.debug(f"Failed to parse genres for item {item.trakt_id}: {e}")
                    continue
        
        # Convert to list of dicts and sort
        top_genres = [
            {"genre": genre, "count": count}
            for genre, count in genre_counter.most_common(limit)
        ]
        
        return top_genres
    
    def get_top_genre(self) -> Optional[Dict[str, Any]]:
        """
        Get single top genre with count.
        Returns None if no genres found.
        
        Returns:
            {"genre": str, "count": int} or None
        """
        top_genres = self.get_top_genres(limit=1)
        return top_genres[0] if top_genres else None
    
    def enrich_candidates_with_watched_status(
        self,
        candidates: List[Dict[str, Any]],
        media_type_field: str = "media_type"
    ) -> List[Dict[str, Any]]:
        """
        Enrich list of candidates with watched status from database.
        Adds 'is_watched' and 'watched_at' fields to each candidate.
        
        Args:
            candidates: List of candidate dicts with 'trakt_id' field
            media_type_field: Field name for media type (default "media_type")
        
        Returns:
            Enriched candidates with watched status
        """
        # Get watched status for both types in bulk
        watched_movies = self.get_watched_status_dict("movie")
        watched_shows = self.get_watched_status_dict("show")
        
        for candidate in candidates:
            trakt_id = candidate.get("trakt_id")
            media_type = candidate.get(media_type_field, "movie")
            
            if trakt_id:
                watched_dict = watched_movies if media_type == "movie" else watched_shows
                watched_info = watched_dict.get(trakt_id)
                
                candidate["is_watched"] = bool(watched_info)
                candidate["watched_at"] = watched_info.get("watched_at") if watched_info else None
            else:
                candidate["is_watched"] = False
                candidate["watched_at"] = None
        
        return candidates
    
    def get_recent_watches(
        self,
        limit: int = 50,
        media_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get recent watch history items.
        
        Args:
            limit: Maximum number of items to return
            media_type: Filter by 'movie' or 'show', None for all
        
        Returns:
            List of watch history items with metadata
        """
        query = self.db.query(TraktWatchHistory).filter(
            TraktWatchHistory.user_id == self.user_id
        )
        
        if media_type:
            query = query.filter(TraktWatchHistory.media_type == media_type)
        
        items = query.order_by(TraktWatchHistory.watched_at.desc()).limit(limit).all()
        
        result = []
        for item in items:
            result.append({
                "trakt_id": item.trakt_id,
                "tmdb_id": item.tmdb_id,
                "media_type": item.media_type,
                "title": item.title,
                "year": item.year,
                "watched_at": item.watched_at.isoformat() if item.watched_at else None,
                "genres": json.loads(item.genres) if item.genres and isinstance(item.genres, str) else item.genres,
                "poster_path": item.poster_path
            })
        
        return result


def get_watched_ids_for_user(user_id: int, media_type: Optional[str] = None, db: Optional[Session] = None) -> Set[int]:
    """
    Quick helper to get watched Trakt IDs for a user.
    
    Args:
        user_id: User ID
        media_type: 'movie' or 'show' to filter, None for all
        db: Optional database session
    
    Returns:
        Set of watched Trakt IDs
    """
    helper = WatchHistoryHelper(user_id, db)
    return helper.get_watched_trakt_ids(media_type)


def get_user_watch_stats(user_id: int, db: Optional[Session] = None) -> Dict[str, int]:
    """
    Quick helper to get watch statistics for a user.
    
    Args:
        user_id: User ID
        db: Optional database session
    
    Returns:
        Dictionary with watch stats
    """
    helper = WatchHistoryHelper(user_id, db)
    return helper.get_watch_stats()


def get_user_top_genres(user_id: int, limit: int = 10, db: Optional[Session] = None) -> List[Dict[str, Any]]:
    """
    Quick helper to get top genres for a user.
    
    Args:
        user_id: User ID
        limit: Number of top genres to return
        db: Optional database session
    
    Returns:
        List of top genres with counts
    """
    helper = WatchHistoryHelper(user_id, db)
    return helper.get_top_genres(limit)
