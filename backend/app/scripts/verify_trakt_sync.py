"""
Verify that a custom list syncs to Trakt by checking the Trakt API.
"""
import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.services.trakt_client import TraktClient
from app.core.database import SessionLocal
from app.models import UserList, ListItem
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def verify_sync(list_id: int):
    """Check if list items are synced to Trakt."""
    db = SessionLocal()
    try:
        # Get list info
        user_list = db.query(UserList).filter(UserList.id == list_id).first()
        if not user_list:
            logger.error(f"List {list_id} not found")
            return
        
        logger.info(f"List: {user_list.title}")
        logger.info(f"Type: {user_list.list_type}")
        logger.info(f"Trakt List ID: {user_list.trakt_list_id}")
        
        if not user_list.trakt_list_id:
            logger.error("No Trakt list ID - cannot sync!")
            return
        
        # Count local items
        local_count = db.query(ListItem).filter(
            ListItem.smartlist_id == list_id
        ).count()
        logger.info(f"Local items: {local_count}")
        
        # Get Trakt items
        trakt = TraktClient(user_id=1)
        trakt_items = await trakt.get_list_items(user_list.trakt_list_id)
        logger.info(f"Trakt items: {len(trakt_items)}")
        
        if len(trakt_items) == local_count:
            logger.info("✓ Sync appears complete!")
        else:
            logger.warning(f"✗ Mismatch! Local has {local_count}, Trakt has {len(trakt_items)}")
            
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
    finally:
        db.close()

if __name__ == "__main__":
    list_id = int(sys.argv[1]) if len(sys.argv) > 1 else 65
    asyncio.run(verify_sync(list_id))
