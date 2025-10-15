#!/usr/bin/env python3
from app.core.database import SessionLocal
from app.models import UserList

db = SessionLocal()
try:
    user_list = db.query(UserList).filter(UserList.id == 37).first()
    if user_list:
        print(f"List: {user_list.title}")
        print(f"User ID: {user_list.user_id}")
    else:
        print("List 37 not found")
finally:
    db.close()