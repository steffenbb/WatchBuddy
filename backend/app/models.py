
"""
models.py

SQLAlchemy models for User, SmartList, ListItem, and encrypted Secret.
"""
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, Float, Text, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
import datetime
from app.utils.timezone import utc_now

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=utc_now)
    # ... other fields ...

class SmartList(Base):
    __tablename__ = "smartlists"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    name = Column(String, nullable=False)
    criteria = Column(Text)
    created_at = Column(DateTime, default=utc_now)
    user = relationship("User", back_populates="smartlists")
    # ... other fields ...

User.smartlists = relationship("SmartList", order_by=SmartList.id, back_populates="user")

class ListItem(Base):
    __tablename__ = "list_items"
    id = Column(Integer, primary_key=True)
    smartlist_id = Column(Integer, ForeignKey("user_lists.id"))
    item_id = Column(String, nullable=False)
    title = Column(String, nullable=True)  # Store title directly for fast API access
    score = Column(Float)
    explanation = Column(Text)
    is_watched = Column(Boolean, default=False, index=True)
    watched_at = Column(DateTime, nullable=True)
    trakt_id = Column(Integer, index=True)
    media_type = Column(String, nullable=False)  # 'movie' or 'show'
    added_at = Column(DateTime, default=utc_now)
    user_list = relationship("UserList", back_populates="items")



class Secret(Base):
    __tablename__ = "secrets"
    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True, nullable=False)
    value_encrypted = Column(Text, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=utc_now)

class MediaMetadata(Base):
    __tablename__ = "media_metadata"
    id = Column(Integer, primary_key=True)
    trakt_id = Column(Integer, unique=True, nullable=False, index=True)
    tmdb_id = Column(Integer, index=True)
    imdb_id = Column(String, index=True)
    media_type = Column(String, nullable=False)  # 'movie' or 'show'
    title = Column(String, nullable=False)
    year = Column(Integer)
    overview = Column(Text)
    poster_path = Column(String)
    backdrop_path = Column(String)
    genres = Column(Text)  # JSON string
    keywords = Column(Text)  # JSON string
    language = Column(String)
    rating = Column(Float)
    votes = Column(Integer)
    popularity = Column(Float)
    is_active = Column(Boolean, default=True, index=True)
    last_updated = Column(DateTime, default=utc_now)
    created_at = Column(DateTime, default=utc_now)

class PersistentCandidate(Base):
    """Persistent pool of candidate media items (movies & shows) used for fast recommendation sourcing.

    This table denormalizes a subset of TMDB + Trakt fields so list syncs can pull
    large candidate sets without hitting external APIs. New content (>=2024 or newer than
    last ingestion checkpoint) will be appended via background ingestion tasks.

    Obscurity scoring principles:
      - High vote_average with low vote_count => potentially interesting obscure item
      - Popularity & vote_count used to distinguish mainstream vs obscure
    We'll compute an "obscurity_score" heuristically (popularity and votes inverted) when inserting/updating.
    """
    __tablename__ = "persistent_candidates"
    __table_args__ = (
        UniqueConstraint('tmdb_id', 'media_type', name='uq_persistent_candidates_tmdb_media'),
    )
    id = Column(Integer, primary_key=True)
    trakt_id = Column(Integer, unique=True, nullable=True, index=True)  # May be null if unmapped yet
    tmdb_id = Column(Integer, nullable=False, index=True)  # UNIQUE with media_type via __table_args__
    imdb_id = Column(String, index=True, nullable=True)
    media_type = Column(String, nullable=False, index=True)  # 'movie' or 'show'
    title = Column(String, nullable=False, index=True)
    original_title = Column(String, nullable=True)
    year = Column(Integer, index=True)
    release_date = Column(String, nullable=True, index=True)  # Keep raw for incremental fetch comparison (YYYY-MM-DD)
    language = Column(String, index=True)
    genres = Column(Text)  # JSON array of genre names
    keywords = Column(Text)  # JSON array of keyword strings
    overview = Column(Text)
    popularity = Column(Float, index=True)
    vote_average = Column(Float, index=True)
    vote_count = Column(Integer, index=True)
    runtime = Column(Integer, nullable=True)
    status = Column(String, nullable=True)
    poster_path = Column(String, nullable=True)
    backdrop_path = Column(String, nullable=True)
    # Derived / heuristic fields
    obscurity_score = Column(Float, index=True)  # Lower popularity & vote_count but decent rating => higher obscurity
    mainstream_score = Column(Float, index=True)  # Opposite weighting for quick mainstream queries
    freshness_score = Column(Float, index=True)  # Boost for newly released content
    is_adult = Column(Boolean, default=False, index=True)
    inserted_at = Column(DateTime, default=utc_now, index=True)
    last_refreshed = Column(DateTime, default=utc_now, index=True)
    manual = Column(Boolean, default=False, index=True)  # Mark rows inserted manually / via CSV bootstrap
    active = Column(Boolean, default=True, index=True)

    __table_args__ = (
        UniqueConstraint('tmdb_id', name='uq_persistent_candidates_tmdb'),
        {'comment': 'Persistent combined TMDB/Trakt candidate pool'}
    )

    def compute_scores(self):
        """Compute derived scores heuristically.
        Obscurity: favor items with low vote_count/popularity but respectable rating.
        Mainstream: favor high popularity & vote_count & rating.
        Freshness: based on release_date recency (simple year / days decay placeholder).
        """
        try:
            import math, datetime as _dt
            pop = self.popularity or 0.0
            votes = float(self.vote_count or 0)
            rating = self.vote_average or 0.0
            # Obscurity: good rating * inverse log(popularity+1) * inverse log(votes+2)
            self.obscurity_score = rating * (1.0 / math.log(pop + 2)) * (1.0 / math.log(votes + 3))
            # Mainstream: rating * log(popularity+2) * log(votes+3)
            self.mainstream_score = rating * math.log(pop + 2) * math.log(votes + 3)
            # Freshness: if release_date available, inverse age (simple 0-1 scaling over ~3 years)
            freshness = 0.0
            if self.release_date and len(self.release_date) >= 4:
                try:
                    # Accept YYYY or YYYY-MM-DD
                    if len(self.release_date) == 4:
                        rd = _dt.datetime(int(self.release_date), 1, 1)
                    else:
                        y, m, d = self.release_date.split('-')[:3]
                        rd = _dt.datetime(int(y), int(m), int(d))
                    days = (utc_now() - rd).days
                    freshness = max(0.0, 1.0 - (days / (365 * 3)))  # 3-year decay
                except Exception:
                    freshness = 0.0
            self.freshness_score = freshness
        except Exception:
            # Fail silently; scores remain None
            pass

