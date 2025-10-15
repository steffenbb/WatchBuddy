#!/usr/bin/env python3
from app.core.database import SessionLocal
from app.models import UserList
import json

db = SessionLocal()
try:
    user_list = db.query(UserList).filter(UserList.id == 37).first()
    if user_list:
        print(f"List: {user_list.title}")
        print(f"Filters: {user_list.filters}")
        filters = json.loads(user_list.filters) if user_list.filters else {}
        print(f"Parsed filters: {json.dumps(filters, indent=2)}")
    else:
        print("List 37 not found")
finally:
    db.close()