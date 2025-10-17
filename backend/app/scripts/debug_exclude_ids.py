#!/usr/bin/env python3
"""Debug exclude_ids collection."""
import sys
sys.path.insert(0, '/app')

from app.core.database import SessionLocal
from app.models import ListItem, UserList

db = SessionLocal()
try:
    # Test what list 15 would exclude
    other_items = db.query(ListItem).join(UserList).filter(
        UserList.user_id == 1,
        UserList.id != 15  # Simulating sync of list 15
    ).all()
    
    exclude_ids = set()
    for item in other_items:
        if item.trakt_id:
            exclude_ids.add(item.trakt_id)
    
    print(f"When syncing list 15, would exclude {len(exclude_ids)} items from other lists")
    print(f"Sample IDs: {list(exclude_ids)[:20]}")
    
    # Now check what list 15 actually has
    list15_items = db.query(ListItem).filter(ListItem.smartlist_id == 15).all()
    list15_trakt_ids = set([item.trakt_id for item in list15_items if item.trakt_id])
    
    print(f"\nList 15 has {len(list15_trakt_ids)} items")
    
    # Check overlap
    overlap = exclude_ids.intersection(list15_trakt_ids)
    print(f"Items in list 15 that SHOULD have been excluded: {len(overlap)}")
    if overlap:
        print(f"Example overlapping items: {list(overlap)[:10]}")
        
finally:
    db.close()
