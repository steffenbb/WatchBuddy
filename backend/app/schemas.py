
"""
schemas.py

Pydantic schemas for User, SmartList, ListItem, and Secret.
"""
from pydantic import BaseModel
from typing import Optional, Dict, Any
import datetime

class UserSchema(BaseModel):
    id: int
    email: str
    created_at: datetime.datetime
    class Config:
        orm_mode = True

class SmartListSchema(BaseModel):
    id: int
    user_id: int
    name: str
    criteria: Optional[str]
    created_at: datetime.datetime
    class Config:
        orm_mode = True

class ListItemSchema(BaseModel):
    id: int
    smartlist_id: int
    item_id: str
    score: Optional[float]
    explanation: Optional[str]
    added_at: datetime.datetime
    class Config:
        orm_mode = True

class SecretSchema(BaseModel):
    id: int
    key: str
    value_encrypted: str
    user_id: Optional[int]
    created_at: datetime.datetime
    class Config:
        orm_mode = True


# Payloads
class ListCreate(BaseModel):
    title: str
    filters: Dict[str, Any]
    sort_order: Optional[str] = None
    item_limit: Optional[int] = 50
    sync_interval: Optional[int] = None
    list_type: Optional[str] = "custom"
