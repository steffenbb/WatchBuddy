
"""
models.py

SQLAlchemy models for User, SmartList, ListItem, and encrypted Secret.
"""
from sqlalchemy import Column, Integer, BigInteger, String, Boolean, ForeignKey, DateTime, Float, Text, UniqueConstraint, Index, text, LargeBinary
from sqlalchemy.orm import declarative_base
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
    trakt_id = Column(Integer, nullable=False, index=True)
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

    __table_args__ = (
        Index('ix_media_metadata_trakt_media', 'trakt_id', 'media_type', unique=True),
    )

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
    
    id = Column(Integer, primary_key=True)
    trakt_id = Column(Integer, nullable=True)  # Not globally unique - same ID can exist for movie and show - indexed via composite unique index
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
    cast = Column(Text, nullable=True)  # JSON array of cast member names for actor filtering
    production_companies = Column(Text, nullable=True)  # JSON array of studio/production company names
    production_countries = Column(Text, nullable=True)  # JSON array of production countries from TMDB
    spoken_languages = Column(Text, nullable=True)  # JSON array of spoken languages from TMDB
    budget = Column(BigInteger, nullable=True)  # Budget in USD (BigInteger for blockbuster budgets >2B)
    revenue = Column(BigInteger, nullable=True)  # Revenue in USD (BigInteger for blockbuster revenues >2B)
    tagline = Column(String, nullable=True)  # Marketing tagline
    homepage = Column(String, nullable=True)  # Official homepage URL
    embedding = Column(LargeBinary, nullable=True)  # Serialized numpy array (float16) for AI semantic search
    # TV-specific fields
    number_of_seasons = Column(Integer, nullable=True)  # Total seasons (TV shows only)
    number_of_episodes = Column(Integer, nullable=True)  # Total episodes (TV shows only)
    in_production = Column(Boolean, nullable=True)  # Still in production (TV shows)
    created_by = Column(Text, nullable=True)  # JSON array of creators (TV shows)
    networks = Column(Text, nullable=True)  # JSON array of networks (TV shows)
    episode_run_time = Column(Text, nullable=True)  # JSON array of episode runtimes (TV shows)
    first_air_date = Column(String, nullable=True)  # First air date for TV shows
    last_air_date = Column(String, nullable=True)  # Last air date for TV shows
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
        UniqueConstraint('tmdb_id', 'media_type', name='uq_persistent_candidates_tmdb_media'),
        Index('ix_persistent_candidates_trakt_id', 'trakt_id', 'media_type', unique=True, postgresql_where=text('trakt_id IS NOT NULL')),
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
    # Enum: 'mood', 'fusion', 'theme', 'chat', 'suggested', 'custom', 'trending', 'discovery', etc.
    list_type = Column(String, nullable=False, index=True)
    # For dynamic lists: persistent_id is 1-7 for the 7 core lists, null for others
    persistent_id = Column(Integer, nullable=True, index=True)
    # For dynamic lists: stores current theme context (e.g. genres, mood, etc.)
    dynamic_theme = Column(String, nullable=True)
    trakt_list_id = Column(String, nullable=True, index=True)  # Trakt list ID for synchronization
    last_sync_at = Column(DateTime, nullable=True)
    last_full_sync_at = Column(DateTime, nullable=True)
    sync_status = Column(String, default="pending")  # pending, syncing, complete, error
    sync_watched_status = Column(Boolean, default=True)  # whether to sync watched status
    exclude_watched = Column(Boolean, default=False)  # whether to exclude watched items
    poster_path = Column(String(500), nullable=True)  # Generated poster blend image path
    created_at = Column(DateTime, default=utc_now)
    last_updated = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)

    __table_args__ = (
        # For fast lookup of dynamic lists by type/id
        Index('ix_userlist_type_pid', 'user_id', 'list_type', 'persistent_id'),
    )

# Add relationship after class definition
UserList.items = relationship("ListItem", order_by=ListItem.id, back_populates="user_list")


