"""
suggested_lists.py

Generates personalized list suggestions based on user viewing history,
preferences, and trending content patterns.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from app.core.database import SessionLocal
from app.models import UserList, ListItem, MediaMetadata
from app.services.trakt_client import TraktClient
from app.services.bulk_candidate_provider import BulkCandidateProvider
import json

logger = logging.getLogger(__name__)

class SuggestedListsService:
    def __init__(self, user_id: Optional[int] = None):
        self.user_id = user_id
        self.trakt_client = TraktClient(user_id)
        self.candidate_provider = BulkCandidateProvider(self.user_id or 0)

    async def generate_suggestions(self, limit: int = 12) -> List[Dict[str, Any]]:
        """Generate personalized list suggestions based on user data."""
        suggestions = []
        
        try:
            # Get user's viewing history for analysis
            user_profile = await self._analyze_user_profile()
            total_watched = user_profile.get("total_watched", 0)
            cold_start = total_watched < 40  # threshold for personalization
            
            # Generate different types of suggestions
            if not cold_start:
                suggestions.extend(await self._get_genre_based_suggestions(user_profile))
                suggestions.extend(await self._get_mood_based_suggestions(user_profile))
            suggestions.extend(await self._get_temporal_suggestions(user_profile))
            suggestions.extend(await self._get_discovery_suggestions(user_profile))
            suggestions.extend(await self._get_trending_suggestions())
            
            # Score and sort suggestions
            scored_suggestions = self._score_suggestions(suggestions, user_profile)
            
            return scored_suggestions[:limit]
            
        except Exception as e:
            logger.error(f"Error generating suggestions: {e}")
            return self._get_fallback_suggestions()

    async def _analyze_user_profile(self) -> Dict[str, Any]:
        """Analyze user's viewing history to build preference profile."""
        profile = {
            "favorite_genres": [],
            "preferred_years": [],
            "rating_preferences": {},
            "content_types": {"movies": 0, "shows": 0},
            "viewing_patterns": {},
            "total_watched": 0
        }
        
        try:
            # Get user's watch history using DB helper
            try:
                from app.services.watch_history_helper import WatchHistoryHelper
                from app.core.database import SessionLocal
                
                db = SessionLocal()
                try:
                    helper = WatchHistoryHelper(db=db, user_id=self.user_id)
                    # Get watch stats
                    stats = helper.get_watch_stats()
                    profile["total_watched"] = stats.get("total_watches", 0)
                    profile["content_types"]["movies"] = stats.get("movies_watched", 0)
                    profile["content_types"]["shows"] = stats.get("shows_watched", 0)
                    
                    # Get all history from DB (convert to API format)
                    movie_status = helper.get_watched_status_dict("movie")
                    show_status = helper.get_watched_status_dict("show")
                    
                    all_history = []
                    for trakt_id, data in movie_status.items():
                        all_history.append({"movie": data, "watched_at": data.get("watched_at")})
                    for trakt_id, data in show_status.items():
                        all_history.append({"show": data, "watched_at": data.get("watched_at")})
                    
                    logger.debug(f"[SUGGESTED] Using WatchHistoryHelper for user profile")
                finally:
                    db.close()
            except Exception as e:
                logger.warning(f"Failed to get watch history from DB, falling back to API: {e}")
                # Fallback to Trakt API
                movie_history = await self.trakt_client.get_my_history("movies", limit=500)
                show_history = await self.trakt_client.get_my_history("shows", limit=500)
                all_history = movie_history + show_history
                profile["total_watched"] = len(all_history)
            
            if not all_history:
                return profile
            
            # Analyze genres
            genre_counts = {}
            year_counts = {}
            rating_sum = 0
            rating_count = 0
            
            for item in all_history:
                # Determine content type
                if "movie" in item:
                    profile["content_types"]["movies"] += 1
                    content = item["movie"]
                else:
                    profile["content_types"]["shows"] += 1
                    content = item["show"]
                
                # Extract genres
                genres = content.get("genres", [])
                for genre in genres:
                    genre_counts[genre] = genre_counts.get(genre, 0) + 1
                
                # Extract years
                year = content.get("year")
                if year:
                    year_counts[year] = year_counts.get(year, 0) + 1
                
                # Extract ratings (if available)
                rating = content.get("rating")
                if rating:
                    rating_sum += rating
                    rating_count += 1
            
            # Top genres (sorted by frequency)
            profile["favorite_genres"] = sorted(genre_counts.items(), key=lambda x: x[1], reverse=True)[:5]
            
            # Preferred years (top 3 decades)
            decade_counts = {}
            for year, count in year_counts.items():
                decade = (year // 10) * 10
                decade_counts[decade] = decade_counts.get(decade, 0) + count
            profile["preferred_years"] = sorted(decade_counts.items(), key=lambda x: x[1], reverse=True)[:3]
            
            # Average rating preference
            if rating_count > 0:
                profile["rating_preferences"]["average"] = rating_sum / rating_count
                profile["rating_preferences"]["min_preferred"] = max(5.0, rating_sum / rating_count - 1.0)
            
            return profile
            
        except Exception as e:
            logger.error(f"Error analyzing user profile: {e}")
            return profile

    async def _get_genre_based_suggestions(self, user_profile: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Generate suggestions based on user's favorite genres."""
        suggestions = []
        favorite_genres = user_profile.get("favorite_genres", [])
        
        if not favorite_genres:
            return suggestions
        
        # Create suggestions for top genres
        for genre, count in favorite_genres[:3]:
            # Single genre deep dive
            suggestions.append({
                "title": f"Best of {genre}",
                "description": f"Top-rated {genre.lower()} content you haven't watched yet",
                "filters": {
                    "genres": [genre],
                    "min_rating": user_profile.get("rating_preferences", {}).get("min_preferred", 6.0),
                    "exclude_watched": True,
                    "mood": "popular"
                },
                "type": "genre_focused",
                "priority": 0.9,
                "item_limit": 25,
                "icon": "🎭",
                "color": "purple"
            })
            
            # Genre discovery (mix with less common genres)
            suggestions.append({
                "title": f"{genre} Hidden Gems",
                "description": f"Underrated {genre.lower()} content that deserves more attention",
                "filters": {
                    "genres": [genre],
                    "min_rating": 6.5,
                    "exclude_watched": True,
                    "mood": "obscure",
                    "max_popularity": 50
                },
                "type": "genre_discovery",
                "priority": 0.7,
                "item_limit": 20,
                "icon": "💎",
                "color": "emerald"
            })
        
        return suggestions

    async def _get_mood_based_suggestions(self, user_profile: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Generate mood-based suggestions."""
        suggestions = []
        
        mood_suggestions = [
            {
                "title": "Feel-Good Favorites",
                "description": "Uplifting content to brighten your day",
                "filters": {
                    "mood": "popular",
                    "min_rating": 7.0,
                    "genres": ["comedy", "family", "animation"],
                    "exclude_watched": True
                },
                "type": "mood_uplifting",
                "priority": 0.8,
                "item_limit": 20,
                "icon": "😊",
                "color": "yellow"
            },
            {
                "title": "Mind-Bending Thrillers",
                "description": "Complex plots that will keep you guessing",
                "filters": {
                    "mood": "balanced",
                    "min_rating": 7.5,
                    "genres": ["thriller", "mystery", "sci-fi"],
                    "exclude_watched": True
                },
                "type": "mood_complex",
                "priority": 0.7,
                "item_limit": 15,
                "icon": "🧠",
                "color": "indigo"
            },
            {
                "title": "Relaxing Watch",
                "description": "Easy-going content perfect for unwinding",
                "filters": {
                    "mood": "popular",
                    "min_rating": 6.5,
                    "genres": ["documentary", "comedy", "romance"],
                    "exclude_watched": True,
                    "max_runtime": 120
                },
                "type": "mood_relaxing",
                "priority": 0.6,
                "item_limit": 25,
                "icon": "🛋️",
                "color": "blue"
            }
        ]
        
        return mood_suggestions

    async def _get_temporal_suggestions(self, user_profile: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Generate time-based suggestions (decades, recent releases, etc.)."""
        suggestions = []
        preferred_years = user_profile.get("preferred_years", [])
        
        # Decade-based suggestions
        if preferred_years:
            top_decade = preferred_years[0][0]
            suggestions.append({
                "title": f"Best of the {top_decade}s",
                "description": f"Iconic content from your favorite decade",
                "filters": {
                    "year_min": top_decade,
                    "year_max": top_decade + 9,
                    "min_rating": 7.0,
                    "exclude_watched": True,
                    "mood": "popular"
                },
                "type": "temporal_decade",
                "priority": 0.8,
                "item_limit": 20,
                "icon": "📅",
                "color": "amber"
            })
        
        # Recent releases
        current_year = datetime.now().year
        suggestions.append({
            "title": "This Year's Best",
            "description": f"Top-rated releases from {current_year}",
            "filters": {
                "year_min": current_year,
                "min_rating": 7.0,
                "exclude_watched": True,
                "mood": "popular"
            },
            "type": "temporal_recent",
            "priority": 0.9,
            "item_limit": 15,
            "icon": "🆕",
            "color": "green"
        })
        
        # Classic films
        suggestions.append({
            "title": "Timeless Classics",
            "description": "Legendary films that defined cinema",
            "filters": {
                "year_max": 1990,
                "min_rating": 8.0,
                "exclude_watched": True,
                "mood": "popular",
                "media_types": ["movies"]
            },
            "type": "temporal_classic",
            "priority": 0.6,
            "item_limit": 15,
            "icon": "🎬",
            "color": "rose"
        })
        
        return suggestions

    async def _get_discovery_suggestions(self, user_profile: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Generate discovery-focused suggestions for exploring new content."""
        suggestions = []
        
        discovery_suggestions = [
            {
                "title": "International Cinema",
                "description": "Acclaimed films from around the world",
                "filters": {
                    "languages": ["ja", "ko", "fr", "es", "de", "it"],
                    "min_rating": 7.5,
                    "exclude_watched": True,
                    "mood": "balanced",
                    "media_types": ["movies"]
                },
                "type": "discovery_international",
                "priority": 0.5,
                "item_limit": 20,
                "icon": "🌍",
                "color": "cyan"
            },
            {
                "title": "Deep Cuts",
                "description": "Hidden gems with passionate cult followings",
                "filters": {
                    "min_rating": 7.0,
                    "max_popularity": 25,
                    "exclude_watched": True,
                    "mood": "obscure"
                },
                "type": "discovery_cult",
                "priority": 0.4,
                "item_limit": 15,
                "icon": "🔍",
                "color": "violet"
            },
            {
                "title": "Award Winners",
                "description": "Oscar, Emmy, and festival award winners",
                "filters": {
                    "min_rating": 7.5,
                    "keywords": ["oscar", "award", "winner", "nominated"],
                    "exclude_watched": True,
                    "mood": "popular"
                },
                "type": "discovery_awards",
                "priority": 0.7,
                "item_limit": 20,
                "icon": "🏆",
                "color": "orange"
            }
        ]
        
        return discovery_suggestions

    async def _get_trending_suggestions(self) -> List[Dict[str, Any]]:
        """Generate suggestions based on current trending content."""
        suggestions = [
            {
                "title": "Trending Now",
                "description": "What everyone is watching right now",
                "filters": {
                    "mood": "trending",
                    "exclude_watched": True,
                    "min_rating": 6.0
                },
                "type": "trending_now",
                "priority": 0.9,
                "item_limit": 20,
                "icon": "🔥",
                "color": "red"
            },
            {
                "title": "Rising Stars",
                "description": "Content that's rapidly gaining popularity",
                "filters": {
                    "mood": "balanced",
                    "exclude_watched": True,
                    "min_rating": 6.5,
                    "year_min": datetime.now().year - 2
                },
                "type": "trending_rising",
                "priority": 0.7,
                "item_limit": 15,
                "icon": "⭐",
                "color": "pink"
            }
        ]
        
        return suggestions

    def _score_suggestions(self, suggestions: List[Dict[str, Any]], user_profile: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Score and sort suggestions based on user profile relevance."""
        total_watched = user_profile.get("total_watched", 0)
        
        for suggestion in suggestions:
            base_priority = suggestion.get("priority", 0.5)
            
            # Boost priority based on user activity level
            if total_watched > 100:
                # Experienced users get more discovery content
                if suggestion["type"].startswith("discovery"):
                    base_priority += 0.2
            elif total_watched < 20:
                # New users get more popular content
                if suggestion["type"].startswith("mood") or "popular" in suggestion.get("filters", {}).get("mood", ""):
                    base_priority += 0.3
            
            # Genre alignment bonus
            suggestion_genres = suggestion.get("filters", {}).get("genres", [])
            user_genres = [genre for genre, count in user_profile.get("favorite_genres", [])]
            if suggestion_genres and user_genres:
                overlap = len(set(suggestion_genres) & set(user_genres))
                base_priority += overlap * 0.1
            
            suggestion["final_score"] = min(1.0, base_priority)
        
        return sorted(suggestions, key=lambda x: x["final_score"], reverse=True)

    def _get_fallback_suggestions(self) -> List[Dict[str, Any]]:
        """Fallback suggestions when user analysis fails."""
        return [
            {
                "title": "Popular Picks",
                "description": "Currently trending and highly-rated content",
                "filters": {"mood": "popular", "min_rating": 7.0, "exclude_watched": True},
                "type": "fallback_popular",
                "priority": 0.8,
                "item_limit": 20,
                "icon": "⭐",
                "color": "blue"
            },
            {
                "title": "Hidden Gems",
                "description": "Underrated content worth discovering",
                "filters": {"mood": "obscure", "min_rating": 7.5, "exclude_watched": True},
                "type": "fallback_discovery",
                "priority": 0.6,
                "item_limit": 15,
                "icon": "💎",
                "color": "emerald"
            },
            {
                "title": "New Releases",
                "description": "Recently released content",
                "filters": {"year_min": datetime.now().year, "min_rating": 6.5, "exclude_watched": True},
                "type": "fallback_recent",
                "priority": 0.7,
                "item_limit": 15,
                "icon": "🆕",
                "color": "green"
            }
        ]

    async def create_suggested_list(self, suggestion: Dict[str, Any]) -> Dict[str, Any]:
        """Create an actual UserList from a suggestion."""
        db = SessionLocal()
        
        try:
            filters = suggestion.get("filters", {})
            
            # Create the list
            user_list = UserList(
                user_id=self.user_id,
                title=suggestion["title"],
                filters=json.dumps(filters),
                item_limit=suggestion.get("item_limit", 20),
                list_type="suggested",
                sync_interval=24,  # Auto-sync daily (hours)
                sync_watched_status=True,
                exclude_watched=filters.get("exclude_watched", False),
                sync_status="queued"  # Mark as queued for population
            )
            
            db.add(user_list)
            db.commit()
            db.refresh(user_list)
            
            # Create corresponding Trakt list (best-effort)
            try:
                trakt = TraktClient(self.user_id or 1)
                trakt_result = await trakt.create_list(
                    name=user_list.title,
                    description="Suggested list managed by WatchBuddy",
                    privacy="private"
                )
                trakt_list_id = trakt_result.get("ids", {}).get("trakt")
                if trakt_list_id:
                    user_list.trakt_list_id = str(trakt_list_id)
                    db.commit()
                    logger.info(f"Created Trakt list {trakt_list_id} for suggested list {user_list.id}")
                    # Notify success
                    from app.api.notifications import send_notification
                    try:
                        await send_notification(self.user_id or 1, f"Created Trakt list for '{user_list.title}'", "success")
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"Failed to create Trakt list for suggested list {user_list.id}: {e}")
                from app.api.notifications import send_notification
                try:
                    await send_notification(self.user_id or 1, f"List '{user_list.title}' created locally, but Trakt list creation failed", "warning")
                except Exception:
                    pass
            
            # Queue async population task
            from app.services.tasks import populate_new_list_async
            task = populate_new_list_async.delay(
                list_id=user_list.id,
                user_id=self.user_id,
                discovery=filters.get("discovery", "balanced"),
                media_types=filters.get("media_types", ["movies", "shows"]),
                items_per_list=suggestion.get("item_limit", 20),
                fusion_mode=filters.get("fusion_mode", False),
                list_type="suggested"
            )
            
            logger.info(f"Queued population task {task.id} for suggested list {user_list.id}")
            
            return {
                "id": user_list.id,
                "title": user_list.title,
                "description": suggestion.get("description", ""),
                "type": suggestion.get("type", "suggested"),
                "icon": suggestion.get("icon", "📋"),
                "color": suggestion.get("color", "blue"),
                "status": "populating",
                "task_id": task.id
            }
            
        except Exception as e:
            db.rollback()
            raise
        finally:
            db.close()