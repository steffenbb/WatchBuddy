"""
Similar items service using FAISS semantic search.

Finds similar movies/shows using embedding-based similarity search.
Uses both the main FAISS HNSW index and BGE index for comprehensive results.
Integrates user preferences to personalize recommendations.
"""
import logging
import numpy as np
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session

from app.services.ai_engine.faiss_index import load_index, search_index, deserialize_embedding, _l2_normalize
from app.services.ai_engine.bge_index import BGEIndex
from app.models import PersistentCandidate, UserRating, BGEEmbedding
from app.core.config import settings

logger = logging.getLogger(__name__)


class SimilarItemsService:
    """Find similar items using dual-index FAISS semantic search with user preference integration."""
    
    def __init__(self, db: Session, user_id: int = 1):
        self.db = db
        self.user_id = user_id
        self.bge_index = None
        try:
            self.bge_index = BGEIndex(settings.ai_bge_index_dir)
            if self.bge_index.is_available:
                self.bge_index.load()
                logger.info("BGE index loaded for similar items search")
        except Exception as e:
            logger.warning(f"BGE index not available: {e}")
    
    def find_similar(
        self,
        tmdb_id: int,
        media_type: str,
        top_k: int = 20,
        same_type_only: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Find similar items using FAISS semantic search.
        
        Args:
            tmdb_id: TMDB ID of the source item
            media_type: 'movie' or 'show'
            top_k: Number of similar items to return (default 20)
            same_type_only: Only return items of the same media type
        
        Returns:
            List of similar items with metadata and similarity scores
        """
        try:
            # Get source item with embedding
            source = self.db.query(PersistentCandidate).filter_by(
                tmdb_id=tmdb_id,
                media_type=media_type
            ).first()
            
            if not source:
                logger.warning(f"Source item not found: {media_type}/{tmdb_id}")
                return []
            
            if not source.embedding:
                logger.warning(f"Source item has no embedding: {media_type}/{tmdb_id}")
                return []
            
            # Deserialize and normalize embedding
            query_vec = deserialize_embedding(source.embedding)
            query_vec = _l2_normalize(query_vec.reshape(1, -1)).flatten()
            
            # Load FAISS index and search
            try:
                index, mapping = load_index()
            except Exception as e:
                logger.error(f"Failed to load FAISS index: {e}")
                return []
            
            # Search for similar items (get extra to filter out source and wrong types)
            search_k = top_k * 5 if same_type_only else top_k + 1  # Increased multiplier for better filtering
            indices, distances = search_index(index, query_vec, top_k=search_k)
            
            # Convert distances to similarity scores (L2 distance on normalized vectors → cosine)
            # Distance 0 = identical, distance 2 = opposite
            # Convert to similarity: 1 - (distance/2) = cosine similarity
            similarities = [1.0 - (dist / 2.0) for dist in distances]
            
            # Get trakt_ids from mapping
            trakt_ids = []
            scores = []
            for idx, sim in zip(indices, similarities):
                if idx in mapping:
                    trakt_ids.append(mapping[idx])
                    scores.append(sim)
            
            if not trakt_ids:
                logger.warning(f"No valid trakt_ids found in FAISS results")
                return []
            
            # Fetch items from database (exclude adult content)
            # FAISS mapping uses COALESCE(trakt_id, tmdb_id), so we need to match on both
            from sqlalchemy import or_, and_
            import json
            candidates = self.db.query(PersistentCandidate).filter(
                or_(
                    PersistentCandidate.trakt_id.in_(trakt_ids),
                    PersistentCandidate.tmdb_id.in_(trakt_ids)
                ),
                PersistentCandidate.is_adult == False
            ).all()
            
            # Parse source genres and keywords for boosting
            source_genres = set()
            source_keywords = set()
            try:
                if source.genres:
                    source_genres = set(g.lower() for g in json.loads(source.genres))
            except:
                pass
            try:
                if source.keywords:
                    source_keywords = set(k.lower() for k in json.loads(source.keywords)[:10])  # Top 10 keywords
            except:
                pass
            
            # Create lookup by COALESCE(trakt_id, tmdb_id) to match FAISS mapping logic
            candidates_by_id = {}
            for c in candidates:
                lookup_id = c.trakt_id if c.trakt_id else c.tmdb_id
                candidates_by_id[lookup_id] = c
            
            # Build results preserving order and scores
            results = []
            for faiss_id, score in zip(trakt_ids, scores):
                if faiss_id not in candidates_by_id:
                    continue
                
                candidate = candidates_by_id[faiss_id]
                
                # Skip source item
                if candidate.tmdb_id == tmdb_id and candidate.media_type == media_type:
                    continue
                
                # Filter by media type if requested
                if same_type_only and candidate.media_type != media_type:
                    continue
                
                # Apply genre/keyword boosting to similarity score
                boosted_score = score
                try:
                    # Genre overlap boost (up to +0.15)
                    if source_genres:
                        candidate_genres = set()
                        if candidate.genres:
                            candidate_genres = set(g.lower() for g in json.loads(candidate.genres))
                        genre_overlap = len(source_genres & candidate_genres) / len(source_genres) if source_genres else 0
                        boosted_score += genre_overlap * 0.15
                    
                    # Keyword overlap boost (up to +0.10)
                    if source_keywords:
                        candidate_keywords = set()
                        if candidate.keywords:
                            candidate_keywords = set(k.lower() for k in json.loads(candidate.keywords)[:10])
                        keyword_overlap = len(source_keywords & candidate_keywords) / len(source_keywords) if source_keywords else 0
                        boosted_score += keyword_overlap * 0.10
                    
                    # Cap at 1.0
                    boosted_score = min(1.0, boosted_score)
                except:
                    pass  # Use unboosted score on error
                
                # Build result
                results.append({
                    'tmdb_id': candidate.tmdb_id,
                    'media_type': candidate.media_type,
                    'title': candidate.title,
                    'original_title': candidate.original_title,
                    'year': candidate.year,
                    'overview': candidate.overview,
                    'poster_path': candidate.poster_path,
                    'backdrop_path': candidate.backdrop_path,
                    'genres': candidate.genres,
                    'vote_average': candidate.vote_average,
                    'vote_count': candidate.vote_count,
                    'popularity': candidate.popularity,
                    'similarity_score': round(boosted_score, 3)
                })
            
            # Re-sort by boosted scores
            results.sort(key=lambda x: x['similarity_score'], reverse=True)
            
            # Return top K after sorting
            results = results[:top_k]
            
            logger.info(f"Found {len(results)} similar items for {media_type}/{tmdb_id}")
            return results
            
        except Exception as e:
            logger.error(f"Error finding similar items for {media_type}/{tmdb_id}: {e}", exc_info=True)
            return []
