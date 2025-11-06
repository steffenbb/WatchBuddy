"""
Generate poster for individual list
"""
import logging
import sys
from app.core.database import SessionLocal
from app.models import IndividualList, IndividualListItem
from app.services.poster_generator import generate_list_poster

logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s:%(name)s:%(message)s'
)
logger = logging.getLogger(__name__)

def main():
    db = SessionLocal()
    try:
        # Get all individual lists
        lists = db.query(IndividualList).all()
        
        logger.info(f"Found {len(lists)} individual lists")
        
        for lst in lists:
            logger.info(f"\n{'='*60}")
            logger.info(f"Processing list: {lst.id} - {lst.name}")
            
            # Get items with posters
            items = db.query(IndividualListItem).filter(
                IndividualListItem.list_id == lst.id
            ).order_by(IndividualListItem.order_index).limit(20).all()
            
            logger.info(f"  Found {len(items)} items")
            
            items_with_posters = [
                item for item in items 
                if item.poster_path and item.poster_path.strip()
            ]
            
            logger.info(f"  {len(items_with_posters)} items have posters")
            
            if len(items_with_posters) < 3:
                logger.warning(f"  ⚠ SKIPPED: Need at least 3 items with posters")
                continue
            
            # Generate poster
            try:
                logger.info(f"  Generating poster with {len(items_with_posters)} items...")
                
                # Convert items to dictionaries (poster_generator expects dicts)
                items_for_poster = []
                for item in items_with_posters:
                    items_for_poster.append({
                        'poster_path': item.poster_path,
                        'score': item.fit_score or 0.0,
                        'genres': item.genres
                    })
                
                poster_filename = generate_list_poster(
                    lst.id, 
                    items_for_poster, 
                    list_type="individual",
                    max_items=5
                )
                
                # Update database
                lst.poster_path = poster_filename
                db.commit()
                
                logger.info(f"  ✓ SUCCESS: Poster saved to {poster_filename}")
            except Exception as e:
                logger.error(f"  ✗ FAILED: {e}")
                db.rollback()
        
        logger.info(f"\n{'='*60}")
        logger.info("COMPLETED")
        
    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        db.close()

if __name__ == "__main__":
    main()
