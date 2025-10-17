#!/usr/bin/env python3
"""Fix duplicate Trakt list IDs by clearing the trakt_list_id for list 8."""
from app.core.database import SessionLocal
from app.models import UserList

db = SessionLocal()
try:
    # Find list 8 and clear its trakt_list_id
    list_8 = db.query(UserList).filter(UserList.id == 8).first()
    if list_8:
        print(f"List 8: {list_8.title} - Trakt ID: {list_8.trakt_list_id}")
        list_8.trakt_list_id = None
        db.commit()
        print("Cleared Trakt list ID for list 8")
    else:
        print("List 8 not found")
finally:
    db.close()
