"""
individual_lists.py

API router for Individual Lists feature.
Endpoints for CRUD operations, search, suggestions, and Trakt sync.
"""
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from datetime import datetime

from app.core.database import SessionLocal
from app.models import IndividualList, IndividualListItem
from app.services.individual_list_search import IndividualListSearchService
from app.services.individual_list_suggestions import IndividualListSuggestionsService
from app.services.individual_list_trakt_sync import IndividualListTraktSync
from app.utils.timezone import utc_now

import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/individual-lists", tags=["individual-lists"])


# Pydantic Schemas

class CreateListRequest(BaseModel):
    """Request to create a new Individual List."""
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=1000)
    is_public: bool = False
    user_id: int = 1


class UpdateListRequest(BaseModel):
    """Request to update list metadata."""
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    is_public: Optional[bool] = None


class AddItemsRequest(BaseModel):
    """Request to add items to a list."""
    items: List[Dict[str, Any]] = Field(..., description="List of items with tmdb_id, media_type, title, etc.")
    user_id: int = 1


class ReorderItemsRequest(BaseModel):
    """Request to reorder list items."""
    item_ids: List[int] = Field(..., description="Ordered list of item IDs")
    user_id: int = 1


class SyncTraktRequest(BaseModel):
    """Request to sync list to Trakt."""
    user_id: int = 1


class ListItemResponse(BaseModel):
    """Response model for a list item."""
    id: int
    tmdb_id: int
    trakt_id: Optional[int]
    media_type: str
    title: str
    original_title: Optional[str]
    year: Optional[int]
    overview: Optional[str]
    poster_path: Optional[str]
    backdrop_path: Optional[str]
    genres: Optional[str]
    order_index: float
    fit_score: Optional[float]
    added_at: datetime
    metadata_json: Optional[Dict[str, Any]]
    # Enriched fields for watched status (from Trakt all-time history)
    watched: Optional[bool] = None
    watched_at: Optional[str] = None
    
    class Config:
        orm_mode = True


class ListResponse(BaseModel):
    """Response model for an Individual List."""
    id: int
    name: str
    description: Optional[str]
    created_at: datetime
    updated_at: datetime
    trakt_list_id: Optional[str]
    trakt_synced_at: Optional[datetime]
    is_public: bool
    item_count: Optional[int] = None
    items: Optional[List[ListItemResponse]] = None
    poster_path: Optional[str] = None
    
    class Config:
        orm_mode = True


class SearchResultResponse(BaseModel):
    """Response model for search results."""
    tmdb_id: int
    media_type: str
    title: str
    original_title: Optional[str]
    year: Optional[int]
    overview: Optional[str]
    poster_path: Optional[str]
    backdrop_path: Optional[str]
    genres: Optional[str]
    vote_average: Optional[float]
    vote_count: Optional[int]
    popularity: Optional[float]
    fit_score: Optional[float]
    relevance_score: Optional[float]


class SuggestionResponse(BaseModel):
    """Response model for suggestions."""
    tmdb_id: int
    media_type: str
    title: str
    original_title: Optional[str]
    year: Optional[int]
    overview: Optional[str]
    poster_path: Optional[str]
    backdrop_path: Optional[str]
    genres: Optional[str]
    vote_average: Optional[float]
    vote_count: Optional[int]
    popularity: Optional[float]
    fit_score: Optional[float]
    similarity_score: Optional[float]
    is_high_fit: bool


class SyncStatusResponse(BaseModel):
    """Response model for Trakt sync status."""
    success: bool
    message: str
    trakt_list_id: Optional[str]
    items_added: Optional[int]
    items_removed: Optional[int]
    items_failed: Optional[int]
    errors: List[str]


# Dependency for database session

