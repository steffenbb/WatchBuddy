#!/usr/bin/env python3
"""
Test poster regeneration and old file disposal.
"""
import sys
import os
sys.path.insert(0, '/app')

from app.core.database import SessionLocal
from app.models import UserList, ListItem, MediaMetadata
from app.services.poster_generator import generate_list_poster
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_poster_regeneration():
    """Test poster regeneration with old file disposal."""
    db = SessionLocal()
    try:
        # Get list 6 (Thriller)
        lst = db.query(UserList).filter(UserList.id == 6).first()
        if not lst:
            logger.error("List 6 not found")
            return False
        
        logger.info(f"Testing regeneration for list {lst.id} - {lst.title}")
        logger.info(f"Current poster_path: {lst.poster_path}")
        
        # Check if old poster exists
        old_poster_path = lst.poster_path
        if old_poster_path:
            full_old_path = f"/app/data/posters/{old_poster_path}"
            if os.path.exists(full_old_path):
                logger.info(f"Old poster exists: {full_old_path}")
                old_size = os.path.getsize(full_old_path)
                logger.info(f"Old poster size: {old_size} bytes")
            else:
                logger.warning(f"Old poster path in DB but file doesn't exist: {full_old_path}")
        
        # Get list items
        items = db.query(ListItem).filter(
            ListItem.smartlist_id == lst.id
        ).order_by(ListItem.score.desc()).limit(20).all()
        
        logger.info(f"Found {len(items)} items")
        
        # Get metadata
        trakt_ids = [item.trakt_id for item in items if item.trakt_id]
        media_by_trakt = {}
        
        if trakt_ids:
            metadata = db.query(MediaMetadata).filter(
                MediaMetadata.trakt_id.in_(trakt_ids)
            ).all()
            for m in metadata:
                media_by_trakt[m.trakt_id] = m
        
        # Prepare items
        poster_items = []
        for item in items:
            meta = media_by_trakt.get(item.trakt_id) if item.trakt_id else None
            poster_path = meta.poster_path if meta else None
            
            if not poster_path:
                continue
            
            if poster_path.startswith('http'):
                poster_path = poster_path.replace('https://image.tmdb.org/t/p/w342', '')
                poster_path = poster_path.replace('https://image.tmdb.org/t/p/w500', '')
            
            poster_items.append({
                'poster_path': poster_path,
                'score': item.score or 0.5,
                'title': item.title or meta.title if meta else 'Unknown',
                'genres': '[]'
            })
        
        logger.info(f"{len(poster_items)} items have posters")
        
        # Regenerate poster
        logger.info("Regenerating poster...")
        new_poster_path = generate_list_poster(
            list_id=lst.id,
            items=poster_items,
            list_type=lst.list_type or 'custom',
            max_items=5
        )
        
        if not new_poster_path:
            logger.error("Poster generation failed")
            return False
        
        logger.info(f"New poster generated: {new_poster_path}")
        
        # Check if old file was removed
        if old_poster_path and old_poster_path != new_poster_path:
            full_old_path = f"/app/data/posters/{old_poster_path}"
            if os.path.exists(full_old_path):
                logger.error(f"✗ FAILED: Old poster still exists: {full_old_path}")
                return False
            else:
                logger.info(f"✓ SUCCESS: Old poster was properly disposed")
        
        # Check new file
        full_new_path = f"/app/data/posters/{new_poster_path}"
        if os.path.exists(full_new_path):
            new_size = os.path.getsize(full_new_path)
            logger.info(f"✓ SUCCESS: New poster exists: {full_new_path}")
            logger.info(f"New poster size: {new_size} bytes")
        else:
            logger.error(f"✗ FAILED: New poster doesn't exist: {full_new_path}")
            return False
        
        # Update database
        lst.poster_path = new_poster_path
        db.commit()
        logger.info("✓ Database updated")
        
        return True
        
    except Exception as e:
        logger.error(f"Error during regeneration test: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        db.close()

if __name__ == '__main__':
    success = test_poster_regeneration()
    sys.exit(0 if success else 1)
