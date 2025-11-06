"""
fit_scoring.py

On-the-fly fit scoring for Individual Lists.
Computes 0-1 scores showing how well candidates match user profile.

Scoring weights:
- 40% genre overlap with user preferences
- 40% similarity to recent highly-rated items (embedding similarity)
- 20% popularity/novelty balance
"""
import json
import logging
import numpy as np
from typing import Dict, List, Any, Optional

from app.services.user_profile import UserProfileService
from app.services.ai_engine.embeddings import EmbeddingService
from app.services.ai_engine.faiss_index import get_embedding_from_index
from app.models import PersistentCandidate
from app.core.database import SessionLocal

logger = logging.getLogger(__name__)

# Scoring weights
GENRE_WEIGHT = 0.4
SIMILARITY_WEIGHT = 0.4
POPULARITY_WEIGHT = 0.2


class FitScorer:
    """
    Calculate fit scores for candidates based on user profile.
    
    Fit score (0-1) indicates how well a candidate matches:
    - User's genre preferences (from watch history)
    - User's recent highly-rated content (embedding similarity)
    - User's popularity preference (mainstream vs obscure)
    """
    
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.profile_service = UserProfileService(user_id)
        self.embedding_service = EmbeddingService()
    
    def score_candidates(
        self, 
        candidates: List[Dict[str, Any]],
        use_cached_profile: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Add fit_score field to each candidate.
        
        Args:
            candidates: List of candidate dicts with genres, tmdb_id, media_type, popularity
            use_cached_profile: Use cached profile or force refresh
            
        Returns:
            Same candidates with added 'fit_score' field (0-1)
        """
        # Get user profile
        profile = self.profile_service.get_profile(force_refresh=not use_cached_profile)
        
        if not profile or profile.get('total_watched', 0) == 0:
            # No profile data - provide a lightweight non-uniform score using popularity as signal
            logger.debug(f"No profile data for user {self.user_id}, using popularity-based fallback scores")
            fallback_profile = {"preferred_obscurity": "balanced"}
            for candidate in candidates:
                try:
                    popularity_score = self._calculate_popularity_score(candidate, fallback_profile)
                    # Small adjustment: if candidate has genres, nudge slightly above raw popularity_score
                    has_genres = bool(candidate.get('genres'))
                    candidate['fit_score'] = round(min(1.0, popularity_score + (0.05 if has_genres else 0.0)), 3)
                except Exception:
                    candidate['fit_score'] = 0.5
            return candidates
        
        # Score each candidate
        for candidate in candidates:
            try:
                genre_score = self._calculate_genre_score(candidate, profile)
                similarity_score = self._calculate_similarity_score(candidate, profile)
                popularity_score = self._calculate_popularity_score(candidate, profile)

                # Dynamically adjust weights based on available signals to avoid flat 0.5s
                g_w = GENRE_WEIGHT
                s_w = SIMILARITY_WEIGHT
                p_w = POPULARITY_WEIGHT

                # If recent history is missing or weak, shift some weight from similarity to genre
                has_recent = bool(profile.get('recent_tmdb_ids'))
                # Candidate-specific genre presence
                cand_genres = candidate.get('genres')
                has_genres = bool(cand_genres) and (isinstance(cand_genres, list) and len(cand_genres) > 0 or isinstance(cand_genres, str))
                if not has_recent:
                    transfer = min(0.2, s_w)
                    s_w -= transfer
                    g_w += transfer
                # If candidate lacks genre data or profile has no genre weights, shift to similarity
                if not has_genres or not profile.get('genre_weights'):
                    transfer = min(0.2, g_w)
                    g_w -= transfer
                    s_w += transfer

                # Normalize just in case numerical drift occurs
                total_w = max(1e-6, g_w + s_w + p_w)
                g_w /= total_w; s_w /= total_w; p_w /= total_w

                # Weighted average
                fit_score = (
                    genre_score * g_w +
                    similarity_score * s_w +
                    popularity_score * p_w
                )
                
                candidate['fit_score'] = round(fit_score, 3)
                
                # Optional: log components for debugging
                candidate['_score_components'] = {
                    'genre': round(genre_score, 3),
                    'similarity': round(similarity_score, 3),
                    'popularity': round(popularity_score, 3)
                }
                
            except Exception as e:
                logger.error(f"Failed to score candidate {candidate.get('tmdb_id')}: {e}")
                candidate['fit_score'] = 0.5  # Neutral fallback
        
        return candidates
    
    def _calculate_genre_score(
        self, 
        candidate: Dict[str, Any], 
        profile: Dict[str, Any]
    ) -> float:
        """
        Calculate genre overlap score (0-1).
        
        Compares candidate genres with user's genre_weights from profile.
        Higher score = more overlap with preferred genres.
        """
        genre_weights = profile.get('genre_weights', {})
        if not genre_weights:
            return 0.5  # Neutral if no genre data
        
        # Get candidate genres
        candidate_genres = candidate.get('genres', [])
        if isinstance(candidate_genres, str):
            try:
                candidate_genres = json.loads(candidate_genres)
            except:
                candidate_genres = []
        
        if not candidate_genres:
            return 0.3  # Slight penalty for missing genre data
        
        # Calculate average weight of candidate's genres
        genre_scores = []
        for genre in candidate_genres:
            genre_lower = genre.lower() if isinstance(genre, str) else str(genre).lower()
            weight = genre_weights.get(genre_lower, 0.1)  # Default 0.1 for unknown genres
            genre_scores.append(weight)
        
        if not genre_scores:
            return 0.3
        
        # Average of genre weights
        return np.mean(genre_scores)
    
    def _calculate_similarity_score(
        self, 
        candidate: Dict[str, Any], 
        profile: Dict[str, Any]
    ) -> float:
        """
        Calculate embedding similarity to user's recent highly-rated items (0-1).
        
        Uses FAISS embeddings to compare candidate with user's recent watches.
        Higher score = more similar to recently watched/liked content.
        """
        recent_tmdb_ids = profile.get('recent_tmdb_ids', [])
        if not recent_tmdb_ids:
            return 0.5  # Neutral if no recent history
        
        candidate_tmdb_id = candidate.get('tmdb_id')
        candidate_media_type = candidate.get('media_type')
        
        if not candidate_tmdb_id:
            return 0.5
        
        try:
            # Get candidate embedding from FAISS index
            candidate_embedding = get_embedding_from_index(candidate_tmdb_id, candidate_media_type)
            if candidate_embedding is None:
                # Try to compute embedding on-the-fly if not in index
                db = SessionLocal()
                try:
                    pc = db.query(PersistentCandidate).filter(
                        PersistentCandidate.tmdb_id == candidate_tmdb_id,
                        PersistentCandidate.media_type == candidate_media_type
                    ).first()
                    
                    if pc:
                        # Compute embedding
                        from app.services.ai_engine.metadata_processing import compose_text_for_embedding
                        text = compose_text_for_embedding(
                            title=pc.title,
                            overview=pc.overview or "",
                            genres=json.loads(pc.genres) if pc.genres else [],
                            keywords=json.loads(pc.keywords) if pc.keywords else [],
                            cast=json.loads(pc.cast) if pc.cast else []
                        )
                        candidate_embedding = self.embedding_service.encode_text(text)
                finally:
                    db.close()
            
            if candidate_embedding is None:
                return 0.5  # Can't compute similarity without embedding
            
            # Get embeddings for recent items
            recent_embeddings = []
            for tmdb_id in recent_tmdb_ids[:10]:  # Limit to 10 most recent
                emb = get_embedding_from_index(tmdb_id, "movie")  # Try movie first
                if emb is None:
                    emb = get_embedding_from_index(tmdb_id, "show")
                if emb is not None:
                    recent_embeddings.append(emb)
            
            if not recent_embeddings:
                return 0.5
            
            # Calculate cosine similarity to each recent item
            similarities = []
            for recent_emb in recent_embeddings:
                # Cosine similarity
                similarity = np.dot(candidate_embedding, recent_emb) / (
                    np.linalg.norm(candidate_embedding) * np.linalg.norm(recent_emb)
                )
                similarities.append(similarity)
            
            # Take max similarity (most similar to any recent item)
            max_similarity = max(similarities)
            
            # Convert from [-1, 1] to [0, 1]
            normalized_similarity = (max_similarity + 1) / 2
            
            return float(normalized_similarity)
            
        except Exception as e:
            logger.error(f"Failed to calculate similarity score: {e}")
            return 0.5
    
    def _calculate_popularity_score(
        self, 
        candidate: Dict[str, Any], 
        profile: Dict[str, Any]
    ) -> float:
        """
        Calculate popularity fit score (0-1).
        
        Compares candidate's popularity with user's preferred obscurity level.
        - obscure preference: lower popularity = higher score
        - mainstream preference: higher popularity = higher score
        - balanced: neutral scoring
        """
        obscurity_pref = profile.get('preferred_obscurity', 'balanced')
        candidate_popularity = candidate.get('popularity', 50.0)
        
        if obscurity_pref == 'balanced':
            # Neutral - slight preference for moderate popularity (30-70)
            if 30 <= candidate_popularity <= 70:
                return 0.7
            else:
                return 0.5
        
        elif obscurity_pref == 'obscure':
            # Prefer low popularity (< 30)
            if candidate_popularity < 20:
                return 1.0
            elif candidate_popularity < 40:
                return 0.7
            elif candidate_popularity < 60:
                return 0.5
            else:
                return 0.3
        
        else:  # mainstream
            # Prefer high popularity (> 60)
            if candidate_popularity > 80:
                return 1.0
            elif candidate_popularity > 60:
                return 0.7
            elif candidate_popularity > 40:
                return 0.5
            else:
                return 0.3
    
    def score_single_candidate(self, candidate: Dict[str, Any]) -> float:
        """
        Convenience method to score a single candidate.
        
        Returns fit_score (0-1).
        """
        scored = self.score_candidates([candidate])
        return scored[0]['fit_score']
