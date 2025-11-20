"""
History compression service for user watch history.

Compresses user watch history into compact persona vectors and text summaries
using phi3:mini LLM. Implements recency decay and version tracking.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
import hashlib

from sqlalchemy import desc
from sqlalchemy.orm import Session
from app.core.redis_client import get_redis
from app.core.database import SessionLocal
from app.models import User, UserTextProfile
from app.services.trakt_client import TraktClient

logger = logging.getLogger(__name__)


class HistoryCompressor:
    """Compresses watch history into persona summaries."""
    
    def __init__(self, user_id: int = 1):
        self.user_id = user_id
        self.redis = get_redis()
        
    async def compress_history(
        self, 
        db: Session,
        max_items: int = 200,
        force_rebuild: bool = False
    ) -> Dict[str, Any]:
        """Compress user watch history into persona summary.
        
        Args:
            db: Database session
            max_items: Maximum number of recent history items to include
            force_rebuild: Force rebuild even if recent version exists
            
        Returns:
            Dict with persona_text, watch_vector, version, and metadata
        """
        # Check for existing compressed history
        if not force_rebuild:
            existing = await self._get_cached_compression()
            if existing and self._is_fresh(existing):
                logger.info(f"Using cached history compression for user {self.user_id}")
                return existing
                
        # Fetch watch history from Trakt
        trakt_client = TraktClient(user_id=self.user_id)
        
        try:
            # Get watched movies and shows with ratings
            movies = await trakt_client.get_watched_movies(limit=max_items // 2)
            shows = await trakt_client.get_watched_shows(limit=max_items // 2)
            
            # Get user ratings
            movie_ratings = await trakt_client.get_user_ratings(media_type='movies', limit=100)
            show_ratings = await trakt_client.get_user_ratings(media_type='shows', limit=100)
            
        except Exception as e:
            logger.error(f"Failed to fetch Trakt history for user {self.user_id}: {e}")
            return self._get_empty_compression()
            
        # Combine and sort by recency
        all_history = self._combine_history(movies, shows, movie_ratings, show_ratings)
        
        if not all_history:
            logger.warning(f"No watch history found for user {self.user_id}")
            return self._get_empty_compression()
            
        # Apply recency decay weighting
        weighted_history = self._apply_recency_decay(all_history, max_items)
        
        # Generate persona text summary via phi3:mini
        persona_text = await self._generate_persona_text(weighted_history)
        
        # Generate compressed watch vector (genre/keyword weights)
        watch_vector = self._generate_watch_vector(weighted_history)
        
        # Create compression result
        compression = {
            "persona_text": persona_text,
            "watch_vector": watch_vector,
            "version": self._compute_version(all_history),
            "compressed_at": datetime.now(timezone.utc).isoformat(),
            "item_count": len(weighted_history),
            "user_id": self.user_id
        }
        
        # Cache in Redis with 7-day TTL
        await self._cache_compression(compression)
        
        # Update UserTextProfile in database
        self._update_user_profile(db, persona_text, watch_vector)
        
        logger.info(f"Compressed history for user {self.user_id}: {len(weighted_history)} items -> {len(persona_text)} chars persona")
        
        return compression
        
    def _combine_history(
        self, 
        movies: List[Dict], 
        shows: List[Dict],
        movie_ratings: List[Dict],
        show_ratings: List[Dict]
    ) -> List[Dict]:
        """Combine watch history and ratings into unified list."""
        history = []
        
        # Add movies
        for movie in movies:
            history.append({
                "type": "movie",
                "title": movie.get("movie", {}).get("title", "Unknown"),
                "year": movie.get("movie", {}).get("year"),
                "genres": movie.get("movie", {}).get("genres", []),
                "watched_at": movie.get("last_watched_at"),
                "plays": movie.get("plays", 1),
                "rating": None
            })
            
        # Add shows
        for show in shows:
            history.append({
                "type": "show",
                "title": show.get("show", {}).get("title", "Unknown"),
                "year": show.get("show", {}).get("year"),
                "genres": show.get("show", {}).get("genres", []),
                "watched_at": show.get("last_watched_at"),
                "plays": show.get("plays", 1),
                "rating": None
            })
            
        # Merge ratings
        rating_map = {}
        for rating in movie_ratings:
            title = rating.get("movie", {}).get("title")
            if title:
                rating_map[title] = rating.get("rating")
                
        for rating in show_ratings:
            title = rating.get("show", {}).get("title")
            if title:
                rating_map[title] = rating.get("rating")
                
        for item in history:
            if item["title"] in rating_map:
                item["rating"] = rating_map[item["title"]]
                
        # Sort by watched_at descending (most recent first)
        history.sort(key=lambda x: x.get("watched_at") or "", reverse=True)
        
        return history
        
    def _apply_recency_decay(self, history: List[Dict], max_items: int) -> List[Dict]:
        """Apply recency decay weighting to history items.
        
        More recent items get higher weights. Uses exponential decay.
        """
        if not history:
            return []
            
        # Limit to max_items
        history = history[:max_items]
        
        # Apply exponential decay: weight = e^(-0.01 * position)
        for i, item in enumerate(history):
            import math
            decay_factor = math.exp(-0.01 * i)
            item["recency_weight"] = decay_factor
            
        return history
        
    async def _generate_persona_text(self, weighted_history: List[Dict]) -> str:
        """Generate persona text summary using phi3:mini LLM.
        
        Creates a 2-5 sentence narrative describing user preferences.
        """
        if not weighted_history:
            return "No viewing history available."
            
        # Prepare history summary for LLM
        top_items = weighted_history[:20]  # Use top 20 most recent
        
        # Extract key patterns
        genre_counts: Dict[str, int] = {}
        top_rated: List[str] = []
        
        for item in top_items:
            # Count genres
            for genre in item.get("genres", []):
                genre_counts[genre] = genre_counts.get(genre, 0) + 1
                
            # Track highly rated items
            if item.get("rating") and item["rating"] >= 8:
                top_rated.append(item["title"])
                
        # Get top genres
        top_genres = sorted(genre_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        top_genre_names = [g[0] for g in top_genres]
        
        # Build LLM prompt
        prompt = f"""Based on the following viewing history, write a 2-3 sentence persona summary:

