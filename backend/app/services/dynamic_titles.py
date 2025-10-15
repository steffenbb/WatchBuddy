"""
dynamic_titles.py

Service for generating Netflix-style dynamic titles for SmartLists based on user preferences and viewing history.
"""

import random
from typing import Dict, List, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
import logging

from ..core.database import SessionLocal
from ..models import UserRating, MediaMetadata, ListItem, UserList
from ..services.trakt_client import TraktClient

logger = logging.getLogger(__name__)

class DynamicTitleGenerator:
    """Generates personalized, dynamic titles for SmartLists based on user behavior."""
    
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.db = SessionLocal()
    
    def __del__(self):
        if hasattr(self, 'db') and self.db:
            self.db.close()
    
    async def generate_title(
        self, 
        list_type: str = "smartlist", 
        discovery: str = "balanced",
        media_types: List[str] = None,
        fusion_mode: bool = False
    ) -> str:
        """Generate a dynamic title based on user preferences and behavior."""
        
        media_types = media_types or ["movies", "shows"]
        
        try:
            # Get user's viewing patterns
            liked_items = await self._get_liked_items()
            recent_watches = await self._get_recent_watches()
            top_genres = await self._get_top_genres()
            
            # Generate title based on available data
            title = await self._create_contextual_title(
                list_type, discovery, media_types, fusion_mode,
                liked_items, recent_watches, top_genres
            )
            
            return title
            
        except Exception as e:
            logger.error(f"Error generating dynamic title: {e}")
            # Fallback to basic title
            return self._get_fallback_title(list_type, discovery, fusion_mode)
    
    async def _get_liked_items(self) -> List[Dict[str, Any]]:
        """Get items the user has rated positively (thumbs up)."""
        try:
            liked_ratings = self.db.query(UserRating).filter(
                UserRating.user_id == self.user_id,
                UserRating.rating == 1
            ).order_by(desc(UserRating.updated_at)).limit(10).all()
            
            result = []
            for rating in liked_ratings:
                media = self.db.query(MediaMetadata).filter(
                    MediaMetadata.trakt_id == rating.trakt_id
                ).first()
                
                if media:
                    result.append({
                        'trakt_id': rating.trakt_id,
                        'title': media.title,
                        'year': media.year,
                        'genres': media.genres,
                        'media_type': media.media_type,
                        'rating': media.rating
                    })
            
            return result
        except Exception as e:
            logger.error(f"Error getting liked items: {e}")
            return []
    
    async def _get_recent_watches(self) -> List[Dict[str, Any]]:
        """Get recently watched items from user's lists."""
        try:
            # Get recent watched items from all user's lists
            recent_items = self.db.query(ListItem).join(UserList).filter(
                UserList.user_id == self.user_id,
                ListItem.is_watched == True,
                ListItem.watched_at.isnot(None)
            ).order_by(desc(ListItem.watched_at)).limit(20).all()
            
            result = []
            for item in recent_items:
                media = self.db.query(MediaMetadata).filter(
                    MediaMetadata.trakt_id == item.trakt_id
                ).first()
                
                if media:
                    result.append({
                        'trakt_id': item.trakt_id,
                        'title': media.title,
                        'year': media.year,
                        'genres': media.genres,
                        'media_type': media.media_type,
                        'watched_at': item.watched_at
                    })
            
            return result
        except Exception as e:
            logger.error(f"Error getting recent watches: {e}")
            return []
    
    async def _get_top_genres(self) -> List[str]:
        """Get user's most preferred genres based on ratings and viewing history."""
        try:
            # Get genres from liked items
            liked_genres = []
            
            # From thumbs up ratings
            liked_ratings = self.db.query(UserRating).filter(
                UserRating.user_id == self.user_id,
                UserRating.rating == 1
            ).all()
            
            for rating in liked_ratings:
                media = self.db.query(MediaMetadata).filter(
                    MediaMetadata.trakt_id == rating.trakt_id
                ).first()
                if media and media.genres:
                    try:
                        import json
                        genres = json.loads(media.genres) if isinstance(media.genres, str) else media.genres
                        if isinstance(genres, list):
                            liked_genres.extend(genres)
                    except:
                        pass
            
            # Count genre frequency
            genre_counts = {}
            for genre in liked_genres:
                genre = genre.lower().strip()
                genre_counts[genre] = genre_counts.get(genre, 0) + 1
            
            # Return top 5 genres
            return sorted(genre_counts.keys(), key=lambda x: genre_counts[x], reverse=True)[:5]
            
        except Exception as e:
            logger.error(f"Error getting top genres: {e}")
            return []
    
    async def _create_contextual_title(
        self, 
        list_type: str, 
        discovery: str, 
        media_types: List[str],
        fusion_mode: bool,
        liked_items: List[Dict],
        recent_watches: List[Dict],
        top_genres: List[str]
    ) -> str:
        """Create a contextual title based on user data."""
        
        # Netflix-style title templates
        templates = {
            'because_you_watched': [
                "Because You Watched {title}",
                "More Like {title}",
                "If You Liked {title}",
                "Fans of {title} Also Enjoyed"
            ],
            'genre_based': [
                "Great {genre} Picks",
                "Top {genre} Recommendations", 
                "Must-Watch {genre}",
                "{genre} You'll Love",
                "Hidden {genre} Gems"
            ],
            'discovery_based': [
                "Hidden Gems for You",
                "Undiscovered Favorites",
                "Your Next Great Find",
                "Overlooked Masterpieces"
            ],
            'trending_based': [
                "Trending Now",
                "Popular This Week",
                "What Everyone's Watching",
                "Hot Right Now"
            ],
            'fusion_based': [
                "AI-Curated Just for You",
                "Your Perfect Match",
                "Personalized Picks",
                "Tailored Recommendations"
            ],
            'mixed_media': [
                "Movies & Shows You'll Love",
                "Your Next Binge",
                "Top Picks Across All Genres"
            ]
        }
        
        # Choose title strategy based on available data and parameters
        if fusion_mode and random.random() < 0.4:
            return random.choice(templates['fusion_based'])
        
        # "Because you watched" style (40% chance if we have recent watches)
        if recent_watches and random.random() < 0.4:
            recent_item = random.choice(recent_watches[:5])  # Use recent watches
            title = recent_item.get('title', '').strip()
            if title:  # Only use template if we have a valid title
                template = random.choice(templates['because_you_watched'])
                try:
                    return template.format(title=title)
                except (KeyError, ValueError) as e:
                    logger.warning(f"Failed to format title template: {e}")
        
        # "Because you liked" style using thumbs up data (30% chance)
        if liked_items and random.random() < 0.3:
            liked_item = random.choice(liked_items[:3])  # Use most recent likes
            title = liked_item.get('title', '').strip()
            if title:  # Only use template if we have a valid title
                template = random.choice(templates['because_you_watched'])
                try:
                    return template.format(title=title)
                except (KeyError, ValueError) as e:
                    logger.warning(f"Failed to format title template: {e}")
        
        # Genre-based titles (25% chance if we have genre data)
        if top_genres and random.random() < 0.25:
            genre = random.choice(top_genres[:3]).title()  # Use top genres
            template = random.choice(templates['genre_based'])
            return template.format(genre=genre)
        
        # Discovery-based titles
        if discovery in ['obscure', 'very_obscure']:
            return random.choice(templates['discovery_based'])
        
        # Trending-based titles  
        if discovery in ['popular', 'mainstream']:
            return random.choice(templates['trending_based'])
        
        # Mixed media titles
        if len(media_types) > 1:
            return random.choice(templates['mixed_media'])
        
        # Fallback to genre or generic
        if top_genres:
            genre = top_genres[0].title()
            return f"Great {genre} Picks"
        
        return self._get_fallback_title(list_type, discovery, fusion_mode)
    
    def _get_fallback_title(self, list_type: str, discovery: str, fusion_mode: bool) -> str:
        """Generate a fallback title when personalization data is limited."""
        
        if fusion_mode:
            return "AI-Curated Recommendations"
        
        discovery_titles = {
            'obscure': 'Hidden Gems',
            'very_obscure': 'Deep Cuts', 
            'popular': 'Popular Picks',
            'mainstream': 'Trending Now',
            'balanced': 'Smart Picks'
        }
        
        return discovery_titles.get(discovery, 'Smart Picks')

    async def should_update_title(self, user_list: UserList) -> bool:
        """Determine if a list's title should be updated based on changed preferences."""
        
        if not user_list.title or 'Smart Picks' in user_list.title:
            return True  # Always update generic titles
        
        # Update if it's been a while since last update (7+ days)
        import datetime
        if user_list.last_updated:
            days_since_update = (datetime.datetime.utcnow() - user_list.last_updated).days
            if days_since_update >= 7:
                return True
        
        # Update if user has new ratings since last list update
        if user_list.last_updated:
            recent_ratings = self.db.query(UserRating).filter(
                UserRating.user_id == self.user_id,
                UserRating.updated_at > user_list.last_updated
            ).count()
            
            if recent_ratings >= 3:  # 3+ new ratings warrant a title update
                return True
        
        return False