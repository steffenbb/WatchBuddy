"""
Trigger a manual sync for a list via the API.
"""
import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.services.list_sync import ListSyncService
from app.core.database import SessionLocal
from app.models import UserList
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def trigger_sync(list_id: int):
    """Trigger sync for a list."""
    db = SessionLocal()
    try:
        user_list = db.query(UserList).filter(UserList.id == list_id).first()
        if not user_list:
            logger.error(f"List {list_id} not found")
            return
        
        logger.info(f"Triggering sync for list {list_id}: {user_list.title}")
        logger.info(f"Trakt list ID: {user_list.trakt_list_id}")
        
        sync_service = ListSyncService(user_id=user_list.user_id or 1)
        result = await sync_service._sync_single_list(user_list, force_full=True)
        
        logger.info(f"Sync result: {result}")
        
    except Exception as e:
        logger.error(f"Sync failed: {e}", exc_info=True)
    finally:
        db.close()

if __name__ == "__main__":
    list_id = int(sys.argv[1]) if len(sys.argv) > 1 else 65
    asyncio.run(trigger_sync(list_id))
