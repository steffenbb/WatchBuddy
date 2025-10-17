#!/usr/bin/env python3
from app.core.database import SessionLocal
from app.models import ListItem

db = SessionLocal()
try:
    # Check items from list 5 (Hidden Romance + Adventure Treasures - fusion)
    list_id = 5
    items = db.query(ListItem).filter(ListItem.smartlist_id == list_id).limit(10).all()
    print(f"Sample items from list {list_id}:\n")
    for item in items:
        print(f"  Trakt ID: {item.trakt_id}")
        print(f"  Media Type: {item.media_type}")
        print(f"  Score: {item.score:.3f}")
        print(f"  Watched: {item.is_watched}")
        print(f"  Explanation: {item.explanation[:80] if item.explanation else 'N/A'}...")
        print()
finally:
    db.close()
