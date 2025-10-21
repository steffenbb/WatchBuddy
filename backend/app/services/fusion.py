"""
FusionEngine blends multiple recommendation sources into a single score.
Sources:
- Core ScoringEngine components (genre/semantic/mood/rating/novelty)
- Trakt trending score (normalized rank/popularity)
- User history affinity (keyword/genre overlap with recent history)

Design goals:
- Lightweight: numpy, sklearn already available; no heavy models.
- Configurable weights; safe defaults.
- Deterministic and easy to debug; expose per-source breakdown.
"""
from __future__ import annotations
from typing import List, Dict, Any, Optional, Tuple
import math
import numpy as np
import json

from .semantic import SemanticEngine
from .scoring_engine import ScoringEngine

_DEFAULT_WEIGHTS = {
    "components.genre": 0.30,
    "components.semantic": 0.25,
    "components.mood": 0.20,
    "components.rating": 0.10,
    "components.novelty": 0.05,
    "trending": 0.07,
    "history": 0.03,
}

class FusionEngine:
    def __init__(self, weights: Optional[Dict[str, float]] = None, user_id: Optional[int] = None):
        self.weights = dict(_DEFAULT_WEIGHTS)
        if weights:
            self.weights.update(weights)
        self.semantic = SemanticEngine()
        self.scorer = ScoringEngine()
        self.user_id = user_id

    async def _load_user_settings(self) -> Dict[str, Any]:
        """Load user-specific fusion settings from database."""
        if not self.user_id:
            # Single-user mode: prefer Redis global settings if available
            try:
                from app.core.redis_client import get_redis
                r = get_redis()
                enabled = await r.get("settings:global:fusion_enabled")
                weights_json = await r.get("settings:global:fusion_weights")
                aggr = await r.get("settings:global:fusion_aggressiveness")
                settings: Dict[str, Any] = {
                    "enabled": (enabled == "true") if isinstance(enabled, str) else True,
                    "weights": self.weights,
                    "aggressiveness": int(aggr) if isinstance(aggr, str) and aggr.isdigit() else 1,
                }
                if weights_json:
                    try:
                        custom = json.loads(weights_json)
                        settings["weights"] = custom
                    except Exception:
                        pass
                # Apply aggressiveness scaling to weights for display and usage
                settings["weights"] = self._apply_aggressiveness(settings["weights"], settings["aggressiveness"])
                self.weights = settings["weights"]
                return settings
            except Exception:
                return {"enabled": True, "weights": self.weights, "aggressiveness": 1}
        
        try:
            from app.core import database
            async with database.get_async_session() as session:
                enabled = await database.get_secret(session, "fusion_enabled", user_id=self.user_id)
                weights_json = await database.get_secret(session, "fusion_weights", user_id=self.user_id)
                
                settings = {
                    "enabled": enabled == "true" if enabled else True,
                    "weights": self.weights,
                    "aggressiveness": 1,
                }
                
                if weights_json:
                    try:
                        custom_weights = json.loads(weights_json)
                        settings["weights"] = custom_weights
                        self.weights = custom_weights
                    except:
                        pass
                        
                # Also check Redis for global aggressiveness as a fallback
                try:
                    from app.core.redis_client import get_redis
                    r = get_redis()
                    aggr = await r.get("settings:global:fusion_aggressiveness")
                    settings["aggressiveness"] = int(aggr) if isinstance(aggr, str) and aggr.isdigit() else 1
                except Exception:
                    settings["aggressiveness"] = 1
                settings["weights"] = self._apply_aggressiveness(settings["weights"], settings.get("aggressiveness", 1))
                self.weights = settings["weights"]
                return settings
        except:
            return {"enabled": True, "weights": self.weights, "aggressiveness": 1}

    def _apply_aggressiveness(self, weights: Dict[str, float], aggr: int) -> Dict[str, float]:
        """Adjust weights based on aggressiveness: 0=more conservative (reduce novelty/history), 2=more exploratory (boost novelty/trending/history)."""
        try:
            aggr = int(aggr)
        except Exception:
            aggr = 1
        if aggr == 1:
            return dict(weights)
        # Define simple scaling factors
        # Base keys present in weights
        keys = [
            "components.genre",
            "components.semantic",
            "components.mood",
            "components.rating",
            "components.novelty",
            "trending",
            "history",
        ]
        w = {k: float(weights.get(k, _DEFAULT_WEIGHTS.get(k, 0.0))) for k in keys}
        if aggr == 0:
            # Conservative: emphasize genre/rating/semantic, reduce novelty/history/trending
            scale = {
                "components.genre": 1.10,
                "components.semantic": 1.05,
                "components.mood": 1.00,
                "components.rating": 1.10,
                "components.novelty": 0.60,
                "trending": 0.75,
                "history": 0.80,
            }
        else:
            # Aggressive/High: boost novelty/trending/history; slightly reduce rating/genre
            scale = {
                "components.genre": 0.90,
                "components.semantic": 1.00,
                "components.mood": 1.05,
                "components.rating": 0.90,
                "components.novelty": 1.50,
                "trending": 1.25,
                "history": 1.25,
            }
        for k, s in scale.items():
            w[k] = max(0.0, w.get(k, 0.0) * s)
        # Renormalize to sum to ~1.0
        total = sum(w.values()) or 1.0
        w = {k: v / total for k, v in w.items()}
        return w

    async def _get_trending_scores(self, media_type: str = "movies", limit: int = 100) -> Dict[str, float]:
        """Fetch Trakt trending (public) and return a normalized score per Trakt ID (0..1)."""
        try:
            import httpx
            from app.core import database
            async with database.get_async_session() as session:
                client_id = await database.get_secret(session, "trakt_client_id")
            headers = {"trakt-api-version": "2"}
            if client_id:
                headers["trakt-api-key"] = client_id
            url = "https://api.trakt.tv/movies/trending" if media_type=="movies" else "https://api.trakt.tv/shows/trending"
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, params={"limit": limit}, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return {}
        # data shape: [{"watchers": n, media_type: {"ids": {"trakt": ...}}}, ...]
        scores = {}
        if not data:
            return scores
        # Normalize by rank (inverse rank) and watchers
        max_watchers = max((item.get("watchers", 0) for item in data), default=1) or 1
        for idx, item in enumerate(data):
            ids = (item.get("movie") or item.get("show") or {}).get("ids", {})
            tid = ids.get("trakt")
            watchers = item.get("watchers", 0) / max_watchers
            inv_rank = 1.0 - (idx / max(1, len(data)-1)) if len(data) > 1 else 1.0
            scores[str(tid)] = float(0.6 * watchers + 0.4 * inv_rank)
        return scores

    def _history_affinity(self, user_profile_texts: List[str], candidate_texts: List[str]) -> List[float]:
        # Use SemanticEngine to score by clusters and return 0..1
        return self.semantic.score_by_clusters(user_profile_texts, candidate_texts, cluster_weight=0.6)

    async def _get_user_history_texts(self) -> List[str]:
        """Get real user history texts from recent watches."""
        if not self.user_id:
            # Return generic fallback for anonymous/uninitialized users
            return ["diverse viewer", "appreciates quality storytelling", "enjoys varied genres"]
        
        try:
            from app.core import database
            from app.models import MediaMetadata
            from app.services.trakt_client import TraktClient
            from sqlalchemy import select
            
            # Get recent history from Trakt
            trakt = TraktClient(user_id=self.user_id)
            try:
                history = await trakt.get_user_history(username="me", limit=20)
            except:
                history = []
            
            # Extract metadata for history items
            texts = []
            async with database.get_async_session() as session:
                for item in history[:10]:  # Limit to recent 10
                    trakt_id = (item.get("movie") or item.get("show") or {}).get("ids", {}).get("trakt")
                    if trakt_id:
                        stmt = select(MediaMetadata).where(MediaMetadata.trakt_id == trakt_id)
                        result = await session.execute(stmt)
                        metadata = result.scalar_one_or_none()
                        
                        if metadata and metadata.overview:
                            # Combine title, overview, and genres for rich text
                            genre_text = " ".join(json.loads(metadata.genres or "[]"))
                            text = f"{metadata.title} {metadata.overview} {genre_text}"
                            texts.append(text)
            
            return texts if texts else ["user with varied taste", "enjoys quality storytelling"]
        except Exception:
            return ["diverse viewer", "appreciates good content"]

    async def fuse(self, user: Dict[str, Any], candidates: List[Dict[str, Any]], list_type: str = "smartlist", media_type: str = "movies", limit: int = 50) -> List[Dict[str, Any]]:
        """
        Blend component scores, trending, and history into a final score.
        Returns a list of items with per-source breakdowns.
        """
        if not candidates:
            return []

        # Load user settings
        settings = await self._load_user_settings()
        if not settings["enabled"]:
            # Fall back to standard scoring if fusion is disabled
            return self.scorer.score_candidates(user=user, candidates=candidates, list_type=list_type, item_limit=limit)

        # 1) Base scoring to get component metrics
        base = self.scorer.score_candidates(user=user, candidates=candidates, list_type=list_type, item_limit=min(200, max(limit*2, 100)))
        # build quick lookup by trakt_id
        by_id = { str(item.get("trakt_id")): item for item in base }

        # 2) Trending per ID
        trending = await self._get_trending_scores(media_type=media_type, limit=200)

        # 3) Real user history profile texts
        user_texts = await self._get_user_history_texts()
        cand_texts = []
        order_ids = []
        for item in base:
            # Build candidate text from available metadata
            text_parts = []
            if item.get("explanation_text"):
                text_parts.append(item["explanation_text"])
            
            # Try to get more text from components/metadata
            meta = item.get("explanation_meta", {})
            if isinstance(meta, dict):
                for key, value in meta.items():
                    if isinstance(value, str) and len(value) > 10:
                        text_parts.append(value)
            
            cand_text = " ".join(text_parts) if text_parts else f"item {item.get('trakt_id', 'unknown')}"
            cand_texts.append(cand_text)
            order_ids.append(str(item.get("trakt_id")))
            
        hist_scores = self._history_affinity(user_texts, cand_texts) if cand_texts else [0.0]*len(base)

        # 4) Combine with weights (use loaded settings)
        weights = settings["weights"]
        fused = []
        for idx, tid in enumerate(order_ids):
            b = by_id.get(tid)
            if not b: continue
            comps = b.get("components", {})
            s_genre = comps.get("genre_overlap", 0.0)
            s_sem = comps.get("semantic_sim", 0.0)
            s_mood = comps.get("mood_score", 0.0)
            s_rating = comps.get("rating_norm", 0.0)
            s_nov = comps.get("novelty", 0.0)
            s_trend = trending.get(tid, 0.0)
            s_hist = hist_scores[idx] if idx < len(hist_scores) else 0.0

            total = (
                weights.get("components.genre", 0.3) * s_genre +
                weights.get("components.semantic", 0.25) * s_sem +
                weights.get("components.mood", 0.2) * s_mood +
                weights.get("components.rating", 0.1) * s_rating +
                weights.get("components.novelty", 0.05) * s_nov +
                weights.get("trending", 0.07) * s_trend +
                weights.get("history", 0.03) * s_hist
            )
            fused.append({
                **b,
                "fusion_score": float(total),
                "fusion_breakdown": {
                    "genre": s_genre,
                    "semantic": s_sem,
                    "mood": s_mood,
                    "rating": s_rating,
                    "novelty": s_nov,
                    "trending": s_trend,
                    "history": s_hist,
                },
                "fusion_enabled": True,
                "fusion_weights": weights
            })

        fused.sort(key=lambda x: x["fusion_score"], reverse=True)
        return fused[:limit]
