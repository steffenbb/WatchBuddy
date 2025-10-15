#!/usr/bin/env python3
from app.core.database import SessionLocal
from app.models import ListItem, UserList

db = SessionLocal()
try:
    # Get list 37 items
    user_list = db.query(UserList).filter(UserList.id == 37).first()
    if user_list:
        print(f"List: {user_list.title}")
        items = db.query(ListItem).filter(ListItem.smartlist_id == 37).limit(10).all()
        print(f"Total items in list: {len(items)}")
        
        for item in items[:5]:
            print(f"- ID: {item.item_id}, Trakt ID: {item.trakt_id}, Type: {item.media_type}, Score: {item.score}")
    else:
        print("List 37 not found")
finally:
    db.close()