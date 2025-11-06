"""
individual_list_search.py

Hybrid search service for Individual Lists combining:
1. FAISS semantic search (embeddings)
2. ElasticSearch literal fuzzy search (title, cast, keywords, etc.)

Results are merged, deduplicated, and enriched with metadata.
"""
import json
import logging
from typing import List, Dict, Any, Optional
import numpy as np

from app.services.elasticsearch_client import get_elasticsearch_client
from app.services.ai_engine.embeddings import EmbeddingService
from app.services.ai_engine.faiss_index import load_index
from app.services.fit_scoring import FitScorer
from app.core.database import SessionLocal
from app.models import PersistentCandidate
from sqlalchemy import or_
from app.core.redis_client import get_redis_sync

logger = logging.getLogger(__name__)

# Search configuration
FAISS_TOP_K = 30  # Get top 30 from semantic search
ES_TOP_K = 12  # Get top 12 from ElasticSearch to reduce scoring work per query
FINAL_LIMIT = 50  # Return top 50 after merging


class IndividualListSearchService:
    """
    Hybrid search combining semantic (FAISS) and literal (ElasticSearch) search.
    
    Workflow:
    1. Run FAISS semantic search on query embedding
    2. Run ElasticSearch fuzzy search on query text
    3. Merge results with deduplication (FAISS score + ES score)
    4. Enrich with full metadata from DB
    5. Apply fit scoring for user
    6. Return top N results sorted by combined relevance + fit score
    """
    
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.embedding_service = EmbeddingService()
        self.fit_scorer = FitScorer(user_id)
        self.es_client = get_elasticsearch_client()
    
    def search(
        self,
        query: str,
        media_type: Optional[str] = None,
        limit: int = FINAL_LIMIT,
        skip_fit_scoring: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Hybrid search combining FAISS + ElasticSearch.
        
        Args:
            query: Search query text
            media_type: Filter by 'movie' or 'show' (optional)
            limit: Max results to return
            skip_fit_scoring: Skip fit scoring for faster autocomplete (returns neutral 0.5)
            
        Returns:
            List of enriched candidates with fit scores, sorted by relevance
        """
        logger.info(f"Hybrid search for user {self.user_id}: '{query}' (media_type={media_type}, skip_fit={skip_fit_scoring})")
        # Short-lived cache (45s) to reduce repeated queries during typing
        r = None
        try:
            r = get_redis_sync()
        except Exception:
            r = None
        cache_key = None
        if r:
            try:
                qsig = (query or "").strip().lower()
                cache_key = f"ilist:search:v2:user:{self.user_id}:mt:{media_type or 'any'}:q:{qsig[:80]}:n:{limit}:sf:{skip_fit_scoring}"
                cached = r.get(cache_key)
                if cached:
                    return json.loads(cached)
            except Exception:
                pass
        
        # Run both searches in parallel conceptually
        faiss_results = self._faiss_search(query, media_type)
        es_results = self._elasticsearch_search(query, media_type)
        
        # Merge and deduplicate
        merged = self._merge_results(faiss_results, es_results)
        
        # Limit early to avoid over-fetching from DB
        merged = merged[:limit * 2]  # Get 2x limit to ensure we have enough after filtering
        
        # Enrich with full metadata from DB
        enriched = self._enrich_with_metadata(merged)
        
        # Apply fit scoring - but skip for autocomplete
        if skip_fit_scoring:
            # Just add neutral fit scores for fast autocomplete
            scored = enriched
            for item in scored:
                item['fit_score'] = 0.5
        else:
            # Full fit scoring with cached profile to speed things up
            try:
                scored = self.fit_scorer.score_candidates(enriched, use_cached_profile=True)
            except Exception as e:
                logger.warning(f"Fit scoring failed, using neutral scores: {e}")
                # Fallback: just add neutral fit scores
                scored = enriched
                for item in scored:
                    item['fit_score'] = 0.5
        
        # Sort by combined score: relevance * 0.7 + fit_score * 0.3 (prioritize search relevance)
        for candidate in scored:
            relevance = candidate.get('_search_score', 0.5)
            fit = candidate.get('fit_score', 0.5)
            candidate['relevance_score'] = relevance
            candidate['_final_score'] = relevance * 0.7 + fit * 0.3
        
        scored.sort(key=lambda x: x['_final_score'], reverse=True)
        
        # Return top N (and cache)
        out = scored[:limit]
        if r and cache_key:
            try:
                r.set(cache_key, json.dumps(out), ex=45)
            except Exception:
                pass
        return out
    
    def _faiss_search(
        self,
        query: str,
        media_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Semantic search using FAISS embeddings.
        
        Returns list of {tmdb_id, media_type, score} from FAISS.
        """
        try:
            # Load FAISS index first to fail fast if not available
            index, mapping = load_index()
            if index is None or mapping is None or index.ntotal == 0:
                logger.debug("FAISS index not available or empty, skipping semantic search")
                return []
            
            # Encode query
            query_embedding = self.embedding_service.encode_text(query)
            
            # Search
            query_embedding = query_embedding.astype(np.float32)
            query_embedding = query_embedding / (np.linalg.norm(query_embedding) + 1e-8)  # Normalize
            query_embedding = query_embedding.reshape(1, -1)
            
            distances, indices = index.search(query_embedding, FAISS_TOP_K)
            
            # Collect all valid trakt_ids first
            trakt_to_distance = {}
            for i, idx in enumerate(indices[0]):
                if idx == -1:  # No result
                    continue
                
                trakt_id = mapping.get(int(idx))
                if trakt_id is None:
                    continue
                
                trakt_to_distance[trakt_id] = float(distances[0][i])
            
            # Batch lookup all candidates in one query
            if trakt_to_distance:
                db = SessionLocal()
                try:
                    candidates = db.query(PersistentCandidate).filter(
                        PersistentCandidate.trakt_id.in_(list(trakt_to_distance.keys()))
                    ).all()
                    
                    results = []
                    for candidate in candidates:
                        # Apply media_type filter if specified
                        if media_type and candidate.media_type != media_type:
                            continue
                        # faiss returns a distance (lower is better). Convert to a similarity score in 0..1
                        # using similarity = 1 / (1 + distance) so higher is better and comparable to ES normalized scores.
                        raw_distance = trakt_to_distance[candidate.trakt_id]
                        faiss_similarity = 1.0 / (1.0 + raw_distance) if raw_distance is not None else 0.0

                        results.append({
                            'tmdb_id': candidate.tmdb_id,
                            'media_type': candidate.media_type,
                            'faiss_score': faiss_similarity,
                            'faiss_distance': raw_distance,
                            'source': 'faiss'
                        })
                finally:
                    db.close()
            else:
                results = []
            
            logger.debug(f"FAISS found {len(results)} results")
            return results
            
        except Exception as e:
            logger.error(f"FAISS search failed: {e}")
            return []
    
    def _elasticsearch_search(
        self,
        query: str,
        media_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Literal fuzzy search using ElasticSearch.
        
        Returns list of {tmdb_id, media_type, es_score} from ES.
        """
        try:
            # Skip ES for very short queries to keep autocomplete snappy
            if not query or len(query.strip()) < 3:
                return []
            if not self.es_client.is_connected():
                logger.warning("ElasticSearch not connected, skipping literal search")
                return []
            
            # Use the broader field set even for multi-word queries to avoid missing obvious matches
            results = self.es_client.search(query, media_type, limit=ES_TOP_K, strict_titles_only=False)
            
            # Normalize ES scores to 0-1 range
            if results:
                max_score = max(r['es_score'] for r in results)
                for r in results:
                    r['es_score'] = r['es_score'] / max_score if max_score > 0 else 0.5
                    r['source'] = 'elasticsearch'
            
            logger.debug(f"ElasticSearch found {len(results)} results")
            return results
            
        except Exception as e:
            logger.error(f"ElasticSearch search failed: {e}")
            return []
    
    def _merge_results(
        self,
        faiss_results: List[Dict[str, Any]],
        es_results: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Merge FAISS and ES results with deduplication.
        
        For duplicates (same tmdb_id + media_type), combine scores:
        - If from both sources: average the scores with 60% FAISS, 40% ES
        - If from one source: use that score
        
        Returns merged list sorted by combined score.
        """
        # Create lookup by (tmdb_id, media_type)
        merged_map = {}
        
        # Add FAISS results
        for result in faiss_results:
            key = (result['tmdb_id'], result['media_type'])
            merged_map[key] = {
                'tmdb_id': result['tmdb_id'],
                'media_type': result['media_type'],
                'faiss_score': result['faiss_score'],
                'es_score': None,
                'sources': ['faiss']
            }
        
        # Add/merge ES results
        for result in es_results:
            key = (result['tmdb_id'], result['media_type'])
            if key in merged_map:
                # Already have it from FAISS - merge scores
                merged_map[key]['es_score'] = result['es_score']
                merged_map[key]['sources'].append('elasticsearch')
            else:
                # New result from ES only
                merged_map[key] = {
                    'tmdb_id': result['tmdb_id'],
                    'media_type': result['media_type'],
                    'faiss_score': None,
                    'es_score': result['es_score'],
                    'sources': ['elasticsearch']
                }
        
        # Calculate combined search score
        merged_list = []
        for item in merged_map.values():
            faiss_score = item['faiss_score'] if item['faiss_score'] is not None else 0.3
            es_score = item['es_score'] if item['es_score'] is not None else 0.3
            
            # If from both sources, weight FAISS higher (semantic usually better)
            if len(item['sources']) == 2:
                combined_score = faiss_score * 0.6 + es_score * 0.4
            elif 'faiss' in item['sources']:
                combined_score = faiss_score
            else:
                combined_score = es_score
            
            item['_search_score'] = combined_score
            merged_list.append(item)
        
        # Sort by combined score
        merged_list.sort(key=lambda x: x['_search_score'], reverse=True)
        
        logger.debug(f"Merged to {len(merged_list)} unique results")
        return merged_list
    
    def _enrich_with_metadata(
        self,
        results: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Enrich results with full metadata from persistent_candidates.
        
        Adds: title, year, poster_path, backdrop_path, genres, overview, popularity, etc.
        """
        if not results:
            return []
        
        # Extract tmdb_ids and media_types
        tmdb_ids = [r['tmdb_id'] for r in results]
        
        # Fetch from DB
        db = SessionLocal()
        try:
            candidates = db.query(PersistentCandidate).filter(
                PersistentCandidate.tmdb_id.in_(tmdb_ids),
                PersistentCandidate.active == True
            ).all()
            
            # Create lookup
            candidate_map = {
                (c.tmdb_id, c.media_type): c
                for c in candidates
            }
            
            # Enrich results
            enriched = []
            for result in results:
                key = (result['tmdb_id'], result['media_type'])
                candidate = candidate_map.get(key)
                
                if not candidate:
                    logger.warning(f"No candidate found for tmdb_id={result['tmdb_id']}, media_type={result['media_type']}")
                    continue
                
                # Parse JSON fields
                genres = []
                try:
                    genres = json.loads(candidate.genres) if candidate.genres else []
                except:
                    pass
                
                enriched_result = {
                    'tmdb_id': candidate.tmdb_id,
                    'trakt_id': candidate.trakt_id,
                    'media_type': candidate.media_type,
                    'title': candidate.title,
                    'original_title': candidate.original_title,
                    'year': candidate.year,
                    'overview': candidate.overview,
                    'poster_path': candidate.poster_path,
                    'backdrop_path': candidate.backdrop_path,
                    'genres': genres,
                    'popularity': candidate.popularity,
                    'vote_average': candidate.vote_average,
                    '_search_score': result['_search_score'],
                    '_sources': result['sources']
                }
                enriched.append(enriched_result)
            
            return enriched
            
        finally:
            db.close()