def get_db():
    """Dependency for database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# API Endpoints

@router.post("/", response_model=ListResponse, status_code=201)
def create_list(request: CreateListRequest, db=Depends(get_db)):
    """Create a new Individual List."""
    try:
        new_list = IndividualList(
            user_id=request.user_id,
            name=request.name,
            description=request.description,
            is_public=request.is_public,
            created_at=utc_now(),
            updated_at=utc_now()
        )
        
        db.add(new_list)
        db.commit()
        db.refresh(new_list)
        
        logger.info(f"Created Individual List {new_list.id}: {new_list.name}")
        
        # Return with item_count
        response = ListResponse.from_orm(new_list)
        response.item_count = 0
        return response
        
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to create list: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create list: {str(e)}")


@router.get("/", response_model=List[ListResponse])
def get_all_lists(user_id: int = Query(1), db=Depends(get_db)):
    """Get all Individual Lists for a user."""
    try:
        lists = db.query(IndividualList).filter(
            IndividualList.user_id == user_id
        ).order_by(IndividualList.updated_at.desc()).all()
        
        # Add item counts
        responses = []
        for lst in lists:
            item_count = db.query(IndividualListItem).filter(
                IndividualListItem.list_id == lst.id
            ).count()
            
            response = ListResponse.from_orm(lst)
            response.item_count = item_count
            responses.append(response)
        
        return responses
        
    except Exception as e:
        logger.error(f"Failed to get lists: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get lists: {str(e)}")


@router.get("/{list_id}", response_model=ListResponse)
def get_list(list_id: int, user_id: int = Query(1), db=Depends(get_db)):
    """Get a single Individual List with all items."""
    try:
        lst = db.query(IndividualList).filter(
            IndividualList.id == list_id,
            IndividualList.user_id == user_id
        ).first()
        
        if not lst:
            raise HTTPException(status_code=404, detail="List not found")
        
        # Get items ordered by order_index
        items = db.query(IndividualListItem).filter(
            IndividualListItem.list_id == list_id
        ).order_by(IndividualListItem.order_index).all()
        
        # Enrich with watched status using Trakt all-time watched dicts (cached in Redis)
        watched_movies: Dict[int, Dict[str, Any]] = {}
        watched_shows: Dict[int, Dict[str, Any]] = {}
        try:
            from app.services.trakt_client import TraktClient
            import asyncio as _asyncio
            tc = TraktClient(user_id=user_id)
            watched_movies = _asyncio.run(tc.get_watched_status("movies")) or {}
            watched_shows = _asyncio.run(tc.get_watched_status("shows")) or {}
        except Exception:
            # If Trakt isn't configured or rate-limited, proceed without watched flags
            watched_movies, watched_shows = {}, {}

        # Build response items with watched flags
        items_out: List[ListItemResponse] = []
        for it in items:
            watched = False
            watched_at = None
            if it.trakt_id:
                if it.media_type == "movie":
                    st = watched_movies.get(it.trakt_id)
                    if st:
                        watched = True
                        watched_at = st.get("watched_at")
                elif it.media_type == "show":
                    st = watched_shows.get(it.trakt_id)
                    if st:
                        watched = True
                        watched_at = st.get("watched_at")
            items_out.append(
                ListItemResponse(
                    id=it.id,
                    tmdb_id=it.tmdb_id,
                    trakt_id=it.trakt_id,
                    media_type=it.media_type,
                    title=it.title,
                    original_title=it.original_title,
                    year=it.year,
                    overview=it.overview,
                    poster_path=it.poster_path,
                    backdrop_path=it.backdrop_path,
                    genres=it.genres,
                    order_index=it.order_index,
                    fit_score=it.fit_score,
                    added_at=it.added_at,
                    metadata_json=None,
                    watched=watched,
                    watched_at=watched_at
                )
            )

        response = ListResponse.from_orm(lst)
        response.item_count = len(items_out)
        response.items = items_out
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get list {list_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get list: {str(e)}")


@router.patch("/{list_id}", response_model=ListResponse)
def update_list(list_id: int, request: UpdateListRequest, user_id: int = Query(1), db=Depends(get_db)):
    """Update list metadata (name, description, visibility)."""
    try:
        lst = db.query(IndividualList).filter(
            IndividualList.id == list_id,
            IndividualList.user_id == user_id
        ).first()
        
        if not lst:
            raise HTTPException(status_code=404, detail="List not found")
        
        # Update fields
        if request.name is not None:
            lst.name = request.name
        if request.description is not None:
            lst.description = request.description
        if request.is_public is not None:
            lst.is_public = request.is_public
        
        lst.updated_at = utc_now()
        
        db.commit()
        db.refresh(lst)
        
        logger.info(f"Updated Individual List {list_id}")
        
        # Get item count
        item_count = db.query(IndividualListItem).filter(
            IndividualListItem.list_id == list_id
        ).count()
        
        response = ListResponse.from_orm(lst)
        response.item_count = item_count
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to update list {list_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update list: {str(e)}")


@router.delete("/{list_id}", status_code=204)
def delete_list(list_id: int, user_id: int = Query(1), db=Depends(get_db)):
    """Delete an Individual List and all its items."""
    try:
        lst = db.query(IndividualList).filter(
            IndividualList.id == list_id,
            IndividualList.user_id == user_id
        ).first()
        
        if not lst:
            raise HTTPException(status_code=404, detail="List not found")
        
        # Delete from Trakt if it has a Trakt list ID
        if lst.trakt_list_id:
            try:
                import asyncio
                from app.services.trakt_list_sync import delete_trakt_list_for_individual_list
                asyncio.run(delete_trakt_list_for_individual_list(lst, user_id))
                logger.info(f"Deleted Trakt list {lst.trakt_list_id} for individual list {list_id}")
            except Exception as e:
                logger.warning(f"Failed to delete Trakt list {lst.trakt_list_id}: {e}")
        
        db.delete(lst)  # Cascade will delete items
        db.commit()
        
        logger.info(f"Deleted Individual List {list_id}")
        return None
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to delete list {list_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete list: {str(e)}")


@router.post("/{list_id}/items", response_model=Dict[str, Any])
def add_items(list_id: int, request: AddItemsRequest, db=Depends(get_db)):
    """Add items to a list."""
    try:
        # Verify list exists
        lst = db.query(IndividualList).filter(
            IndividualList.id == list_id,
            IndividualList.user_id == request.user_id
        ).first()
        
        if not lst:
            raise HTTPException(status_code=404, detail="List not found")
        
        # Get current max order_index
        max_order = db.query(IndividualListItem.order_index).filter(
            IndividualListItem.list_id == list_id
        ).order_by(IndividualListItem.order_index.desc()).first()
        
        next_order = (max_order[0] + 1.0) if max_order else 0.0
        
        added_count = 0
        skipped_count = 0
        
        for item_data in request.items:
            # Check if already exists
            existing = db.query(IndividualListItem).filter(
                IndividualListItem.list_id == list_id,
                IndividualListItem.tmdb_id == item_data['tmdb_id'],
                IndividualListItem.media_type == item_data['media_type']
            ).first()
            
            if existing:
                skipped_count += 1
                continue
            
            # Create new item
            new_item = IndividualListItem(
                list_id=list_id,
                tmdb_id=item_data['tmdb_id'],
                trakt_id=item_data.get('trakt_id'),
                media_type=item_data['media_type'],
                title=item_data['title'],
                original_title=item_data.get('original_title'),
                year=item_data.get('year'),
                overview=item_data.get('overview'),
                poster_path=item_data.get('poster_path'),
                backdrop_path=item_data.get('backdrop_path'),
                genres=item_data.get('genres'),
                order_index=next_order,
                fit_score=item_data.get('fit_score'),
                added_at=utc_now(),
                metadata_json=item_data.get('metadata_json')
            )
            
            db.add(new_item)
            next_order += 1.0
            added_count += 1
        
        lst.updated_at = utc_now()
        db.commit()
        
        logger.info(f"Added {added_count} items to list {list_id}, skipped {skipped_count} duplicates")

        # Generate/refresh list poster based on current items
        try:
            from app.services.poster_generator import generate_list_poster, delete_list_poster
            # Build a lightweight items list with poster_path and scores
            items_for_poster = []
            items_query = db.query(IndividualListItem).filter(
                IndividualListItem.list_id == list_id
            ).order_by(IndividualListItem.order_index.asc()).limit(12).all()
            for it in items_query:
                items_for_poster.append({
                    'poster_path': it.poster_path,
                    'score': it.fit_score or 0.0,
                    'genres': it.genres
                })
            old_poster = lst.poster_path
            poster_filename = generate_list_poster(list_id, items_for_poster, list_type="individual", max_items=5)
            if poster_filename:
                if old_poster and old_poster != poster_filename:
                    delete_list_poster(old_poster)
                lst.poster_path = poster_filename
                db.commit()
        except Exception as e:
            logger.warning(f"[INDIVIDUAL_POSTER] Failed to generate poster for list {list_id}: {e}")
        
        return {
            "success": True,
            "added": added_count,
            "skipped": skipped_count,
            "message": f"Added {added_count} items"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to add items to list {list_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to add items: {str(e)}")


@router.delete("/{list_id}/items/{item_id}", status_code=204)
def remove_item(list_id: int, item_id: int, user_id: int = Query(1), db=Depends(get_db)):
    """Remove an item from a list."""
    try:
        # Verify list ownership
        lst = db.query(IndividualList).filter(
            IndividualList.id == list_id,
            IndividualList.user_id == user_id
        ).first()
        
        if not lst:
            raise HTTPException(status_code=404, detail="List not found")
        
        # Get and delete item
        item = db.query(IndividualListItem).filter(
            IndividualListItem.id == item_id,
            IndividualListItem.list_id == list_id
        ).first()
        
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")
        
        db.delete(item)
        lst.updated_at = utc_now()
        db.commit()
        
        logger.info(f"Removed item {item_id} from list {list_id}")

        # Refresh poster after removal
        try:
            from app.services.poster_generator import generate_list_poster, delete_list_poster
            items_for_poster = []
            items_query = db.query(IndividualListItem).filter(
                IndividualListItem.list_id == list_id
            ).order_by(IndividualListItem.order_index.asc()).limit(12).all()
            for it in items_query:
                items_for_poster.append({
                    'poster_path': it.poster_path,
                    'score': it.fit_score or 0.0,
                    'genres': it.genres
                })
            old_poster = lst.poster_path
            poster_filename = generate_list_poster(list_id, items_for_poster, list_type="individual", max_items=5)
            if poster_filename:
                if old_poster and old_poster != poster_filename:
                    delete_list_poster(old_poster)
                lst.poster_path = poster_filename
                db.commit()
        except Exception as e:
            logger.warning(f"[INDIVIDUAL_POSTER] Failed to refresh poster for list {list_id}: {e}")
        return None
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to remove item {item_id} from list {list_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to remove item: {str(e)}")


@router.put("/{list_id}/items/reorder", response_model=Dict[str, Any])
def reorder_items(list_id: int, request: ReorderItemsRequest, db=Depends(get_db)):
    """Reorder list items by updating order_index values."""
    try:
        # Verify list ownership
        lst = db.query(IndividualList).filter(
            IndividualList.id == list_id,
            IndividualList.user_id == request.user_id
        ).first()
        
        if not lst:
            raise HTTPException(status_code=404, detail="List not found")
        
        # Update order_index for each item
        for idx, item_id in enumerate(request.item_ids):
            db.query(IndividualListItem).filter(
                IndividualListItem.id == item_id,
                IndividualListItem.list_id == list_id
            ).update({"order_index": float(idx)})
        
        lst.updated_at = utc_now()
        db.commit()
        
        logger.info(f"Reordered {len(request.item_ids)} items in list {list_id}")
        
        return {
            "success": True,
            "reordered": len(request.item_ids),
            "message": f"Reordered {len(request.item_ids)} items"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to reorder items in list {list_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to reorder items: {str(e)}")


@router.get("/{list_id}/search", response_model=List[SearchResultResponse])
def search_content(
    list_id: int,
    q: str = Query(..., min_length=1),
    user_id: int = Query(1),
    limit: int = Query(50, ge=1, le=100),
    skip_fit_scoring: bool = Query(False, description="Skip fit scoring for faster autocomplete")
):
    """Hybrid search for content to add to list (FAISS + ElasticSearch)."""
    try:
        # Verify list exists
        db = SessionLocal()
        try:
            lst = db.query(IndividualList).filter(
                IndividualList.id == list_id,
                IndividualList.user_id == user_id
            ).first()
            
            if not lst:
                raise HTTPException(status_code=404, detail="List not found")
        finally:
            db.close()
        
        # Perform hybrid search
        search_service = IndividualListSearchService(user_id=user_id)
        results = search_service.search(query=q, limit=limit, skip_fit_scoring=skip_fit_scoring)
        
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
                fit_score=result.get('fit_score'),
                relevance_score=result.get('relevance_score')
            ))
        
        logger.info(f"Search for '{q}' in list {list_id} returned {len(responses)} results")
        return responses
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Search failed for list {list_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


@router.get("/{list_id}/suggestions", response_model=List[SuggestionResponse])
def get_suggestions(list_id: int, user_id: int = Query(1)):
    """Get FAISS-based suggestions for a list."""
    try:
        # Verify list exists
        db = SessionLocal()
        try:
            lst = db.query(IndividualList).filter(
                IndividualList.id == list_id,
                IndividualList.user_id == user_id
            ).first()
            
            if not lst:
                raise HTTPException(status_code=404, detail="List not found")
        finally:
            db.close()
        
        # Get suggestions
        suggestions_service = IndividualListSuggestionsService(user_id=user_id)
        suggestions = suggestions_service.get_suggestions(list_id=list_id)
        
        # Convert to response models
        responses = []
        for sug in suggestions:
            # Convert genres list to string if needed
            genres = sug.get('genres')
            if isinstance(genres, list):
                genres = ', '.join(genres) if genres else None
            
            responses.append(SuggestionResponse(
                tmdb_id=sug['tmdb_id'],
                media_type=sug['media_type'],
                title=sug['title'],
                original_title=sug.get('original_title'),
                year=sug.get('year'),
                overview=sug.get('overview'),
                poster_path=sug.get('poster_path'),
                backdrop_path=sug.get('backdrop_path'),
                genres=genres,
                vote_average=sug.get('vote_average'),
                vote_count=sug.get('vote_count'),
                popularity=sug.get('popularity'),
                fit_score=sug.get('fit_score'),
                similarity_score=sug.get('similarity_score'),
                is_high_fit=sug.get('is_high_fit', False)
            ))
        
        logger.info(f"Generated {len(responses)} suggestions for list {list_id}")
        return responses
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get suggestions for list {list_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get suggestions: {str(e)}")


@router.post("/{list_id}/sync-trakt", response_model=SyncStatusResponse)
def sync_to_trakt(list_id: int, request: SyncTraktRequest):
    """Manually sync list to Trakt."""
    try:
        # Verify list exists
        db = SessionLocal()
        try:
            lst = db.query(IndividualList).filter(
                IndividualList.id == list_id,
                IndividualList.user_id == request.user_id
            ).first()
            
            if not lst:
                raise HTTPException(status_code=404, detail="List not found")
        finally:
            db.close()
        
        # Perform sync
        sync_service = IndividualListTraktSync(user_id=request.user_id)
        result = sync_service.sync_list(list_id=list_id)
        
        logger.info(f"Trakt sync for list {list_id}: {result.get('message')}")
        
        return SyncStatusResponse(
            success=result['success'],
            message=result['message'],
            trakt_list_id=result.get('trakt_list_id'),
            items_added=result.get('items_added'),
            items_removed=result.get('items_removed'),
            items_failed=result.get('items_failed'),
            errors=result.get('errors', [])
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Trakt sync failed for list {list_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Trakt sync failed: {str(e)}")
