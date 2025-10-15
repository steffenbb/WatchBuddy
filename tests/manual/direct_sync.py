#!/usr/bin/env python3
"""Direct sync test without HTTP"""
import asyncio
import sys
import os
sys.path.append(os.path.abspath('.'))

from app.core.database import SessionLocal
from app.services.list_sync import ListSyncService
from app.models import UserList

async def direct_sync(list_id: int):
    print(f"Direct sync test for list {list_id}...")
    
    db = SessionLocal()
    try:
        user_list = db.query(UserList).filter(UserList.id == list_id).first()
        if not user_list:
            print(f"List {list_id} not found")
            return
            
        print(f"List: {user_list.title}")
        print(f"User ID: {user_list.user_id}")
        
        # Create sync service
        sync_service = ListSyncService(user_list.user_id)
        
        # Try the sync
        try:
            result = await sync_service._sync_single_list(user_list, force_full=True)
            print(f"Sync result: {result}")
        except Exception as e:
            print(f"Sync error: {e}")
            import traceback
            traceback.print_exc()
            
    finally:
        db.close()

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python direct_sync.py <list_id>")
        sys.exit(1)
    
    list_id = int(sys.argv[1])
    asyncio.run(direct_sync(list_id))