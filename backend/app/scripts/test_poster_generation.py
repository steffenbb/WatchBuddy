#!/usr/bin/env python3
"""
Test poster generation for different list types.
"""
import sys
sys.path.insert(0, '/app')

from app.core.database import SessionLocal
from app.models import UserList, ListItem, MediaMetadata
from app.services.poster_generator import generate_list_poster
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_poster_generation():
    """Test poster generation for existing lists."""
    db = SessionLocal()
    try:
        # Get lists with items
        lists = db.query(UserList).all()
        
        results = []
        for lst in lists:
            logger.info(f"\n{'='*60}")
            logger.info(f"Testing list: {lst.id} - {lst.title} ({lst.list_type})")
            
            # Get list items with metadata
            items = db.query(ListItem).filter(
                ListItem.smartlist_id == lst.id
            ).order_by(ListItem.score.desc()).limit(20).all()
            
            if not items:
                logger.warning(f"  No items found for list {lst.id}")
                results.append({
                    'list_id': lst.id,
                    'title': lst.title,
                    'status': 'SKIP',
                    'reason': 'No items'
                })
                continue
            
            logger.info(f"  Found {len(items)} items")
            
            # Get metadata for items
            trakt_ids = [item.trakt_id for item in items if item.trakt_id]
            media_by_trakt = {}
            
            if trakt_ids:
                metadata = db.query(MediaMetadata).filter(
                    MediaMetadata.trakt_id.in_(trakt_ids)
                ).all()
                for m in metadata:
                    media_by_trakt[m.trakt_id] = m
            
            # Prepare items for poster generation
            poster_items = []
            for item in items:
                meta = media_by_trakt.get(item.trakt_id) if item.trakt_id else None
                poster_path = meta.poster_path if meta else None
                
                if not poster_path:
                    continue
                
                # Ensure poster_path doesn't include full URL
                if poster_path.startswith('http'):
                    poster_path = poster_path.replace('https://image.tmdb.org/t/p/w342', '')
                    poster_path = poster_path.replace('https://image.tmdb.org/t/p/w500', '')
                
                poster_items.append({
                    'poster_path': poster_path,
                    'score': item.score or 0.5,
                    'title': item.title or meta.title if meta else 'Unknown',
                    'genres': '[]'
                })
            
            logger.info(f"  {len(poster_items)} items have posters")
            
            if len(poster_items) < 3:
                logger.warning(f"  Not enough items with posters ({len(poster_items)} < 3)")
                results.append({
                    'list_id': lst.id,
                    'title': lst.title,
                    'status': 'SKIP',
                    'reason': f'Only {len(poster_items)} items with posters'
                })
                continue
            
            # Generate poster
            logger.info(f"  Generating poster with {len(poster_items)} items...")
            poster_path = generate_list_poster(
                list_id=lst.id,
                items=poster_items,
                list_type=lst.list_type or 'custom',
                max_items=5
            )
            
            if poster_path:
                logger.info(f"  ✓ SUCCESS: Poster saved to {poster_path}")
                
                # Update database
                lst.poster_path = poster_path
                db.commit()
                
                results.append({
                    'list_id': lst.id,
                    'title': lst.title,
                    'status': 'SUCCESS',
                    'poster_path': poster_path
                })
            else:
                logger.error(f"  ✗ FAILED: Poster generation failed")
                results.append({
                    'list_id': lst.id,
                    'title': lst.title,
                    'status': 'FAILED',
                    'reason': 'Generation returned None'
                })
        
        # Summary
        logger.info(f"\n{'='*60}")
        logger.info("SUMMARY")
        logger.info(f"{'='*60}")
        
        success = [r for r in results if r['status'] == 'SUCCESS']
        failed = [r for r in results if r['status'] == 'FAILED']
        skipped = [r for r in results if r['status'] == 'SKIP']
        
        logger.info(f"Total lists: {len(results)}")
        logger.info(f"Success: {len(success)}")
        logger.info(f"Failed: {len(failed)}")
        logger.info(f"Skipped: {len(skipped)}")
        
        if success:
            logger.info("\nSuccessful generations:")
            for r in success:
                logger.info(f"  - {r['list_id']}: {r['title']} → {r['poster_path']}")
        
        if failed:
            logger.error("\nFailed generations:")
            for r in failed:
                logger.error(f"  - {r['list_id']}: {r['title']} - {r.get('reason', 'Unknown')}")
        
        if skipped:
            logger.info("\nSkipped:")
            for r in skipped:
                logger.info(f"  - {r['list_id']}: {r['title']} - {r.get('reason', 'Unknown')}")
        
        return len(success) > 0
        
    except Exception as e:
        logger.error(f"Error during poster generation test: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        db.close()

if __name__ == '__main__':
    success = test_poster_generation()
    sys.exit(0 if success else 1)