class IndividualList(Base):
    """
    Individual Lists - fully user-controlled lists with manual item selection.
    
    Features:
    - User manually adds/removes items via semantic + literal search
    - FAISS-powered suggestions based on current list items
    - On-the-fly fit scoring shows how well items match user profile
    - Manual Trakt sync only (no automatic syncing)
    - Drag & drop reordering support
    """
    __tablename__ = "individual_lists"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now, index=True)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    trakt_list_id = Column(String, nullable=True, index=True)  # Trakt list ID if synced
    trakt_synced_at = Column(DateTime, nullable=True)  # Last successful sync timestamp
    is_public = Column(Boolean, default=False)  # For future sharing features
    poster_path = Column(String(500), nullable=True)  # Generated poster blend image path
    
    # Relationship to items
    items = relationship("IndividualListItem", back_populates="list", cascade="all, delete-orphan", order_by="IndividualListItem.order_index")
    
    __table_args__ = (
        Index('ix_individual_lists_user_created', 'user_id', 'created_at'),
        {'comment': 'User-controlled lists with manual item selection and FAISS suggestions'}
    )


class IndividualListItem(Base):
    """
    Items in an Individual List with fit scoring and ordering.
    
    Each item stores:
    - Media identifiers (tmdb_id, trakt_id)
    - Display metadata (title, poster, etc.)
    - User profile fit score (0-1, computed on-the-fly)
    - Order for drag & drop reordering
    - Additional metadata as JSON for extensibility
    """
    __tablename__ = "individual_list_items"
    
    id = Column(Integer, primary_key=True)
    list_id = Column(Integer, ForeignKey("individual_lists.id", ondelete="CASCADE"), nullable=False, index=True)
    tmdb_id = Column(Integer, nullable=False, index=True)
    trakt_id = Column(Integer, nullable=True, index=True)  # May be null if not yet mapped
    media_type = Column(String, nullable=False, index=True)  # 'movie' or 'show'
    title = Column(String, nullable=False)
    original_title = Column(String, nullable=True)
    year = Column(Integer, nullable=True)
    overview = Column(Text, nullable=True)
    poster_path = Column(String, nullable=True)
    backdrop_path = Column(String, nullable=True)
    genres = Column(Text, nullable=True)  # JSON array
    order_index = Column(Float, nullable=False, index=True)  # Float for easier reordering (insert between items)
    fit_score = Column(Float, nullable=True)  # 0-1 score from user profile matching
    added_at = Column(DateTime, default=utc_now, index=True)
    metadata_json = Column(Text, nullable=True)  # Additional metadata (mood, theme, fusion, etc.) as JSON
    
    # Relationship back to list
    list = relationship("IndividualList", back_populates="items")
    
    __table_args__ = (
        Index('ix_individual_list_items_list_order', 'list_id', 'order_index'),
        # Prevent duplicate items in same list
        UniqueConstraint('list_id', 'tmdb_id', 'media_type', name='uq_individual_list_item'),
        {'comment': 'Items in Individual Lists with fit scores and ordering'}
    )


