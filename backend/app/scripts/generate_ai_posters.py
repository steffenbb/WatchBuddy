"""
Generate posters for all AI lists (mood/theme/fusion/chat types)
"""
import logging
import sys
from app.core.database import SessionLocal
from app.models_ai import AiList, AiListItem
from app.models import MediaMetadata
from app.services.poster_generator import generate_list_poster

logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s:%(name)s:%(message)s'
)
logger = logging.getLogger(__name__)

def main():
    db = SessionLocal()
    try:
        # Get all AI lists
        ai_lists = db.query(AiList).all()
        
        logger.info(f"Found {len(ai_lists)} AI lists")
        
        success_count = 0
        failed_count = 0
        skipped_count = 0
        
        for ai_list in ai_lists:
            logger.info(f"\n{'='*60}")
            logger.info(f"Processing AI list: {ai_list.id}")
            logger.info(f"  Type: {ai_list.type}")
            logger.info(f"  Title: {ai_list.generated_title}")
            
            # Get items with posters from ai_list_items table
            ai_items = db.query(AiListItem).filter(
                AiListItem.ai_list_id == ai_list.id
            ).order_by(AiListItem.score.desc()).limit(20).all()
            
            logger.info(f"  Found {len(ai_items)} AI list items")
            
            # Fetch metadata for items
            items_for_poster = []
            for ai_item in ai_items:
                # Get metadata for this item
                metadata = db.query(MediaMetadata).filter(
                    MediaMetadata.tmdb_id == ai_item.tmdb_id
                ).first()
                
                if metadata and metadata.poster_path:
                    items_for_poster.append({
                        'poster_path': metadata.poster_path,
                        'score': ai_item.score or 0.0,
                        'genres': metadata.genres  # Already JSON string
                    })
            
            logger.info(f"  {len(items_for_poster)} items have poster metadata")
            
            if len(items_for_poster) < 3:
                logger.warning(f"  ⚠ SKIPPED: Need at least 3 items with posters")
                skipped_count += 1
                continue
            
            # Generate poster
            try:
                logger.info(f"  Generating poster with {len(items_for_poster)} items...")
                
                # Items already in dict format with poster_path, score, genres
                
                # Use AI list ID (UUID) and type for filename
                poster_filename = generate_list_poster(
                    ai_list.id,  # UUID
                    items_for_poster, 
                    list_type=ai_list.type,  # mood/theme/fusion/chat
                    max_items=5
                )
                
                if poster_filename:
                    # Update ai_list poster_path
                    ai_list.poster_path = poster_filename
                    db.commit()
                    logger.info(f"  ✓ SUCCESS: Poster saved to {poster_filename}")
                    success_count += 1
                else:
                    logger.error(f"  ✗ FAILED: generate_list_poster returned None")
                    failed_count += 1
                    
            except Exception as e:
                logger.error(f"  ✗ FAILED: {e}")
                import traceback
                traceback.print_exc()
                failed_count += 1
                db.rollback()
        
        logger.info(f"\n{'='*60}")
        logger.info("SUMMARY")
        logger.info(f"{'='*60}")
        logger.info(f"Total AI lists: {len(ai_lists)}")
        logger.info(f"Success: {success_count}")
        logger.info(f"Failed: {failed_count}")
        logger.info(f"Skipped: {skipped_count}")
        
    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        db.close()

if __name__ == "__main__":
    main()
