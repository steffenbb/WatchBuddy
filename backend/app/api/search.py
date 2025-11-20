"""
search.py - Global search API endpoints
"""
from fastapi import APIRouter, Query, HTTPException
from typing import List
import logging
from app.services.individual_list_search import IndividualListSearchService
from app.api.individual_lists import SearchResultResponse

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/search", response_model=List[SearchResultResponse])
def global_search(
    q: str = Query(..., min_length=1, description="Search query"),
    user_id: int = Query(1),
    limit: int = Query(50, ge=1, le=200),
    media_type: str = Query(None, description="Filter by media type: 'movie' or 'tv'"),
    min_year: int = Query(None, description="Minimum release year"),
    max_year: int = Query(None, description="Maximum release year"),
    genre: str = Query(None, description="Filter by genre")
):
    """
    Global hybrid search across all content (FAISS + ElasticSearch).
    
    Combines:
    - Elasticsearch text search for relevance
    - FAISS semantic search for meaning
    - Manual filters for precise control
    """
    try:
        # Use the same search service (list_id not actually required for search logic)
        search_service = IndividualListSearchService(user_id=user_id)
        results = search_service.search(
            query=q, 
            limit=limit,
            skip_fit_scoring=True  # Skip fit scoring for global search
        )
        
        # Apply filters
        if media_type:
            normalized_type = 'tv' if media_type == 'show' else media_type
            results = [r for r in results if r.get('media_type') == normalized_type]
        
        if min_year:
            results = [r for r in results if r.get('year') and r.get('year') >= min_year]
        
        if max_year:
            results = [r for r in results if r.get('year') and r.get('year') <= max_year]
        
        if genre:
            genre_lower = genre.lower()
            results = [
                r for r in results 
                if r.get('genres') and genre_lower in str(r.get('genres')).lower()
            ]
        
        # Limit after filtering
        results = results[:limit]
        
        # Convert to response models
        responses = []
        for result in results:
            # Convert genres list to string if needed
            genres = result.get('genres')
            if isinstance(genres, list):
                genres = ', '.join(genres) if genres else None
            
            responses.append(SearchResultResponse(
                tmdb_id=result['tmdb_id'],
                media_type=result['media_type'],
                title=result['title'],
                original_title=result.get('original_title'),
                year=result.get('year'),
                overview=result.get('overview'),
                poster_path=result.get('poster_path'),
                backdrop_path=result.get('backdrop_path'),
                genres=genres,
                vote_average=result.get('vote_average'),
                vote_count=result.get('vote_count'),
                popularity=result.get('popularity'),
                fit_score=None,  # No fit scoring for global search
                relevance_score=result.get('relevance_score')
            ))
        
        logger.info(f"Global search for '{q}' returned {len(responses)} results")
        return responses
        
    except Exception as e:
        logger.error(f"Global search failed: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")
