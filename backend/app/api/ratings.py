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
        logger.error(f"Error getting user ratings: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get ratings: {str(e)}")
    finally:
        db.close()


@router.get("/stats")
async def get_rating_stats(user_id: int = 1):
    """Get statistics about user ratings for home page dashboard."""
    from ..services.trakt_client import TraktClient
    from ..core.redis_client import get_redis
    import json
    
    # Try to get cached stats first (cache for 5 minutes)
    redis = get_redis()
    cache_key = f"rating_stats:{user_id}"
    try:
        cached = await redis.get(cache_key)
        if cached:
            logger.debug(f"Returning cached rating stats for user {user_id}")
            return json.loads(cached)
    except Exception as e:
        logger.warning(f"Redis cache get failed: {e}")
    
    db = SessionLocal()
    try:
        # Get Trakt stats from database (TraktWatchHistory) instead of API
        # This is much faster and doesn't consume API rate limits
        from app.services.watch_history_helper import WatchHistoryHelper
        
        trakt_stats = None
        trakt_movies_count = 0
        trakt_shows_count = 0
        trakt_ratings_count = 0
        top_genre_trakt = None
        
        try:
            # Get watch stats from database
            helper = WatchHistoryHelper(user_id, db)
            watch_stats = helper.get_watch_stats()
            
            trakt_movies_count = watch_stats.get("movies_watched", 0)
            trakt_shows_count = watch_stats.get("shows_watched", 0)
            
            # Get top genre from database
            top_genre_data = helper.get_top_genre()
            if top_genre_data:
                top_genre_trakt = {
                    "genre": top_genre_data["genre"],
                    "count": top_genre_data["count"]
                }
                logger.info(f"Top genre from DB: {top_genre_trakt}")
            else:
                logger.info("No top genre found in watch history")
            
            # Try to get ratings count from Trakt API (not stored in watch history)
            try:
                trakt_client = TraktClient(user_id=user_id)
                api_stats = await trakt_client.get_user_stats()
                if api_stats:
                    trakt_ratings_count = api_stats.get("ratings", {}).get("total", 0)
            except Exception:
                pass  # Ratings count is optional
                
        except Exception as e:
            logger.warning(f"Could not fetch DB watch stats, falling back to API: {e}")
            # Fallback to API if database query fails
            try:
                trakt_client = TraktClient(user_id=user_id)
                trakt_stats = await trakt_client.get_user_stats()
                if trakt_stats:
                    trakt_movies_count = trakt_stats.get("movies", {}).get("watched", 0)
                    trakt_shows_count = trakt_stats.get("shows", {}).get("watched", 0)
                    trakt_ratings_count = trakt_stats.get("ratings", {}).get("total", 0)
                top_genre_trakt = await trakt_client.get_top_genre()
            except Exception as api_err:
                logger.warning(f"API fallback also failed: {api_err}")
        
        # Get all ratings for this user (local WatchBuddy ratings)
        ratings = db.query(UserRating).filter(UserRating.user_id == user_id).all()
        
        # Don't early-return if we have Trakt watch history counts (movies/shows watched)
        has_trakt_data = trakt_movies_count > 0 or trakt_shows_count > 0 or top_genre_trakt is not None
        
        if not ratings and not has_trakt_data:
            logger.info(f"No local ratings and no Trakt data for user {user_id}")
            return {
                "status": "success",
                "total_ratings": 0,
                "average_rating": 0,
                "movies_vs_shows": {"movies": 0, "shows": 0},
                "top_genres": [],
                "recent_activity": [],
                "trakt_stats": {
                    "movies_watched": 0,
                    "shows_watched": 0,
                    "ratings_count": 0,
                    "top_genre": None
                }
            }
        
        logger.info(f"Stats for user {user_id}: {trakt_movies_count} movies, {trakt_shows_count} shows watched")
        
        # Calculate basic stats from local ratings
        total_ratings = len(ratings)
        total_score = sum(r.rating for r in ratings) if ratings else 0
        average_rating = (total_score / total_ratings) if total_ratings > 0 else 0
        
        # Movies vs shows
        movies = sum(1 for r in ratings if r.media_type == 'movie') if ratings else 0
        shows = sum(1 for r in ratings if r.media_type == 'show') if ratings else 0
        
        # Get top genres from database watch history (more comprehensive than just local ratings)
        top_genres_list = []
        try:
            from app.services.watch_history_helper import WatchHistoryHelper
            helper = WatchHistoryHelper(user_id, db)
            top_genres_list = helper.get_top_genres(limit=10)
            logger.info(f"Got {len(top_genres_list)} top genres from watch history")
        except Exception as e:
            logger.warning(f"Failed to get top genres from DB, falling back to local ratings: {e}")
            # Fallback: Get metadata for genre analysis from local ratings only
            genre_counts = {}
            import json
            for rating in (ratings[:50] if ratings else []):  # Limit to recent 50 for performance
                metadata = db.query(MediaMetadata).filter(
                    MediaMetadata.trakt_id == rating.trakt_id
                ).first()
                
                if metadata and metadata.genres:
                    try:
                        genres = json.loads(metadata.genres) if isinstance(metadata.genres, str) else metadata.genres
                    except Exception:
                        genres = []
                    if isinstance(genres, list):
                        for genre in genres:
                            if not genre or genre.lower() == "n/a":
                                continue
                            g = str(genre).strip()
                            if not g:
                                continue
                            genre_counts[g] = genre_counts.get(g, 0) + 1
            
            # Top genres from local ratings
            top_genres = sorted(genre_counts.items(), key=lambda x: x[1], reverse=True)[:10]
            top_genres_list = [{"genre": g[0], "count": g[1]} for g in top_genres]
        
        # Recent activity (last 10 ratings)
        recent_ratings = sorted(ratings, key=lambda r: r.updated_at, reverse=True)[:10] if ratings else []
        recent_activity = []

        for rating in recent_ratings:
            metadata = db.query(MediaMetadata).filter(
                MediaMetadata.trakt_id == rating.trakt_id
            ).first()

            recent_activity.append({
                "trakt_id": rating.trakt_id,
                "media_type": rating.media_type,
                "rating": rating.rating,
                "title": metadata.title if metadata else "Unknown",
                "updated_at": rating.updated_at.isoformat() if rating.updated_at else None
            })

        result = {
            "status": "success",
            "total_ratings": total_ratings,
            "average_rating": round(average_rating, 2),
            "movies_vs_shows": {"movies": movies, "shows": shows},
            "top_genres": top_genres_list,
            "recent_activity": recent_activity,
            "trakt_stats": {
                "movies_watched": trakt_movies_count,
                "shows_watched": trakt_shows_count,
                "ratings_count": trakt_ratings_count,
                "top_genre": top_genre_trakt
            }
        }

        # Cache the result for 5 minutes
        try:
            await redis.set(cache_key, json.dumps(result), ex=300)
        except Exception as e:
            logger.warning(f"Redis cache set failed: {e}")

        return result
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