"""
Dual-index search utility for hybrid BGE + MiniLM FAISS retrieval.

Strategy:
1. Try BGE multi-vector search first (more sophisticated)
2. Fall back to MiniLM FAISS for items without BGE coverage
3. Merge and deduplicate results with score normalization

Multi-vector BGE aspects:
- embedding_base: Full metadata (title + overview + genres + keywords)
- embedding_title: Title and concept matching
- embedding_keywords: Thematic elements
- embedding_people: Cast and crew
- embedding_brands: Studios and networks
"""
import logging
import numpy as np
from typing import List, Dict, Any, Optional, Tuple
from sklearn.metrics.pairwise import cosine_similarity
from sqlalchemy.orm import Session

from app.models import BGEEmbedding, PersistentCandidate
from app.services.ai_engine.faiss_index import (
    load_index, search_index, deserialize_embedding, _l2_normalize
)

logger = logging.getLogger(__name__)


def weighted_avg(vectors: List[np.ndarray], weights: List[float]) -> Optional[np.ndarray]:
    """Compute weighted average of vectors."""
    if not vectors or not weights:
        return None
    
    vectors = [v for v in vectors if v is not None and len(v) > 0]
    if not vectors:
        return None
    
    # Normalize weights
    total_weight = sum(weights)
    if total_weight == 0:
        return None
    
    weights = [w / total_weight for w in weights]
    
    # Compute weighted average
    result = np.zeros_like(vectors[0], dtype=np.float32)
    for vec, weight in zip(vectors, weights):
        result += vec * weight
    
    return result


def build_user_profile_vectors(
    db: Session,
    user_id: int,
    recent_days: int = 90,
    max_items: int = 50
) -> Dict[str, Optional[np.ndarray]]:
    """
    Build multi-vector user profile from watch history using BGE embeddings.
    
    Returns dict with keys: 'base', 'title', 'keywords', 'people', 'brands'
    Each value is a weighted average vector or None if no data available.
    """
    from app.models import TraktWatchHistory
    from datetime import datetime, timedelta
    
    # Fetch recent watch history (use naive datetime since DB column is timezone-naive)
    cutoff = datetime.utcnow() - timedelta(days=recent_days)
    watches = db.query(TraktWatchHistory).filter(
        TraktWatchHistory.user_id == user_id,
        TraktWatchHistory.watched_at >= cutoff
    ).order_by(TraktWatchHistory.watched_at.desc()).limit(max_items).all()
    
    if not watches:
        logger.debug(f"[DualIndex] No recent watch history for user {user_id}")
        return {'base': None, 'title': None, 'keywords': None, 'people': None, 'brands': None}
    
    # Aggregate vectors per aspect
    vectors_by_aspect = {
        'base': [],
        'title': [],
        'keywords': [],
        'people': [],
        'brands': []
    }
    
    for watch in watches:
        bge_emb = db.query(BGEEmbedding).filter(
            BGEEmbedding.tmdb_id == watch.tmdb_id,
            BGEEmbedding.media_type == watch.media_type
        ).first()
        
        if not bge_emb:
            continue
        
        # Weight recent watches higher (exponential decay over 30 days)
        days_ago = (datetime.utcnow() - watch.watched_at).days
        weight = 1.0 / (1.0 + days_ago / 30.0)
        
        # Also boost by user rating if available
        if watch.user_trakt_rating:
            rating_boost = (watch.user_trakt_rating / 10.0) ** 2  # Square for stronger effect
            weight *= (1.0 + rating_boost)
        
        # Deserialize and collect embeddings
        if bge_emb.embedding_base:
            vec = deserialize_embedding(bge_emb.embedding_base)
            vectors_by_aspect['base'].append((vec, weight))
        
        if bge_emb.embedding_title:
            vec = deserialize_embedding(bge_emb.embedding_title)
            vectors_by_aspect['title'].append((vec, weight))
        
        if bge_emb.embedding_keywords:
            vec = deserialize_embedding(bge_emb.embedding_keywords)
            vectors_by_aspect['keywords'].append((vec, weight))
        
        if bge_emb.embedding_people:
            vec = deserialize_embedding(bge_emb.embedding_people)
            vectors_by_aspect['people'].append((vec, weight))
        
        if bge_emb.embedding_brands:
            vec = deserialize_embedding(bge_emb.embedding_brands)
            vectors_by_aspect['brands'].append((vec, weight))
    
    # Compute weighted averages
    profile_vectors = {}
    for aspect, vec_weight_pairs in vectors_by_aspect.items():
        if vec_weight_pairs:
            vectors = [v[0] for v in vec_weight_pairs]
            weights = [v[1] for v in vec_weight_pairs]
            profile_vectors[aspect] = weighted_avg(vectors, weights)
        else:
            profile_vectors[aspect] = None
    
    logger.info(f"[DualIndex] Built user profile vectors: {sum(1 for v in profile_vectors.values() if v is not None)}/5 aspects")
    return profile_vectors


