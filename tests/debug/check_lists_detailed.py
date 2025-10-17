#!/usr/bin/env python3
from app.core.database import SessionLocal
from app.models import UserList, ListItem

db = SessionLocal()
try:
    lists = db.query(UserList).all()
    print(f"Total lists: {len(lists)}\n")
    for l in lists:
        item_count = db.query(ListItem).filter(ListItem.smartlist_id == l.id).count()
        print(f"ID: {l.id}")
        print(f"  Title: {l.title}")
        print(f"  Type: {l.list_type}")
        print(f"  Persistent ID: {l.persistent_id}")
        print(f"  Dynamic Theme: {l.dynamic_theme}")
        print(f"  Trakt List ID: {l.trakt_list_id}")
        print(f"  Items: {item_count}")
        print(f"  Last Sync: {l.last_sync_at}")
        print()
finally:
    db.close()
