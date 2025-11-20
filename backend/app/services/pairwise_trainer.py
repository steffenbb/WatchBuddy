"""
Pairwise preference training service for user feedback collection.

Implements tournament-style pairwise comparisons to learn user preferences.
Updates user vectors immediately on judgment submission.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any

from sqlalchemy.orm import Session
from app.core.redis_client import get_redis_sync
from app.models import (
    PairwiseTrainingSession, 
    PairwiseJudgment, 
    PersistentCandidate,
    User
)

logger = logging.getLogger(__name__)


class PairwiseTrainer:
    """Manages pairwise preference training sessions."""
    
    def __init__(self, db: Session, user_id: int = 1):
        self.db = db
        self.user_id = user_id
        self.redis = get_redis_sync()
        
    def create_session(
        self, 
        prompt: str, 
        candidate_ids: List[int],
        list_type: str = "chat",
        filters: Optional[Dict[str, Any]] = None
    ) -> PairwiseTrainingSession:
        """Create a new pairwise training session.
        
        Args:
            prompt: User query/intent for this session
            candidate_ids: List of persistent candidate IDs to compare
            list_type: Type of list (chat, mood, theme, etc.)
            filters: Optional filter dict for session context
            
        Returns:
            PairwiseTrainingSession object
        """
        # Enforce 10-20 judgments per session (spec requirement)
        # Choose target based on candidate pool size
        if len(candidate_ids) >= 15:
            total_pairs = 20  # Full session
        elif len(candidate_ids) >= 10:
            total_pairs = 15  # Medium session
        else:
            total_pairs = max(10, len(candidate_ids))  # At least 10 or pool size
        
        session = PairwiseTrainingSession(
            user_id=self.user_id,
            prompt=prompt,
            filters_json=json.dumps(filters) if filters else None,
            list_type=list_type,
            candidate_pool_snapshot=json.dumps(candidate_ids),
            total_pairs=total_pairs,
            completed_pairs=0,
            status="active"
        )
        
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        
        # Track session start in telemetry
        try:
            from app.services.telemetry import TelemetryTracker
            tracker = TelemetryTracker(user_id=self.user_id)
            tracker.track_trainer_start()
        except Exception as e:
            logger.warning(f"Failed to track trainer start: {e}")
        
        logger.info(f"Created pairwise session {session.id} for user {self.user_id} with {len(candidate_ids)} candidates")
        return session
        
    def get_next_pair(self, session_id: int) -> Optional[Tuple[Dict, Dict]]:
        """Get next pair of candidates to judge.
        
        Uses Elo-inspired tournament scheduling to prioritize uncertain matchups.
        
        Args:
            session_id: Session ID to get pair from
            
        Returns:
            Tuple of (candidate_a_dict, candidate_b_dict) or None if session complete
        """
        session = self.db.query(PairwiseTrainingSession).filter_by(id=session_id).first()
        if not session or session.status != "active":
            return None
            
        # Check if already complete (>= not just ==)
        if session.completed_pairs >= session.total_pairs:
            self._complete_session(session_id)
            return None
            
        # Load candidate pool
        candidate_ids = json.loads(session.candidate_pool_snapshot)
        
        # Get already judged pairs
        judged_pairs = set()
        judgments = self.db.query(PairwiseJudgment).filter_by(
            session_id=session_id,
            user_id=self.user_id
        ).filter(PairwiseJudgment.winner != 'skip').all()
        
        for j in judgments:
            judged_pairs.add((min(j.candidate_a_id, j.candidate_b_id), max(j.candidate_a_id, j.candidate_b_id)))
            
        # Generate next pair (simple round-robin for now)
        for i, id_a in enumerate(candidate_ids):
            for id_b in candidate_ids[i+1:]:
                pair_key = (min(id_a, id_b), max(id_a, id_b))
                if pair_key not in judged_pairs:
                    # Fetch candidate details
                    cand_a = self.db.query(PersistentCandidate).filter_by(id=id_a).first()
                    cand_b = self.db.query(PersistentCandidate).filter_by(id=id_b).first()
                    
                    if cand_a and cand_b:
                        return (self._candidate_to_dict(cand_a), self._candidate_to_dict(cand_b))
                        
        # No more pairs
        self._complete_session(session_id)
        return None
        
    def submit_judgment(
        self, 
        session_id: int, 
        candidate_a_id: int, 
        candidate_b_id: int, 
        winner: str,
        confidence: Optional[float] = None,
        response_time_ms: Optional[int] = None,
        explanation: Optional[str] = None
    ) -> PairwiseJudgment:
        """Submit a pairwise judgment.
        
        Args:
            session_id: Session ID
            candidate_a_id: First candidate ID
            candidate_b_id: Second candidate ID
            winner: 'a', 'b', or 'skip'
            confidence: Optional confidence score (0.0-1.0)
            response_time_ms: Time taken to judge (milliseconds)
            explanation: Optional user explanation
            
        Returns:
            PairwiseJudgment object
        """
        if winner not in ('a', 'b', 'skip'):
            raise ValueError(f"Invalid winner: {winner}. Must be 'a', 'b', or 'skip'.")
            
        judgment = PairwiseJudgment(
            session_id=session_id,
            user_id=self.user_id,
            candidate_a_id=candidate_a_id,
            candidate_b_id=candidate_b_id,
            winner=winner,
            confidence=confidence,
            response_time_ms=response_time_ms,
            explanation=explanation
        )
        
        self.db.add(judgment)
        
        # Update session progress - don't increment beyond total_pairs
        session = self.db.query(PairwiseTrainingSession).filter_by(id=session_id).first()
        if session and winner != 'skip':
            # Only increment if not already complete
            if session.completed_pairs < session.total_pairs:
                session.completed_pairs += 1
            # Use utc_now() for consistency (avoid naive/aware datetime mix)
            from app.utils.timezone import utc_now
            session.updated_at = utc_now()
            
        self.db.commit()
        self.db.refresh(judgment)
        
        # Update user preference vectors immediately
        if winner != 'skip':
            self._update_user_vectors(candidate_a_id, candidate_b_id, winner)
            
        logger.info(f"Recorded judgment for session {session_id}: {candidate_a_id} vs {candidate_b_id}, winner={winner}")
        return judgment
        
    def get_session_status(self, session_id: int) -> Optional[Dict[str, Any]]:
        """Get session status and progress.
        
        Args:
            session_id: Session ID
            
        Returns:
            Dict with session details or None if not found
        """
        session = self.db.query(PairwiseTrainingSession).filter_by(id=session_id).first()
        if not session:
            return None
            
        return {
            "id": session.id,
            "user_id": session.user_id,
            "prompt": session.prompt,
            "list_type": session.list_type,
            "total_pairs": session.total_pairs,
            "completed_pairs": session.completed_pairs,
            "progress": session.completed_pairs / session.total_pairs if session.total_pairs > 0 else 0,
            "status": session.status,
            "started_at": session.started_at.isoformat() if session.started_at else None,
            "completed_at": session.completed_at.isoformat() if session.completed_at else None
        }
        
    def _complete_session(self, session_id: int) -> None:
        """Mark session as completed and generate persona micro-update."""
        session = self.db.query(PairwiseTrainingSession).filter_by(id=session_id).first()
        if session:
            # Calculate session duration
            duration_seconds = 0.0
            if session.started_at:
                # Ensure both datetimes are timezone-aware for comparison
                started_at = session.started_at if session.started_at.tzinfo else session.started_at.replace(tzinfo=timezone.utc)
                duration_seconds = (datetime.now(timezone.utc) - started_at).total_seconds()
            
            session.status = "completed"
            session.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            
            # Track session completion in telemetry
            try:
                from app.services.telemetry import TelemetryTracker
                tracker = TelemetryTracker(user_id=self.user_id)
                tracker.track_trainer_completion(session.completed_pairs, duration_seconds)
            except Exception as e:
                logger.warning(f"Failed to track trainer completion: {e}")
            
            logger.info(f"Completed pairwise session {session_id}")
            
            # Generate persona micro-update from session judgments
            try:
                self._generate_persona_delta(session_id)
            except Exception as e:
                logger.warning(f"Failed to generate persona delta for session {session_id}: {e}")
    
    def _generate_persona_delta(self, session_id: int) -> None:
        """Generate persona delta snippet from session judgments using phi3:mini."""
        import requests
        
        # Get all judgments from this session
        judgments = self.db.query(PairwiseJudgment).filter_by(
            session_id=session_id,
            user_id=self.user_id
        ).all()
        
        if len(judgments) < 5:
            logger.debug(f"Skipping persona delta: only {len(judgments)} judgments")
            return
        
        # Build summary of session outcomes
        session = self.db.query(PairwiseTrainingSession).filter_by(id=session_id).first()
        if not session:
            return
        
        # Get candidate details for top preferences
        preferred_ids = []
        for j in judgments:
            if j.winner == 'a':
                preferred_ids.append(j.candidate_a_id)
            elif j.winner == 'b':
                preferred_ids.append(j.candidate_b_id)
        
        # Get top 5 most preferred candidates
        from collections import Counter
        top_preferred = [cid for cid, _ in Counter(preferred_ids).most_common(5)]
        
        # Fetch candidate details
        candidates = self.db.query(PersistentCandidate).filter(
            PersistentCandidate.id.in_(top_preferred)
        ).all() if top_preferred else []
        
        if not candidates:
            return
        
        # Build items summary
        items_text = []
        for cand in candidates:
            genres = json.loads(cand.genres) if cand.genres else []
            genre_str = ', '.join(genres[:3]) if isinstance(genres, list) else ''
            items_text.append(f"- {cand.title} ({cand.year}) [{genre_str}]")
        
        # Call phi3:mini to generate persona delta
        prompt = f"""SYSTEM:
