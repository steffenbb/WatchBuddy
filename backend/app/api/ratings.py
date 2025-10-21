"""
ratings.py

API endpoints for user ratings (thumbs up/down system).
"""

from fastapi import APIRouter, HTTPException, Body
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import Optional
import logging

from ..core.database import SessionLocal
from ..models import UserRating, MediaMetadata
import datetime

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/rate")
async def rate_item(
    trakt_id: int = Body(...),
    media_type: str = Body(...),
    rating: int = Body(...),  # 1 for thumbs up, -1 for thumbs down, 0 to remove rating
    user_id: int = Body(1),
    list_item_id: Optional[int] = Body(None)
):
    """Rate an item with thumbs up/down."""
    if rating not in [-1, 0, 1]:
        raise HTTPException(status_code=400, detail="Rating must be -1 (thumbs down), 0 (remove), or 1 (thumbs up)")
    
    if media_type not in ["movie", "show"]:
        raise HTTPException(status_code=400, detail="Media type must be 'movie' or 'show'")
    
    db = SessionLocal()
    try:
        # Check if rating already exists
        existing_rating = db.query(UserRating).filter(
            UserRating.user_id == user_id,
            UserRating.trakt_id == trakt_id
        ).first()
        
        if rating == 0:
            # Remove rating if it exists
            if existing_rating:
                db.delete(existing_rating)
                db.commit()
                return {"status": "success", "message": "Rating removed"}
            else:
                return {"status": "success", "message": "No rating to remove"}
        
        if existing_rating:
            # Update existing rating
            existing_rating.rating = rating
            existing_rating.updated_at = datetime.datetime.utcnow()
            existing_rating.list_item_id = list_item_id
        else:
            # Create new rating
            new_rating = UserRating(
                user_id=user_id,
                trakt_id=trakt_id,
                media_type=media_type,
                rating=rating,
                list_item_id=list_item_id
            )
            db.add(new_rating)
        
        db.commit()
        
        action = "updated" if existing_rating else "created"
        rating_text = "thumbs up" if rating == 1 else "thumbs down"
        
        return {
            "status": "success", 
            "message": f"Rating {action}: {rating_text}",
            "rating": rating
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error rating item: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to rate item: {str(e)}")
    finally:
        db.close()

@router.get("/item/{trakt_id}")
async def get_item_rating(
    trakt_id: int,
    user_id: int = 1
):
    """Get user's rating for a specific item."""
    db = SessionLocal()
    try:
        rating = db.query(UserRating).filter(
            UserRating.user_id == user_id,
            UserRating.trakt_id == trakt_id
        ).first()
        
        if rating:
            return {
                "status": "success",
                "rating": rating.rating,
                "created_at": rating.created_at.isoformat(),
                "updated_at": rating.updated_at.isoformat()
            }
        else:
            return {
                "status": "success",
                "rating": 0,  # No rating
                "created_at": None,
                "updated_at": None
            }
    
    except Exception as e:
        logger.error(f"Error getting item rating: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get rating: {str(e)}")
    finally:
        db.close()

@router.get("/user/{user_id}")
async def get_user_ratings(
    user_id: int = 1,
    rating_filter: Optional[int] = None  # Filter by rating value (1, -1, or None for all)
):
    """Get all ratings for a user."""
    db = SessionLocal()
    try:
        query = db.query(UserRating).filter(UserRating.user_id == user_id)
        
        if rating_filter is not None:
            query = query.filter(UserRating.rating == rating_filter)
        
        ratings = query.order_by(UserRating.updated_at.desc()).all()
        
        # Enrich with media metadata if available
        result = []
        for rating in ratings:
            media = db.query(MediaMetadata).filter(
                MediaMetadata.trakt_id == rating.trakt_id,
                MediaMetadata.media_type == rating.media_type
            ).first()
            
            result.append({
                "id": rating.id,
                "trakt_id": rating.trakt_id,
                "media_type": rating.media_type,
                "rating": rating.rating,
                "created_at": rating.created_at.isoformat(),
                "updated_at": rating.updated_at.isoformat(),
                "title": media.title if media else None,
                "year": media.year if media else None,
                "poster_path": media.poster_path if media else None
            })
        
        return {
            "status": "success",
            "total": len(result),
            "ratings": result
        }
    
    except Exception as e:
        logger.error(f"Error getting user ratings: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get ratings: {str(e)}")
    finally:
        db.close()