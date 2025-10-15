"""
Test watched status sync for a list.
"""
import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.services.list_sync import ListSyncService
from app.core.database import SessionLocal
from app.models import ListItem
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_watched_sync(list_id: int):
    """Test watched status sync."""
    db = SessionLocal()
    try:
        # Get some items
        items = db.query(ListItem).filter(ListItem.smartlist_id == list_id).limit(5).all()
        logger.info(f"Sample items before sync:")
        for item in items:
            logger.info(f"  {item.title}: is_watched={item.is_watched}, watched_at={item.watched_at}")
        
        # Trigger watched-only sync
        logger.info(f"\nRunning watched-only sync...")
        sync_service = ListSyncService(user_id=1)
        result = await sync_service.sync_watched_status_only(list_id)
        logger.info(f"Sync result: {result}")
        
        # Check items after sync
        db.expire_all()  # Refresh from database
        items = db.query(ListItem).filter(ListItem.smartlist_id == list_id).limit(5).all()
        logger.info(f"\nSample items after sync:")
        for item in items:
            logger.info(f"  {item.title}: is_watched={item.is_watched}, watched_at={item.watched_at}")
        
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
    finally:
        db.close()

if __name__ == "__main__":
    list_id = int(sys.argv[1]) if len(sys.argv) > 1 else 66
    asyncio.run(test_watched_sync(list_id))