You are a concise persona summarizer. Given a user's training session preferences, generate a brief persona delta (2-3 sentences) describing what this session reveals about their taste.

USER:
Session context: "{session.prompt}"
User preferred these items:
{chr(10).join(items_text)}

**TASK:** Generate a 2-3 sentence persona delta summarizing this user's preferences from this session. Focus on patterns (genres, themes, styles).

**IMPORTANT:** Return ONLY the plain text persona summary. No JSON, no markdown formatting, no extra commentary.

**Output the persona delta now:**
"""
        
        try:
            resp = requests.post(
                "http://ollama:11434/api/generate",
                json={
                    "model": "phi3.5:3.8b-mini-instruct-q4_K_M",
                    "prompt": prompt,
                    "options": {"temperature": 0.3, "num_predict": 150, "num_ctx": 4096},
                    "stream": False,
                    "keep_alive": "24h"
                },
                timeout=60
            )
            
            if resp.status_code != 200:
                logger.warning(f"[PairwiseTrainer] LLM request failed with status {resp.status_code}")
                return
            
            data = resp.json()
            persona_delta = data.get("response", "").strip()
            
            # Validate response length and content
            if persona_delta and len(persona_delta) >= 20 and len(persona_delta) <= 500:
                # Store persona delta in Redis (append to existing persona)
                persona_key = f"persona_micro_updates:{self.user_id}"
                existing = self.redis.get(persona_key)
                
                if existing:
                    updates = json.loads(existing)
                else:
                    updates = []
                
                updates.append({
                    "session_id": session_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "delta": persona_delta
                })
                
                # Keep last 10 micro-updates
                updates = updates[-10:]
                
                # Store with 90-day expiry
                self.redis.setex(persona_key, 60 * 60 * 24 * 90, json.dumps(updates))
                
                logger.info(f"Generated persona delta for session {session_id}: {persona_delta[:100]}")
            else:
                logger.warning(f"LLM returned invalid persona delta (length={len(persona_delta)}): {persona_delta[:200]}")
                
        except Exception as e:
            logger.warning(f"Failed to call LLM for persona delta: {e}")
            
    def _candidate_to_dict(self, candidate: PersistentCandidate) -> Dict[str, Any]:
        """Convert candidate to dict for API response."""
        return {
            "id": candidate.id,
            "tmdb_id": candidate.tmdb_id,
            "media_type": candidate.media_type,
            "title": candidate.title,
            "year": candidate.year,
            "overview": candidate.overview[:200] if candidate.overview else None,  # Truncate for display
            "genres": candidate.genres,
            "poster_path": candidate.poster_path,
            "backdrop_path": candidate.backdrop_path,
            "vote_average": candidate.vote_average,
            "vote_count": candidate.vote_count,
            "popularity": candidate.popularity
        }
        
    def _update_user_vectors(self, winner_id: int, loser_id: int, winner: str) -> None:
        """Update user preference vectors based on judgment using proper vector arithmetic.
        
        Implements immediate personalization updates:
        1. Update embedding vector: user_vector <- normalize(user_vector + α * (vec_winner - vec_loser))
        2. Update genre/decade weights for interpretability
        3. Store pairwise outcome for analytics
        
        Args:
            winner_id: Candidate A ID
            loser_id: Candidate B ID
            winner: 'a', 'b', 'both', or 'neither' indicating choice
        """
        import numpy as np
        
        actual_winner_id = winner_id if winner == 'a' else loser_id
        actual_loser_id = loser_id if winner == 'a' else winner_id
        
        # Fetch candidates
        winner_cand = self.db.query(PersistentCandidate).filter_by(id=actual_winner_id).first()
        loser_cand = self.db.query(PersistentCandidate).filter_by(id=actual_loser_id).first()
        
        if not winner_cand or not loser_cand:
            logger.warning(f"Could not find candidates for vector update: winner={actual_winner_id}, loser={actual_loser_id}")
            return
        
        # Learning rate (alpha parameter from spec: 0.05-0.12, using 0.08)
        alpha = 0.08
        
        try:
            # 1. UPDATE EMBEDDING VECTOR (proper vector arithmetic)
            vector_key = f"user_vector:{self.user_id}"
            
            # Get binary data without UTF-8 decoding by using connection directly
            existing_vector = None
            try:
                # Get connection from pool and send raw command
                conn = self.redis.connection_pool.get_connection('GET')
                try:
                    conn.send_command('GET', vector_key)
                    # Read response without decoding
                    existing_vector = conn.read_response(disable_decoding=True)
                finally:
                    self.redis.connection_pool.release(conn)
            except Exception as e:
                logger.debug(f"Failed to get existing vector: {e}")
                existing_vector = None
            
            # Get or initialize user vector (384-dim for MiniLM)
            if existing_vector and existing_vector != b'':
                user_vec = np.frombuffer(existing_vector, dtype=np.float32)
            else:
                user_vec = np.zeros(384, dtype=np.float32)  # MiniLM dimension
            
            # Get candidate embedding vectors (from FAISS or compute on-demand)
            winner_vec = self._get_or_compute_embedding(winner_cand)
            loser_vec = self._get_or_compute_embedding(loser_cand)
            
            if winner_vec is not None and loser_vec is not None:
                # Apply update based on choice
                if winner in ['a', 'b']:
                    # Standard pairwise: user_vector += α * (vec_winner - vec_loser)
                    delta = alpha * (winner_vec - loser_vec)
                    user_vec = user_vec + delta
                elif winner == 'both':
                    # Both liked: move toward average of both (weighted)
                    avg_vec = 0.5 * (winner_vec + loser_vec)
                    delta = alpha * 0.6 * (avg_vec - user_vec)  # Reduced alpha for "both"
                    user_vec = user_vec + delta
                elif winner == 'neither':
                    # Neither liked: move away from average (repulsion)
                    avg_vec = 0.5 * (winner_vec + loser_vec)
                    delta = alpha * 0.4 * (avg_vec - user_vec)  # Small magnitude
                    user_vec = user_vec - delta
                
                # Normalize to unit length (as per spec)
                norm = np.linalg.norm(user_vec)
                if norm > 1e-6:
                    user_vec = user_vec / norm
                
                # Store updated vector with 90-day expiry
                self.redis.setex(vector_key, 60 * 60 * 24 * 90, user_vec.tobytes())
                logger.debug(f"Updated user {self.user_id} embedding vector (norm={norm:.4f})")
            
            # 2. UPDATE GENRE/DECADE WEIGHTS (for interpretability and persona)
            profile_key = f"user_pairwise_profile:{self.user_id}"
            existing_profile = self.redis.get(profile_key)
            
            if existing_profile:
                profile = json.loads(existing_profile)
            else:
                profile = {
                    "genre_weights": {},
                    "decade_weights": {},
                    "language_weights": {},
                    "obscurity_preference": 0.5,
                    "freshness_preference": 0.5,
                    "judgment_count": 0
                }
            
            # Genre boosting (for persona generation)
            boost_factor = 0.1
            
            if winner in ['a', 'b']:
                if winner_cand.genres:
                    winner_genres = json.loads(winner_cand.genres) if isinstance(winner_cand.genres, str) else winner_cand.genres
                    if isinstance(winner_genres, list):
                        for genre in winner_genres:
                            genre = genre.strip().lower()
                            profile["genre_weights"][genre] = profile["genre_weights"].get(genre, 0) + boost_factor
                
                if loser_cand.genres:
                    loser_genres = json.loads(loser_cand.genres) if isinstance(loser_cand.genres, str) else loser_cand.genres
                    if isinstance(loser_genres, list):
                        for genre in loser_genres:
                            genre = genre.strip().lower()
                            profile["genre_weights"][genre] = profile["genre_weights"].get(genre, 0) - boost_factor * 0.5
            
            # Decade boosting
            if winner_cand.year:
                winner_decade = (winner_cand.year // 10) * 10
                profile["decade_weights"][str(winner_decade)] = profile["decade_weights"].get(str(winner_decade), 0) + boost_factor
            
            # Language boosting
            if winner_cand.original_language:
                lang = winner_cand.original_language.lower()
                profile["language_weights"][lang] = profile["language_weights"].get(lang, 0) + boost_factor
            
            # Obscurity/Freshness preferences
            if winner_cand.vote_count and loser_cand.vote_count:
                if winner_cand.vote_count < loser_cand.vote_count:
                    profile["obscurity_preference"] = min(1.0, profile["obscurity_preference"] + boost_factor * 0.5)
                else:
                    profile["obscurity_preference"] = max(0.0, profile["obscurity_preference"] - boost_factor * 0.5)
            
            if winner_cand.year and loser_cand.year:
                if winner_cand.year > loser_cand.year:
                    profile["freshness_preference"] = min(1.0, profile["freshness_preference"] + boost_factor * 0.5)
                else:
                    profile["freshness_preference"] = max(0.0, profile["freshness_preference"] - boost_factor * 0.5)
            
            profile["judgment_count"] += 1
            
            # Store updated profile with 30-day expiry
            self.redis.setex(profile_key, 60 * 60 * 24 * 30, json.dumps(profile))
            
            logger.info(f"Updated user {self.user_id} vectors: {profile['judgment_count']} total judgments")
            
        except Exception as e:
            logger.error(f"Failed to update user vectors: {e}", exc_info=True)
    
    def _get_or_compute_embedding(self, candidate: PersistentCandidate) -> Optional[Any]:
        """Get embedding vector for candidate from FAISS or compute on-demand."""
        import numpy as np
        
        try:
            # Try to get from persistent_candidates.embedding first (stored as bytes)
            if candidate.embedding:
                from app.services.ai_engine.faiss_index import deserialize_embedding
                vec = deserialize_embedding(candidate.embedding)
                if vec is not None:
                    return np.array(vec, dtype=np.float32)
            
            # Try to get from FAISS index using tmdb_id (NOT trakt_id - spec violation!)
            from app.services.ai_engine.faiss_index import get_embedding_from_index
            if candidate.tmdb_id:
                vec = get_embedding_from_index(candidate.tmdb_id, candidate.media_type)
                if vec is not None:
                    return np.array(vec, dtype=np.float32)
            
            # Fallback: compute embedding on-demand
            from app.services.ai_engine.embeddings import encode_text
            text = f"{candidate.title} {candidate.overview or ''}"
            vec = encode_text(text)
            if vec is not None:
                return np.array(vec, dtype=np.float32)
            
            return None
            
        except Exception as e:
            logger.debug(f"Failed to get embedding for candidate {candidate.id}: {e}")
            return None
            
    def get_user_profile(self) -> Dict[str, Any]:
        """Get current user preference profile from pairwise training.
        
        Returns:
            Dict with genre_weights, decade_weights, etc. or empty dict if not found
        """
        redis_key = f"user_pairwise_profile:{self.user_id}"
        try:
            profile_json = self.redis.get(redis_key)
            if profile_json:
                return json.loads(profile_json)
            return {
                "genre_weights": {},
                "decade_weights": {},
                "language_weights": {},
                "obscurity_preference": 0.5,
                "freshness_preference": 0.5,
                "judgment_count": 0
            }
        except Exception as e:
            logger.error(f"Failed to fetch user profile: {e}")
            return {}
