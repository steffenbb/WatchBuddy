"""
watch_history_sync.py

Service for fetching and persisting user's Trakt watch history.
Runs on Trakt OAuth callback and daily schedule to keep history up-to-date.
Enriches watch events with TMDB metadata for phase detection.
"""
import logging
import asyncio
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Set
import json
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.database import SessionLocal
from app.models import TraktWatchHistory, PersistentCandidate
from app.services.trakt_client import TraktClient
from app.services.trakt_id_resolver import TraktIdResolver
from app.utils.timezone import utc_now

logger = logging.getLogger(__name__)


class WatchHistorySync:
    """
    Syncs user's Trakt watch history to local database for phase detection.
    Fetches all-time history on first sync, then incremental updates.
    """
    
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.db = SessionLocal()
        
    def __del__(self):
        try:
            self.db.close()
        except Exception:
            pass
    
    async def sync_full_history(self) -> Dict[str, int]:
        """
        Fetch and persist user's complete watch history from Trakt.
        Returns stats: {movies: int, shows: int, total: int, new: int}
        """
        logger.info(f"[WatchHistorySync] Starting full history sync for user {self.user_id}")
        
        try:
            client = TraktClient(self.user_id)
            
            # Fetch full movie and show history (paged until completion)
            logger.debug(f"[WatchHistorySync] Fetching full movie history...")
            movie_history = await client.get_full_history(media_type="movies", page_size=100)
            
            logger.debug(f"[WatchHistorySync] Fetching full show history...")
            show_history = await client.get_full_history(media_type="shows", page_size=100)
            
            # Process and persist
            movies_added = await self._process_history_batch(movie_history, "movie")
            shows_added = await self._process_history_batch(show_history, "show")
            
            stats = {
                "movies": len(movie_history),
                "shows": len(show_history),
                "total": len(movie_history) + len(show_history),
                "new": movies_added + shows_added
            }
            
            logger.info(f"[WatchHistorySync] ✅ Full sync complete for user {self.user_id}: {stats}")
            return stats
            
        except Exception as e:
            logger.error(f"[WatchHistorySync] Full sync failed for user {self.user_id}: {e}", exc_info=True)
            raise
    
    async def sync_recent_history(self, days: int = 7) -> Dict[str, int]:
        """
        Fetch and persist recent watch history (last N days).
        Used for daily incremental updates.
        """
        logger.info(f"[WatchHistorySync] Starting recent sync (last {days} days) for user {self.user_id}")
        
        try:
            client = TraktClient(self.user_id)
            
            # Fetch recent history (Trakt API doesn't have date filter, so fetch and filter)
            movie_history = await client.get_my_history(media_type="movies", limit=500)
            show_history = await client.get_my_history(media_type="shows", limit=500)
            
            # Filter to recent N days
            from datetime import timedelta
            cutoff = utc_now() - timedelta(days=days)
            
            recent_movies = [h for h in movie_history if self._parse_watched_at(h.get("watched_at")) > cutoff]
            recent_shows = [h for h in show_history if self._parse_watched_at(h.get("watched_at")) > cutoff]
            
            movies_added = await self._process_history_batch(recent_movies, "movie")
            shows_added = await self._process_history_batch(recent_shows, "show")
            
            stats = {
                "movies": len(recent_movies),
                "shows": len(recent_shows),
                "total": len(recent_movies) + len(recent_shows),
                "new": movies_added + shows_added
            }
            
            logger.info(f"[WatchHistorySync] ✅ Recent sync complete for user {self.user_id}: {stats}")
            return stats
            
        except Exception as e:
            logger.error(f"[WatchHistorySync] Recent sync failed for user {self.user_id}: {e}", exc_info=True)
            raise
    
    async def _process_history_batch(self, history_items: List[Dict], media_type: str) -> int:
        """
        Process batch of history items and persist to database.
        Enriches with metadata from PersistentCandidate.
        Uses TraktIdResolver to handle items without stored Trakt IDs.
        Returns count of new items added.
        """
        if not history_items:
            return 0
        
        # Initialize resolver for TMDB→Trakt lookups
        resolver = TraktIdResolver(user_id=self.user_id)
        
        # Build a list of rows to insert and de-duplicate within the batch
        rows: List[Dict] = []
        seen_keys: Set[Tuple[int, datetime]] = set()

        for item in history_items:
            try:
                # Extract Trakt data
                watched_at = self._parse_watched_at(item.get("watched_at"))

                # Get media IDs
                if media_type == "movie":
                    media = item.get("movie", {})
                else:
                    media = item.get("show", {})

                ids = media.get("ids", {})
                trakt_id = ids.get("trakt")
                tmdb_id = ids.get("tmdb")

                # If no Trakt ID but we have TMDB ID, resolve it
                if not trakt_id and tmdb_id:
                    logger.debug(f"[WatchHistorySync] Resolving Trakt ID for TMDB {tmdb_id} ({media_type})")
                    trakt_id = await resolver.get_trakt_id(tmdb_id, media_type)
                
                if not trakt_id:
                    logger.debug(f"[WatchHistorySync] Skipping item without trakt_id: {media.get('title')}")
                    continue

                # De-duplicate within this batch to avoid self-conflicts
                key = (int(trakt_id), watched_at)
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                # Enrich with metadata from persistent candidates (best-effort)
                candidate = None
                if tmdb_id:
                    candidate = self.db.query(PersistentCandidate).filter(
                        PersistentCandidate.tmdb_id == tmdb_id,
                        PersistentCandidate.media_type == media_type
                    ).first()

                title = media.get("title", "Unknown")
                year = media.get("year")

                genres = candidate.genres if candidate else None
                keywords = candidate.keywords if candidate else None
                overview = candidate.overview if candidate else None
                poster_path = candidate.poster_path if candidate else None
                runtime = candidate.runtime if candidate else None
                language = candidate.language if candidate else None

                rows.append({
                    "user_id": self.user_id,
                    "trakt_id": int(trakt_id),
                    "tmdb_id": tmdb_id,
                    "media_type": media_type,
                    "title": title,
                    "year": year,
                    "watched_at": watched_at,
                    "genres": genres,
                    "keywords": keywords,
                    "overview": overview,
                    "poster_path": poster_path,
                    "collection_id": None,
                    "collection_name": None,
                    "runtime": runtime,
                    "language": language,
                })
            except Exception as e:
                logger.warning(f"[WatchHistorySync] Failed to process history item: {e}")
                continue

        if not rows:
            return 0

        # Upsert with ON CONFLICT DO NOTHING to make sync resilient to duplicates
        try:
            stmt = pg_insert(TraktWatchHistory.__table__).values(rows)
            stmt = stmt.on_conflict_do_nothing(constraint='uq_watch_event')
            result = self.db.execute(stmt)
            self.db.commit()
            inserted = int(result.rowcount or 0)
            logger.info(f"[WatchHistorySync] Persisted {inserted} new {media_type} watch events (duplicates ignored)")
            return inserted
        except Exception as e:
            self.db.rollback()
            logger.error(f"[WatchHistorySync] Failed to upsert watch history batch: {e}")
            # As a last resort, try row-by-row with conflict ignore to avoid whole-batch failure
            inserted = 0
            for row in rows:
                try:
                    stmt_one = pg_insert(TraktWatchHistory.__table__).values(row).on_conflict_do_nothing(constraint='uq_watch_event')
                    res_one = self.db.execute(stmt_one)
                    inserted += int(res_one.rowcount or 0)
                except Exception:
                    self.db.rollback()
                    continue
            try:
                self.db.commit()
            except Exception:
                self.db.rollback()
            logger.info(f"[WatchHistorySync] Persisted {inserted} new {media_type} watch events after fallback")
            return inserted
    
    def _parse_watched_at(self, timestamp_str: Optional[str]) -> datetime:
        """Parse Trakt watched_at timestamp to datetime."""
        if not timestamp_str:
            return utc_now()
        
        try:
            # Trakt format: "2024-01-15T20:30:00.000Z"
            return datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        except Exception:
            return utc_now()
    
    def get_recent_watches(self, limit: int = 50, media_type: Optional[str] = None) -> List[TraktWatchHistory]:
        """
        Get user's recent watch history from database.
        Used by phase detector to analyze recent patterns.
        """
        query = self.db.query(TraktWatchHistory).filter(
            TraktWatchHistory.user_id == self.user_id
        )
        
        if media_type:
            query = query.filter(TraktWatchHistory.media_type == media_type)
        
        query = query.order_by(TraktWatchHistory.watched_at.desc()).limit(limit)
        
        return query.all()
    
    def get_history_in_range(self, start_date: datetime, end_date: datetime) -> List[TraktWatchHistory]:
        """
        Get watch history for a specific time range.
        Used for historical phase detection.
        """
        return self.db.query(TraktWatchHistory).filter(
            TraktWatchHistory.user_id == self.user_id,
            TraktWatchHistory.watched_at >= start_date,
            TraktWatchHistory.watched_at <= end_date
        ).order_by(TraktWatchHistory.watched_at.desc()).all()
    
    def get_watch_count_stats(self) -> Dict[str, int]:
        """Get statistics about user's watch history."""
        try:
            result = self.db.execute(text("""
                SELECT 
                    COUNT(*) as total,
                    COUNT(CASE WHEN media_type = 'movie' THEN 1 END) as movies,
                    COUNT(CASE WHEN media_type = 'show' THEN 1 END) as shows,
                    MIN(watched_at) as earliest_watch,
                    MAX(watched_at) as latest_watch
                FROM trakt_watch_history
                WHERE user_id = :user_id
            """), {"user_id": self.user_id}).fetchone()
            
            if result:
                return {
                    "total": result[0] or 0,
                    "movies": result[1] or 0,
                    "shows": result[2] or 0,
                    "earliest_watch": result[3],
                    "latest_watch": result[4]
                }
            return {"total": 0, "movies": 0, "shows": 0}
            
        except Exception as e:
            logger.error(f"[WatchHistorySync] Failed to get stats: {e}")
            return {"total": 0, "movies": 0, "shows": 0}


