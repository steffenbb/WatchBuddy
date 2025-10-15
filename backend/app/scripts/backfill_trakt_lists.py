"""
Backfill Trakt lists for existing custom lists that don't have trakt_list_id.
Run this once to fix existing lists.
"""
import sys
import asyncio
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.core.database import SessionLocal
from app.models import UserList
from app.services.trakt_client import TraktClient
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def backfill_trakt_lists():
    """Create Trakt lists for all custom lists that don't have trakt_list_id."""
    db = SessionLocal()
    try:
        # Find custom lists without trakt_list_id
        custom_lists = db.query(UserList).filter(
            UserList.list_type.in_(["custom", "manual"]),
            UserList.trakt_list_id.is_(None)
        ).all()
        
        logger.info(f"Found {len(custom_lists)} custom lists without Trakt list ID")
        
        if not custom_lists:
            logger.info("All custom lists already have Trakt IDs!")
            return
        
        trakt = TraktClient(user_id=1)  # Single-user mode
        
        for user_list in custom_lists:
            try:
                logger.info(f"Creating Trakt list for: {user_list.id} - {user_list.title}")
                
                trakt_result = await trakt.create_list(
                    name=user_list.title,
                    description=f"Custom list managed by WatchBuddy (ID: {user_list.id})",
                    privacy="private"
                )
                
                trakt_list_id = str(trakt_result.get("ids", {}).get("trakt"))
                logger.info(f"✓ Created Trakt list {trakt_list_id} for list {user_list.id}")
                
                # Update database
                db.query(UserList).filter(UserList.id == user_list.id).update({
                    "trakt_list_id": trakt_list_id
                })
                db.commit()
                
                logger.info(f"✓ Updated database for list {user_list.id}")
                
            except Exception as e:
                logger.error(f"✗ Failed to create Trakt list for {user_list.id}: {e}")
                db.rollback()
                continue
        
        logger.info("✓ Backfill complete!")
        
    except Exception as e:
        logger.error(f"Backfill failed: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(backfill_trakt_lists())