def search_with_bge_multivector(
    db: Session,
    user_profile_vectors: Dict[str, Optional[np.ndarray]],
    candidate_pool: List[PersistentCandidate],
    weights: Optional[Dict[str, float]] = None
) -> List[Tuple[PersistentCandidate, float, Dict[str, float]]]:
    """
    Score candidate pool using BGE multi-vector similarity.
    
    Args:
        db: Database session
        user_profile_vectors: User profile vectors per aspect (from build_user_profile_vectors)
        candidate_pool: List of PersistentCandidate objects to score
        weights: Aspect weights (default: title=0.25, keywords=0.35, people=0.25, brands=0.15)
    
    Returns:
        List of (candidate, final_score, score_breakdown) tuples
    """
    if weights is None:
        weights = {
            'base': 0.20,
            'title': 0.25,
            'keywords': 0.30,
            'people': 0.20,
            'brands': 0.05
        }
    
    scored_items = []
    
    for candidate in candidate_pool:
        bge_emb = db.query(BGEEmbedding).filter(
            BGEEmbedding.tmdb_id == candidate.tmdb_id,
            BGEEmbedding.media_type == candidate.media_type
        ).first()
        
        if not bge_emb:
            # No BGE embedding - will need MiniLM fallback
            scored_items.append((candidate, 0.0, {}))
            continue
        
        # Compute cosine similarity per aspect
        scores = {}
        
        if bge_emb.embedding_base and user_profile_vectors.get('base') is not None:
            item_vec = deserialize_embedding(bge_emb.embedding_base)
            sim = cosine_similarity([user_profile_vectors['base']], [item_vec])[0][0]
            scores['base'] = float(sim)
        
        if bge_emb.embedding_title and user_profile_vectors.get('title') is not None:
            item_vec = deserialize_embedding(bge_emb.embedding_title)
            sim = cosine_similarity([user_profile_vectors['title']], [item_vec])[0][0]
            scores['title'] = float(sim)
        
        if bge_emb.embedding_keywords and user_profile_vectors.get('keywords') is not None:
            item_vec = deserialize_embedding(bge_emb.embedding_keywords)
            sim = cosine_similarity([user_profile_vectors['keywords']], [item_vec])[0][0]
            scores['keywords'] = float(sim)
        
        if bge_emb.embedding_people and user_profile_vectors.get('people') is not None:
            item_vec = deserialize_embedding(bge_emb.embedding_people)
            sim = cosine_similarity([user_profile_vectors['people']], [item_vec])[0][0]
            scores['people'] = float(sim)
        
        if bge_emb.embedding_brands and user_profile_vectors.get('brands') is not None:
            item_vec = deserialize_embedding(bge_emb.embedding_brands)
            sim = cosine_similarity([user_profile_vectors['brands']], [item_vec])[0][0]
            scores['brands'] = float(sim)
        
        # Compute weighted average score
        if not scores:
            final_score = 0.0
        else:
            # Normalize weights for available aspects
            available_weights = {k: weights.get(k, 0) for k in scores.keys()}
            total_weight = sum(available_weights.values())
            if total_weight > 0:
                final_score = sum(scores[k] * available_weights[k] for k in scores.keys()) / total_weight
            else:
                final_score = 0.0
        
        scored_items.append((candidate, final_score, scores))
    
    return scored_items


