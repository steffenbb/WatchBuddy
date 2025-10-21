"""
db_schema_ai.py
- Alembic migration hints for AI list tables and indices.
"""
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text, ForeignKey, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base
import uuid
import datetime

Base = declarative_base()

def utc_now():
    return datetime.datetime.utcnow()

class AiList(Base):
    __tablename__ = "ai_lists"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(Integer, index=True)
    type = Column(String, index=True)  # 'chat','mood','theme','fusion'
    prompt_text = Column(Text)
    normalized_prompt = Column(Text)
    filters = Column(JSON)
    seed_tmdb_ids = Column(JSON)
    tone_vector = Column(JSON)
    generated_title = Column(String)
    item_limit = Column(Integer)
    trakt_list_id = Column(String, nullable=True)
    status = Column(String, index=True)
    last_synced_at = Column(DateTime)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    last_error = Column(Text, nullable=True)

class AiListItem(Base):
    __tablename__ = "ai_list_items"
    id = Column(Integer, primary_key=True)
    ai_list_id = Column(UUID(as_uuid=True), ForeignKey("ai_lists.id"), index=True)
    tmdb_id = Column(Integer, index=True)
    trakt_id = Column(Integer, index=True, nullable=True)
    rank = Column(Integer)
    score = Column(Float)
    explanation_meta = Column(JSON)
    explanation_text = Column(Text)
    added_at = Column(DateTime, default=utc_now)
    removed = Column(Boolean, default=False)

class PromptCache(Base):
    __tablename__ = "prompt_cache"
    id = Column(Integer, primary_key=True)
    prompt_hash = Column(String, unique=True, index=True)
    topk_tmdb_ids = Column(JSON)
    generated_title = Column(String)
    tone_vector = Column(JSON)
    created_at = Column(DateTime, default=utc_now)
    expires_at = Column(DateTime)

class FaissIndexManifest(Base):
    __tablename__ = "faiss_index_manifest"
    id = Column(Integer, primary_key=True)
    model_version = Column(String)
    candidate_count = Column(Integer)
    index_path = Column(String)
    mapping_path = Column(String)
    created_at = Column(DateTime, default=utc_now)

class RegenAttempt(Base):
    __tablename__ = "regen_attempt"
    id = Column(Integer, primary_key=True)
    ai_list_id = Column(UUID(as_uuid=True), ForeignKey("ai_lists.id"), index=True)
    tmdb_id = Column(Integer, index=True)
    attempt_count = Column(Integer, default=0)
    last_attempt_at = Column(DateTime, default=utc_now)