class TraktWatchHistory(Base):
    """
    Persistent storage of user's Trakt watch history for phase detection.
    Stores individual watch events with timestamps for temporal pattern analysis.
    """
    __tablename__ = "trakt_watch_history"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    trakt_id = Column(Integer, nullable=False, index=True)
    tmdb_id = Column(Integer, nullable=True, index=True)
    media_type = Column(String, nullable=False, index=True)  # 'movie' or 'show'
    title = Column(String, nullable=False)
    year = Column(Integer, nullable=True)
    watched_at = Column(DateTime, nullable=False, index=True)  # When user watched this item
    user_trakt_rating = Column(Integer, nullable=True)  # User's 1-10 rating from Trakt (null if unrated)
    # Store enriched metadata snapshot for phase analysis without external lookups
    genres = Column(Text, nullable=True)  # JSON array
    keywords = Column(Text, nullable=True)  # JSON array
    overview = Column(Text, nullable=True)
    poster_path = Column(String, nullable=True)
    collection_id = Column(Integer, nullable=True, index=True)  # TMDB collection ID for franchise detection
    collection_name = Column(String, nullable=True)
    runtime = Column(Integer, nullable=True)
    language = Column(String, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    
    __table_args__ = (
        # One watch event per user per item per timestamp (allow rewatches at different times)
        UniqueConstraint('user_id', 'trakt_id', 'watched_at', name='uq_watch_event'),
        Index('ix_watch_history_user_time', 'user_id', 'watched_at'),
        {'comment': 'User watch history from Trakt for phase detection and analysis'}
    )


class UserPhase(Base):
    """
    Detected thematic phases in user's viewing history.
    Each phase represents a cluster of content watched during a time period,
    characterized by common themes, genres, franchises, or moods.
    Phases are auto-detected every 24 hours from watch history.
    """
    __tablename__ = "user_phases"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    label = Column(String, nullable=False)  # e.g., "Space Sci-Fi Phase", "Star Wars Phase"
    icon = Column(String, nullable=True)  # Emoji or SVG key (e.g., "🚀", "thriller-icon")
    start_at = Column(DateTime, nullable=False, index=True)
    end_at = Column(DateTime, nullable=True, index=True)  # NULL = currently active
    
    # Phase composition data
    tmdb_ids = Column(Text, nullable=False)  # JSON array of member tmdb_ids
    trakt_ids = Column(Text, nullable=True)  # JSON array of member trakt_ids
    media_types = Column(Text, nullable=True)  # JSON array matching tmdb_ids (movie/show)
    
    # Phase characterization
    dominant_genres = Column(Text, nullable=True)  # JSON array of top genres
    dominant_keywords = Column(Text, nullable=True)  # JSON array of top keywords
    franchise_id = Column(Integer, nullable=True, index=True)  # TMDB collection ID if franchise-dominated
    franchise_name = Column(String, nullable=True)
    
    # Computed metrics
    cohesion = Column(Float, nullable=False)  # Average cosine similarity among cluster members (0-1)
    watch_density = Column(Float, nullable=False)  # Fraction of watch window occupied by this cluster
    franchise_dominance = Column(Float, default=0.0)  # Fraction of items from same franchise
    thematic_consistency = Column(Float, default=0.0)  # Genre/mood agreement
    phase_score = Column(Float, nullable=False, index=True)  # Overall phase quality score
    
    # Metadata
    item_count = Column(Integer, nullable=False)  # Number of items in phase
    movie_count = Column(Integer, default=0)
    show_count = Column(Integer, default=0)
    avg_runtime = Column(Integer, nullable=True)
    top_language = Column(String, nullable=True)
    
    # Phase type classification
    phase_type = Column(String, nullable=False, index=True)  # 'active', 'minor', 'historical', 'future'
    
    # AI-generated explanation
    explanation = Column(Text, nullable=True)  # "Why this phase?" - template or LLM-generated
    
    # Representative posters for UI (JSON array of 3-6 poster paths)
    representative_posters = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=utc_now, index=True)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    
    __table_args__ = (
        Index('ix_user_phases_user_time', 'user_id', 'start_at', 'end_at'),
        Index('ix_user_phases_active', 'user_id', 'phase_type', 'end_at'),
        {'comment': 'Detected viewing phases from watch history clustering'}
    )


class UserPhaseEvent(Base):
    """
    Audit log of phase lifecycle events (created, closed, converted to list, shared).
    Useful for analytics and debugging phase detection.
    """
    __tablename__ = "user_phase_events"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    phase_id = Column(Integer, ForeignKey("user_phases.id", ondelete="CASCADE"), nullable=False, index=True)
    action = Column(String, nullable=False, index=True)  # 'created', 'closed', 'converted', 'shared', 'updated'
    meta = Column(Text, nullable=True)  # JSON metadata about the event
    timestamp = Column(DateTime, default=utc_now, index=True)
    
    __table_args__ = (
        Index('ix_phase_events_user_time', 'user_id', 'timestamp'),
        {'comment': 'Audit log for phase lifecycle events'}
    )


