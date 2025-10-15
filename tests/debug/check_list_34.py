#!/usr/bin/env python3
from app.core.database import SessionLocal
from app.models import UserList, ListItem

db = SessionLocal()
try:
    # Check list 34
    user_list = db.query(UserList).filter(UserList.id == 34).first()
    if user_list:
        print(f"List 34: {user_list.title}")
        print(f"Filters: {user_list.filters}")
        
        # Check item count
        item_count = db.query(ListItem).filter(ListItem.smartlist_id == 34).count()
        print(f"Current item count: {item_count}")
        
        if item_count < 20:  # Consider "underfilled" if less than 20 items
            print("❌ List appears underfilled")
        else:
            print("✅ List has adequate items")
    else:
        print("❌ List 34 not found")
        
finally:
    db.close()