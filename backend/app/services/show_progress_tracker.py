"""
Show Progress Tracker Service

Analyzes watch history to determine:
- Which shows user is currently watching
- Which shows user has paused (haven't watched in a while)
- Next episode to watch
- How many episodes behind
- Show completion status
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_, desc, func

from app.models import TraktWatchHistory, UserShowProgress, PersistentCandidate
from app.utils.timezone import utc_now

logger = logging.getLogger(__name__)


class ShowProgressTracker:
    """Tracks user progress through TV shows."""
    
    def __init__(self, user_id: int):
        self.user_id = user_id
    
    def compute_show_progress(self, db: Session) -> Dict[str, int]:
        """
        Analyze watch history and populate UserShowProgress table.
        Returns stats about the operation.
        """
        try:
            logger.info(f"[ShowProgressTracker] Computing show progress for user {self.user_id}")
            
            # Get all show watch history, grouped by show
            show_history = db.query(TraktWatchHistory).filter(
                and_(
                    TraktWatchHistory.user_id == self.user_id,
                    TraktWatchHistory.media_type == 'show'
                )
            ).order_by(
                TraktWatchHistory.trakt_id,
                TraktWatchHistory.watched_at
            ).all()
            
            if not show_history:
                logger.info(f"[ShowProgressTracker] No show watch history found for user {self.user_id}")
                return {'shows_processed': 0, 'shows_in_progress': 0, 'shows_completed': 0}
            
            # Group episodes by show
            shows_dict = {}
            for episode in show_history:
                trakt_id = episode.trakt_id
                if trakt_id not in shows_dict:
                    shows_dict[trakt_id] = {
                        'trakt_id': trakt_id,
                        'tmdb_id': episode.tmdb_id,
                        'title': episode.title,
                        'poster_path': episode.poster_path,
                        'episodes': []
                    }
                shows_dict[trakt_id]['episodes'].append(episode)
            
            logger.info(f"[ShowProgressTracker] Found {len(shows_dict)} unique shows")
            
            # Get existing progress records to update them instead of deleting
            existing_progress = db.query(UserShowProgress).filter(
                UserShowProgress.user_id == self.user_id
            ).all()
            
            # Create lookup by trakt_id
            existing_by_trakt = {p.trakt_id: p for p in existing_progress}
            
            shows_in_progress = 0
            shows_completed = 0
            
            # Analyze each show
            for show_data in shows_dict.values():
                progress_data = self._analyze_show_progress(db, show_data)
                if progress_data:
                    # Check if progress already exists
                    trakt_id = show_data['trakt_id']
                    if trakt_id in existing_by_trakt:
                        # Update existing record
                        existing = existing_by_trakt[trakt_id]
                        existing.tmdb_id = progress_data.tmdb_id
                        existing.title = progress_data.title
                        existing.poster_path = progress_data.poster_path
                        existing.last_watched_season = progress_data.last_watched_season
                        existing.last_watched_episode = progress_data.last_watched_episode
                        existing.last_watched_at = progress_data.last_watched_at
                        existing.next_episode_season = progress_data.next_episode_season
                        existing.next_episode_number = progress_data.next_episode_number
                        existing.next_episode_title = progress_data.next_episode_title
                        existing.next_episode_air_date = progress_data.next_episode_air_date
                        existing.total_seasons = progress_data.total_seasons
                        existing.total_episodes = progress_data.total_episodes
                        existing.show_status = progress_data.show_status
                        existing.is_completed = progress_data.is_completed
                        existing.is_behind = progress_data.is_behind
                        existing.episodes_behind = progress_data.episodes_behind
                        existing.updated_at = utc_now()
                        
                        if existing.is_completed:
                            shows_completed += 1
                        else:
                            shows_in_progress += 1
                    else:
                        # Add new record
                        db.add(progress_data)
                        if progress_data.is_completed:
                            shows_completed += 1
                        else:
                            shows_in_progress += 1
            
            # Note: Caller (overview_service) will commit the transaction
            db.flush()  # Write to DB but don't commit the transaction
            
            logger.info(f"[ShowProgressTracker] ✅ Computed progress: {shows_in_progress} in progress, {shows_completed} completed")
            
            return {
                'shows_processed': len(shows_dict),
                'shows_in_progress': shows_in_progress,
                'shows_completed': shows_completed
            }
            
        except Exception as e:
            logger.error(f"[ShowProgressTracker] Failed to compute show progress: {e}", exc_info=True)
            db.rollback()
            raise
    
    def _analyze_show_progress(self, db: Session, show_data: Dict) -> Optional[UserShowProgress]:
        """
        Analyze a single show's watch history to determine progress.
        
        For now, we'll use a simple heuristic:
        - Count total episodes watched
        - Find last watched episode
        - Determine if show is likely completed (haven't watched in 90+ days and watched multiple episodes)
        """
        try:
            episodes = show_data['episodes']
            if not episodes:
                return None
            
            # Sort by watch date
            episodes.sort(key=lambda e: e.watched_at)
            
            last_episode = episodes[-1]
            total_episodes_watched = len(episodes)
            
            # Calculate days since last watch
            days_since_watch = (utc_now().replace(tzinfo=None) - last_episode.watched_at).days
            
            # Try to get show info from persistent_candidates
            show_info = db.query(PersistentCandidate).filter(
                PersistentCandidate.trakt_id == show_data['trakt_id']
            ).first()
            
            # Determine if show is completed
            # Simple heuristic: If user watched 3+ episodes and hasn't watched in 90+ days,
            # and there's no indication of more episodes, mark as completed/abandoned
            is_completed = False
            is_behind = False
            episodes_behind = 0
            
            # For now, we'll consider shows "in progress" if watched within last 180 days
            # and mark as "behind" if not watched in last 30 days but within 180 days
            if days_since_watch <= 180:
                # Show is still active
                if days_since_watch > 30:
                    is_behind = True
                    # Rough estimate: assume weekly show, calculate episodes behind
                    weeks_behind = (days_since_watch - 7) // 7
                    episodes_behind = max(0, weeks_behind)
            elif total_episodes_watched >= 3:
                # Watched multiple episodes but not in 180+ days - likely completed or abandoned
                is_completed = True
            
            # Create progress entry
            progress = UserShowProgress(
                user_id=self.user_id,
                trakt_id=show_data['trakt_id'],
                tmdb_id=show_data['tmdb_id'],
                title=show_data['title'],
                poster_path=show_data['poster_path'],
                last_watched_season=1,  # We don't have season/episode info in watch history yet
                last_watched_episode=total_episodes_watched,
                last_watched_at=last_episode.watched_at,
                next_episode_season=None,  # Would need TMDB API call to determine
                next_episode_number=None,
                next_episode_title=None,
                next_episode_air_date=None,
                total_seasons=None,
                total_episodes=total_episodes_watched,  # At minimum we know this many exist
                show_status=None,
                is_completed=is_completed,
                is_behind=is_behind,
                episodes_behind=episodes_behind
            )
            
            return progress
            
        except Exception as e:
            logger.error(f"[ShowProgressTracker] Failed to analyze show {show_data.get('title')}: {e}")
            return None