class UserShowProgress(Base):
    """
    Tracks user progress through TV shows for 'Upcoming Continuations' feature.
    Pre-computed nightly from TraktWatchHistory + TMDB season/episode data.
    """
    __tablename__ = "user_show_progress"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    trakt_id = Column(Integer, nullable=False, index=True)
    tmdb_id = Column(Integer, nullable=True, index=True)
    title = Column(String, nullable=False)
    poster_path = Column(String, nullable=True)
    
    # Progress tracking
    last_watched_season = Column(Integer, nullable=False)
    last_watched_episode = Column(Integer, nullable=False)
    last_watched_at = Column(DateTime, nullable=False)
    
    # Next episode info (computed from TMDB)
    next_episode_season = Column(Integer, nullable=True)  # NULL if show completed
    next_episode_number = Column(Integer, nullable=True)
    next_episode_title = Column(String, nullable=True)
    next_episode_air_date = Column(String, nullable=True)  # YYYY-MM-DD
    
    # Show totals (from TMDB/persistent_candidates)
    total_seasons = Column(Integer, nullable=True)
    total_episodes = Column(Integer, nullable=True)
    show_status = Column(String, nullable=True)  # 'Returning Series', 'Ended', 'In Production'
    
    # Metadata
    is_completed = Column(Boolean, default=False, index=True)  # User finished the show
    is_behind = Column(Boolean, default=False, index=True)  # Next episode already aired
    episodes_behind = Column(Integer, default=0)  # How many episodes behind
    
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    created_at = Column(DateTime, default=utc_now)
    
    __table_args__ = (
        UniqueConstraint('user_id', 'trakt_id', name='uq_user_show_progress'),
        Index('ix_user_show_progress_behind', 'user_id', 'is_behind', 'is_completed'),
        {'comment': 'User TV show progress for continuation recommendations'}
    )


class OverviewCache(Base):
    """
    Pre-computed cache for Overview page modules (Investment Tracker, New Shows, Trending, Upcoming).
    Refreshed nightly by Celery task. Stores JSON blobs per user per module.
    """
    __tablename__ = "overview_cache"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    module_type = Column(String, nullable=False, index=True)  # 'investment', 'new_shows', 'trending', 'upcoming'
    
    # Cached data (JSON string)
    data_json = Column(Text, nullable=False)  # Module-specific structure
    
    # Module priority for dynamic reordering (computed by meta-ranking logic)
    priority_score = Column(Float, nullable=False, default=0.0, index=True)
    
    # Metadata
    item_count = Column(Integer, default=0)
    computed_at = Column(DateTime, nullable=False, default=utc_now, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)  # TTL: 24 hours
    
    __table_args__ = (
        UniqueConstraint('user_id', 'module_type', name='uq_overview_cache_user_module'),
        Index('ix_overview_cache_expiry', 'user_id', 'expires_at'),
        {'comment': 'Pre-computed cache for Overview page discovery modules'}
    )


class TrendingIngestionQueue(Base):
    """
    Queue of TMDB IDs from trending/upcoming lists pending ingestion into persistent_candidates.
    Separate from bulk ingestion - targets specific high-value items.
    """
    __tablename__ = "trending_ingestion_queue"
    
    id = Column(Integer, primary_key=True)
    tmdb_id = Column(Integer, nullable=False, index=True)
    media_type = Column(String, nullable=False, index=True)  # 'movie' or 'show'
    source_list = Column(String, nullable=False, index=True)  # 'trending_week', 'trending_day', 'upcoming', 'popular'
    
    # Ingestion status
    status = Column(String, nullable=False, default='pending', index=True)  # 'pending', 'ingesting', 'completed', 'failed'
    trakt_id = Column(Integer, nullable=True, index=True)  # Filled after successful ingestion
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)
    
    # Priority (higher = ingest first)
    priority = Column(Integer, default=0, index=True)
    
    # Timestamps
    discovered_at = Column(DateTime, default=utc_now, index=True)
    ingested_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    
    __table_args__ = (
        UniqueConstraint('tmdb_id', 'media_type', 'source_list', name='uq_trending_queue_item'),
        Index('ix_trending_queue_status_priority', 'status', 'priority'),
        {'comment': 'Queue for targeted ingestion of trending/upcoming TMDB items'}
    )

