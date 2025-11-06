"""
models_ai.py

SQLAlchemy models for AI-powered lists (AiList, AiListItem, PromptCache, FaissIndexManifest, RegenAttempt).
"""
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, Float, Text, JSON, UniqueConstraint, Index
from sqlalchemy.sql import func
import uuid
from app.models import Base

class AiList(Base):
    __tablename__ = "ai_lists"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(Integer, index=True)
    type = Column(String, index=True)  # chat/mood/theme/fusion
    prompt_text = Column(Text, nullable=True)
    normalized_prompt = Column(Text, nullable=True, index=True)
    seed_tmdb_ids = Column(JSON, nullable=True)
    filters = Column(JSON, nullable=True)
    tone_vector = Column(JSON, nullable=True)
    generated_title = Column(String, nullable=True)
    generated_theme = Column(String, nullable=True)
    item_limit = Column(Integer, default=50)
    trakt_list_id = Column(String, nullable=True)
    poster_path = Column(String(500), nullable=True)
    status = Column(String, default="queued")
    last_synced_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class AiListItem(Base):
    __tablename__ = "ai_list_items"
    id = Column(Integer, primary_key=True)
    ai_list_id = Column(String, index=True)
    tmdb_id = Column(Integer, index=True)
    trakt_id = Column(Integer, index=True, nullable=True)
    rank = Column(Integer)
    score = Column(Float)
    explanation_meta = Column(JSON)
    explanation_text = Column(String)
    added_at = Column(DateTime(timezone=True), server_default=func.now())
    removed = Column(Boolean, default=False)

class PromptCache(Base):
    __tablename__ = "ai_prompt_cache"
    id = Column(Integer, primary_key=True)
    prompt_hash = Column(String, unique=True, index=True)
    normalized_prompt = Column(Text, nullable=False)
    topk_tmdb_ids = Column(JSON, nullable=False)
    generated_title = Column(String, nullable=True)
    tone_vector = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=True)

class FaissIndexManifest(Base):
    __tablename__ = "ai_faiss_manifest"
    id = Column(Integer, primary_key=True)
    model_version = Column(String, nullable=False)
    candidate_count = Column(Integer, nullable=False)
    index_path = Column(String, nullable=False)
    mapping_path = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class RegenAttempt(Base):
    __tablename__ = "ai_regen_attempt"
    id = Column(Integer, primary_key=True)
    ai_list_id = Column(String, index=True)
    tmdb_id = Column(Integer, index=True)
    attempt_count = Column(Integer, default=1)
    last_attempt_at = Column(DateTime(timezone=True), server_default=func.now())
    status = Column(String, default="pending")
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
