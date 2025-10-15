"""
Manually create Trakt list for list 66 and sync items.
"""
import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.core.database import SessionLocal
from app.models import UserList, ListItem
from app.services.trakt_client import TraktClient
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def fix_list_66():
    """Create Trakt list for list 66 and sync items."""
    db = SessionLocal()
    try:
        # Get list 66
        user_list = db.query(UserList).filter(UserList.id == 66).first()
        if not user_list:
            logger.error("List 66 not found")
            return
        
        logger.info(f"List: {user_list.title} (ID: {user_list.id})")
        logger.info(f"Type: {user_list.list_type}")
        logger.info(f"Current Trakt ID: {user_list.trakt_list_id}")
        
        # Count items
        items = db.query(ListItem).filter(ListItem.smartlist_id == 66).all()
        logger.info(f"Local items: {len(items)}")
        
        # Create Trakt list
        trakt = TraktClient(user_id=1)
        
        if not user_list.trakt_list_id:
            logger.info("Creating Trakt list...")
            trakt_result = await trakt.create_list(
                name=user_list.title,
                description=f"Custom list managed by WatchBuddy (ID: {user_list.id})",
                privacy="private"
            )
            
            trakt_list_id = str(trakt_result.get("ids", {}).get("trakt"))
            logger.info(f"✓ Created Trakt list {trakt_list_id}")
            
            # Update database
            db.query(UserList).filter(UserList.id == 66).update({
                "trakt_list_id": trakt_list_id
            })
            db.commit()
            logger.info(f"✓ Updated database with trakt_list_id")
        else:
            trakt_list_id = user_list.trakt_list_id
            logger.info(f"Trakt list already exists: {trakt_list_id}")
        
        # Sync items to Trakt
        if items and trakt_list_id:
            logger.info(f"Syncing {len(items)} items to Trakt...")
            
            # Format items for Trakt
            trakt_items = []
            for item in items:
                if item.trakt_id and isinstance(item.trakt_id, int):
                    trakt_items.append({
                        "trakt_id": item.trakt_id,
                        "media_type": item.media_type or "movie"
                    })
            
            logger.info(f"Prepared {len(trakt_items)} valid items for sync")
            
            # Sync to Trakt
            sync_stats = await trakt.sync_list_items(trakt_list_id, trakt_items)
            logger.info(f"✓ Sync complete: {sync_stats}")
            
            added = sync_stats.get("added", {})
            movies_added = added.get("movies", 0)
            shows_added = added.get("shows", 0)
            logger.info(f"✓ Added {movies_added} movies and {shows_added} shows to Trakt")
        
        logger.info("✓ Done!")
        
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(fix_list_66())
