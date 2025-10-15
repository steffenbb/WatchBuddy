#!/usr/bin/env python3
from app.core.database import SessionLocal
from app.models import UserList

db = SessionLocal()
try:
    lists = db.query(UserList).all()
    print("Available lists:")
    for l in lists[:5]:
        print(f"  ID {l.id}: {l.title}")
finally:
    db.close()