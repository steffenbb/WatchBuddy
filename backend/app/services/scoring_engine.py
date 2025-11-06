import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Tuple, Optional
import math
import re
from collections import defaultdict

from app.services.trakt_client import TraktClient

from app.services.semantic import SemanticEngine


import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from app.services.mood import get_cached_user_mood, compute_mood_vector_for_tmdb, get_contextual_mood_adjustment, ensure_user_mood
from app.utils.timezone import utc_now
from app.services.explain import generate_explanation

class ScoringEngine:
    """
    ScoringEngine for WatchBuddy.
    - No torch or sentence-transformers. Uses only TF-IDF (scikit-learn) and numpy.
    - Blends genre, popularity, rating, mood, novelty, and semantic features.
    - Disposes of all large objects after use (del, gc.collect()).
    """
    def __init__(self, trakt_client: Optional[TraktClient] = None):
        self.trakt_client = trakt_client
        self._user_ratings_cache = {}  # Cache user ratings for performance
        self._user_genre_prefs_cache: Dict[int, List[str]] = {}

    def _get_user_ratings(self, user_id: int) -> Dict[int, int]:
        """Fetch and cache user ratings (trakt_id -> rating value)."""
        if user_id in self._user_ratings_cache:
            return self._user_ratings_cache[user_id]
        
        from app.core.database import SessionLocal
        from app.models import UserRating
        
        db = SessionLocal()
        try:
            ratings = db.query(UserRating).filter(UserRating.user_id == user_id).all()
            rating_dict = {r.trakt_id: r.rating for r in ratings}
            self._user_ratings_cache[user_id] = rating_dict
            return rating_dict
        finally:
            db.close()

    def _get_user_genre_preferences(self, user_id: Optional[int]) -> List[str]:
        """Infer user's preferred genres using local data first, fallback to mood mapping.
        Strategy:
        - Use genres of items the user thumbed up (UserRating=1), via MediaMetadata if available
        - If none, derive from cached mood axes mapping
        Returns a de-duplicated list of canonical genre strings (case-insensitive compare later)
        """
        if not user_id:
            return []
        if user_id in self._user_genre_prefs_cache:
            return self._user_genre_prefs_cache[user_id]

        from app.core.database import SessionLocal
        from app.models import UserRating, MediaMetadata
        import json as _json

        db = SessionLocal()
        try:
            likes = db.query(UserRating).filter(UserRating.user_id == user_id, UserRating.rating == 1).all()
            genre_counts: Dict[str, int] = defaultdict(int)
            for r in likes:
                mm = db.query(MediaMetadata).filter(
                    MediaMetadata.trakt_id == r.trakt_id,
                    MediaMetadata.media_type == r.media_type
                ).first()
                if mm and mm.genres:
                    try:
                        gl = _json.loads(mm.genres)
                        if isinstance(gl, list):
                            for g in gl:
                                if g:
                                    genre_counts[str(g).lower()] += 1
                    except Exception:
                        pass
            if genre_counts:
                # Take top 8
                prefs = [g for g, _ in sorted(genre_counts.items(), key=lambda x: x[1], reverse=True)[:8]]
                self._user_genre_prefs_cache[user_id] = prefs
                return prefs
        finally:
            db.close()

        # Fallback to mood-based mapping
        mood_genres = self._preferred_genres_from_mood({ 'id': user_id })
        self._user_genre_prefs_cache[user_id] = [g.lower() for g in mood_genres]
        return self._user_genre_prefs_cache[user_id]

    async def score_candidate(self, candidate: Dict[str, Any], user_profile: Dict[str, Any], filters: Dict[str, Any]) -> float:
        """
        Simple scoring method for individual candidates used by sync service.
        Returns a normalized score between 0 and 1.
        Enhanced to fetch missing TMDB metadata from database.
        """
        try:
            # Check if candidate has TMDB data, if not try to fetch from database
            if not candidate.get('tmdb_data') and not candidate.get('cached_metadata'):
                candidate = await self._enrich_candidate_with_metadata(candidate)
            
            # Extract metadata (prefer tmdb_data, fallback to cached_metadata, then candidate direct)
            tmdb_data = candidate.get('tmdb_data') or candidate.get('cached_metadata') or {}
            
            # Basic scoring components with enhanced metadata extraction
            rating = tmdb_data.get("vote_average", 0) or tmdb_data.get("rating", 0) or candidate.get("rating", 0) or candidate.get("vote_average", 0)
            votes = tmdb_data.get("vote_count", 0) or tmdb_data.get("votes", 0) or candidate.get("votes", 0) or candidate.get("vote_count", 0)
            popularity = tmdb_data.get("popularity", 0) or candidate.get("popularity", 0)
            
            # Use pre-computed persistent candidate scores if available (for DB-sourced items)
            from_persistent = candidate.get('_from_persistent_store', False)
            obscurity_score = candidate.get('obscurity_score', 0.0)
            mainstream_score = candidate.get('mainstream_score', 0.0)
            freshness_score = candidate.get('freshness_score', 0.0)
            
            # Normalize components with better ranges
            rating_norm = self._norm(rating, 0, 10)
            votes_norm = self._norm(votes, 0, 5000)  # Lowered from 10000 to give more weight to moderate vote counts
            popularity_norm = self._norm(popularity, 0, 50)  # Lowered from 100 for better sensitivity
            
            # If sourced from persistent store, leverage pre-computed scores for discovery alignment
            discovery_mode = filters.get("discovery") or filters.get("mood")
            if from_persistent and discovery_mode:
                if discovery_mode in ("obscure", "very_obscure"):
                    # Boost items with high obscurity score
                    # Obscurity scores typically range 0-2 for quality items (50+ votes)
                    obscurity_norm = self._norm(obscurity_score, 0, 2.0)
                    # Apply stronger boost for obscure mode
                    boost_factor = 0.6 if discovery_mode == "very_obscure" else 0.5
                    rating_norm = (rating_norm + obscurity_norm * boost_factor) / (1.0 + boost_factor)
                    # Penalize high popularity for obscure mode
                    if popularity > 50:
                        popularity_penalty = min(0.3, (popularity - 50) / 200)  # Up to -0.3 for very popular items
                        rating_norm = rating_norm * (1.0 - popularity_penalty)
                elif discovery_mode in ("popular", "mainstream"):
                    # Boost items with high mainstream score
                    mainstream_norm = self._norm(mainstream_score, 0, 400)  # Full range for mainstream score
                    popularity_norm = (popularity_norm + mainstream_norm * 0.6) / 1.6  # Strong mainstream boost
                # Always add freshness bonus if available
                if freshness_score > 0:
                    votes_norm = (votes_norm + freshness_score * 0.3) / 1.3
            
            # Watched status penalty/bonus
            watched_penalty = 0.0
            if candidate.get("is_watched", False):
                watched_penalty = 0.2  # Reduced from 0.3 to be less harsh
            
            # Recency bonus for newer content
            year = candidate.get("year", 0) or candidate.get("release_date", "").split("-")[0] if candidate.get("release_date") else 0
            try:
                year = int(year) if year else 0
            except:
                year = 0
            current_year = utc_now().year
            recency_bonus = 0.15 if year >= current_year - 2 else 0.10 if year >= current_year - 5 else 0.05
            
            # Enhanced genre preference scoring
            genre_score = 0.5  # Default neutral score
            candidate_genres = self._extract_genres(candidate)
            filter_genres = filters.get("genres", []) or filters.get("preferred_genres", [])
            
            if filter_genres and candidate_genres:
                # Calculate genre overlap more generously
                filter_set = set(g.lower() for g in filter_genres)
                candidate_set = set(g.lower() for g in candidate_genres)
                overlap = len(filter_set & candidate_set)
                genre_score = min(1.0, (overlap + 0.3) / len(filter_set))  # Add baseline boost
            elif candidate_genres:
                # Bonus for having genre information even if no filter match
                genre_score = 0.6
            
            # Language bonus
            language_bonus = 0.0
            filter_languages = filters.get("languages", [])
            candidate_language = tmdb_data.get("original_language") or candidate.get("language")
            if filter_languages and candidate_language and candidate_language in filter_languages:
                language_bonus = 0.1
            
            # Quality indicators
            quality_bonus = 0.0
            if tmdb_data.get("overview"):
                quality_bonus += 0.05  # Has description
            if tmdb_data.get("poster_path"):
                quality_bonus += 0.05  # Has poster
            
            # Combine with improved weights (total = 1.0)
            base_score = (
                0.30 * rating_norm +      # Increased weight for rating
                0.20 * votes_norm +       # Votes for credibility
                0.15 * popularity_norm +  # General popularity
                0.20 * genre_score +      # Increased weight for genre match
                0.10 * recency_bonus +    # Recency bonus
                0.05 * quality_bonus      # Quality indicators
            )
            
            # Apply language bonus
            base_score += language_bonus
            
            # Apply user rating influence (strong signal)
            user_id = user_profile.get("id")
            if user_id:
                user_ratings = self._get_user_ratings(user_id)
                trakt_id = candidate.get("trakt_id")
                if trakt_id and trakt_id in user_ratings:
                    user_rating = user_ratings[trakt_id]
                    if user_rating == 1:  # Thumbs up
                        base_score *= 1.3  # 30% boost
                    elif user_rating == -1:  # Thumbs down
                        base_score *= 0.3  # 70% penalty
            
            # Apply watched penalty
            final_score = max(0, base_score - watched_penalty)
            
            # Add small random component for variety (reduced)
            import random
            final_score += random.uniform(0, 0.02)
            
            return min(1.0, final_score)
            
        except Exception as e:
            logger.error(f"Error scoring candidate: {e}")
            return 0.5  # Return neutral score on error

    async def _enrich_candidate_with_metadata(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch metadata from database for candidates that lack TMDB data."""
        try:
            from app.core.database import SessionLocal
            from app.models import MediaMetadata
            import json
            
            trakt_id = candidate.get('trakt_id') or candidate.get('ids', {}).get('trakt')
            if not trakt_id:
                return candidate
            
            db = SessionLocal()
            try:
                metadata = db.query(MediaMetadata).filter(
                    MediaMetadata.trakt_id == trakt_id,
                    MediaMetadata.media_type == (candidate.get('media_type') or candidate.get('type'))
                ).first()
                if metadata:
                    # Build metadata dict from individual columns
                    candidate['cached_metadata'] = {
                        'vote_average': metadata.rating or 0,
                        'vote_count': metadata.votes or 0,
                        'popularity': metadata.popularity or 0,
                        'overview': metadata.overview or '',
                        'poster_path': metadata.poster_path,
                        'genres': json.loads(metadata.genres) if metadata.genres else [],
                        'original_language': metadata.language,
                    }
                    logger.debug(f"Enriched candidate {trakt_id} with cached metadata: rating={metadata.rating}, votes={metadata.votes}")
            finally:
                db.close()
                
        except Exception as e:
            logger.debug(f"Failed to enrich candidate with metadata: {e}")
        
        return candidate

    def _extract_genres(self, candidate: Dict[str, Any]) -> List[str]:
        """Extract genre names from various possible locations in candidate data."""
        # Try TMDB data first
        tmdb_data = candidate.get('tmdb_data') or candidate.get('cached_metadata') or {}
        if tmdb_data.get('genres'):
            genres = tmdb_data['genres']
            if isinstance(genres, list):
                if genres and isinstance(genres[0], dict):
                    return [g.get('name', '') for g in genres if g.get('name')]
                elif genres and isinstance(genres[0], str):
                    return genres
        
        # Fallback to direct candidate fields
        return candidate.get('genres', []) or candidate.get('genre_names', [])

    def score_candidates(self, user, candidates: list, list_type: str, explore_factor: float=0.15, item_limit: int=50, filters: Optional[Dict]=None, semantic_anchor: Optional[str]=None) -> list:
        """
        Scores and ranks candidates for a user.
        Returns sorted list of dicts with: trakt_id, tmdb_id, media_type, final_score, explanation_text, explanation_meta.
        For SmartLists, mood, fusion, theme: Uses advanced features (semantic similarity, mood-aware scoring, diversity)
        For regular/custom/suggested lists: Uses traditional scoring (genre, popularity, rating only)
        """
        # 1. Strictly apply user filters (including actors/studios)
        filtered = [c for c in candidates if self._passes_explicit_filters(c, filters or {})]
        if not filtered:
            return []

        # 2. Compute basic features (always computed)
        user_id = user.get('id') if isinstance(user, dict) else None
        pref_genres_set = set(self._get_user_genre_preferences(user_id))
        for c in filtered:
            c['genre_overlap'] = self._genre_overlap(user, c)
            c['popularity_norm'] = self._norm(c.get('votes', 0), 0, 100000)
            c['rating_norm'] = self._norm(c.get('rating', 0), 0, 10)
            # Filter alignment features
            c['filter_align'] = self._filter_alignment(c, filters or {})
            # User preferred genres alignment (from Trakt thumbs-up or mood fallback)
            cand_genres = set(g.lower() for g in (self._candidate_genres(c) or []))
            c['user_pref_align'] = min(1.0, len(cand_genres & pref_genres_set) / max(1, len(pref_genres_set))) if pref_genres_set else 0.0

        # 3. Reduce to top_K by fast composite score
        for c in filtered:
            # Blend classic overlap with user preference alignment
            profile_blend = 0.6*c['genre_overlap'] + 0.4*c['user_pref_align']
            c['fast_score'] = 0.5*profile_blend + 0.20*c['popularity_norm'] + 0.15*c['rating_norm'] + 0.10*c['filter_align'] + 0.05
        top_k = sorted(filtered, key=lambda x: x['fast_score'], reverse=True)[:200]

        # 4. Advanced features for dynamic list types
        advanced_types = {'smartlist', 'mood', 'fusion', 'theme', 'chat'}
        if list_type in advanced_types:
            # Use advanced scoring for mood, fusion, theme, smartlist, and chat
            return self._score_smartlist_advanced(
                user,
                top_k,
                explore_factor,
                item_limit,
                semantic_anchor=semantic_anchor,
                list_type=list_type,
                filters=filters
            )
        elif list_type == 'discovery_overview':
            # Use discovery-focused scoring that doesn't penalize lack of watch history
            return self._score_discovery_overview(user, top_k, explore_factor, item_limit)
        else:
            # Use traditional scoring for regular/custom/suggested lists
            return self._score_traditional(user, top_k, explore_factor, item_limit)

    def _score_traditional(self, user, candidates: list, explore_factor: float, item_limit: int) -> list:
        """Traditional scoring for regular lists - simple, fast, reliable."""
        logger.info(f"[ScoringEngine._score_traditional] Received {len(candidates)} candidates")
        if candidates:
            logger.info(f"[ScoringEngine._score_traditional] Sample candidate keys: {list(candidates[0].keys())}")
            logger.info(f"[ScoringEngine._score_traditional] Sample candidate title: {candidates[0].get('title', 'NO_TITLE')}")
        
        # Get user ratings once for all candidates
        user_id = user.get("id") if isinstance(user, dict) else None
        user_ratings = self._get_user_ratings(user_id) if user_id else {}
        
        for c in candidates:
            c['novelty'] = 1.0 - c['popularity_norm']
            # Simple weighted combination - no mood or semantic features
            # Include alignment with explicit user preferred genres
            c['final_score'] = (
                0.35 * c['genre_overlap'] +
                0.15 * c.get('user_pref_align', 0.0) +
                0.20 * c['rating_norm'] +
                0.15 * c['popularity_norm'] +
                0.10 * min(c['novelty'], 0.3) +
                0.05 * c['filter_align']
            )
            
            # Apply user rating influence
            trakt_id = c.get('ids', {}).get('trakt') if isinstance(c.get('ids'), dict) else c.get('trakt_id')
            if trakt_id and trakt_id in user_ratings:
                user_rating = user_ratings[trakt_id]
                if user_rating == 1:  # Thumbs up
                    c['final_score'] *= 1.3
                elif user_rating == -1:  # Thumbs down
                    c['final_score'] *= 0.3
            
            # Basic explanation
            c['explanation_meta'] = {
                'similarity_score': c.get('genre_overlap', 0),
                'genre_overlap': [],
                'novelty_score': c.get('novelty', 0),
                'top_history_matches': [],
                'scoring_type': 'traditional'
            }
            c['explanation_text'] = f"Traditional scoring based on genre similarity and ratings"

        # Apply diversity-aware selection (MMR algorithm) for traditional lists too
        result = self._select_diverse_items(candidates, item_limit, diversity_lambda=0.7)
        
        # Preserve all original candidate fields and add scoring metadata
        return [
            {
                **c,  # Spread original candidate dict to preserve all fields
                'trakt_id': c.get('ids', {}).get('trakt') or c.get('trakt_id'),
                'tmdb_id': c.get('ids', {}).get('tmdb') or c.get('tmdb_id'),
                'media_type': c.get('type') or c.get('media_type'),
                'final_score': c['final_score'],
                'explanation_text': c['explanation_text'],
                'explanation_meta': c['explanation_meta'],
                'components': {
                    'genre_overlap': c.get('genre_overlap', 0.0),
                    'semantic_sim': 0.0,  # Not used in traditional
                    'mood_score': 0.0,    # Not used in traditional
                    'rating_norm': c.get('rating_norm', 0.0),
                    'novelty': c.get('novelty', 0.0),
                    'popularity_norm': c.get('popularity_norm', 0.0),
                    'fast_score': c.get('fast_score', 0.0),
                }
            }
            for c in result
        ]

    def _score_discovery_overview(self, user, candidates: list, explore_factor: float, item_limit: int) -> list:
        """
        Discovery-focused scoring for overview modules (NewShows, Trending, Upcoming).
        Uses TMDB quality signals and pre-computed scores instead of requiring watch history.
        Optimized for users with minimal/no history or for general discovery.
        """
        logger.info(f"[ScoringEngine._score_discovery_overview] Scoring {len(candidates)} candidates")
        
        # Get user ratings for influence (optional enhancement)
        user_id = user.get("id") if isinstance(user, dict) else None
        user_ratings = self._get_user_ratings(user_id) if user_id else {}
        
        for c in candidates:
            # Use persistent candidate pre-computed scores if available
            from_persistent = c.get('_from_persistent_store', False)
            
            if from_persistent:
                # Use pre-computed scores from persistent pool
                base_obscurity = c.get('obscurity_score', 0.5)
                base_mainstream = c.get('mainstream_score', 0.5)
                base_freshness = c.get('freshness_score', 0.5)
                
                # Discovery formula optimized for quality + novelty
                c['final_score'] = (
                    0.30 * c.get('rating_norm', 0.5) +        # TMDB rating (quality signal)
                    0.25 * c.get('popularity_norm', 0.3) +    # Popularity (credibility)
                    0.20 * base_obscurity +                    # Obscurity (discovery value)
                    0.15 * c.get('genre_overlap', 0.0) +      # Genre match (if available)
                    0.10 * c.get('user_pref_align', 0.0)      # User prefs (if available)
                )
            else:
                # Fallback to simple quality-based scoring
                c['novelty'] = 1.0 - c.get('popularity_norm', 0.5)
                c['final_score'] = (
                    0.40 * c.get('rating_norm', 0.5) +
                    0.30 * c.get('popularity_norm', 0.3) +
                    0.20 * min(c['novelty'], 0.6) +
                    0.10 * c.get('genre_overlap', 0.0)
                )
            
            # Apply user rating influence (strong signal if available)
            trakt_id = c.get('ids', {}).get('trakt') if isinstance(c.get('ids'), dict) else c.get('trakt_id')
            if trakt_id and trakt_id in user_ratings:
                user_rating = user_ratings[trakt_id]
                if user_rating == 1:  # Thumbs up
                    c['final_score'] *= 1.4
                elif user_rating == -1:  # Thumbs down
                    c['final_score'] *= 0.2
            
            # Explanation
            c['explanation_meta'] = {
                'similarity_score': c.get('rating_norm', 0),
                'genre_overlap': [],
                'novelty_score': c.get('obscurity_score', 0),
                'top_history_matches': [],
                'scoring_type': 'discovery_overview'
            }
            c['explanation_text'] = f"Discovery scoring based on quality and popularity"

        # Apply diversity-aware selection
        result = self._select_diverse_items(candidates, item_limit, diversity_lambda=0.6)
        
        # Return formatted results
        return [
            {
                **c,  # Preserve all fields
                'trakt_id': c.get('ids', {}).get('trakt') or c.get('trakt_id'),
                'tmdb_id': c.get('ids', {}).get('tmdb') or c.get('tmdb_id'),
                'media_type': c.get('type') or c.get('media_type'),
                'final_score': c['final_score'],
                'explanation_text': c['explanation_text'],
                'explanation_meta': c['explanation_meta'],
                'components': {
                    'genre_overlap': c.get('genre_overlap', 0.0),
                    'semantic_sim': 0.0,
                    'mood_score': 0.0,
                    'rating_norm': c.get('rating_norm', 0.0),
                    'novelty': c.get('obscurity_score', 0.0),
                    'popularity_norm': c.get('popularity_norm', 0.0),
                    'fast_score': c.get('final_score', 0.0),  # Use final as fast for discovery
                }
            }
            for c in result
        ]

    def _score_smartlist_advanced(self, user, candidates: list, explore_factor: float, item_limit: int, semantic_anchor: Optional[str]=None, list_type: str = "smartlist", filters: Optional[Dict[str, Any]] = None) -> list:
        """Advanced scoring for SmartLists with TF-IDF, mood, and semantic features."""
        # Get user ratings once for all candidates
        user_id = user.get("id")
        user_ratings = self._get_user_ratings(user_id) if user_id else {}
        
        # TF-IDF semantic similarity (no torch)
        # If semantic_anchor is provided, use it for similarity instead of user profile
        if semantic_anchor:
            anchor_text = semantic_anchor
            texts = [anchor_text] + [self._candidate_text(c) for c in candidates]
        else:
            texts = [self._user_profile_text(user)] + [self._candidate_text(c) for c in candidates]
        vectorizer = TfidfVectorizer(max_features=1000, stop_words='english')
        tfidf_matrix = vectorizer.fit_transform(texts)
        anchor_vec = tfidf_matrix[0]
        cand_vecs = tfidf_matrix[1:]
        semantic_sims = cosine_similarity(anchor_vec, cand_vecs).flatten()

        # Enhanced mood scoring with fallback strategies
        # Priority: filters["mood"] > user cached mood > contextual mood
        filter_moods = filters.get("mood", []) if isinstance(filters, dict) else []
        if filter_moods:
            # Use explicit mood from filters (e.g., ["cozy", "uplifting"])
            enhanced_user_mood = self._mood_keywords_to_vector(filter_moods)
            logger.info(f"Using mood from filters: {filter_moods} -> {enhanced_user_mood}")
        else:
            # Fall back to user's cached mood profile
            user_mood = get_cached_user_mood(user.get('id'))
            if not user_mood or all(v == 0 for v in user_mood.values()):
                # If no cached mood, use neutral but log for future enhancement
                logger.debug(f"No cached mood for user {user.get('id')}, using neutral mood")
                user_mood = user_mood or {}
            
            # Apply contextual mood adjustments with user's timezone preference
            user_timezone = self._get_user_timezone_sync(user.get('id', 1))
            contextual_adjustments = get_contextual_mood_adjustment(user_timezone)
            
            # Enhance user mood with contextual signals
            enhanced_user_mood = user_mood.copy()
            for mood, adjustment in contextual_adjustments.items():
                enhanced_user_mood[mood] = enhanced_user_mood.get(mood, 0) + adjustment
        
        for i, c in enumerate(candidates):
            c['semantic_sim'] = float(semantic_sims[i])
            tmdb_meta = c.get('tmdb_data') or c.get('tmdb') or {}
            cand_mood = compute_mood_vector_for_tmdb(tmdb_meta)
            c['mood_score'] = self._cosine(enhanced_user_mood, cand_mood)
            c['obscurity'] = c.get('obscurity_score', 0.0) or c.get('mainstream_score', 0.0) or c.get('popularity_norm', 0.0)
            c['freshness'] = c.get('freshness_score', 0.0)
            c['novelty'] = 1.0 - c['popularity_norm']

        # Per-list-type weights from user table
        weights = {
            'manual':      {'profile': 0.8, 'mood': 0.5, 'semantic': 0.6, 'obscurity': 0.4, 'freshness': 0.2},
            'suggested':   {'profile': 1.0, 'mood': 0.7, 'semantic': 0.5, 'obscurity': 0.4, 'freshness': 0.3},
            'smartlist':   {'profile': 0.6, 'mood': 1.0, 'semantic': 0.8, 'obscurity': 0.5, 'freshness': 0.4},
            'mood':        {'profile': 0.6, 'mood': 1.0, 'semantic': 0.8, 'obscurity': 0.5, 'freshness': 0.4},
            'fusion':      {'profile': 0.6, 'mood': 1.0, 'semantic': 0.8, 'obscurity': 0.5, 'freshness': 0.4},
            'theme':       {'profile': 0.6, 'mood': 1.0, 'semantic': 0.8, 'obscurity': 0.5, 'freshness': 0.4},
            'chat':        {'profile': 0.3, 'mood': 0.6, 'semantic': 1.0, 'obscurity': 0.8, 'freshness': 0.4},
            'prompt':      {'profile': 0.3, 'mood': 0.6, 'semantic': 1.0, 'obscurity': 0.8, 'freshness': 0.4},
        }
        w = weights.get(list_type, weights['smartlist'])
        for c in candidates:
            # User profile similarity: combine genre overlap and explicit preference alignment
            profile_sim = 0.6*c.get('genre_overlap', 0.0) + 0.4*c.get('user_pref_align', 0.0)
            mood_sim = c.get('mood_score', 0.0)
            semantic_sim = c.get('semantic_sim', 0.0)
            obscurity = c.get('obscurity', 0.0)
            freshness = c.get('freshness', 0.0)
            c['final_score'] = (
                w['profile']   * profile_sim +
                w['mood']      * mood_sim +
                w['semantic']  * semantic_sim +
                w['obscurity'] * obscurity +
                w['freshness'] * freshness
            )
            # Apply user rating influence
            trakt_id = c.get('ids', {}).get('trakt') if isinstance(c.get('ids'), dict) else c.get('trakt_id')
            if trakt_id and trakt_id in user_ratings:
                user_rating = user_ratings[trakt_id]
                if user_rating == 1:  # Thumbs up
                    c['final_score'] *= 1.3
                elif user_rating == -1:  # Thumbs down
                    c['final_score'] *= 0.3
        # Generate explanations
        for c in candidates:
            c['explanation_meta'] = self._build_explanation_meta(c)
            c['explanation_text'] = generate_explanation(c['explanation_meta'])
        # Apply diversity-aware selection (MMR algorithm)
        result = self._select_diverse_items(candidates, item_limit, diversity_lambda=0.6)
        # Memory cleanup (explicit)
        del vectorizer; del tfidf_matrix; import gc; gc.collect()
        
        # Preserve all original candidate fields and add scoring metadata
        return [
            {
                **c,  # Spread original candidate dict to preserve all fields
                'trakt_id': c.get('ids', {}).get('trakt') or c.get('trakt_id'),
                'tmdb_id': c.get('ids', {}).get('tmdb') or c.get('tmdb_id'),
                'media_type': c.get('type') or c.get('media_type'),
                'final_score': c['final_score'],
                'explanation_text': c['explanation_text'],
                'explanation_meta': c['explanation_meta'],
                # Components exposed for fusion blending
                'components': {
                    'genre_overlap': c.get('genre_overlap', 0.0),
                    'semantic_sim': c.get('semantic_sim', 0.0),
                    'mood_score': c.get('mood_score', 0.0),
                    'rating_norm': c.get('rating_norm', 0.0),
                    'novelty': c.get('novelty', 0.0),
                    'popularity_norm': c.get('popularity_norm', 0.0),
                    'fast_score': c.get('fast_score', 0.0),
                }
            }
            for c in result
        ]

    def _select_diverse_items(self, candidates: List[Dict[str, Any]], item_limit: int, diversity_lambda: float = 0.6) -> List[Dict[str, Any]]:
        """
        Select diverse items using Maximal Marginal Relevance (MMR) algorithm.
        
        Args:
            candidates: List of scored candidates
            item_limit: Maximum number of items to select
            diversity_lambda: Balance between relevance (1.0) and diversity (0.0)
                            0.6 = 60% relevance, 40% diversity
        
        Returns:
            List of diverse items
        """
        if not candidates or item_limit <= 0:
            return []
        
        # Start with empty selection
        selected = []
        remaining = candidates.copy()
        
        # Sort by score initially
        remaining.sort(key=lambda x: x.get('final_score', 0), reverse=True)
        
        # Always pick the highest scored item first
        if remaining:
            selected.append(remaining.pop(0))
        
        # Iteratively select items that balance relevance and diversity
        while len(selected) < item_limit and remaining:
            best_mmr_score = -1
            best_idx = 0
            
            for idx, candidate in enumerate(remaining):
                # Relevance component (normalized score)
                relevance = candidate.get('final_score', 0)
                
                # Diversity component: minimum similarity to already selected items
                max_similarity = 0
                for selected_item in selected:
                    similarity = self._compute_similarity(candidate, selected_item)
                    max_similarity = max(max_similarity, similarity)
                
                # MMR formula: λ * relevance - (1-λ) * max_similarity
                mmr_score = diversity_lambda * relevance - (1 - diversity_lambda) * max_similarity
                
                if mmr_score > best_mmr_score:
                    best_mmr_score = mmr_score
                    best_idx = idx
            
            # Add the best candidate and remove from remaining
            selected.append(remaining.pop(best_idx))
        
        return selected
    
    def _compute_similarity(self, item1: Dict[str, Any], item2: Dict[str, Any]) -> float:
        """
        Compute similarity between two items based on genres, metadata, and content.
        Returns value between 0 (completely different) and 1 (identical).
        """
        similarity_score = 0.0
        components = 0
        
        # 1. Genre similarity (most important for diversity)
        genres1 = set(g.lower() for g in self._extract_genres(item1))
        genres2 = set(g.lower() for g in self._extract_genres(item2))
        if genres1 or genres2:
            genre_similarity = len(genres1 & genres2) / max(len(genres1 | genres2), 1)
            similarity_score += genre_similarity * 0.4
            components += 0.4
        
        # 2. Year proximity (items from same era)
        year1 = item1.get('year', 0) or 0
        year2 = item2.get('year', 0) or 0
        try:
            year1 = int(year1)
            year2 = int(year2)
            if year1 and year2:
                year_diff = abs(year1 - year2)
                # Items within 5 years are very similar, >20 years are different
                year_similarity = max(0, 1 - year_diff / 20.0)
                similarity_score += year_similarity * 0.2
                components += 0.2
        except (ValueError, TypeError):
            pass
        
        # 3. Rating proximity (items with similar quality)
        rating1 = item1.get('rating', 0) or item1.get('vote_average', 0) or 0
        rating2 = item2.get('rating', 0) or item2.get('vote_average', 0) or 0
        if rating1 and rating2:
            rating_diff = abs(rating1 - rating2)
            # Ratings within 1 point are similar, >3 points are different
            rating_similarity = max(0, 1 - rating_diff / 3.0)
            similarity_score += rating_similarity * 0.15
            components += 0.15
        
        # 4. Media type (movie vs show)
        media1 = item1.get('media_type', '') or item1.get('type', '')
        media2 = item2.get('media_type', '') or item2.get('type', '')
        if media1 and media2:
            media_similarity = 1.0 if media1 == media2 else 0.0
            similarity_score += media_similarity * 0.25
            components += 0.25
        
        # Normalize by total components used
        if components > 0:
            return similarity_score / components
        return 0.0

    def _passes_filters(self, user, c):
        """Apply strict filtering based on provided filters. Returns True if candidate passes all filters."""
        # If called without filters in score_candidates context, we can't enforce here
        # This method needs filters dict passed explicitly; for now it's a placeholder for future
        # filter enforcement at the candidate-level before scoring
        # Real enforcement should happen in score_candidates where filters are available
        return True
    
    def _passes_explicit_filters(self, c: Dict[str, Any], filters: Dict[str, Any]) -> bool:
        """Apply strict filtering based on provided filters dict. Matches AI engine scorer pattern."""
        if not filters:
            return True
        
        # Genres
        if "genres" in filters and filters["genres"]:
            cand_genres = set()
            try:
                import json
                cand_genres_raw = c.get("genres")
                if isinstance(cand_genres_raw, str):
                    if cand_genres_raw.startswith("["):
                        cand_genres = set(json.loads(cand_genres_raw))
                    else:
                        cand_genres = set(g.strip() for g in cand_genres_raw.split(","))
                elif isinstance(cand_genres_raw, list):
                    cand_genres = set(cand_genres_raw)
            except Exception:
                pass
            filter_genres = set([g.lower() for g in filters["genres"]])
            cand_genres_lower = set([g.lower() for g in cand_genres])
            if not (filter_genres & cand_genres_lower):
                return False
        
        # Actors - check if any filtered actor appears in candidate's cast
        if "actors" in filters and filters["actors"]:
            cand_cast = []
            try:
                import json
                cast_raw = c.get("cast")
                if isinstance(cast_raw, str):
                    if cast_raw.startswith("["):
                        cand_cast = json.loads(cast_raw)
                    else:
                        cand_cast = [n.strip() for n in cast_raw.split(",")]
                elif isinstance(cast_raw, list):
                    cand_cast = cast_raw
                cand_cast = [str(name).lower() for name in cand_cast]
            except Exception:
                pass
            filter_actors = [str(name).lower() for name in filters["actors"]]
            if not any(actor in " ".join(cand_cast) for actor in filter_actors):
                return False
        
        # Studios - check if any filtered studio matches production company
        if "studios" in filters and filters["studios"]:
            cand_studios = []
            try:
                import json
                studios_raw = c.get("production_companies")
                if isinstance(studios_raw, str):
                    if studios_raw.startswith("["):
                        cand_studios = json.loads(studios_raw)
                    else:
                        cand_studios = [n.strip() for n in studios_raw.split(",")]
                elif isinstance(studios_raw, list):
                    cand_studios = studios_raw
                cand_studios = [str(name).lower() for name in cand_studios]
            except Exception:
                pass
            filter_studios = [str(name).lower() for name in filters["studios"]]
            if not any(studio in " ".join(cand_studios) for studio in filter_studios):
                return False
        
        # Languages
        if "languages" in filters and filters["languages"]:
            cand_lang = (c.get("language") or "").lower()
            filter_langs = [l.lower() for l in filters["languages"]]
            if cand_lang not in filter_langs:
                return False
        
        # Years
        if "years" in filters and filters["years"]:
            cand_year = int(c.get("year") or 0)
            if cand_year not in set(filters["years"]):
                return False
        
        # Year range
        year_from = filters.get("year_from") or filters.get("min_year")
        year_to = filters.get("year_to") or filters.get("max_year")
        if year_from or year_to:
            cand_year = int(c.get("year") or 0)
            if year_from and cand_year < int(year_from):
                return False
            if year_to and cand_year > int(year_to):
                return False
        
        # Adult flag
        if "adult" in filters and filters["adult"] is not None:
            want_adult = filters["adult"]
            cand_adult = c.get("adult") or c.get("is_adult")
            if isinstance(cand_adult, bool):
                if want_adult is True and cand_adult is False:
                    return False
                if want_adult is False and cand_adult is True:
                    return False
        
        # Original language
        if "original_language" in filters and filters["original_language"]:
            ol = (c.get("original_language") or c.get("language") or "").lower()
            filter_ol = str(filters["original_language"]).lower()
            if ol != filter_ol:
                return False
        
        return True

    def _filter_alignment(self, c: Dict[str, Any], filters: Dict[str, Any]) -> float:
        """Compute how well a candidate aligns with explicit filters (genres, languages, years)."""
        if not filters:
            return 0.0
        score = 0.0
        total = 0.0
        
        # Genres with mode support (any=OR, all=AND)
        f_genres = set([g.lower() for g in (filters.get('genres') or [])])
        c_genres = set([g.lower() for g in (self._candidate_genres(c) or [])])
        if f_genres:
            genre_mode = filters.get('genre_mode', 'any')
            if genre_mode == 'all':
                # ALL mode: Require all filter genres to be present in candidate
                if f_genres.issubset(c_genres):
                    score += 1.0  # Full match
                else:
                    score += 0.0  # No match
            else:
                # ANY mode (default): At least one genre match
                overlap = len(f_genres & c_genres)
                score += min(1.0, overlap / max(1, len(f_genres)))
            total += 1.0
        # Languages
        f_langs = set((filters.get('languages') or []))
        if f_langs:
            lang = c.get('language')
            match = 1.0 if (lang and lang in f_langs) else 0.0
            score += match
            total += 1.0
        # Year range
        min_year = filters.get('min_year')
        max_year = filters.get('max_year')
        if min_year or max_year:
            year = c.get('year') or 0
            try:
                year = int(year)
            except Exception:
                year = 0
            ok = True
            if min_year and year and year < int(min_year):
                ok = False
            if max_year and year and year > int(max_year):
                ok = False
            score += 1.0 if ok else 0.0
            total += 1.0
        if total == 0:
            return 0.0
        return min(1.0, score / total)

    def _genre_overlap(self, user, c):
        """Compute overlap between user's preferred genres (derived from cached mood) and candidate genres."""
        cand_genres = set(self._candidate_genres(c))
        if not cand_genres:
            return 0.0
        pref_genres = set(self._preferred_genres_from_mood(user))
        if not pref_genres:
            # neutral overlap
            return 0.3
        overlap = len(cand_genres & pref_genres)
        return min(1.0, overlap / max(1, len(pref_genres)))

    def _norm(self, val, minv, maxv):
        return min(1.0, max(0.0, (val - minv) / (maxv - minv + 1e-6)))

    def _user_profile_text(self, user):
        """Build a lightweight profile text from cached mood axes as tags."""
        mood = get_cached_user_mood(user.get('id')) or {}
        if not mood:
            return "diverse viewer enjoys quality storytelling"
        # take top 3 moods
        top = sorted(mood.items(), key=lambda x: x[1], reverse=True)[:3]
        tags = [m for m, v in top if v > 0]
        if not tags:
            return "diverse viewer enjoys quality storytelling"
        return "viewer prefers " + ", ".join(tags) + " themes"

    def _candidate_text(self, c):
        """Concatenate title, overview, genres, and keywords from available metadata."""
        title = c.get('title', '')
        overview = c.get('overview', '')
        genres = []
        keywords = []
        # Pull from tmdb_data if present
        tmdb = c.get('tmdb_data') or {}
        if isinstance(tmdb, dict):
            genres = [g for g in tmdb.get('genres', []) if isinstance(g, str)] or [g.get('name') for g in tmdb.get('genres', []) if isinstance(g, dict)]
            kw_list = tmdb.get('keywords', [])
            if isinstance(kw_list, list):
                keywords = [k for k in kw_list if isinstance(k, str)] or [k.get('name') for k in kw_list if isinstance(k, dict)]
        # Fallbacks
        if not genres:
            genres = c.get('genres', []) or c.get('genre_names', [])
        if not keywords:
            cm = c.get('cached_metadata', {})
            if isinstance(cm, dict):
                genres = genres or cm.get('genres', [])
                keywords = cm.get('keywords', [])
        parts = [title, overview, " ".join(genres or []), " ".join(keywords or [])]
        return " ".join(p for p in parts if p)

    def _candidate_genres(self, c):
        tmdb = c.get('tmdb_data') or {}
        if isinstance(tmdb, dict):
            if tmdb.get('genres') and isinstance(tmdb['genres'], list):
                if tmdb['genres'] and isinstance(tmdb['genres'][0], dict):
                    return [g.get('name') for g in tmdb['genres'] if isinstance(g, dict)]
                return [g for g in tmdb['genres'] if isinstance(g, str)]
        return c.get('genres', []) or c.get('genre_names', [])

    def _preferred_genres_from_mood(self, user):
        mood = get_cached_user_mood(user.get('id')) or {}
        if not mood:
            return []
        # Simple mapping from mood axes to representative genres
        MOOD_TO_GENRES = {
            'happy': ['comedy', 'family', 'animation'],
            'sad': ['drama', 'biography'],
            'excited': ['action', 'adventure'],
            'scared': ['horror', 'thriller'],
            'romantic': ['romance'],
            'tense': ['thriller', 'crime', 'mystery'],
            'curious': ['sci-fi', 'science fiction', 'documentary', 'mystery'],
            'thoughtful': ['drama', 'documentary', 'history']
        }
        # pick top 3 moods and accumulate their genres
        top = sorted(mood.items(), key=lambda x: x[1], reverse=True)[:3]
        genres = []
        for m, _ in top:
            genres.extend(MOOD_TO_GENRES.get(m, []))
        # de-duplicate
        return list(dict.fromkeys(g for g in genres if g))

    def _mood_keywords_to_vector(self, mood_keywords: List[str]) -> Dict[str, float]:
        """
        Convert mood keywords (like "cozy", "uplifting") into a mood vector.
        Maps common mood words to mood axes (happy, sad, excited, scared, etc.).
        """
        MOOD_KEYWORD_MAPPING = {
            # Cozy/comfort moods
            "cozy": {"happy": 0.6, "thoughtful": 0.3, "romantic": 0.1},
            "comfort": {"happy": 0.6, "thoughtful": 0.3, "romantic": 0.1},
            "feel-good": {"happy": 0.9, "excited": 0.1},
            "feel good": {"happy": 0.9, "excited": 0.1},
            "uplifting": {"happy": 0.8, "excited": 0.2},
            "heartwarming": {"happy": 0.7, "romantic": 0.3},
            
            # Dark/intense moods
            "dark": {"tense": 0.6, "scared": 0.3, "thoughtful": 0.1},
            "intense": {"tense": 0.7, "excited": 0.3},
            "gritty": {"tense": 0.7, "sad": 0.3},
            "serious": {"thoughtful": 0.7, "tense": 0.3},
            
            # Exciting moods
            "exciting": {"excited": 0.9, "happy": 0.1},
            "thrilling": {"excited": 0.8, "tense": 0.2},
            "action-packed": {"excited": 0.9, "tense": 0.1},
            "adventurous": {"excited": 0.7, "curious": 0.3},
            
            # Scary moods
            "scary": {"scared": 0.9, "tense": 0.1},
            "horror": {"scared": 0.9, "tense": 0.1},
            "creepy": {"scared": 0.8, "tense": 0.2},
            
            # Funny moods
            "funny": {"happy": 0.9, "excited": 0.1},
            "hilarious": {"happy": 0.95, "excited": 0.05},
            "comedy": {"happy": 0.9, "excited": 0.1},
            "lighthearted": {"happy": 0.8, "thoughtful": 0.2},
            
            # Romantic moods
            "romantic": {"romantic": 0.9, "happy": 0.1},
            "love": {"romantic": 0.8, "happy": 0.2},
            "passionate": {"romantic": 0.7, "excited": 0.3},
            
            # Thoughtful moods
            "thoughtful": {"thoughtful": 0.9, "curious": 0.1},
            "contemplative": {"thoughtful": 0.8, "sad": 0.2},
            "philosophical": {"thoughtful": 0.9, "curious": 0.1},
            "cerebral": {"thoughtful": 0.8, "curious": 0.2},
            
            # Sad moods
            "sad": {"sad": 0.9, "thoughtful": 0.1},
            "melancholic": {"sad": 0.8, "thoughtful": 0.2},
            "tragic": {"sad": 0.9, "thoughtful": 0.1},
            "emotional": {"sad": 0.6, "romantic": 0.2, "thoughtful": 0.2},
        }
        
        mood_vector = {}
        for keyword in mood_keywords:
            keyword_lower = keyword.lower().strip()
            if keyword_lower in MOOD_KEYWORD_MAPPING:
                mapping = MOOD_KEYWORD_MAPPING[keyword_lower]
                for mood_axis, weight in mapping.items():
                    mood_vector[mood_axis] = mood_vector.get(mood_axis, 0.0) + weight
        
        # Normalize to sum to 1.0
        total = sum(mood_vector.values())
        if total > 0:
            mood_vector = {k: v / total for k, v in mood_vector.items()}
        
        logger.debug(f"Converted mood keywords {mood_keywords} to vector: {mood_vector}")
        return mood_vector

    def _cosine(self, v1, v2):
        # v1, v2: dicts of floats
        keys = set(v1) | set(v2)
        a = np.array([v1.get(k, 0.0) for k in keys])
        b = np.array([v2.get(k, 0.0) for k in keys])
        if np.linalg.norm(a) == 0 or np.linalg.norm(b) == 0:
            return 0.0
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))

    def _get_user_timezone_sync(self, user_id: int) -> str:
        """Get user timezone preference synchronously with Redis fallback."""
        try:
            # Try to get from Redis settings (sync call)
            from app.core.redis_client import redis_client
            timezone_setting = redis_client.get(f"settings:global:user_timezone")
            if timezone_setting:
                return timezone_setting.decode('utf-8') if isinstance(timezone_setting, bytes) else str(timezone_setting)
        except Exception:
            pass
        return "UTC"  # Default fallback

    def _build_explanation_meta(self, c):
        # Compose a rich explanation meta used by explain.py and the UI
        components = {
            'genre_overlap': float(c.get('genre_overlap', 0) or 0),
            'pref_align': float(c.get('user_pref_align', 0) or 0),
            'semantic_sim': float(c.get('semantic_sim', 0) or 0),
            'mood_score': float(c.get('mood_score', 0) or 0),
            'rating_norm': float(c.get('rating_norm', 0) or 0),
            'novelty': float(c.get('novelty', 0) or 0),
            'popularity_norm': float(c.get('popularity_norm', 0) or 0),
            'fast_score': float(c.get('fast_score', 0) or 0),
        }
        return {
            'similarity_score': components['genre_overlap'],
            'components': components,
            'scoring_type': 'smartlist_advanced'
        }

logger = logging.getLogger(__name__)