def hybrid_search(
    db: Session,
    user_id: int,
    candidate_pool: List[PersistentCandidate],
    top_k: int = 20,
    bge_weight: float = 0.7,
    faiss_weight: float = 0.3
) -> List[Dict[str, Any]]:
    """
    Hybrid search using both BGE multi-vector and MiniLM FAISS fallback.
    
    Strategy:
    1. Build user profile from watch history
    2. Score candidates with BGE multi-vector (for items with BGE embeddings)
    3. Score candidates with MiniLM FAISS (for items without BGE)
    4. Blend scores using weights and return top K
    
    Args:
        db: Database session
        user_id: User ID
        candidate_pool: List of candidates to score
        top_k: Number of results to return
        bge_weight: Weight for BGE score (0-1)
        faiss_weight: Weight for FAISS score (0-1)
    
    Returns:
        List of scored items with metadata
    """
    # Build user profile vectors
    user_profile_vectors = build_user_profile_vectors(db, user_id)
    
    # Check if we have any profile vectors
    has_bge_profile = any(v is not None for v in user_profile_vectors.values())
    
    if not has_bge_profile:
        logger.warning(f"[DualIndex] No BGE profile for user {user_id}, falling back to FAISS-only")
        # Fall back to pure FAISS search
        return _faiss_only_search(db, user_id, candidate_pool, top_k)
    
    # Score with BGE multi-vector
    bge_scored = search_with_bge_multivector(db, user_profile_vectors, candidate_pool)
    
    # Identify items needing FAISS fallback (score=0.0 from BGE)
    items_with_bge = []
    items_need_faiss = []
    
    for candidate, score, breakdown in bge_scored:
        if score > 0.0:
            items_with_bge.append((candidate, score, breakdown))
        else:
            items_need_faiss.append(candidate)
    
    logger.info(f"[DualIndex] BGE coverage: {len(items_with_bge)}/{len(candidate_pool)} items")
    
    # Get FAISS scores for fallback items
    faiss_scores = {}
    if items_need_faiss:
        faiss_scores = _faiss_score_candidates(db, user_id, items_need_faiss)
    
    # Blend scores
    final_scored = []
    
    for candidate, bge_score, breakdown in items_with_bge:
        # Pure BGE items
        final_score = bge_score * bge_weight
        final_scored.append({
            'candidate': candidate,
            'score': final_score,
            'bge_score': bge_score,
            'faiss_score': None,
            'score_breakdown': breakdown,
            'source': 'bge'
        })
    
    for candidate in items_need_faiss:
        # FAISS fallback items
        faiss_score = faiss_scores.get(candidate.trakt_id, 0.0)
        final_score = faiss_score * faiss_weight  # Lower weight since no BGE
        final_scored.append({
            'candidate': candidate,
            'score': final_score,
            'bge_score': None,
            'faiss_score': faiss_score,
            'score_breakdown': {},
            'source': 'faiss'
        })
    
    # Sort by final score and return top K
    final_scored.sort(key=lambda x: x['score'], reverse=True)
    return final_scored[:top_k]


def _faiss_only_search(
    db: Session,
    user_id: int,
    candidate_pool: List[PersistentCandidate],
    top_k: int
) -> List[Dict[str, Any]]:
    """Fallback to pure FAISS search when no BGE profile available."""
    faiss_scores = _faiss_score_candidates(db, user_id, candidate_pool)
    
    scored = []
    for candidate in candidate_pool:
        score = faiss_scores.get(candidate.trakt_id, 0.0)
        scored.append({
            'candidate': candidate,
            'score': score,
            'bge_score': None,
            'faiss_score': score,
            'score_breakdown': {},
            'source': 'faiss_only'
        })
    
    scored.sort(key=lambda x: x['score'], reverse=True)
    return scored[:top_k]


def _faiss_score_candidates(
    db: Session,
    user_id: int,
    candidates: List[PersistentCandidate]
) -> Dict[int, float]:
    """
    Score candidates using MiniLM FAISS index.
    Returns dict of {trakt_id: score}.
    """
    from app.models import TraktWatchHistory
    from datetime import datetime, timedelta
    
    # Build user embedding from recent watches (use naive datetime since DB column is timezone-naive)
    cutoff = datetime.utcnow() - timedelta(days=90)
    watches = db.query(TraktWatchHistory).filter(
        TraktWatchHistory.user_id == user_id,
        TraktWatchHistory.watched_at >= cutoff
    ).order_by(TraktWatchHistory.watched_at.desc()).limit(50).all()
    
    if not watches:
        return {}
    
    # Aggregate watched item embeddings
    watch_embeddings = []
    watch_weights = []
    
    for watch in watches:
        candidate = db.query(PersistentCandidate).filter(
            PersistentCandidate.tmdb_id == watch.tmdb_id,
            PersistentCandidate.media_type == watch.media_type
        ).first()
        
        if candidate and candidate.embedding:
            emb = deserialize_embedding(candidate.embedding)
            days_ago = (datetime.utcnow() - watch.watched_at).days
            weight = 1.0 / (1.0 + days_ago / 30.0)
            
            if watch.user_trakt_rating:
                rating_boost = (watch.user_trakt_rating / 10.0) ** 2
                weight *= (1.0 + rating_boost)
            
            watch_embeddings.append(emb)
            watch_weights.append(weight)
    
    if not watch_embeddings:
        return {}
    
    # Compute user profile embedding
    user_embedding = weighted_avg(watch_embeddings, watch_weights)
    if user_embedding is None:
        return {}
    
    # Score each candidate
    scores = {}
    for candidate in candidates:
        if not candidate.embedding:
            continue
        
        candidate_emb = deserialize_embedding(candidate.embedding)
        sim = cosine_similarity([user_embedding], [candidate_emb])[0][0]
        scores[candidate.trakt_id] = float(sim)
    
    return scores
