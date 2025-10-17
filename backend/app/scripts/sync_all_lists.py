#!/usr/bin/env python3
"""Sync all user lists."""
import asyncio
import sys
sys.path.insert(0, '/app')

from app.core.database import SessionLocal
from app.services.list_sync import ListSyncService

async def sync_all():
    """Sync all lists for user."""
    service = ListSyncService(user_id=1)
    
    # Get all list objects
    db = SessionLocal()
    try:
        from app.models import UserList
        lists = db.query(UserList).filter(UserList.user_id == 1).all()
    finally:
        db.close()
    
    print(f"Found {len(lists)} lists to sync: {[l.id for l in lists]}")
    
    for user_list in lists:
        print(f'\n=== Syncing list {user_list.id}... ===')
        try:
            result = await service._sync_single_list(user_list, force_full=True)
            print(f'✓ List {user_list.id} synced successfully')
        except Exception as e:
            print(f'✗ List {user_list.id} failed: {e}')
            import traceback
            traceback.print_exc()
    
    print("\n=== All syncs completed ===")

if __name__ == "__main__":
    asyncio.run(sync_all())
