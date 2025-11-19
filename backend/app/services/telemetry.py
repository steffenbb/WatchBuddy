"""
User engagement telemetry tracking for WatchBuddy.

Tracks:
- Click-through rates (list item clicks)
- Play/completion rates (Trakt watch events)
- Skip/abandonment metrics (items shown but not engaged)
- Pairwise trainer conversion (users who complete training sessions)
- Satisfaction deltas (ratings before/after training)
"""
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from app.core.redis_client import get_redis_sync

logger = logging.getLogger(__name__)


class TelemetryTracker:
    """Tracks user engagement metrics in Redis."""
    
    def __init__(self, user_id: int = 1):
        self.user_id = user_id
        self.redis = get_redis_sync()
        
    def track_list_view(self, list_id: int, item_count: int) -> None:
        """Track when a user views a list (impression)."""
        try:
            # Global counters
            self.redis.incrby("telemetry:lists:views", 1)
            self.redis.incrby("telemetry:lists:items_shown", item_count)
            
            # Per-list counters
            self.redis.incrby(f"telemetry:list:{list_id}:views", 1)
            
            # Per-user counters
            self.redis.incrby(f"telemetry:user:{self.user_id}:list_views", 1)
            
            logger.debug(f"[Telemetry] List view: list_id={list_id}, items={item_count}")
        except Exception as e:
            logger.warning(f"[Telemetry] Failed to track list view: {e}")
            
    def track_item_click(self, list_id: int, item_id: int, position: int) -> None:
        """Track when a user clicks on an item (click-through).
        
        Args:
            list_id: ID of the list containing the item
            item_id: ID of the clicked item (persistent_candidate.id)
            position: Position in the list (1-indexed)
        """
        try:
            # Global counters
            self.redis.incrby("telemetry:items:clicks", 1)
            
            # Per-list counters
            self.redis.incrby(f"telemetry:list:{list_id}:clicks", 1)
            
            # Per-user counters
            self.redis.incrby(f"telemetry:user:{self.user_id}:clicks", 1)
            
            # Position tracking (for relevance analysis)
            self.redis.hincrby(f"telemetry:clicks:by_position", str(position), 1)
            
            # Store click event with timestamp (keep last 100)
            click_data = json.dumps({
                "list_id": list_id,
                "item_id": item_id,
                "position": position,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            self.redis.lpush(f"telemetry:user:{self.user_id}:click_history", click_data)
            self.redis.ltrim(f"telemetry:user:{self.user_id}:click_history", 0, 99)
            
            logger.debug(f"[Telemetry] Item click: list={list_id}, item={item_id}, pos={position}")
        except Exception as e:
            logger.warning(f"[Telemetry] Failed to track item click: {e}")
            
    def track_play_event(self, item_id: int, media_type: str, completed: bool = False) -> None:
        """Track when a user starts/completes watching an item.
        
        Args:
            item_id: ID of the item (persistent_candidate.id)
            media_type: 'movie' or 'show'
            completed: True if watch was completed, False if started
        """
        try:
            # Global counters
            if completed:
                self.redis.incrby("telemetry:plays:completed", 1)
                self.redis.incrby(f"telemetry:plays:completed:{media_type}", 1)
            else:
                self.redis.incrby("telemetry:plays:started", 1)
                self.redis.incrby(f"telemetry:plays:started:{media_type}", 1)
            
            # Per-user counters
            self.redis.incrby(f"telemetry:user:{self.user_id}:plays", 1)
            
            # Store play event (keep last 50)
            play_data = json.dumps({
                "item_id": item_id,
                "media_type": media_type,
                "completed": completed,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            self.redis.lpush(f"telemetry:user:{self.user_id}:play_history", play_data)
            self.redis.ltrim(f"telemetry:user:{self.user_id}:play_history", 0, 49)
            
            logger.debug(f"[Telemetry] Play event: item={item_id}, type={media_type}, completed={completed}")
        except Exception as e:
            logger.warning(f"[Telemetry] Failed to track play event: {e}")
            
    def track_skip_event(self, list_id: int, item_id: int, reason: Optional[str] = None) -> None:
        """Track when a user skips/dismisses an item without engaging.
        
        Args:
            list_id: ID of the list containing the item
            item_id: ID of the skipped item
            reason: Optional reason code ('not_interested', 'already_seen', etc.)
        """
        try:
            # Global counters
            self.redis.incrby("telemetry:items:skips", 1)
            if reason:
                self.redis.incrby(f"telemetry:skips:reason:{reason}", 1)
            
            # Per-list counters
            self.redis.incrby(f"telemetry:list:{list_id}:skips", 1)
            
            # Per-user counters
            self.redis.incrby(f"telemetry:user:{self.user_id}:skips", 1)
            
            logger.debug(f"[Telemetry] Skip event: list={list_id}, item={item_id}, reason={reason}")
        except Exception as e:
            logger.warning(f"[Telemetry] Failed to track skip event: {e}")
            
    def track_trainer_start(self) -> None:
        """Track when a user starts a pairwise training session."""
        try:
            # Global counter
            self.redis.incrby("telemetry:trainer:sessions_started", 1)
            
            # Per-user counter
            self.redis.incrby(f"telemetry:user:{self.user_id}:trainer_starts", 1)
            
            # Mark session start time
            self.redis.set(
                f"telemetry:user:{self.user_id}:trainer_start_time",
                datetime.now(timezone.utc).isoformat(),
                ex=3600  # Expire after 1 hour
            )
            
            logger.debug(f"[Telemetry] Trainer session started: user={self.user_id}")
        except Exception as e:
            logger.warning(f"[Telemetry] Failed to track trainer start: {e}")
            
    def track_trainer_completion(self, judgments_count: int, duration_seconds: float) -> None:
        """Track when a user completes a pairwise training session.
        
        Args:
            judgments_count: Number of judgments made in session
            duration_seconds: Session duration in seconds
        """
        try:
            # Global counters
            self.redis.incrby("telemetry:trainer:sessions_completed", 1)
            self.redis.incrby("telemetry:trainer:total_judgments", judgments_count)
            self.redis.incrbyfloat("telemetry:trainer:total_duration_seconds", duration_seconds)
            
            # Per-user counter
            self.redis.incrby(f"telemetry:user:{self.user_id}:trainer_completions", 1)
            
            # Clear session start time
            self.redis.delete(f"telemetry:user:{self.user_id}:trainer_start_time")
            
            logger.debug(f"[Telemetry] Trainer completed: user={self.user_id}, judgments={judgments_count}, duration={duration_seconds:.1f}s")
        except Exception as e:
            logger.warning(f"[Telemetry] Failed to track trainer completion: {e}")
            
    def track_trainer_abandonment(self) -> None:
        """Track when a user abandons a pairwise training session without completing."""
        try:
            # Check if there was an active session
            start_time_str = self.redis.get(f"telemetry:user:{self.user_id}:trainer_start_time")
            if not start_time_str:
                return  # No active session
            
            # Global counter
            self.redis.incrby("telemetry:trainer:sessions_abandoned", 1)
            
            # Per-user counter
            self.redis.incrby(f"telemetry:user:{self.user_id}:trainer_abandonments", 1)
            
            # Clear session start time
            self.redis.delete(f"telemetry:user:{self.user_id}:trainer_start_time")
            
            logger.debug(f"[Telemetry] Trainer abandoned: user={self.user_id}")
        except Exception as e:
            logger.warning(f"[Telemetry] Failed to track trainer abandonment: {e}")
            
    def track_satisfaction_rating(self, rating: int, context: str = "general") -> None:
        """Track user satisfaction rating (1-5 scale).
        
        Args:
            rating: Rating from 1 (poor) to 5 (excellent)
            context: Context of rating ('general', 'after_training', 'list_quality')
        """
        try:
            # Global counters
            self.redis.incrby("telemetry:satisfaction:total_ratings", 1)
            self.redis.incrbyfloat("telemetry:satisfaction:sum_ratings", rating)
            self.redis.hincrby(f"telemetry:satisfaction:by_score", str(rating), 1)
            
            # Context-specific counters
            self.redis.incrby(f"telemetry:satisfaction:{context}:count", 1)
            self.redis.incrbyfloat(f"telemetry:satisfaction:{context}:sum", rating)
            
            # Per-user tracking
            self.redis.lpush(
                f"telemetry:user:{self.user_id}:satisfaction_history",
                json.dumps({
                    "rating": rating,
                    "context": context,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
            )
            self.redis.ltrim(f"telemetry:user:{self.user_id}:satisfaction_history", 0, 49)
            
            logger.debug(f"[Telemetry] Satisfaction rating: user={self.user_id}, rating={rating}, context={context}")
        except Exception as e:
            logger.warning(f"[Telemetry] Failed to track satisfaction rating: {e}")
            
    def get_click_through_rate(self, list_id: Optional[int] = None) -> float:
        """Calculate click-through rate (CTR).
        
        Args:
            list_id: Optional list ID for list-specific CTR
            
        Returns:
            CTR as percentage (0-100)
        """
        try:
            if list_id:
                views = int(self.redis.get(f"telemetry:list:{list_id}:views") or 0)
                clicks = int(self.redis.get(f"telemetry:list:{list_id}:clicks") or 0)
            else:
                views = int(self.redis.get("telemetry:lists:views") or 0)
                clicks = int(self.redis.get("telemetry:items:clicks") or 0)
            
            return (clicks / views * 100) if views > 0 else 0.0
        except Exception as e:
            logger.warning(f"[Telemetry] Failed to calculate CTR: {e}")
            return 0.0
            
    def get_play_completion_rate(self, media_type: Optional[str] = None) -> float:
        """Calculate play completion rate.
        
        Args:
            media_type: Optional media type filter ('movie' or 'show')
            
        Returns:
            Completion rate as percentage (0-100)
        """
        try:
            if media_type:
                started = int(self.redis.get(f"telemetry:plays:started:{media_type}") or 0)
                completed = int(self.redis.get(f"telemetry:plays:completed:{media_type}") or 0)
            else:
                started = int(self.redis.get("telemetry:plays:started") or 0)
                completed = int(self.redis.get("telemetry:plays:completed") or 0)
            
            return (completed / started * 100) if started > 0 else 0.0
        except Exception as e:
            logger.warning(f"[Telemetry] Failed to calculate completion rate: {e}")
            return 0.0
            
    def get_trainer_conversion_rate(self) -> float:
        """Calculate pairwise trainer conversion rate (completions / starts).
        
        Returns:
            Conversion rate as percentage (0-100)
        """
        try:
            started = int(self.redis.get("telemetry:trainer:sessions_started") or 0)
            completed = int(self.redis.get("telemetry:trainer:sessions_completed") or 0)
            
            return (completed / started * 100) if started > 0 else 0.0
        except Exception as e:
            logger.warning(f"[Telemetry] Failed to calculate trainer conversion rate: {e}")
            return 0.0
            
    def get_satisfaction_delta(self, context: str = "after_training") -> Dict[str, float]:
        """Calculate satisfaction delta (before vs after training).
        
        Args:
            context: Context to analyze ('after_training', 'list_quality')
            
        Returns:
            Dict with 'before_avg', 'after_avg', 'delta'
        """
        try:
            # Get user's satisfaction history
            history_raw = self.redis.lrange(f"telemetry:user:{self.user_id}:satisfaction_history", 0, -1)
            if not history_raw:
                return {"before_avg": 0.0, "after_avg": 0.0, "delta": 0.0}
            
            # Parse history
            history = [json.loads(h) for h in history_raw]
            
            # Split before/after context
            before_ratings = [h["rating"] for h in history if h["context"] == "general"]
            after_ratings = [h["rating"] for h in history if h["context"] == context]
            
            before_avg = sum(before_ratings) / len(before_ratings) if before_ratings else 0.0
            after_avg = sum(after_ratings) / len(after_ratings) if after_ratings else 0.0
            delta = after_avg - before_avg
            
            return {
                "before_avg": before_avg,
                "after_avg": after_avg,
                "delta": delta
            }
        except Exception as e:
            logger.warning(f"[Telemetry] Failed to calculate satisfaction delta: {e}")
            return {"before_avg": 0.0, "after_avg": 0.0, "delta": 0.0}
            
    def get_global_metrics(self) -> Dict[str, Any]:
        """Get global telemetry metrics summary.
        
        Returns:
            Dict with all key metrics
        """
        try:
            return {
                "lists": {
                    "views": int(self.redis.get("telemetry:lists:views") or 0),
                    "items_shown": int(self.redis.get("telemetry:lists:items_shown") or 0),
                },
                "items": {
                    "clicks": int(self.redis.get("telemetry:items:clicks") or 0),
                    "skips": int(self.redis.get("telemetry:items:skips") or 0),
                    "click_through_rate": self.get_click_through_rate(),
                },
                "plays": {
                    "started": int(self.redis.get("telemetry:plays:started") or 0),
                    "completed": int(self.redis.get("telemetry:plays:completed") or 0),
                    "completion_rate": self.get_play_completion_rate(),
                },
                "trainer": {
                    "sessions_started": int(self.redis.get("telemetry:trainer:sessions_started") or 0),
                    "sessions_completed": int(self.redis.get("telemetry:trainer:sessions_completed") or 0),
                    "sessions_abandoned": int(self.redis.get("telemetry:trainer:sessions_abandoned") or 0),
                    "total_judgments": int(self.redis.get("telemetry:trainer:total_judgments") or 0),
                    "conversion_rate": self.get_trainer_conversion_rate(),
                },
                "satisfaction": {
                    "total_ratings": int(self.redis.get("telemetry:satisfaction:total_ratings") or 0),
                    "average_rating": self._get_average_satisfaction(),
                },
            }
        except Exception as e:
            logger.error(f"[Telemetry] Failed to get global metrics: {e}")
            return {}
            
    def _get_average_satisfaction(self) -> float:
        """Calculate average satisfaction rating."""
        try:
            count = int(self.redis.get("telemetry:satisfaction:total_ratings") or 0)
            total = float(self.redis.get("telemetry:satisfaction:sum_ratings") or 0)
            return (total / count) if count > 0 else 0.0
        except Exception:
            return 0.0
