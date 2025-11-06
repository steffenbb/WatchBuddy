"""
overview_service.py

Service for computing Overview page modules:
1. Investment Tracker - stats from watch history
2. New Shows & Movies - personalized recommendations from recent releases
3. Trending Now - TMDB trending + user taste matching
4. Coming Soon - upcoming movies + user taste matching

All modules pre-computed nightly and cached in overview_cache table.
Dynamic section reordering based on user momentum/state.
"""

import json
import logging
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from sqlalchemy import desc, and_, or_, func

from app.models import (
    TraktWatchHistory, UserRating, PersistentCandidate, OverviewCache,
    UserShowProgress, TrendingIngestionQueue
)
from app.services.scoring_engine import ScoringEngine
from app.services.trakt_client import TraktClient
from app.services.tmdb_client import fetch_tmdb_metadata, fetch_tmdb_upcoming
from app.models import MediaMetadata
from app.utils.timezone import utc_now
from app.core.redis_client import get_redis_sync

logger = logging.getLogger(__name__)


class OverviewService:
    """Computes all 4 overview modules for a user."""
    
    def __init__(self, user_id: int = 1):
        self.user_id = user_id
        self.scoring_engine = ScoringEngine()
        self.trakt_client = TraktClient(user_id=user_id)
        self.redis = get_redis_sync()
    
    async def compute_all_modules(self, db: Session, mood_overrides: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        """
        Compute all 4 modules and cache results.
        
        Args:
            db: Database session
            mood_overrides: Optional dict with 'energy', 'exploration', 'commitment' (0-100)
        
        Returns:
            Dict with all module data + priority scores
        """
        try:
            logger.info(f"Computing overview modules for user {self.user_id}")
            
            # Compute each module with error isolation using savepoints
            try:
                sp = db.begin_nested()  # Create savepoint
                investment_data = await self._compute_investment_tracker(db)
                sp.commit()
            except Exception as e:
                logger.error(f"Failed to compute investment tracker: {e}", exc_info=True)
                try:
                    sp.rollback()
                except Exception:
                    pass
                investment_data = {'error': str(e), 'total_hours': 0, 'total_items': 0}
            
            try:
                sp = db.begin_nested()  # Create savepoint
                new_shows_data = await self._compute_new_shows(db, mood_overrides)
                sp.commit()
            except Exception as e:
                logger.error(f"Failed to compute new shows: {e}", exc_info=True)
                try:
                    sp.rollback()
                except Exception:
                    pass
                new_shows_data = {'items': [], 'error': str(e)}
            
            try:
                sp = db.begin_nested()  # Create savepoint
                trending_data = await self._compute_trending(db, mood_overrides)
                sp.commit()
            except Exception as e:
                logger.error(f"Failed to compute trending: {e}", exc_info=True)
                try:
                    sp.rollback()
                except Exception:
                    pass
                trending_data = {'items': [], 'error': str(e)}
            
            try:
                sp = db.begin_nested()  # Create savepoint
                upcoming_data = await self._compute_upcoming(db, mood_overrides)
                sp.commit()
            except Exception as e:
                logger.error(f"Failed to compute upcoming: {e}", exc_info=True)
                try:
                    sp.rollback()
                except Exception:
                    pass
                upcoming_data = {'items': [], 'error': str(e)}
            
            # Compute priority scores for dynamic reordering
            priorities = self._compute_module_priorities(db, {
                'investment_tracker': investment_data,
                'new_shows': new_shows_data,
                'trending': trending_data,
                'upcoming': upcoming_data
            })
            
            # Cache results with transaction isolation
            try:
                await self._cache_modules(db, {
                    'investment_tracker': (investment_data, priorities['investment_tracker']),
                    'new_shows': (new_shows_data, priorities['new_shows']),
                    'trending': (trending_data, priorities['trending']),
                    'upcoming': (upcoming_data, priorities['upcoming'])
                })
            except Exception as e:
                logger.error(f"Failed to cache modules: {e}")
                db.rollback()
            
            logger.info(f"Overview modules computed successfully for user {self.user_id}")
            
            return {
                'investment_tracker': investment_data,
                'new_shows': new_shows_data,
                'trending': trending_data,
                'upcoming': upcoming_data,
                'priorities': priorities,
                'computed_at': utc_now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Failed to compute overview modules: {e}", exc_info=True)
            db.rollback()
            raise
    
    async def _compute_investment_tracker(self, db: Session) -> Dict[str, Any]:
        """
        Module 1: Investment Tracker
        
        Shows:
        - Total time invested
        - Top genres by time
        - Quality investment score
        - Longest binge streak
        - Watch time this week
        - Upcoming continuations
        - Most valuable show
        """
        try:
            # First, compute show progress to populate continuations
            from app.services.show_progress_tracker import ShowProgressTracker
            tracker = ShowProgressTracker(self.user_id)
            progress_stats = tracker.compute_show_progress(db)
            logger.info(f"[InvestmentTracker] Show progress computed: {progress_stats}")
            
            # Get watch history (limit to last 2 years for memory efficiency)
            from app.core.memory_manager import managed_memory
            
            with managed_memory("investment_tracker"):
                cutoff = utc_now() - timedelta(days=730)  # Last 2 years
                history = db.query(TraktWatchHistory).filter(
                    TraktWatchHistory.user_id == self.user_id,
                    TraktWatchHistory.watched_at >= cutoff
                ).order_by(desc(TraktWatchHistory.watched_at)).all()
            
            if not history:
                return self._empty_investment_data()
            
            # Total time invested (in hours)
            total_minutes = sum(h.runtime or 0 for h in history)
            total_hours = total_minutes / 60.0
            
            # Top genres by time
            genre_time = {}
            for h in history:
                if h.genres:
                    try:
                        genres = json.loads(h.genres) if isinstance(h.genres, str) else h.genres
                        runtime = h.runtime or 0
                        for genre in genres:
                            genre_time[genre] = genre_time.get(genre, 0) + runtime
                    except:
                        pass
            
            top_genres = sorted(genre_time.items(), key=lambda x: x[1], reverse=True)[:5]
            top_genres_formatted = [{'genre': g[0], 'minutes': g[1], 'hours': round(g[1]/60, 1)} for g in top_genres]
            
            # Quality investment score (average user rating * global popularity / hours invested)
            rated_items = [h for h in history if h.user_trakt_rating]
            if rated_items:
                avg_rating = sum(h.user_trakt_rating for h in rated_items) / len(rated_items)
                quality_score = (avg_rating / 10.0) * 100  # Normalize to 0-100
            else:
                quality_score = 0
            
            # Longest binge streak (consecutive days with watch activity)
            watch_dates = sorted(set(h.watched_at.date() for h in history))
            longest_streak = 0
            current_streak = 1
            for i in range(1, len(watch_dates)):
                if (watch_dates[i] - watch_dates[i-1]).days == 1:
                    current_streak += 1
                    longest_streak = max(longest_streak, current_streak)
                else:
                    current_streak = 1
            longest_streak = max(longest_streak, current_streak) if watch_dates else 0
            
            # Watch time this week
            week_ago = utc_now() - timedelta(days=7)
            # Make week_ago naive for comparison with database timestamps
            week_ago_naive = week_ago.replace(tzinfo=None) if week_ago.tzinfo else week_ago
            this_week = [h for h in history if h.watched_at >= week_ago_naive]
            week_minutes = sum(h.runtime or 0 for h in this_week)
            week_hours = week_minutes / 60.0
            
            # Upcoming continuations (from user_show_progress table)
            continuations = db.query(UserShowProgress).filter(
                and_(
                    UserShowProgress.user_id == self.user_id,
                    UserShowProgress.is_behind == True,
                    UserShowProgress.is_completed == False
                )
            ).order_by(desc(UserShowProgress.last_watched_at)).limit(10).all()
            
            continuations_list = []
            for c in continuations:
                continuations_list.append({
                    'trakt_id': c.trakt_id,
                    'tmdb_id': c.tmdb_id,
                    'title': c.title,
                    'poster_path': c.poster_path,
                    'last_watched_season': c.last_watched_season,
                    'last_watched_episode': c.last_watched_episode,
                    'next_episode_season': c.next_episode_season,
                    'next_episode_number': c.next_episode_number,
                    'next_episode_title': c.next_episode_title,
                    'episodes_behind': c.episodes_behind,
                    'last_watched_at': c.last_watched_at.isoformat() if c.last_watched_at else None
                })
            
            # Forgotten continuations (>45 days since last watch)
            cutoff = utc_now() - timedelta(days=45)
            cutoff_naive = cutoff.replace(tzinfo=None) if cutoff.tzinfo else cutoff
            forgotten = db.query(UserShowProgress).filter(
                and_(
                    UserShowProgress.user_id == self.user_id,
                    UserShowProgress.is_behind == True,
                    UserShowProgress.is_completed == False,
                    UserShowProgress.last_watched_at != None,
                    UserShowProgress.last_watched_at < cutoff_naive
                )
            ).order_by(UserShowProgress.last_watched_at.asc()).limit(20).all()

            forgotten_list = []
            for c in forgotten:
                forgotten_list.append({
                    'trakt_id': c.trakt_id,
                    'tmdb_id': c.tmdb_id,
                    'title': c.title,
                    'poster_path': c.poster_path,
                    'last_watched_season': c.last_watched_season,
                    'last_watched_episode': c.last_watched_episode,
                    'next_episode_season': c.next_episode_season,
                    'next_episode_number': c.next_episode_number,
                    'next_episode_title': c.next_episode_title,
                    'episodes_behind': c.episodes_behind,
                    'last_watched_at': c.last_watched_at.isoformat() if c.last_watched_at else None
                })

            # Most valuable show (highest rating * episodes / total hours)
            show_history = [h for h in history if h.media_type == 'show']
            show_value_scores = {}
            for h in show_history:
                if h.trakt_id not in show_value_scores:
                    show_value_scores[h.trakt_id] = {
                        'title': h.title,
                        'trakt_id': h.trakt_id,
                        'poster_path': h.poster_path,
                        'episodes': 0,
                        'total_minutes': 0,
                        'avg_rating': 0,
                        'ratings_count': 0
                    }
                show_value_scores[h.trakt_id]['episodes'] += 1
                show_value_scores[h.trakt_id]['total_minutes'] += h.runtime or 0
                if h.user_trakt_rating:
                    show_value_scores[h.trakt_id]['avg_rating'] += h.user_trakt_rating
                    show_value_scores[h.trakt_id]['ratings_count'] += 1
            
            # Compute value score
            for show_id, data in show_value_scores.items():
                if data['ratings_count'] > 0:
                    data['avg_rating'] = data['avg_rating'] / data['ratings_count']
                show_hours = data['total_minutes'] / 60.0  # Use show_hours instead of total_hours to avoid variable shadowing
                if show_hours > 0:
                    data['value_score'] = (data['avg_rating'] * data['episodes']) / show_hours
                else:
                    data['value_score'] = 0
            
            most_valuable = sorted(show_value_scores.values(), key=lambda x: x['value_score'], reverse=True)
            top_valuable = most_valuable[0] if most_valuable else None
            
            # Build payload compatible with frontend Overview module
            payload = {
                'total_hours': round(total_hours, 1),
                'total_items': len(history),
                'top_genres': top_genres_formatted,
                'quality_score': round(quality_score, 1),
                'longest_streak': longest_streak,
                'week_hours': round(week_hours, 1),
                'continuations': continuations_list,
                'forgotten_continuations': forgotten_list,
                'most_valuable_show': top_valuable,
                'last_watched': history[0].watched_at.isoformat() if history else None
            }

            # Add alias keys expected by frontend component
            payload.update({
                'total_time_invested': f"{round(total_hours, 1)}h",
                'quality_investment_score': round(quality_score, 1),
                'longest_binge_streak': longest_streak,
                'watch_time_this_week': f"{round(week_hours, 1)}h",
                'upcoming_continuations': [
                    {
                        'trakt_id': c.get('trakt_id'),
                        'title': c.get('title'),
                        'poster_path': c.get('poster_path'),
                        'next_season': c.get('next_episode_season'),
                        'next_episode': c.get('next_episode_number')
                    }
                    for c in continuations_list
                ]
            })

            # Align most_valuable_show shape
            if payload.get('most_valuable_show'):
                mvs = payload['most_valuable_show']
                mvs['episodes_watched'] = mvs.get('episodes', 0)

            return payload
            
        except Exception as e:
            logger.error(f"Failed to compute investment tracker: {e}", exc_info=True)
            return self._empty_investment_data()
    
    def _empty_investment_data(self) -> Dict[str, Any]:
        """Return empty investment data structure."""
        return {
            'total_hours': 0,
            'total_items': 0,
            'top_genres': [],
            'quality_score': 0,
            'longest_streak': 0,
            'week_hours': 0,
            'continuations': [],
            'most_valuable_show': None,
            'last_watched': None
        }
    
    async def _compute_new_shows(self, db: Session, mood_overrides: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Module 2: New Shows & Movies You Might Like
        
        Fetches from Trakt list: https://trakt.tv/users/acoucalancha/lists/tv-movie-new-releases
        For each item:
        1. Check if exists in persistent_candidates
        2. If not, fetch TMDB metadata and add to persistent_candidates
        3. Score and return personalized recommendations
        """
        try:
            # Fetch from external Trakt list
            logger.info("[NewShows] Fetching from Trakt list: tv-movie-new-releases")
            list_items = await self.trakt_client.get_list_items(
                'tv-movie-new-releases',
                username='acoucalancha'
            )
            
            if not list_items:
                return {'items': [], 'message': 'No new releases found in Trakt list'}
            
            logger.info(f"[NewShows] Found {len(list_items)} items from Trakt list")
            
            # Extract Trakt IDs and check what's already in persistent pool
            trakt_ids = []
            tmdb_ids_missing = []
            trakt_media_map = {}  # trakt_id -> media_type
            tmdb_to_trakt = {}  # tmdb_id -> trakt_id
            
            for item in list_items:
                movie = item.get('movie')
                show = item.get('show')
                if movie:
                    trakt_id = movie.get('ids', {}).get('trakt')
                    tmdb_id = movie.get('ids', {}).get('tmdb')
                    if trakt_id and tmdb_id:
                        trakt_ids.append(trakt_id)
                        trakt_media_map[trakt_id] = 'movie'
                        tmdb_to_trakt[tmdb_id] = trakt_id
                elif show:
                    trakt_id = show.get('ids', {}).get('trakt')
                    tmdb_id = show.get('ids', {}).get('tmdb')
                    if trakt_id and tmdb_id:
                        trakt_ids.append(trakt_id)
                        trakt_media_map[trakt_id] = 'show'
                        tmdb_to_trakt[tmdb_id] = trakt_id
            
            # Check which items already exist in persistent pool
            existing_candidates = db.query(PersistentCandidate).filter(
                PersistentCandidate.trakt_id.in_(trakt_ids)
            ).all()
            
            existing_trakt_ids = {c.trakt_id for c in existing_candidates}
            missing_trakt_ids = [tid for tid in trakt_ids if tid not in existing_trakt_ids]
            
            # Only ingest items that are missing from the pool
            if missing_trakt_ids:
                logger.info(f"[NewShows] Ingesting {len(missing_trakt_ids)} missing items into persistent pool")
                missing_items = [
                    item for item in list_items
                    if (item.get('movie', {}).get('ids', {}).get('trakt') in missing_trakt_ids or
                        item.get('show', {}).get('ids', {}).get('trakt') in missing_trakt_ids)
                ]
                await self._ingest_trakt_list_items(db, missing_items)
            else:
                logger.info("[NewShows] All items already exist in persistent pool, skipping ingestion")
            
            # Get watched items to exclude (need both trakt_id and media_type)
            watched_pairs = set(
                (row[0], row[1]) for row in db.query(
                    TraktWatchHistory.trakt_id, 
                    TraktWatchHistory.media_type
                ).filter(
                    TraktWatchHistory.user_id == self.user_id
                ).distinct()
            )
            
            # Query candidates from persistent pool
            candidates = db.query(PersistentCandidate).filter(
                PersistentCandidate.trakt_id.in_(trakt_ids)
            ).all()
            
            # Filter candidates to match correct media_type
            candidates = [
                c for c in candidates 
                if c.trakt_id in trakt_media_map and c.media_type == trakt_media_map[c.trakt_id]
            ]
            
            # Filter out watched items (match both trakt_id AND media_type)
            candidates = [
                c for c in candidates 
                if (c.trakt_id, c.media_type) not in watched_pairs
            ]
            
            if not candidates:
                return {'items': [], 'message': 'No new releases matching your taste found'}
            
            logger.info(f"[NewShows] Processing {len(candidates)} unwatched candidates")
            
            # Convert to scoring format
            candidates_dict = [self._candidate_to_dict(c) for c in candidates]
            
            # Apply mood overrides to filters
            filters = self._build_filters_from_mood(mood_overrides) if mood_overrides else {}
            
            # Score using existing FAISS + ScoringEngine
            # Use 'discovery_overview' type for overview-specific scoring (doesn't penalize lack of history)
            user_obj = {'id': self.user_id}
            scored = self.scoring_engine.score_candidates(
                user=user_obj,
                candidates=candidates_dict,
                list_type='discovery_overview',  # Special mode for overview modules
                explore_factor=0.20,  # Balance between taste match and discovery
                item_limit=50,
                filters=filters
            )
            
            # Attempt to backfill missing images for scored items (best-effort)
            await self._fill_missing_images_for_items(db, scored)

            # Format results - preserve all fields from scoring engine
            items = []
            for item in scored[:30]:  # Top 30
                # Ensure score is properly calculated and not "Match 3%"
                final_score = item.get('final_score', item.get('score', 0))
                match_percentage = round(final_score * 100) if final_score > 0 else 0
                
                items.append({
                    **item,  # Preserve all fields from scored item
                    'overview': str(item.get('overview', ''))[:200],  # Truncate
                    'score': round(final_score, 2),
                    'match_percentage': match_percentage,
                    'rationale': item.get('explanation_text', f'{match_percentage}% match with your taste')
                })
            
            logger.info(f"[NewShows] Returning {len(items)} scored items")
            
            return {
                'items': items,
                'total_candidates': len(candidates),
                'source': 'trakt:tv-movie-new-releases'
            }
            
        except Exception as e:
            logger.error(f"Failed to compute new shows: {e}", exc_info=True)
            return {'items': [], 'error': str(e)}
    
    async def _compute_trending(self, db: Session, mood_overrides: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Module 3: Trending Now for You
        
        Items from TrendingIngestionQueue (completed status) scored by user taste.
        Also refreshes TMDB metadata for trending items.
        """
        try:
            # Get completed trending items
            trending_queue = db.query(TrendingIngestionQueue).filter(
                and_(
                    TrendingIngestionQueue.status == 'completed',
                    TrendingIngestionQueue.source_list.like('trending%'),
                    TrendingIngestionQueue.trakt_id.isnot(None)
                )
            ).order_by(desc(TrendingIngestionQueue.priority)).limit(200).all()
            
            if not trending_queue:
                return {'items': [], 'message': 'Trending data not yet available. Will be computed nightly.'}
            
            # Get trakt_ids AND media_types from queue (trakt_id alone isn't unique!)
            queue_items = [(t.trakt_id, t.media_type) for t in trending_queue if t.trakt_id and t.media_type]
            
            # Get watched items to exclude (need both trakt_id and media_type)
            watched_pairs = set(
                (row[0], row[1]) for row in db.query(
                    TraktWatchHistory.trakt_id, 
                    TraktWatchHistory.media_type
                ).filter(
                    TraktWatchHistory.user_id == self.user_id
                ).distinct()
            )
            
            # Filter unwatched
            unwatched_items = [(tid, mtype) for tid, mtype in queue_items if (tid, mtype) not in watched_pairs]
            
            if not unwatched_items:
                return {'items': [], 'message': 'You\'ve watched all trending items!'}
            
            # Get candidates from persistent pool - MUST match BOTH trakt_id AND media_type
            unwatched_trakt_ids = [tid for tid, _ in unwatched_items]
            unwatched_media_types = {tid: mtype for tid, mtype in unwatched_items}
            
            candidates = db.query(PersistentCandidate).filter(
                PersistentCandidate.trakt_id.in_(unwatched_trakt_ids)
            ).all()
            
            # Filter candidates to match correct media_type AND update stale metadata
            valid_candidates = []
            for c in candidates:
                if c.trakt_id in unwatched_media_types and c.media_type == unwatched_media_types[c.trakt_id]:
                    # Check if metadata needs refresh (>7 days old)
                    # Handle timezone-aware vs naive datetime comparison
                    if c.tmdb_id:
                        now = utc_now()
                        last_refresh = c.last_refreshed
                        if last_refresh and last_refresh.tzinfo is None:
                            # Make last_refresh timezone-aware (assume UTC)
                            last_refresh = last_refresh.replace(tzinfo=timezone.utc)
                        
                        if not last_refresh or (now - last_refresh).days > 7:
                            await self._update_candidate_metadata(db, c, c.tmdb_id, c.media_type)
                    valid_candidates.append(c)
            
            candidates = valid_candidates
            
            if not candidates:
                return {'items': [], 'message': 'Trending items not yet ingested'}
            
            # NOTE: Don't commit here - we're inside a savepoint in compute_all_modules
            # Metadata updates will be committed when the outer transaction commits
            
            # Convert to scoring format
            candidates_dict = [self._candidate_to_dict(c) for c in candidates]
            
            logger.info(f"[Trending] Found {len(candidates_dict)} candidates to score")
            if candidates_dict:
                logger.info(f"[Trending] Sample candidate: {candidates_dict[0].get('title', 'NO_TITLE')} (trakt:{candidates_dict[0].get('trakt_id')})")
            
            # Apply mood overrides
            filters = self._build_filters_from_mood(mood_overrides) if mood_overrides else {}
            
            # Score: 70% taste match + 30% trending popularity
            user_obj = {'id': self.user_id}
            scored = self.scoring_engine.score_candidates(
                user=user_obj,
                candidates=candidates_dict,
                list_type='discovery_overview',  # Use discovery-focused scoring
                explore_factor=0.15,  # Less discovery, more taste match
                item_limit=40,
                filters=filters
            )
            
            logger.info(f"[Trending] After scoring: {len(scored)} items")
            if scored:
                sample = scored[0]
                logger.info(f"[Trending] Scored sample: title={sample.get('title')}, trakt_id={sample.get('trakt_id')}, tmdb_id={sample.get('tmdb_id')}")

            # Best-effort image backfill for missing posters/backdrops
            await self._fill_missing_images_for_items(db, scored)
            
            # Boost scores by trending rank
            trending_boost = {t.trakt_id: (200 - i) / 200.0 for i, t in enumerate(trending_queue)}
            for item in scored:
                trakt_id = item.get('trakt_id')
                # Use final_score from scoring engine
                base_score = item.get('final_score', item.get('score', 0))
                if trakt_id in trending_boost:
                    item['trending_boost'] = trending_boost[trakt_id]
                    item['score'] = base_score * 0.7 + trending_boost[trakt_id] * 0.3
                else:
                    item['score'] = base_score
            
            # Re-sort by boosted score
            scored = sorted(scored, key=lambda x: x.get('score', 0), reverse=True)
            
            # Format results - preserve all fields from scoring engine
            items = []
            for item in scored[:25]:  # Top 25
                final_score = item.get('score', 0)
                match_percentage = round(final_score * 100) if final_score > 0 else 0
                
                items.append({
                    **item,  # Preserve all fields from scored item
                    'overview': str(item.get('overview', ''))[:200],  # Truncate overview
                    'score': round(final_score, 2),
                    'match_percentage': match_percentage,
                    'trending_badge': '🔥 Trending',
                    'rationale': f'{match_percentage}% match • Trending now'
                })
            
            return {
                'items': items,
                'total_trending': len(trending_queue)
            }
            
        except Exception as e:
            logger.error(f"Failed to compute trending: {e}", exc_info=True)
            return {'items': [], 'error': str(e)}
    
    async def _compute_upcoming(self, db: Session, mood_overrides: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Module 4: Coming Soon You'll Love
        
        Fetches from two Trakt lists:
        - Movies: https://trakt.tv/users/acoucalancha/lists/upcoming-anticipated-movies (sort=rank,asc)
        - Series: https://trakt.tv/users/acoucalancha/lists/upcoming-anticipated-series (sort=released,desc)
        """
        try:
            logger.info("[Upcoming] Fetching from Trakt lists: upcoming-anticipated-movies and upcoming-anticipated-series")
            
            # Fetch both lists in parallel
            import asyncio
            movies_task = self.trakt_client.get_list_items(
                'upcoming-anticipated-movies',
                username='acoucalancha'
            )
            series_task = self.trakt_client.get_list_items(
                'upcoming-anticipated-series',
                username='acoucalancha'
            )
            
            movies_list, series_list = await asyncio.gather(movies_task, series_task, return_exceptions=True)
            
            # Handle potential errors
            if isinstance(movies_list, Exception):
                logger.error(f"[Upcoming] Failed to fetch movies list: {movies_list}")
                movies_list = []
            if isinstance(series_list, Exception):
                logger.error(f"[Upcoming] Failed to fetch series list: {series_list}")
                series_list = []
            
            all_items = list(movies_list) + list(series_list)
            
            if not all_items:
                return {'items': [], 'message': 'No upcoming releases found in Trakt lists'}
            
            logger.info(f"[Upcoming] Found {len(all_items)} items from Trakt lists")
            
            # Extract trakt_ids from list items to check persistent pool
            trakt_ids_from_list = []
            trakt_media_map_check = {}
            for item in all_items:
                movie = item.get('movie')
                show = item.get('show')
                if movie:
                    trakt_id = movie.get('ids', {}).get('trakt')
                    if trakt_id:
                        trakt_ids_from_list.append(trakt_id)
                        trakt_media_map_check[trakt_id] = 'movie'
                elif show:
                    trakt_id = show.get('ids', {}).get('trakt')
                    if trakt_id:
                        trakt_ids_from_list.append(trakt_id)
                        trakt_media_map_check[trakt_id] = 'show'
            
            # Check which items already exist in persistent pool
            existing_candidates = db.query(PersistentCandidate).filter(
                PersistentCandidate.trakt_id.in_(trakt_ids_from_list)
            ).all()
            
            existing_trakt_ids = {c.trakt_id for c in existing_candidates}
            missing_trakt_ids = [tid for tid in trakt_ids_from_list if tid not in existing_trakt_ids]
            
            # Only ingest missing items
            if missing_trakt_ids:
                logger.info(f"[Upcoming] Ingesting {len(missing_trakt_ids)} missing items into persistent pool")
                missing_items = []
                for item in all_items:
                    movie = item.get('movie')
                    show = item.get('show')
                    trakt_id = None
                    if movie:
                        trakt_id = movie.get('ids', {}).get('trakt')
                    elif show:
                        trakt_id = show.get('ids', {}).get('trakt')
                    
                    if trakt_id and trakt_id in missing_trakt_ids:
                        missing_items.append(item)
                
                await self._ingest_trakt_list_items(db, missing_items)
            else:
                logger.info("[Upcoming] All items already exist in persistent pool, skipping ingestion")

            
            # Get watched items to exclude
            watched_pairs = set(
                (row[0], row[1]) for row in db.query(
                    TraktWatchHistory.trakt_id, 
                    TraktWatchHistory.media_type
                ).filter(
                    TraktWatchHistory.user_id == self.user_id
                ).distinct()
            )
            
            # Get trakt IDs from lists
            trakt_ids = []
            trakt_media_map = {}
            for item in all_items:
                movie = item.get('movie')
                show = item.get('show')
                if movie:
                    trakt_id = movie.get('ids', {}).get('trakt')
                    if trakt_id:
                        trakt_ids.append(trakt_id)
                        trakt_media_map[trakt_id] = 'movie'
                elif show:
                    trakt_id = show.get('ids', {}).get('trakt')
                    if trakt_id:
                        trakt_ids.append(trakt_id)
                        trakt_media_map[trakt_id] = 'show'
            
            # Query candidates from persistent pool
            candidates = db.query(PersistentCandidate).filter(
                PersistentCandidate.trakt_id.in_(trakt_ids)
            ).all()
            
            # Filter candidates to match correct media_type
            candidates = [
                c for c in candidates 
                if c.trakt_id in trakt_media_map and c.media_type == trakt_media_map[c.trakt_id]
            ]
            
            # Filter out watched items
            candidates = [
                c for c in candidates 
                if (c.trakt_id, c.media_type) not in watched_pairs
            ]
            
            if not candidates:
                return {'items': [], 'message': 'No upcoming releases matching your taste found'}
            
            logger.info(f"[Upcoming] Processing {len(candidates)} unwatched candidates")
            
            # Convert to scoring format
            candidates_dict = [self._candidate_to_dict(c) for c in candidates]
            
            # Filter to future releases only
            today = utc_now().date()
            future_only = []
            for c in candidates_dict:
                rd = c.get('release_date')
                if not rd:
                    continue
                try:
                    d = datetime.strptime(str(rd)[:10], '%Y-%m-%d').date()
                    if d >= today:
                        future_only.append(c)
                except Exception:
                    continue
            
            if not future_only:
                return {'items': [], 'message': 'No future releases found'}
            
            # Apply mood overrides
            filters = self._build_filters_from_mood(mood_overrides) if mood_overrides else {}
            
            # Score candidates
            user_obj = {'id': self.user_id}
            scored = self.scoring_engine.score_candidates(
                user=user_obj,
                candidates=future_only,
                list_type='discovery_overview',  # Use discovery-focused scoring
                explore_factor=0.18,
                item_limit=30,
                filters=filters
            )
            
            # Add release proximity bonus
            for item in scored:
                base_score = item.get('final_score', item.get('score', 0))
                
                release_date_str = item.get('release_date')
                if release_date_str:
                    try:
                        release_date = datetime.strptime(release_date_str[:10], '%Y-%m-%d').date()
                        days_until = (release_date - today).days
                        
                        if days_until >= 0 and days_until <= 180:
                            proximity_score = max(0, 1.0 - (days_until / 180.0))
                            item['days_until_release'] = days_until
                            item['proximity_score'] = proximity_score
                            item['score'] = base_score * 0.9 + proximity_score * 0.1
                        else:
                            item['score'] = base_score
                            item['days_until_release'] = days_until if days_until >= 0 else 0
                    except:
                        item['score'] = base_score
                else:
                    item['score'] = base_score
            
            # Re-sort by boosted score
            scored = sorted(scored, key=lambda x: x.get('score', 0), reverse=True)
            
            # Backfill missing images
            await self._fill_missing_images_for_items(db, scored)
            
            # Format results
            items = []
            for item in scored[:20]:
                days_until = item.get('days_until_release', 999)
                release_badge = '🎬 Out Now!' if days_until == 0 else f'📅 In {days_until} days'
                
                final_score = item.get('score', 0)
                match_percentage = round(final_score * 100) if final_score > 0 else 0
                
                items.append({
                    **item,
                    'overview': str(item.get('overview', ''))[:200],
                    'days_until_release': days_until,
                    'score': round(final_score, 2),
                    'match_percentage': match_percentage,
                    'release_badge': release_badge,
                    'rationale': f'{match_percentage}% match • Releases soon'
                })
            
            logger.info(f"[Upcoming] Returning {len(items)} scored items")
            
            return {
                'items': items,
                'total_candidates': len(all_items),
                'source': 'trakt:upcoming-anticipated'
            }
            
        except Exception as e:
            logger.error(f"Failed to compute upcoming: {e}", exc_info=True)
            return {'items': [], 'error': str(e)}
    
    def _compute_module_priorities(self, db: Session, modules: Dict[str, Dict]) -> Dict[str, float]:
        """
        Compute priority scores for dynamic section reordering.
        
        Logic:
        - Investment Tracker: high if user actively watching show with continuations
        - New Shows: high if no active watching or completed show recently
        - Trending: baseline medium priority
        - Upcoming: high if major release within 7 days
        
        Returns dict of module_type -> priority score (0-100)
        """
        priorities = {
            'investment_tracker': 50.0,
            'new_shows': 50.0,
            'trending': 50.0,
            'upcoming': 50.0
        }
        
        # Boost investment if continuations exist
        if modules.get('investment_tracker', {}).get('continuations') or modules.get('investment_tracker', {}).get('upcoming_continuations'):
            priorities['investment_tracker'] = 80.0
        
        # Boost new shows if no continuations or last watch >3 days ago
        investment = modules.get('investment_tracker', {})
        if not investment.get('continuations'):
            priorities['new_shows'] = 75.0
        
        # Boost trending baseline
        priorities['trending'] = 60.0
        
        # Boost upcoming if releases within 7 days
        # (will implement after _compute_upcoming is done)
        
        return priorities

    async def _fill_missing_images_for_items(self, db: Session, items: List[Dict[str, Any]], max_lookups: int = 10) -> None:
        """Best-effort backfill for missing poster/backdrop paths using MediaMetadata or TMDB.
        Updates both the in-memory items and persists to PersistentCandidate when possible.
        """
        if not items:
            return
        patched = 0
        for it in items:
            if patched >= max_lookups:
                break
            poster = it.get('poster_path') or ''
            backdrop = it.get('backdrop_path') or ''
            if poster and backdrop:
                continue
            tmdb_id = it.get('tmdb_id')
            media_type = it.get('media_type') or 'movie'
            if not tmdb_id:
                continue
            try:
                # Try MediaMetadata cache first
                meta = db.query(MediaMetadata).filter(
                    and_(MediaMetadata.tmdb_id == tmdb_id, MediaMetadata.media_type == media_type)
                ).first()
                poster_path = meta.poster_path if meta else None
                backdrop_path = meta.backdrop_path if meta else None
                if not poster_path or not backdrop_path:
                    # Fetch from TMDB (async call)
                    tmdb = await fetch_tmdb_metadata(tmdb_id, 'tv' if media_type == 'show' else 'movie')
                    if tmdb:
                        poster_path = poster_path or tmdb.get('poster_path')
                        backdrop_path = backdrop_path or tmdb.get('backdrop_path')
                # Apply if we have improvements
                updated = False
                if poster_path and not poster:
                    it['poster_path'] = poster_path
                    updated = True
                if backdrop_path and not backdrop:
                    it['backdrop_path'] = backdrop_path
                    updated = True
                if updated:
                    # Persist to persistent_candidates if row exists
                    pc = db.query(PersistentCandidate).filter(
                        and_(
                            PersistentCandidate.tmdb_id == tmdb_id,
                            PersistentCandidate.media_type == media_type
                        )
                    ).first()
                    if pc:
                        if poster_path and not pc.poster_path:
                            pc.poster_path = poster_path
                        if backdrop_path and not pc.backdrop_path:
                            pc.backdrop_path = backdrop_path
                        pc.last_refreshed = utc_now()
                        db.add(pc)
                        # Important: use flush instead of commit to avoid closing
                        # the active (nested) transaction/savepoint managed by callers.
                        # The outer transaction will be committed by the caller.
                        db.flush()
                    patched += 1
            except Exception as e:
                # Silent best-effort; just log at debug level
                logger.debug(f"[Images] Failed to patch images for tmdb {tmdb_id}: {e}")
    
    async def _cache_modules(self, db: Session, modules: Dict[str, Tuple[Dict, float]]):
        """
        Cache module results in overview_cache table.
        
        Args:
            modules: Dict of module_type -> (data_dict, priority_score)
        """
        expires_at = utc_now() + timedelta(hours=24)
        
        for module_type, (data, priority) in modules.items():
            # Delete existing cache
            db.query(OverviewCache).filter(
                and_(
                    OverviewCache.user_id == self.user_id,
                    OverviewCache.module_type == module_type
                )
            ).delete()
            
            # Insert new cache
            cache_entry = OverviewCache(
                user_id=self.user_id,
                module_type=module_type,
                data_json=json.dumps(data),
                priority_score=priority,
                item_count=self._count_items_in_module(data),
                computed_at=utc_now(),
                expires_at=expires_at
            )
            db.add(cache_entry)
        
        db.commit()
        logger.info(f"Cached {len(modules)} overview modules for user {self.user_id}")
    
    def _count_items_in_module(self, data: Dict) -> int:
        """Count items in a module data structure."""
        if 'items' in data and isinstance(data['items'], list):
            return len(data['items'])
        if 'continuations' in data and isinstance(data['continuations'], list):
            return len(data['continuations'])
        return 0
    
    def get_cached_overview(self, db: Session, apply_mood: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Retrieve cached overview data with dynamic reordering.
        
        Args:
            db: Database session
            apply_mood: Optional mood overrides to filter/reorder
        
        Returns:
            Dict with sections ordered by priority
        """
        # Fetch all cached modules
        cache_entries = db.query(OverviewCache).filter(
            and_(
                OverviewCache.user_id == self.user_id,
                OverviewCache.expires_at > utc_now()
            )
        ).order_by(desc(OverviewCache.priority_score)).all()
        
        if not cache_entries:
            return {'sections': [], 'message': 'No cached data available. Overview will be computed nightly.'}
        
        sections = []
        for entry in cache_entries:
            try:
                data = json.loads(entry.data_json)
                sections.append({
                    'type': entry.module_type,
                    'priority': entry.priority_score,
                    'data': data,
                    'computed_at': entry.computed_at.isoformat(),
                    'item_count': entry.item_count
                })
            except Exception as e:
                logger.warning(f"Failed to parse cached module {entry.module_type}: {e}")
        
        # Apply mood filtering if provided
        if apply_mood:
            sections = self._apply_mood_filters(sections, apply_mood)
        
        return {
            'sections': sections,
            'user_id': self.user_id,
            'retrieved_at': utc_now().isoformat()
        }
    
    def _apply_mood_filters(self, sections: List[Dict], mood: Dict) -> List[Dict]:
        """
        Apply mood slider filters to sections.
        
        Mood params:
        - energy: 0-100 (chill -> intense)
        - exploration: 0-100 (familiar -> discover)
        - commitment: 0-100 (quick -> epic)
        """
        energy = mood.get('energy', 50)  # 0-100
        exploration = mood.get('exploration', 50)
        commitment = mood.get('commitment', 50)
        
        filtered_sections = []
        
        for section in sections:
            filtered_items = []
            
            for item in section.get('data', {}).get('items', []):
                # Energy filter: genre-based
                genres = [g.lower() if isinstance(g, str) else str(g).lower() 
                         for g in item.get('genres', [])]
                
                high_energy_genres = {'action', 'thriller', 'horror', 'adventure'}
                low_energy_genres = {'documentary', 'drama', 'romance', 'animation'}
                
                energy_score = 50  # Neutral
                if any(g in high_energy_genres for g in genres):
                    energy_score = 75
                elif any(g in low_energy_genres for g in genres):
                    energy_score = 25
                
                energy_match = 100 - abs(energy - energy_score)
                
                # Exploration filter: popularity-based
                popularity = item.get('popularity', 50)
                obscurity_score = max(0, 100 - (popularity / 10))  # Invert popularity
                exploration_match = 100 - abs(exploration - obscurity_score)
                
                # Commitment filter: runtime/media_type based
                media_type = item.get('media_type', 'movie')
                commitment_score = 70 if media_type == 'show' else 30
                commitment_match = 100 - abs(commitment - commitment_score)
                
                # Calculate overall match (all filters must pass threshold)
                avg_match = (energy_match + exploration_match + commitment_match) / 3
                
                # Threshold: 40% match required (allows some flexibility)
                if avg_match >= 40:
                    item['mood_match'] = round(avg_match, 1)
                    filtered_items.append(item)
            
            # Update section data
            if filtered_items:
                section_copy = section.copy()
                section_copy['data'] = section['data'].copy()
                section_copy['data']['items'] = filtered_items
                section_copy['data']['filtered_count'] = len(filtered_items)
                section_copy['data']['original_count'] = len(section.get('data', {}).get('items', []))
                filtered_sections.append(section_copy)
        
        return filtered_sections
    
    def _build_filters_from_mood(self, mood_overrides: Dict) -> Dict[str, Any]:
        """
        Convert mood slider values to ScoringEngine filter parameters.
        """
        energy = mood_overrides.get('energy', 50)
        exploration = mood_overrides.get('exploration', 50)
        commitment = mood_overrides.get('commitment', 50)
        
        filters = {}
        
        # Energy → genre preferences
        if energy > 70:
            filters['preferred_genres'] = ['action', 'thriller', 'adventure', 'horror']
        elif energy < 30:
            filters['preferred_genres'] = ['documentary', 'drama', 'romance']
        
        # Exploration → discover factor (handled in compute methods)
        
        # Commitment → media type preference
        if commitment > 60:
            filters['media_types'] = ['show']
        elif commitment < 40:
            filters['media_types'] = ['movie']
        
        return filters
    
    def _candidate_to_dict(self, candidate: PersistentCandidate) -> Dict[str, Any]:
        """
        Convert PersistentCandidate model to dict for scoring engine.
        """
        return {
            'trakt_id': candidate.trakt_id,
            'tmdb_id': candidate.tmdb_id,
            'title': candidate.title,
            'media_type': candidate.media_type,
            'year': candidate.year,
            'poster_path': candidate.poster_path,
            'backdrop_path': candidate.backdrop_path,
            'overview': candidate.overview,
            'genres': candidate.genres.split('|') if candidate.genres else [],
            'language': candidate.language,
            'vote_average': candidate.vote_average,
            'vote_count': candidate.vote_count,
            'popularity': candidate.popularity,
            'release_date': candidate.release_date,
            'runtime': candidate.runtime,
            'obscurity_score': candidate.obscurity_score,
            'mainstream_score': candidate.mainstream_score,
            'freshness_score': candidate.freshness_score,
            '_from_persistent_store': True
        }
    
    async def _ingest_trakt_list_items(self, db: Session, list_items: List[Dict[str, Any]]) -> None:
        """
        Process Trakt list items and ensure they exist in persistent_candidates.
        If not found, fetch TMDB metadata and add them.
        
        Args:
            db: Database session
            list_items: List items from Trakt API
        """
        added_count = 0
        updated_count = 0
        
        for item in list_items:
            try:
                movie = item.get('movie')
                show = item.get('show')
                
                if movie:
                    media_type = 'movie'
                    trakt_id = movie.get('ids', {}).get('trakt')
                    tmdb_id = movie.get('ids', {}).get('tmdb')
                    title = movie.get('title')
                    year = movie.get('year')
                elif show:
                    media_type = 'show'
                    trakt_id = show.get('ids', {}).get('trakt')
                    tmdb_id = show.get('ids', {}).get('tmdb')
                    title = show.get('title')
                    year = show.get('year')
                else:
                    continue
                
                if not trakt_id or not tmdb_id:
                    logger.debug(f"[Ingest] Skipping item without IDs: {title}")
                    continue
                
                # Check if already exists by trakt_id + media_type
                existing = db.query(PersistentCandidate).filter(
                    and_(
                        PersistentCandidate.trakt_id == trakt_id,
                        PersistentCandidate.media_type == media_type
                    )
                ).first()

                # Also guard against duplicates by tmdb_id + media_type (unique constraint)
                if not existing:
                    existing_by_tmdb = db.query(PersistentCandidate).filter(
                        and_(
                            PersistentCandidate.tmdb_id == tmdb_id,
                            PersistentCandidate.media_type == media_type
                        )
                    ).first()
                    if existing_by_tmdb:
                        # Optionally backfill trakt_id if missing
                        if not existing_by_tmdb.trakt_id:
                            existing_by_tmdb.trakt_id = trakt_id
                            updated_count += 1
                        # Treat as existing to avoid insert
                        existing = existing_by_tmdb
                
                if existing:
                    # Update metadata from TMDB if stale (>7 days)
                    # Handle timezone-aware vs naive datetime comparison
                    now = utc_now()
                    last_refresh = existing.last_refreshed
                    if last_refresh and last_refresh.tzinfo is None:
                        # Make last_refresh timezone-aware (assume UTC)
                        last_refresh = last_refresh.replace(tzinfo=timezone.utc)
                    
                    if not last_refresh or (now - last_refresh).days > 7:
                        await self._update_candidate_metadata(db, existing, tmdb_id, media_type)
                        updated_count += 1
                else:
                    # Fetch full metadata from TMDB and add to persistent_candidates
                    await self._create_candidate_from_tmdb(db, trakt_id, tmdb_id, media_type, title, year)
                    added_count += 1
                    
            except Exception as e:
                logger.error(f"[Ingest] Failed to process item: {e}", exc_info=True)
                continue
        
        # Do not commit here; caller manages transaction via savepoints
        if added_count > 0 or updated_count > 0:
            logger.info(f"[Ingest] Prepared {added_count} new candidates, updated {updated_count} existing (pending commit)")
    
    async def _create_candidate_from_tmdb(
        self, 
        db: Session, 
        trakt_id: int, 
        tmdb_id: int, 
        media_type: str, 
        title: str, 
        year: Optional[int]
    ) -> None:
        """
        Fetch TMDB metadata and create a new PersistentCandidate entry.
        """
        try:
            # Fetch full metadata from TMDB
            tmdb_media_type = 'tv' if media_type == 'show' else 'movie'
            metadata = await fetch_tmdb_metadata(tmdb_id, tmdb_media_type)
            
            if not metadata:
                logger.debug(f"[Ingest] No TMDB metadata for {title} (tmdb:{tmdb_id})")
                return
            
            # Extract genres
            genres_list = metadata.get('genres', [])
            if isinstance(genres_list, list):
                genres_str = '|'.join([g.get('name', '') for g in genres_list if isinstance(g, dict)])
            else:
                genres_str = ''
            
            # Extract relevant fields
            # Handle year extraction - ensure we don't pass empty strings to integer field
            year_value = None
            if year:
                year_value = year
            else:
                release_date = metadata.get('release_date') or metadata.get('first_air_date')
                if release_date and len(release_date) >= 4:
                    try:
                        year_value = int(release_date[:4])
                    except (ValueError, TypeError):
                        year_value = None
            
            candidate = PersistentCandidate(
                trakt_id=trakt_id,
                tmdb_id=tmdb_id,
                title=metadata.get('title') or metadata.get('name') or title,
                media_type=media_type,
                year=year_value,
                genres=genres_str,
                language=metadata.get('original_language'),
                overview=metadata.get('overview'),
                poster_path=metadata.get('poster_path'),
                backdrop_path=metadata.get('backdrop_path'),
                vote_average=metadata.get('vote_average'),
                vote_count=metadata.get('vote_count'),
                popularity=metadata.get('popularity'),
                release_date=metadata.get('release_date') or metadata.get('first_air_date'),
                 runtime=metadata.get('runtime') or (metadata.get('episode_run_time')[0] if metadata.get('episode_run_time') and len(metadata.get('episode_run_time')) > 0 else None),
                last_refreshed=utc_now(),
                # Compute scores
                obscurity_score=self._compute_obscurity_score(metadata),
                mainstream_score=self._compute_mainstream_score(metadata),
                freshness_score=self._compute_freshness_score(metadata)
            )
            
            db.add(candidate)
            logger.info(f"[Ingest] Added new candidate: {title} (trakt:{trakt_id}, tmdb:{tmdb_id})")
            
        except Exception as e:
            logger.error(f"[Ingest] Failed to create candidate from TMDB: {e}", exc_info=True)
    
    async def _update_candidate_metadata(
        self, 
        db: Session, 
        candidate: PersistentCandidate, 
        tmdb_id: int, 
        media_type: str
    ) -> None:
        """
        Update existing PersistentCandidate with fresh TMDB metadata.
        """
        try:
            tmdb_media_type = 'tv' if media_type == 'show' else 'movie'
            metadata = await fetch_tmdb_metadata(tmdb_id, tmdb_media_type)
            
            if not metadata:
                return
            
            # Update fields
            candidate.title = metadata.get('title') or metadata.get('name') or candidate.title
            candidate.overview = metadata.get('overview') or candidate.overview
            candidate.poster_path = metadata.get('poster_path') or candidate.poster_path
            candidate.backdrop_path = metadata.get('backdrop_path') or candidate.backdrop_path
            candidate.vote_average = metadata.get('vote_average') or candidate.vote_average
            candidate.vote_count = metadata.get('vote_count') or candidate.vote_count
            candidate.popularity = metadata.get('popularity') or candidate.popularity
            candidate.last_refreshed = utc_now()
            
            # Recompute scores
            candidate.obscurity_score = self._compute_obscurity_score(metadata)
            candidate.mainstream_score = self._compute_mainstream_score(metadata)
            candidate.freshness_score = self._compute_freshness_score(metadata)
            
            db.add(candidate)
            logger.debug(f"[Ingest] Updated candidate: {candidate.title} (trakt:{candidate.trakt_id})")
            
        except Exception as e:
            logger.error(f"[Ingest] Failed to update candidate metadata: {e}", exc_info=True)
    
    def _compute_obscurity_score(self, metadata: Dict[str, Any]) -> float:
        """Compute obscurity score from TMDB metadata (0-1, higher = more obscure)."""
        popularity = metadata.get('popularity', 0)
        vote_count = metadata.get('vote_count', 0)
        
        # Normalize: lower popularity/votes = higher obscurity
        pop_score = max(0, 1 - (popularity / 1000))
        vote_score = max(0, 1 - (vote_count / 10000))
        
        return (pop_score + vote_score) / 2
    
    def _compute_mainstream_score(self, metadata: Dict[str, Any]) -> float:
        """Compute mainstream score from TMDB metadata (0-1, higher = more mainstream)."""
        popularity = metadata.get('popularity', 0)
        vote_count = metadata.get('vote_count', 0)
        
        # Normalize: higher popularity/votes = higher mainstream
        pop_score = min(1, popularity / 1000)
        vote_score = min(1, vote_count / 10000)
        
        return (pop_score + vote_score) / 2
    
    def _compute_freshness_score(self, metadata: Dict[str, Any]) -> float:
        """Compute freshness score from TMDB metadata (0-1, higher = more recent)."""
        release_date = metadata.get('release_date') or metadata.get('first_air_date')
        
        if not release_date:
            return 0.5
        
        try:
            release = datetime.strptime(release_date[:10], '%Y-%m-%d')
            days_old = (utc_now() - release).days
            
            # Normalize: releases within last year = 1.0, older = exponential decay
            if days_old < 0:
                return 1.0  # Future release
            elif days_old < 365:
                return 1.0 - (days_old / 365) * 0.5
            else:
                return max(0, 0.5 - ((days_old - 365) / 3650) * 0.5)
        except Exception:
            return 0.5
