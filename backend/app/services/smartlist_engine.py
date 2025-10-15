"""
DEPRECATED MODULE
-----------------
This legacy SmartListEngine has been superseded by BulkCandidateProvider + ScoringEngine
and is no longer used by the API. It remains only for temporary backward-compatibility.
Please use `app.services.bulk_candidate_provider.BulkCandidateProvider` and
`app.services.scoring_engine.ScoringEngine` instead.
"""
import warnings as _warnings
_warnings.warn(
    "app.services.smartlist_engine is deprecated. Use BulkCandidateProvider + ScoringEngine instead.",
    DeprecationWarning,
    stacklevel=2,
)
from typing import List, Dict, Any
import random
import logging
from datetime import datetime
from app.services.scoring_engine import ScoringEngine
from app.services.mood import get_user_mood
from app.services.explain import ExplainEngine
from app.services.trakt_client import TraktClient

class SmartListEngine:
    def __init__(self, trakt_client=None, scoring_engine=None):
        self.trakt = trakt_client or TraktClient()
        self.scoring_engine = scoring_engine or ScoringEngine(trakt_client)
        self.explainer = ExplainEngine()

    def evaluate(self, user_id: int, smartlist, items):
        # smartlist: DB model or dict with criteria
        # items: list of candidate items
        mood = get_user_mood(user_id)
        results = []
        # Basic evaluation using score_candidate for each item
        for item in items:
            try:
                score = 0.5
                try:
                    # Use simple scoring for now (async in event loop context)
                    import asyncio
                    if asyncio.get_event_loop().is_running():
                        score = asyncio.get_event_loop().run_until_complete(
                            self.scoring_engine.score_candidate(item, user_profile={}, filters={})
                        )
                    else:
                        score = asyncio.run(self.scoring_engine.score_candidate(item, user_profile={}, filters={}))
                except RuntimeError:
                    # If event loop is already running, skip scoring here
                    pass
                explanation = ""
                results.append({"item": item, "score": score, "explanation": explanation})
            except Exception:
                continue
        return sorted(results, key=lambda x: -x["score"])
    # MAIN ENTRY POINT
    # ---------------------------------------------

    async def generate_smartlists(self, user_id: str) -> List[Dict[str, Any]]:
        """
        Builds several SmartLists personalized for the user.
        """
        logger = logging.getLogger(__name__)
        logger.info(f"Generating SmartLists for user {user_id}...")

        # Placeholder profile and history until implemented
        profile = {}
        history: List[Dict[str, Any]] = []
        smartlists = []

        # Minimal single template output
        try:
            lst = await self._build_smartlist({
                "name": "Smart Picks",
                "description": "Based on your tastes",
                "limit": 10
            }, profile, history)
            if lst:
                smartlists.append(lst)
        except Exception as e:
            logger.error(f"Error generating smartlist: {e}")

        logger.info(f"✅ Generated {len(smartlists)} SmartLists.")
        return smartlists

    # ---------------------------------------------
    # LIST BUILDER
    # ---------------------------------------------

    async def _build_smartlist(
        self, template: Dict[str, Any], profile: Dict[str, Any], history: List[Dict[str, Any]], filters: dict = None
    ) -> Dict[str, Any]:
        """
        Builds one SmartList using template logic and user filters.
        """
        recent_item = None
        if template.get("based_on_recent") and history:
            recent_item = random.choice(history[:5])  # Pick one of the last 5
            base_title = recent_item.get("movie", {}).get("title") or recent_item.get("show", {}).get("title")
        else:
            base_title = None

        name = template["name"].format(title=base_title or "Your Favorites")
        desc = template["description"].format(title=base_title or "your past favorites")

        # Determine search pool: get candidates for specified media types
        media_types = template.get("filter_types", ["movie", "show"])
        if "movie" not in media_types and "show" not in media_types:
            media_types = ["movie", "show"]

        candidates: List[Dict[str, Any]] = []
        for mt in media_types:
            api_media_type = "movies" if mt == "movie" else "shows"
            batch = await self.trakt.get_recommendations(media_type=api_media_type)
            # Normalize items to a consistent shape
            for item in batch or []:
                entity = item.get("movie") or item.get("show") or item
                if isinstance(entity, dict):
                    entity["type"] = "movie" if item.get("movie") else ("show" if item.get("show") else entity.get("type", mt))
                    candidates.append(entity)

        # Apply filters
        candidates = self._apply_filters(candidates, template)

        # Score and rank
        scored = []
        for c in candidates:
            score = await self.scoring_engine.score_candidate(c, profile, filters)
            scored.append((c, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        top_items = [x[0] for x in scored[: template["limit"]]]

        # Build metadata
        return {
            "title": name,
            "description": desc,
            "items": top_items,
            "context": {
                "based_on": base_title,
                "template": template["name"],
                "generated_at": datetime.utcnow().isoformat(),
            },
        }

    # ---------------------------------------------
    # FILTER UTILITIES
    # ---------------------------------------------

    def _apply_filters(self, candidates: List[Dict[str, Any]], template: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Applies template-specific filters (genre, rating, popularity).
        """
        filtered = []
        for item in candidates:
            try:
                rating = item.get("rating", 0)
                genres = item.get("genres", [])
                popularity = item.get("votes", 0)

                # Genre filter
                if "filter_genres" in template:
                    if not any(g in genres for g in template["filter_genres"]):
                        continue

                # Minimum rating
                if template.get("min_rating") and rating < template["min_rating"]:
                    continue

                # Minimum popularity
                if template.get("min_popularity") and popularity < template["min_popularity"]:
                    continue

                filtered.append(item)
            except Exception:
                continue
        return filtered