class CandidateIngestionState(Base):
    """Tracks incremental ingestion checkpoints for persistent candidate updates."""
    __tablename__ = "candidate_ingestion_state"
    id = Column(Integer, primary_key=True)
    media_type = Column(String, nullable=False, unique=True)
    last_release_date = Column(String, nullable=True)  # ISO date string
    last_run_at = Column(DateTime, default=utc_now)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

class UserRating(Base):
    __tablename__ = "user_ratings"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    trakt_id = Column(Integer, nullable=False, index=True)
    media_type = Column(String, nullable=False)  # 'movie' or 'show'
    rating = Column(Integer, nullable=False)  # 1 for thumbs up, -1 for thumbs down, 0 for neutral/removed
    list_item_id = Column(Integer, ForeignKey("list_items.id"), nullable=True)  # Optional: which list item triggered this rating
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    
    # Ensure one rating per user per item
    __table_args__ = (UniqueConstraint('user_id', 'trakt_id', name='unique_user_rating'),)

class CandidateCache(Base):
    __tablename__ = "candidate_cache"
    id = Column(Integer, primary_key=True)
    cache_key = Column(String, unique=True, nullable=False, index=True)  # Hash of search parameters
    media_type = Column(String, nullable=False, index=True)  # 'movies' or 'shows'
    discovery_type = Column(String, nullable=False, index=True)  # 'trending', 'popular', 'ultra_discovery', etc.
    candidate_data = Column(Text, nullable=False)  # JSON string of candidate items
    item_count = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=utc_now, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    last_accessed = Column(DateTime, default=utc_now)
    
    # Index on expiry for cleanup
    __table_args__ = (
        {'comment': 'Cache for bulk candidate searches to avoid repeated expensive operations'}
    )

# Minimal UserList model used by CRUD and API

class UserList(Base):
    __tablename__ = "user_lists"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=True)
    title = Column(String, nullable=False)
    filters = Column(Text)  # store JSON as text
    sort_order = Column(String, nullable=True)
    item_limit = Column(Integer, nullable=True)
    sync_interval = Column(Integer, nullable=True)
    list_type = Column(String, nullable=True)
    trakt_list_id = Column(String, nullable=True, index=True)  # Trakt list ID for synchronization
    last_sync_at = Column(DateTime, nullable=True)
    last_full_sync_at = Column(DateTime, nullable=True)
    sync_status = Column(String, default="pending")  # pending, syncing, complete, error
    sync_watched_status = Column(Boolean, default=True)  # whether to sync watched status
    exclude_watched = Column(Boolean, default=False)  # whether to exclude watched items
    created_at = Column(DateTime, default=utc_now)
    last_updated = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)

# Add relationship after class definition
UserList.items = relationship("ListItem", order_by=ListItem.id, back_populates="user_list")
