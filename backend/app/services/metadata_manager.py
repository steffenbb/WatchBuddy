"""
metadata_manager.py

Manages local movie/show metadata storage and cleanup.
Stores metadata when added to lists, removes when orphaned.
"""

import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from app.core.database import SessionLocal
from app.models import MediaMetadata, ListItem
from app.services.tmdb_client import fetch_tmdb_metadata
from sqlalchemy import select, delete, and_, func
import asyncio

logger = logging.getLogger(__name__)

class MetadataManager:
    """Manages local media metadata storage and cleanup."""
    
    @staticmethod
    async def store_metadata(trakt_item: Dict[str, Any], fetch_tmdb: bool = True) -> Optional[MediaMetadata]:
        """Store metadata for a Trakt item."""
        session = SessionLocal()
        try:
            # Extract basic info from Trakt item
            ids = trakt_item.get("ids", {}) if isinstance(trakt_item.get("ids"), dict) else {}
            trakt_id = ids.get("trakt") or trakt_item.get("trakt_id")
            tmdb_id = ids.get("tmdb") or trakt_item.get("tmdb_id")
            imdb_id = ids.get("imdb") or trakt_item.get("imdb_id")
            media_type = trakt_item.get("type", "movie")

            if not trakt_id:
                logger.warning("No Trakt ID found in item")
                return None

            # Check if metadata already exists
            metadata = session.query(MediaMetadata).filter(MediaMetadata.trakt_id == trakt_id).first()

            if metadata and metadata.last_updated and metadata.last_updated > datetime.utcnow() - timedelta(days=7):
                # Recent metadata, mark as active and return
                metadata.is_active = True
                metadata.last_updated = datetime.utcnow()
                session.commit()
                return metadata

            # Fetch TMDB data if requested and tmdb_id available
            tmdb_data = None
            if fetch_tmdb and tmdb_id:
                try:
                    tmdb_data = await fetch_tmdb_metadata(tmdb_id, media_type)
                except Exception as e:
                    logger.warning(f"Failed to fetch TMDB data for {tmdb_id}: {e}")

            # Create or update metadata
            if not metadata:
                metadata = MediaMetadata()
                session.add(metadata)

            # Update fields
            metadata.trakt_id = trakt_id
            metadata.tmdb_id = tmdb_id
            metadata.imdb_id = imdb_id
            metadata.media_type = media_type
            metadata.title = trakt_item.get("title", "")
            metadata.year = trakt_item.get("year")
            metadata.language = trakt_item.get("language", "en")
            metadata.rating = trakt_item.get("rating", 0.0)
            metadata.votes = trakt_item.get("votes", 0)
            metadata.is_active = True
            metadata.last_updated = datetime.utcnow()

            # Add TMDB data if available
            if tmdb_data:
                metadata.overview = tmdb_data.get("overview", "")
                metadata.poster_path = tmdb_data.get("poster_path")
                metadata.backdrop_path = tmdb_data.get("backdrop_path")
                metadata.popularity = tmdb_data.get("popularity", 0.0)

                # Store genres and keywords as JSON
                genres = [g.get("name") for g in tmdb_data.get("genres", [])]
                metadata.genres = json.dumps(genres)

                keywords = [k.get("name") for k in tmdb_data.get("keywords", {}).get("keywords", [])]
                metadata.keywords = json.dumps(keywords)
            else:
                # Use Trakt data as fallback
                metadata.overview = trakt_item.get("overview", "")
                genres = trakt_item.get("genres", [])
                metadata.genres = json.dumps(genres) if genres else "[]"
                metadata.keywords = "[]"

            session.commit()
            logger.info(f"Stored metadata for {media_type} '{metadata.title}' (Trakt ID: {trakt_id})")
            return metadata
        finally:
            session.close()
    
    @staticmethod
    async def get_metadata(trakt_id: int) -> Optional[MediaMetadata]:
        """Get metadata by Trakt ID."""
        session = SessionLocal()
        try:
            from sqlalchemy.orm import Session
            from sqlalchemy import select
            stmt = select(MediaMetadata).where(MediaMetadata.trakt_id == trakt_id)
            result = session.execute(stmt)
            return result.scalars().first()
        finally:
            session.close()
    
    @staticmethod
    async def mark_inactive(trakt_ids: List[int]):
        """Mark metadata as inactive for orphaned items."""
        if not trakt_ids:
            return
        
        session = SessionLocal()
        try:
            from sqlalchemy import update
            stmt = update(MediaMetadata).where(
                MediaMetadata.trakt_id.in_(trakt_ids)
            ).values(is_active=False, last_updated=datetime.utcnow())
            session.execute(stmt)
            session.commit()
            logger.info(f"Marked {len(trakt_ids)} metadata items as inactive")
        finally:
            session.close()
    
    @staticmethod
    async def cleanup_orphaned(retention_days: int = 30) -> int:
        """Clean up orphaned metadata older than retention period."""
        session = SessionLocal()
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=retention_days)
            
            # Find metadata not referenced by any active lists
            from sqlalchemy import select
            subquery = select(ListItem.item_id).distinct()
            
            stmt = delete(MediaMetadata).where(
                and_(
                    MediaMetadata.is_active == False,
                    MediaMetadata.last_updated < cutoff_date,
                    MediaMetadata.trakt_id.notin_(subquery)
                )
            )
            
            result = session.execute(stmt)
            deleted_count = result.rowcount if hasattr(result, 'rowcount') else 0
            session.commit()
            
            logger.info(f"Cleaned up {deleted_count} orphaned metadata items")
            return deleted_count
        finally:
            session.close()
    
    @staticmethod
    async def refresh_stale_metadata(days_threshold: int = 30) -> int:
        """Refresh metadata that's older than threshold."""
        session = SessionLocal()
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days_threshold)
            
            from sqlalchemy import select
            stmt = select(MediaMetadata).where(
                and_(
                    MediaMetadata.is_active == True,
                    MediaMetadata.last_updated < cutoff_date,
                    MediaMetadata.tmdb_id.isnot(None)
                )
            ).limit(100)  # Process in batches
            
            result = session.execute(stmt)
            stale_items = result.scalars().all()
            
            refreshed_count = 0
            for item in stale_items:
                try:
                    # Re-fetch TMDB data
                    tmdb_data = await fetch_tmdb_metadata(item.tmdb_id, item.media_type)
                    if tmdb_data:
                        # Update with fresh data
                        item.overview = tmdb_data.get("overview", item.overview)
                        item.poster_path = tmdb_data.get("poster_path", item.poster_path)
                        item.backdrop_path = tmdb_data.get("backdrop_path", item.backdrop_path)
                        item.popularity = tmdb_data.get("popularity", item.popularity)
                        
                        # Update genres and keywords
                        genres = [g.get("name") for g in tmdb_data.get("genres", [])]
                        item.genres = json.dumps(genres)
                        
                        keywords = [k.get("name") for k in tmdb_data.get("keywords", {}).get("keywords", [])]
                        item.keywords = json.dumps(keywords)
                        
                        item.last_updated = datetime.utcnow()
                        refreshed_count += 1
                        
                        # Add small delay to avoid rate limiting
                        await asyncio.sleep(0.1)
                        
                except Exception as e:
                    logger.warning(f"Failed to refresh metadata for {item.title}: {e}")
            
            session.commit()
            logger.info(f"Refreshed {refreshed_count} stale metadata items")
            return refreshed_count
        finally:
            session.close()
    
    @staticmethod
    async def get_stats() -> Dict[str, Any]:
        """Get metadata storage statistics."""
        session = SessionLocal()
        try:
            # Count active and inactive items
            from sqlalchemy import select
            active_count = session.scalar(
                select(func.count(MediaMetadata.id)).where(MediaMetadata.is_active == True)
            )
            inactive_count = session.scalar(
                select(func.count(MediaMetadata.id)).where(MediaMetadata.is_active == False)
            )
            
            # Count by media type
            movie_count = session.scalar(
                select(func.count(MediaMetadata.id)).where(
                    and_(MediaMetadata.media_type == "movie", MediaMetadata.is_active == True)
                )
            )
            show_count = session.scalar(
                select(func.count(MediaMetadata.id)).where(
                    and_(MediaMetadata.media_type == "show", MediaMetadata.is_active == True)
                )
            )
            
            return {
                "total_active": active_count,
                "total_inactive": inactive_count,
                "movies": movie_count,
                "shows": show_count,
                "last_updated": datetime.utcnow().isoformat()
            }
        finally:
            session.close()