"""
dynamic_list_service.py

Service for generating and syncing dynamic Smart Lists (mood, fusion, theme) for WatchBuddy.
- Always filters PersistentCandidate for trakt_id IS NOT NULL
- 3 mood, 2 fusion, 2 theme lists with persistent IDs
- Dynamic titles/themes per sync
- No external API calls during sync
"""

from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from app.models import UserList, PersistentCandidate, ListItem
from app.services.scoring_engine import ScoringEngine
from app.services.trakt_client import TraktClient
from app.core.database import SessionLocal
from app.utils.timezone import utc_now
import random
import json
import logging

logger = logging.getLogger(__name__)

DYNAMIC_LIST_TYPES = [
    ("mood", 1), ("mood", 2), ("mood", 3),
    ("fusion", 1), ("fusion", 2),
    ("theme", 1), ("theme", 2)
]

class DynamicListService:
    def __init__(self, user_id: int = 1):
        self.user_id = user_id
        self.scoring_engine = ScoringEngine()
        self.trakt_client = TraktClient(user_id)

    def ensure_dynamic_lists(self, db: Session) -> List[UserList]:
        """Create 7 dynamic lists if missing, else return them."""
        lists = db.query(UserList).filter(
            UserList.user_id == self.user_id,
            UserList.list_type.in_(["mood", "fusion", "theme"]),
            UserList.persistent_id.isnot(None)
        ).all()
        existing_keys = {(l.list_type, l.persistent_id) for l in lists}
        to_create = [t for t in DYNAMIC_LIST_TYPES if t not in existing_keys]
        new_lists = []
        for list_type, pid in to_create:
            ul = UserList(
                user_id=self.user_id,
                title=f"Dynamic {list_type.title()} List {pid}",
                filters=json.dumps({}),
                list_type=list_type,
                persistent_id=pid,
                item_limit=40,
                sync_interval=1440,
                exclude_watched=True,
                created_at=utc_now()
            )
            db.add(ul)
            new_lists.append(ul)
        if new_lists:
            db.commit()
            lists += new_lists
        return lists

    async def sync_dynamic_lists(self, db: Session):
        """Sync all 7 dynamic lists: update title, theme, items, and Trakt (async)."""
        lists = self.ensure_dynamic_lists(db)
        # Set similar_to_title for the primary mood list (lowest ID)
        mood_lists = [ul for ul in lists if ul.list_type == "mood"]
        if mood_lists:
            primary_mood_list = min(mood_lists, key=lambda ul: ul.id)
            try:
                filters = json.loads(primary_mood_list.filters) if primary_mood_list.filters else {}
                history = await self.trakt_client.get_my_history(media_type="movies", limit=1)
                if history and isinstance(history, list):
                    recent = history[0]
                    recent_title = recent.get("movie", {}).get("title") or recent.get("title")
                    if recent_title:
                        filters["similar_to_title"] = recent_title
                        primary_mood_list.filters = json.dumps(filters)
                        db.commit()
                        logger.info(f"[DynamicList] Set similar_to_title for mood list {primary_mood_list.id}: {recent_title}")
            except Exception as e:
                logger.warning(f"[DynamicList] Failed to set similar_to_title for mood list {primary_mood_list.id}: {e}")
        # ...existing code for syncing each ul in lists...

    def get_candidates(self, db: Session) -> List[PersistentCandidate]:
        """Return all candidates with a Trakt ID."""
        return db.query(PersistentCandidate).filter(PersistentCandidate.trakt_id.isnot(None)).all()

    def _cand_to_dict(self, c: PersistentCandidate) -> Dict[str, Any]:
        genres: Optional[List[str]] = None
        try:
            if c.genres:
                g = json.loads(c.genres)
                if isinstance(g, list):
                    genres = g
        except Exception:
            genres = []
        return {
            'ids': {'trakt': c.trakt_id, 'tmdb': c.tmdb_id},
            'type': c.media_type,
            'media_type': c.media_type,  # Ensure both fields are set
            'trakt_id': c.trakt_id,
            'tmdb_id': c.tmdb_id,
            'title': c.title,
            'year': c.year,
            'rating': c.vote_average or 0,
            'vote_average': c.vote_average or 0,
            'votes': c.vote_count or 0,
            'vote_count': c.vote_count or 0,
            'genres': genres or [],
            'overview': c.overview or '',
            'popularity': c.popularity or 0,
            'language': c.language,
            'obscurity_score': c.obscurity_score or 0,
            'mainstream_score': c.mainstream_score or 0,
            'freshness_score': c.freshness_score or 0,
            '_from_persistent_store': True  # Flag for scoring engine
        }

    async def sync_dynamic_lists(self, db: Session):
        """Sync all 7 dynamic lists: update title, theme, items, and Trakt (async)."""
        lists = self.ensure_dynamic_lists(db)
        # Set similar_to_title for the primary mood list (lowest ID)
        mood_lists = [ul for ul in lists if ul.list_type == "mood"]
        if mood_lists:
            primary_mood_list = min(mood_lists, key=lambda ul: ul.id)
            try:
                filters = json.loads(primary_mood_list.filters) if primary_mood_list.filters else {}
                history = await self.trakt_client.get_my_history(media_type="movies", limit=1)
                if history and isinstance(history, list):
                    recent = history[0]
                    recent_title = recent.get("movie", {}).get("title") or recent.get("title")
                    if recent_title:
                        filters["similar_to_title"] = recent_title
                        primary_mood_list.filters = json.dumps(filters)
                        db.commit()
                        logger.info(f"[DynamicList] Set similar_to_title for mood list {primary_mood_list.id}: {recent_title}")
            except Exception as e:
                logger.warning(f"[DynamicList] Failed to set similar_to_title for mood list {primary_mood_list.id}: {e}")
        # ...existing code for syncing each ul in lists...

    def get_candidates(self, db: Session) -> List[PersistentCandidate]:
        """Return all candidates with a Trakt ID."""
        return db.query(PersistentCandidate).filter(PersistentCandidate.trakt_id.isnot(None)).all()

    async def sync_dynamic_lists_impl(self, db: Session, lists: List[UserList], candidates: List[PersistentCandidate]):
        """Internal implementation of dynamic list sync (extracted for clarity)."""
        def _cand_to_dict(c: PersistentCandidate) -> dict:
            genres = []
            try:
                g = json.loads(c.genres)
                if isinstance(g, list):
                    genres = g
            except Exception:
                genres = []
            return {
                'ids': {'trakt': c.trakt_id, 'tmdb': c.tmdb_id},
                'type': c.media_type,
                'media_type': c.media_type,  # Ensure both fields are set
                'trakt_id': c.trakt_id,
                'tmdb_id': c.tmdb_id,
                'title': c.title,
                'year': c.year,
                'rating': c.vote_average or 0,
                'vote_average': c.vote_average or 0,
                'votes': c.vote_count or 0,
                'vote_count': c.vote_count or 0,
                'genres': genres or [],
                'overview': c.overview or '',
                'popularity': c.popularity or 0,
                'language': c.language,
                'obscurity_score': c.obscurity_score or 0,
                'mainstream_score': c.mainstream_score or 0,
                'freshness_score': c.freshness_score or 0,
                '_from_persistent_store': True  # Flag for scoring engine
            }

        converted = [_cand_to_dict(c) for c in candidates]
        title_by_trakt = {c.trakt_id: c.title for c in candidates if c.trakt_id}

        for ul in lists:
            # Always load filters from DB
            filters = json.loads(ul.filters) if ul.filters else {}
            # For the first mood list, set similar_to_title to most recent watched item
            if ul.list_type == "mood" and ul.persistent_id == 1:
                try:
                    history = await self.trakt_client.get_my_history(media_type="movies", limit=1)
                    if history and isinstance(history, list):
                        recent = history[0]
                        recent_title = recent.get("movie", {}).get("title") or recent.get("title")
                        if recent_title:
                            filters["similar_to_title"] = recent_title
                            ul.filters = json.dumps(filters)
                            db.commit()
                            logger.info(f"[DynamicList] Set similar_to_title for mood list {ul.id}: {recent_title}")
                except Exception as e:
                    logger.warning(f"[DynamicList] Failed to set similar_to_title for mood list {ul.id}: {e}")
            # Generate dynamic theme and title
            theme, title = await self._generate_theme_and_title(ul, candidates)
            old_title = ul.title
            old_trakt_list_id = ul.trakt_list_id
            ul.dynamic_theme = theme
            ul.title = title
            ul.last_updated = utc_now()

            # Score using batch scoring for the specific list type
            user_ctx = {"id": self.user_id}
            item_limit = ul.item_limit or 40
            if ul.list_type == "fusion" and theme:
                fusion_genres = self._extract_fusion_genres(theme)
                if fusion_genres:
                    filters["genres"] = fusion_genres
                    filters["genre_mode"] = "all"  # Require ALL selected genres for fusion lists
                    logger.info(f"[Fusion List] Enforcing all genres for {ul.title}: {fusion_genres}")
            
            # Extract semantic anchor for chat lists
            semantic_anchor = filters.get("similar_to_title") if filters else None
            if semantic_anchor:
                logger.info(f"[DynamicList] Using semantic anchor '{semantic_anchor}' for list {ul.id} ({ul.list_type})")
            
            scored = self.scoring_engine.score_candidates(
                user_ctx, converted, 
                list_type=ul.list_type, 
                explore_factor=0.18, 
                item_limit=item_limit, 
                filters=filters,
                semantic_anchor=semantic_anchor
            )

            # Map scored items to local structure, fill explanations
            top_items = []
            for s in scored[:item_limit]:
                tid = s.get('trakt_id')
                mtype = s.get('media_type')
                if not tid or not mtype:
                    continue
                explanation = s.get('explanation_text')
                if not explanation:
                    # Build explanation from context
                    expl_parts = []
                    if ul.dynamic_theme:
                        expl_parts.append(f"matches theme: {ul.dynamic_theme}")
                    if ul.list_type:
                        expl_parts.append(f"type: {ul.list_type}")
                    if s.get('semantic_match_title'):
                        expl_parts.append(f"similar to {s.get('semantic_match_title')}")
                    if s.get('obscurity_score', 0) > 0.7:
                        expl_parts.append("hidden gem")
                    explanation = ", ".join(expl_parts) or "Recommended by WatchBuddy AI"
                top_items.append({
                    'trakt_id': tid,
                    'media_type': mtype,
                    'title': title_by_trakt.get(tid),
                    'score': s.get('final_score', 0.0),
                    'explanation': explanation
                })

            # Replace items in DB
            db.query(ListItem).filter(ListItem.smartlist_id == ul.id).delete()
            for c in top_items:
                li = ListItem(
                    smartlist_id=ul.id,
                    item_id=str(c['trakt_id']),
                    title=c.get('title'),
                    score=c.get('score', 0.0),
                    trakt_id=c['trakt_id'],
                    media_type=c['media_type'],
                    explanation=c.get('explanation'),
                    added_at=utc_now()
                )
                db.add(li)
            db.commit()

            # Trakt sync: delete old list if title changed, create new, then sync items
            try:
                trakt_items = [
                    {"trakt_id": c['trakt_id'], "media_type": c['media_type']}
                    for c in top_items if c.get('trakt_id') and c.get('media_type')
                ]
                if old_trakt_list_id and old_title and old_title != ul.title:
                    await self.trakt_client.delete_list(old_trakt_list_id)
                    ul.trakt_list_id = None
                    db.commit()
                if not ul.trakt_list_id:
                    tlist = await self.trakt_client.create_list(
                        name=ul.title,
                        description=f"Dynamic {ul.list_type} list managed by WatchBuddy",
                        privacy="private"
                    )
                    trakt_list_id = tlist.get("ids", {}).get("trakt") if isinstance(tlist, dict) else None
                    if trakt_list_id:
                        ul.trakt_list_id = str(trakt_list_id)
                        db.commit()
                if ul.trakt_list_id and trakt_items:
                    await self.trakt_client.sync_list_items(ul.trakt_list_id, trakt_items)
            except Exception as e:
                logger.warning(f"[Trakt Sync] Failed for dynamic list {ul.id}: {e}")

    async def _generate_theme_and_title(self, ul: UserList, candidates: List[PersistentCandidate]):
        """Generate theme and title for a dynamic list based on list type and persistent ID."""
        if ul.list_type == "mood":
            # Fetch moods from metadata_options (fallback to defaults if unavailable)
            try:
                from app.api.metadata_options import get_available_moods
                moods_data = await get_available_moods()
                moods = moods_data.get("extended_moods", [
                    "cozy", "intense", "uplifting", "dark", "melancholic", "adventurous",
                    "nostalgic", "inspiring", "bittersweet", "whimsical", "contemplative", "energetic"
                ])
            except Exception as e:
                logger.warning(f"Failed to fetch moods from metadata_options, using defaults: {e}")
                moods = [
                    "cozy", "intense", "uplifting", "dark", "melancholic", "adventurous",
                    "nostalgic", "inspiring", "bittersweet", "whimsical", "contemplative", "energetic"
                ]
            # Use persistent_id as seed for consistent but unique selection
            random.seed(f"mood_{ul.persistent_id}")
            theme = moods[(ul.persistent_id - 1) % len(moods)]
            title = f"When You're in a {theme.title()} Mood"
        elif ul.list_type == "fusion":
            # Fetch fusions from metadata_options (fallback to defaults if unavailable)
            try:
                from app.api.metadata_options import get_available_fusions
                fusions_data = await get_available_fusions()
                fusions = fusions_data.get("fusions", [
                    "sci-fi + thriller", "comedy + crime", "romance + adventure", "drama + mystery",
                    "horror + comedy", "action + comedy", "sci-fi + horror", "fantasy + adventure",
                    "crime + thriller", "romance + comedy", "war + drama", "western + action"
                ])
            except Exception as e:
                logger.warning(f"Failed to fetch fusions from metadata_options, using defaults: {e}")
                fusions = [
                    "sci-fi + thriller", "comedy + crime", "romance + adventure", "drama + mystery",
                    "horror + comedy", "action + comedy", "sci-fi + horror", "fantasy + adventure",
                    "crime + thriller", "romance + comedy", "war + drama", "western + action"
                ]
            random.seed(f"fusion_{ul.persistent_id}")
            theme = fusions[(ul.persistent_id - 1) % len(fusions)]
            title = f"Hidden {theme.title()} Treasures"
        else:
            # Fetch themes from metadata_options (fallback to defaults if unavailable)
            try:
                from app.api.metadata_options import get_available_themes
                themes_data = await get_available_themes()
                themes = themes_data.get("themes", [
                    "trending", "noir", "witty crime", "dark thriller", "epic saga", "indie gems",
                    "cult classics", "mindbenders", "underrated", "nostalgia", "arthouse", "crowd-pleasers"
                ])
            except Exception as e:
                logger.warning(f"Failed to fetch themes from metadata_options, using defaults: {e}")
                themes = [
                    "trending", "noir", "witty crime", "dark thriller", "epic saga", "indie gems",
                    "cult classics", "mindbenders", "underrated", "nostalgia", "arthouse", "crowd-pleasers"
                ]
            random.seed(f"theme_{ul.persistent_id}")
            theme = themes[(ul.persistent_id - 1) % len(themes)]
            title = f"{theme.title()} Favorites"
        # Reset random seed
        random.seed()
        return theme, title

    def _extract_fusion_genres(self, theme: str) -> List[str]:
        """Extract genres from a fusion theme string like 'sci-fi + thriller'."""
        if not theme:
            return []
        
        # Expanded genre mapping for fusion themes (maps text to canonical genre names)
        genre_mapping = {
            'sci-fi': 'Science Fiction',
            'science fiction': 'Science Fiction',
            'thriller': 'Thriller',
            'comedy': 'Comedy',
            'crime': 'Crime',
            'romance': 'Romance',
            'adventure': 'Adventure',
            'drama': 'Drama',
            'mystery': 'Mystery',
            'action': 'Action',
            'horror': 'Horror',
            'fantasy': 'Fantasy',
            'animation': 'Animation',
            'documentary': 'Documentary',
            'war': 'War',
            'western': 'Western',
            'music': 'Music',
            'family': 'Family',
            'history': 'History',
        }
        
        # Split by + and clean up
        parts = [p.strip().lower() for p in theme.split('+')]
        genres = []
        for part in parts:
            # Map to canonical genre name
            if part in genre_mapping:
                genres.append(genre_mapping[part])
            else:
                # If not found, capitalize first letter of each word as fallback
                genres.append(' '.join(word.capitalize() for word in part.split()))
        
        logger.info(f"Extracted genres from fusion theme '{theme}': {genres}")
        return genres

    def _score_candidates(self, ul: UserList, candidates: List[PersistentCandidate]) -> List[dict]:
        """Deprecated: use batch scoring above. Retained for compatibility."""
        return []
