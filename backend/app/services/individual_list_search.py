"""
individual_list_search.py

Hybrid search service for Individual Lists combining:
1. BGE Multi-Vector + FAISS semantic search (dual-index)
2. ElasticSearch literal fuzzy search with query enhancement (mood/tone/theme)

Results are merged, deduplicated, and enriched with metadata.
Enhanced with natural language understanding and intelligent boosting.
"""
import json
import logging
from typing import List, Dict, Any, Optional
import numpy as np

from app.services.elasticsearch_client import get_elasticsearch_client
from app.services.ai_engine.embeddings import EmbeddingService
from app.services.ai_engine.faiss_index import load_index
from app.services.ai_engine.dual_index_search import hybrid_search
from app.services.ai_engine.query_enhancer import QueryEnhancer
from app.services.fit_scoring import FitScorer
from app.core.database import SessionLocal
from app.models import PersistentCandidate
from sqlalchemy import or_
from app.core.redis_client import get_redis_sync

logger = logging.getLogger(__name__)

# Search configuration
MULTIVEC_TOP_K = 30  # Get top 30 from multi-vector search
ES_TOP_K = 12  # Get top 12 from ElasticSearch to reduce scoring work per query
FINAL_LIMIT = 50  # Return top 50 after merging


class IndividualListSearchService:
    """
    Hybrid search combining semantic (BGE multi-vector + FAISS) and literal (ElasticSearch) search.
    
    Enhanced Workflow:
    1. Run dual-index semantic search (BGE multi-vector → FAISS fallback)
    2. Extract query features (mood, tone, theme, people) via QueryEnhancer
    3. Run ElasticSearch with enhanced boosting
    4. Merge results with deduplication and intelligent scoring
    5. Enrich with full metadata from DB
    6. Apply fit scoring for user
    7. Return top N results with title matches prioritized
    """
    
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.embedding_service = EmbeddingService()
        self.fit_scorer = FitScorer(user_id)
        self.es_client = get_elasticsearch_client()
        self.query_enhancer = QueryEnhancer()
    
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
                cache_key = f"ilist:search:v3:user:{self.user_id}:mt:{media_type or 'any'}:q:{qsig[:80]}:n:{limit}:sf:{skip_fit_scoring}"
                cached = r.get(cache_key)
                if cached:
                    return json.loads(cached)
            except Exception:
                pass
        
        # Enhance query to extract mood/tone/theme/people
        enhanced = self.query_enhancer.enhance(query)
        logger.debug(f"Enhanced query: {enhanced}")
        
        # Run both searches with enhancements
        semantic_results = self._multivector_search(query, media_type, enhanced)
        es_results = self._elasticsearch_search(enhanced['cleaned_query'], media_type, enhanced)
        
        # Merge and deduplicate
        merged = self._merge_results(semantic_results, es_results)
        
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
        # Add exact/prefix title boost to improve queries like "Harry Potter"
        qnorm = (query or "").strip().lower()
        for candidate in scored:
            base_relevance = candidate.get('_search_score', 0.5)
            fit = candidate.get('fit_score', 0.5)

            # Title-based boosts applied post-enrichment
            try:
                title = (candidate.get('title') or "").strip().lower()
                otitle = (candidate.get('original_title') or "").strip().lower()
                boost = 0.0
                if qnorm:
                    if title == qnorm or otitle == qnorm:
                        boost += 0.4  # exact title match
                    elif title.startswith(qnorm) or otitle.startswith(qnorm):
                        boost += 0.25  # prefix match (common for series collections)
                    elif qnorm in title or qnorm in otitle:
                        boost += 0.1  # substring match
                relevance = min(1.0, base_relevance + boost)
            except Exception:
                relevance = base_relevance

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
    
    def _multivector_search(
        self,
        query: str,
        media_type: Optional[str] = None,
        enhanced: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Semantic search using dual-index (BGE multi-vector + FAISS fallback).
        
        Returns list of {tmdb_id, media_type, score} from semantic search.
        """
        try:
            # Use adaptive mode based on query length - quick for autocomplete
            is_autocomplete = len(query.strip()) < 4
            mode = 'auto' if is_autocomplete else 'full'
            
            # Build basic filters from enhanced query
            filters = {}
            if media_type:
                filters['media_type'] = media_type
            
            # Extract mood/theme focus for semantic boosting
            # If query asks for "dark thriller", boost those aspects in semantic search
            focus_aspects = []
            if enhanced:
                if enhanced.get('moods'):
                    focus_aspects.append('keywords')  # Moods often in keywords
                if enhanced.get('themes'):
                    focus_aspects.append('keywords')  # Themes in keywords
                if enhanced.get('people'):
                    focus_aspects.extend(['people', 'brands'])  # Focus on people/studios
            
            # Get candidate pool from database based on query text
            # Use a broad search to get candidates, then let hybrid_search score them
            db = SessionLocal()
            try:
                # Get candidates matching the query text (title, overview, keywords)
                # This is a preliminary filter before semantic scoring
                search_terms = query.lower().split()
                
                # Build query with broad matching
                query_obj = db.query(PersistentCandidate)
                
                if media_type:
                    query_obj = query_obj.filter(PersistentCandidate.media_type == media_type)
                
                # Match any search term in title or overview (broad retrieval)
                if search_terms:
                    conditions = []
                    for term in search_terms[:3]:  # Limit to first 3 terms for performance
                        conditions.append(PersistentCandidate.title.ilike(f'%{term}%'))
                        conditions.append(PersistentCandidate.overview.ilike(f'%{term}%'))
                    query_obj = query_obj.filter(or_(*conditions))
                
                # Get top 500 candidates for semantic scoring
                candidates = query_obj.limit(500).all()
                
                if not candidates:
                    logger.debug(f"No candidates found for query: {query}")
                    return []
                
                logger.debug(f"Found {len(candidates)} candidates for semantic scoring")
                
                # Call dual-index hybrid search with candidate pool
                results = hybrid_search(
                    db=db,
                    user_id=self.user_id,
                    candidate_pool=candidates,
                    top_k=MULTIVEC_TOP_K,
                    bge_weight=0.7,
                    faiss_weight=0.3
                )
            finally:
                db.close()
            
            # Convert to format expected by merge function
            # hybrid_search returns: {'candidate': PersistentCandidate, 'score': float, ...}
            formatted = []
            for item in results:
                candidate = item['candidate']
                formatted.append({
                    'tmdb_id': candidate.tmdb_id,
                    'media_type': candidate.media_type,
                    'faiss_score': item['score'],  # Use hybrid score
                    'source': item.get('source', 'hybrid')
                })
            
            logger.debug(f"Multi-vector search found {len(formatted)} results")
            return formatted
            
        except Exception as e:
            logger.error(f"Multi-vector search failed: {e}")
            return []
    
    def _elasticsearch_search(
        self,
        query: str,
        media_type: Optional[str] = None,
        enhanced: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Literal fuzzy search using ElasticSearch with query enhancement.
        
        Returns list of {tmdb_id, media_type, es_score} from ES.
        """
        try:
            # Skip ES for very short queries to keep autocomplete snappy
            if not query or len(query.strip()) < 3:
                return []
            if not self.es_client.is_connected():
                logger.warning("ElasticSearch not connected, skipping literal search")
                return []
            
            # Build enhanced filters for boosting
            es_filters = {}
            if enhanced:
                es_filters = self.query_enhancer.build_es_filters(enhanced)
            
            # Use the broader field set even for multi-word queries to avoid missing obvious matches
            results = self.es_client.search(
                query, 
                media_type, 
                limit=ES_TOP_K, 
                strict_titles_only=False,
                enhanced_filters=es_filters
            )
            
            # Normalize ES scores to 0-1 range
            if results:
                max_score = max(r['es_score'] for r in results)
                for r in results:
                    r['es_score'] = r['es_score'] / max_score if max_score > 0 else 0.5
                    r['source'] = 'elasticsearch'
            
            logger.debug(f"ElasticSearch found {len(results)} results (enhanced={bool(es_filters)})")
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
