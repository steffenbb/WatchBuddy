"""
Persona helper for retrieving compressed user personas.

Provides trimmed persona text and watch history summaries for LLM prompts.
"""
import json
import logging
from typing import Dict, Any, Optional

from app.core.redis_client import get_redis_sync
from sqlalchemy.orm import Session
from app.models import UserTextProfile

logger = logging.getLogger(__name__)


class PersonaHelper:
    """Helper to fetch and format user persona for LLM prompts."""
    
    @staticmethod
    def get_persona(user_id: int = 1, db: Optional[Session] = None) -> str:
        """Get compressed persona text for user.
        
        Tries Redis cache first, falls back to UserTextProfile in DB.
        Returns trimmed persona (max 200 chars) suitable for LLM prompts.
        
        Args:
            user_id: User ID
            db: Optional database session (if None, will create one)
            
        Returns:
            Persona text (empty string if not found)
        """
        # Try Redis cache first (from history compression job)
        try:
            redis = get_redis_sync()
            key = f"history_compression:{user_id}"
            data = redis.get(key)
            
            if data:
                compression = json.loads(data if isinstance(data, str) else data.decode("utf-8"))
                persona_text = compression.get("persona_text", "")
                
                if persona_text and len(persona_text) > 10:
                    # Trim to max 200 chars for token efficiency
                    return persona_text[:200].strip()
                    
        except Exception as e:
            logger.debug(f"Failed to get persona from Redis: {e}")
            
        # Fallback to database
        if db:
            try:
                profile = db.query(UserTextProfile).filter_by(user_id=user_id).first()
                if profile and profile.summary_text:
                    return profile.summary_text[:200].strip()
            except Exception as e:
                logger.debug(f"Failed to get persona from DB: {e}")
                
        return ""
        
    @staticmethod
    def get_history_summary(user_id: int = 1, max_length: int = 150) -> str:
        """Get compressed watch history summary for user.
        
        Returns top genres/keywords from watch vector, trimmed for token efficiency.
        
        Args:
            user_id: User ID
            max_length: Maximum character length
            
        Returns:
            History summary text (empty string if not found)
        """
        try:
            redis = get_redis_sync()
            key = f"history_compression:{user_id}"
            data = redis.get(key)
            
            if data:
                compression = json.loads(data if isinstance(data, str) else data.decode("utf-8"))
                watch_vector = compression.get("watch_vector", {})
                
                if watch_vector:
                    # Extract top items from watch vector
                    sorted_items = sorted(
                        watch_vector.items(),
                        key=lambda x: x[1],
                        reverse=True
                    )[:10]
                    
                    # Format as comma-separated list
                    items = [k.replace("genre:", "").replace("type:", "") for k, v in sorted_items]
                    summary = ", ".join(items)
                    
                    # Trim to max length
                    if len(summary) > max_length:
                        summary = summary[:max_length].rsplit(",", 1)[0]  # Trim at last comma
                        
                    return summary.strip()
                    
        except Exception as e:
            logger.debug(f"Failed to get history summary from Redis: {e}")
            
        return ""
        
    @staticmethod
    def get_pairwise_profile(user_id: int = 1) -> Dict[str, Any]:
        """Get user preference profile from pairwise training.
        
        Returns genre weights, decade preferences, etc. from pairwise judgments.
        
        Args:
            user_id: User ID
            
        Returns:
            Dict with preference weights (empty dict if not found)
        """
        try:
            redis = get_redis_sync()
            key = f"user_pairwise_profile:{user_id}"
            data = redis.get(key)
            
            if data:
                return json.loads(data if isinstance(data, str) else data.decode("utf-8"))
                
        except Exception as e:
            logger.debug(f"Failed to get pairwise profile: {e}")
            
        return {}
        
    @staticmethod
    def format_for_prompt(
        user_id: int = 1,
        db: Optional[Session] = None,
        include_history: bool = True,
        include_pairwise: bool = False
    ) -> Dict[str, str]:
        """Format persona and history for LLM prompts.
        
        Returns dict with 'persona' and 'history' keys, both trimmed for tokens.
        
        Args:
            user_id: User ID
            db: Optional database session
            include_history: Include watch history summary
            include_pairwise: Include pairwise training preferences
            
        Returns:
            Dict with 'persona' and 'history' strings
        """
        persona = PersonaHelper.get_persona(user_id=user_id, db=db)
        history = ""
        
        if include_history:
            history = PersonaHelper.get_history_summary(user_id=user_id)
            
        if include_pairwise:
            pairwise = PersonaHelper.get_pairwise_profile(user_id=user_id)
            if pairwise:
                # Extract top preferences
                genre_weights = pairwise.get("genre_weights", {})
                if genre_weights:
                    top_genres = sorted(
                        genre_weights.items(),
                        key=lambda x: x[1],
                        reverse=True
                    )[:5]
                    genre_str = ", ".join([g for g, _ in top_genres])
                    
                    if history:
                        history = f"{history}; prefers: {genre_str}"
                    else:
                        history = f"Prefers: {genre_str}"
                        
        return {
            "persona": persona,
            "history": history
        }
