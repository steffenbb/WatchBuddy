#!/usr/bin/env python3
from app.core.database import SessionLocal
from app.models import UserList
import sys

if len(sys.argv) < 2:
    print("Usage: python check_list_items.py <list_id>")
    sys.exit(1)

list_id = int(sys.argv[1])
db = SessionLocal()
try:
    l = db.query(UserList).filter_by(id=list_id).first()
    if not l:
        print(f"No list found with id {list_id}")
        sys.exit(1)
    print(f"Items for list {list_id} ({l.title}):")
    for i in l.items:
        print(f"  Item ID: {i.id}, Title: {i.title}, Trakt ID: {i.trakt_id}, Added At: {i.added_at}")
finally:
    db.close()