async def sync_user_watch_history(user_id: int, full_sync: bool = True) -> Dict[str, int]:
    """
    Convenience function for syncing user watch history.
    Called from Celery tasks and API endpoints.
    """
    sync = WatchHistorySync(user_id)
    try:
        # Always perform a full sync to guarantee complete history coverage
        # (explicitly ignore incremental mode to honor "always full=true")
        return await sync.sync_full_history()
    finally:
        del sync


async def sync_user_ratings(user_id: int) -> Dict[str, int]:
    """
    Sync user's Trakt ratings to database.
    Updates user_trakt_rating column in TraktWatchHistory table.
    
    Returns: {movies: int, shows: int, total: int, updated: int}
    """
    logger.info(f"[RatingSync] Starting rating sync for user {user_id}")
    db = SessionLocal()
    
    try:
        client = TraktClient(user_id)
        
        # Fetch all ratings from Trakt
        all_ratings = await client.get_all_ratings()
        movie_ratings = all_ratings.get("movies", [])
        show_ratings = all_ratings.get("shows", [])
        
        logger.debug(f"[RatingSync] Fetched {len(movie_ratings)} movie ratings, {len(show_ratings)} show ratings")
        
        updated_count = 0
        
        # Process movie ratings
        for rating_entry in movie_ratings:
            rating_value = rating_entry.get("rating")  # 1-10 scale
            movie_data = rating_entry.get("movie", {})
            ids = movie_data.get("ids", {})
            trakt_id = ids.get("trakt")
            
            if trakt_id and rating_value:
                # Find history entry by trakt_id
                history_entry = db.query(TraktWatchHistory).filter(
                    TraktWatchHistory.user_id == user_id,
                    TraktWatchHistory.trakt_id == trakt_id,
                    TraktWatchHistory.media_type == "movie"
                ).first()
                
                if history_entry:
                    history_entry.user_trakt_rating = rating_value
                    updated_count += 1
        
        # Process show ratings
        for rating_entry in show_ratings:
            rating_value = rating_entry.get("rating")
            show_data = rating_entry.get("show", {})
            ids = show_data.get("ids", {})
            trakt_id = ids.get("trakt")
            
            if trakt_id and rating_value:
                # Update all episodes of this show (they share trakt_id in history)
                updated = db.query(TraktWatchHistory).filter(
                    TraktWatchHistory.user_id == user_id,
                    TraktWatchHistory.trakt_id == trakt_id,
                    TraktWatchHistory.media_type == "show"
                ).update({
                    TraktWatchHistory.user_trakt_rating: rating_value
                }, synchronize_session=False)
                
                updated_count += updated
        
        db.commit()
        
        stats = {
            "movies": len(movie_ratings),
            "shows": len(show_ratings),
            "total": len(movie_ratings) + len(show_ratings),
            "updated": updated_count
        }
        
        logger.info(f"[RatingSync] ✅ Rating sync complete for user {user_id}: {stats}")
        return stats
        
    except Exception as e:
        logger.error(f"[RatingSync] Rating sync failed for user {user_id}: {e}", exc_info=True)
        db.rollback()
        raise
    finally:
        db.close()