Recent watches: {', '.join([item['title'] for item in top_items[:10]])}
Top genres: {', '.join(top_genre_names)}
Highly rated: {', '.join(top_rated[:5]) if top_rated else 'None'}

**TASK:** Write a concise persona summary (2-3 sentences) describing this viewer's preferences.

**IMPORTANT:** Return ONLY the plain text persona summary. No JSON, no markdown, no bullet points, no extra commentary.

**Output the persona summary now:**
"""

        # Call phi3:mini via Ollama
        try:
            from app.services.ollama_client import get_ollama_client
            client = get_ollama_client()
            
            logger.info(f"Generating persona text with Ollama, prompt length: {len(prompt)}, items: {len(weighted_history)}")
            
            response = await client.generate(
                model="phi3.5:3.8b-mini-instruct-q4_K_M",
                prompt=prompt,
                options={
                    "temperature": 0.3,  # Low temp for consistent summaries
                    "num_ctx": 4096,  # Large context window for full history
                    "top_p": 0.9
                },
                timeout=60.0  # Explicit 60s timeout
            )
            
            persona_text = response.get("response", "").strip()
            
            logger.info(f"Ollama persona generated: length={len(persona_text)}, preview={persona_text[:100]}")
            
            # Validate response
            if not persona_text or len(persona_text) < 20:
                logger.warning(f"LLM returned invalid persona (length={len(persona_text)}): {persona_text[:200]}")
                raise ValueError("LLM returned empty or too short persona")
            
            # No artificial length cap - let natural LLM response determine length
            return persona_text
            
        except Exception as e:
            logger.error(f"Failed to generate persona text via LLM: {e}. Raw response: {response.get('response', '')[:300] if 'response' in locals() else 'N/A'}")
            # Fallback: template-based summary
            return self._generate_fallback_persona(top_genre_names, top_rated, top_items)
            
    def _generate_fallback_persona(
        self, 
        top_genres: List[str], 
        top_rated: List[str],
        recent_items: List[Dict]
    ) -> str:
        """Generate fallback persona summary without LLM."""
        genre_str = ", ".join(top_genres[:3]) if top_genres else "various genres"
        
        if top_rated:
            return f"Enjoys {genre_str}. Highly rated: {', '.join(top_rated[:3])}. Recently watched {len(recent_items)} titles."
        else:
            return f"Enjoys {genre_str}. Recently watched {len(recent_items)} titles including {recent_items[0]['title']}."
            
    def _generate_watch_vector(self, weighted_history: List[Dict]) -> Dict[str, float]:
        """Generate compressed watch vector with genre/keyword weights.
        
        Uses recency weights to emphasize recent viewing patterns.
        """
        vector: Dict[str, float] = {}
        
        for item in weighted_history:
            weight = item.get("recency_weight", 1.0)
            
            # Genre weights
            for genre in item.get("genres", []):
                genre_key = f"genre:{genre.lower()}"
                vector[genre_key] = vector.get(genre_key, 0) + weight
                
            # Rating boost (if highly rated)
            if item.get("rating") and item["rating"] >= 8:
                vector["high_rated"] = vector.get("high_rated", 0) + weight * 1.5
                
            # Type preference
            type_key = f"type:{item['type']}"
            vector[type_key] = vector.get(type_key, 0) + weight
            
        # Normalize vector (max value = 1.0)
        if vector:
            max_value = max(vector.values())
            if max_value > 0:
                vector = {k: v / max_value for k, v in vector.items()}
                
        return vector
        
    def _compute_version(self, history: List[Dict]) -> str:
        """Compute version hash of watch history for cache invalidation."""
        # Use first 50 items to generate hash (balance freshness vs stability)
        items_for_hash = history[:50]
        hash_input = json.dumps([
            {"title": item["title"], "watched_at": item.get("watched_at")}
            for item in items_for_hash
        ], sort_keys=True)
        
        return hashlib.md5(hash_input.encode()).hexdigest()[:12]
        
    async def _get_cached_compression(self) -> Optional[Dict[str, Any]]:
        """Get cached compression from Redis."""
        try:
            key = f"history_compression:{self.user_id}"
            data = self.redis.get(key)
            if data:
                return json.loads(data)
        except Exception as e:
            logger.error(f"Failed to get cached compression: {e}")
        return None
        
    async def _cache_compression(self, compression: Dict[str, Any]) -> None:
        """Cache compression in Redis with 7-day TTL."""
        try:
            key = f"history_compression:{self.user_id}"
            self.redis.setex(
                key, 
                60 * 60 * 24 * 7,  # 7 days
                json.dumps(compression)
            )
        except Exception as e:
            logger.error(f"Failed to cache compression: {e}")
            
    def _is_fresh(self, compression: Dict[str, Any]) -> bool:
        """Check if cached compression is fresh (< 24 hours old)."""
        try:
            compressed_at = datetime.fromisoformat(compression["compressed_at"])
            age_hours = (datetime.now(timezone.utc) - compressed_at).total_seconds() / 3600
            return age_hours < 24
        except Exception:
            return False
            
    def _get_empty_compression(self) -> Dict[str, Any]:
        """Return empty compression result."""
        return {
            "persona_text": "No viewing history available.",
            "watch_vector": {},
            "version": "empty",
            "compressed_at": datetime.now(timezone.utc).isoformat(),
            "item_count": 0,
            "user_id": self.user_id
        }
        
    def _update_user_profile(
        self, 
        db: Session, 
        persona_text: str, 
        watch_vector: Dict[str, float]
    ) -> None:
        """Update UserTextProfile in database."""
        try:
            profile = db.query(UserTextProfile).filter_by(user_id=self.user_id).first()
            
            # Extract top keywords from watch vector for tags
            top_items = sorted(
                watch_vector.items(), 
                key=lambda x: x[1], 
                reverse=True
            )[:20]
            tags = [k.replace("genre:", "").replace("type:", "") for k, v in top_items]
            
            if profile:
                profile.summary_text = persona_text
                profile.tags_json = json.dumps(tags)
                profile.updated_at = datetime.now(timezone.utc)
            else:
                profile = UserTextProfile(
                    user_id=self.user_id,
                    summary_text=persona_text,
                    tags_json=json.dumps(tags)
                )
                db.add(profile)
                
            db.commit()
            logger.info(f"Updated UserTextProfile for user {self.user_id}")
            
        except Exception as e:
            logger.error(f"Failed to update UserTextProfile: {e}")
            db.rollback()


async def compress_user_history_task(user_id: int = 1, force_rebuild: bool = False) -> Dict[str, Any]:
    """Celery task wrapper for history compression.
    
    Args:
        user_id: User ID to compress history for
        force_rebuild: Force rebuild even if cached
        
    Returns:
        Compression result dict
    """
    db = SessionLocal()
    try:
        compressor = HistoryCompressor(user_id=user_id)
        result = await compressor.compress_history(
            db=db,
            max_items=200,
            force_rebuild=force_rebuild
        )
        return result
    finally:
        db.close()
