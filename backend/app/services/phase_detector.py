"""
phase_detector.py

Core service for detecting viewing phases from user watch history.
Uses HDBSCAN clustering on embeddings, scores phases, generates labels,
and detects franchises. Runs daily to update user phases.

Key workflow:
1. Fetch watch history for time windows (2-week periods)
2. Load embeddings for watched items from FAISS/DB
3. Cluster embeddings with HDBSCAN
4. Score clusters (cohesion, density, franchise dominance, thematic consistency)
5. Label phases (franchise names or genre+keyword combinations)
6. Persist phases to database
7. Close outdated phases, create new ones
"""
import logging
import numpy as np
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta, timezone
from collections import Counter
import json
import math

try:
    import hdbscan
    HDBSCAN_AVAILABLE = True
except ImportError:
    HDBSCAN_AVAILABLE = False
    logging.warning("HDBSCAN not installed, phase detection will use k-means fallback")

from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.metrics.pairwise import cosine_similarity

from app.core.database import SessionLocal
from app.models import TraktWatchHistory, UserPhase, UserPhaseEvent, PersistentCandidate
from app.services.ai_engine.faiss_index import deserialize_embedding
from app.utils.timezone import utc_now, ensure_utc

logger = logging.getLogger(__name__)

# Phase detection parameters
WATCH_WINDOW_DAYS = 14  # 2-week time windows
WATCH_WINDOW_N = 5  # Min items to analyze per time window
PHASE_MIN_SCORE_ACTIVE = 0.55  # Minimum score for "active" phase (lowered for 2-week windows)
PHASE_MIN_SCORE_MINOR = 0.35  # Minimum score for "minor" phase (historical)
PHASE_CLOSE_DAYS = 14  # Close phase if no watches in N days
FRANCHISE_DOMINANCE_THRESHOLD = 0.4  # 40% same collection = franchise phase
MIN_CLUSTER_SIZE = 2  # Minimum items to form a phase

# Genre to emoji mapping
GENRE_EMOJI_MAP = {
    "sci-fi": "🚀",
    "science fiction": "🚀",
    "space": "🌌",
    "thriller": "🧨",
    "horror": "👻",
    "comedy": "😂",
    "romance": "❤️",
    "action": "💥",
    "adventure": "🗺️",
    "drama": "🎭",
    "fantasy": "🧙",
    "mystery": "🔍",
    "crime": "🕵️",
    "documentary": "📹",
    "animation": "🎨",
    "family": "👨‍👩‍👧",
    "war": "⚔️",
    "western": "🤠",
    "music": "🎵",
    "history": "📜"
}


class PhaseDetector:
    """
    Detects and manages viewing phases for a user.
    """
    
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.db = SessionLocal()
    
    def __del__(self):
        try:
            self.db.close()
        except Exception:
            pass
    
    def detect_all_phases(self) -> List[UserPhase]:
        """
        Main entry point: detect all phases from user's complete watch history.
        Analyzes history in 3-month windows, creating phases for each period.
        Also creates a "future" phase prediction based on recent trends.
        """
        logger.info(f"[PhaseDetector] Starting full phase detection for user {self.user_id}")
        # Acquire Redis lock to prevent concurrent phase detection runs for same user
        lock_key = f"phase_detect_lock:{self.user_id}"
        lock_acquired = False
        try:
            from app.core.redis_client import get_redis_sync
            r_lock = get_redis_sync()
            # Set lock with short expiry (10 minutes) if not exists
            if r_lock.set(lock_key, "1", nx=True, ex=600):
                lock_acquired = True
            else:
                logger.warning(f"[PhaseDetector] Another phase detection is in progress for user {self.user_id}; aborting duplicate run.")
                return []
        except Exception as e:
            logger.warning(f"[PhaseDetector] Redis lock unavailable ({e}); proceeding without lock (risk of duplicate runs)")
        
        try:
            # Get watch history date range
            earliest, latest = self._get_history_date_range()
            
            if not earliest or not latest:
                logger.info(f"[PhaseDetector] No watch history for user {self.user_id}")
                return []
            
            logger.info(f"[PhaseDetector] History range: {earliest} to {latest}")
            
            # Generate 2-week windows from earliest to now
            windows = self._generate_time_windows(earliest, latest, days=WATCH_WINDOW_DAYS)
            logger.info(f"[PhaseDetector] Analyzing {len(windows)} time windows (2-week periods)")
            
            all_phases = []
            
            # Detect phases for each window
            for window_start, window_end in windows:
                logger.debug(f"[PhaseDetector] Processing window: {window_start} to {window_end}")
                
                phases = self._detect_phases_in_window(window_start, window_end)
                all_phases.extend(phases)
            
            # Detect future phase (prediction based on last 30 days)
            future_phase = self._detect_future_phase()
            if future_phase:
                all_phases.append(future_phase)
            
            # Close outdated phases
            self._close_stale_phases()
            
            logger.info(f"[PhaseDetector] ✅ Detected {len(all_phases)} phases for user {self.user_id}")
            return all_phases
            
        except Exception as e:
            logger.error(f"[PhaseDetector] Phase detection failed for user {self.user_id}: {e}", exc_info=True)
            raise
        finally:
            # Release lock if acquired
            if lock_acquired:
                try:
                    from app.core.redis_client import get_redis_sync
                    get_redis_sync().delete(lock_key)
                except Exception:
                    pass
    
    def _get_history_date_range(self) -> Tuple[Optional[datetime], Optional[datetime]]:
        """Get earliest and latest watch dates for user."""
        result = self.db.query(
            TraktWatchHistory.watched_at
        ).filter(
            TraktWatchHistory.user_id == self.user_id
        ).order_by(TraktWatchHistory.watched_at.asc()).first()
        
        earliest = ensure_utc(result[0]) if result else None
        
        result = self.db.query(
            TraktWatchHistory.watched_at
        ).filter(
            TraktWatchHistory.user_id == self.user_id
        ).order_by(TraktWatchHistory.watched_at.desc()).first()
        
        latest = ensure_utc(result[0]) if result else None
        
        return earliest, latest
    
    def _generate_time_windows(self, start: datetime, end: datetime, days: int = 14) -> List[Tuple[datetime, datetime]]:
        """Generate non-overlapping time windows for analysis."""
        # Normalize to UTC-aware datetimes to avoid naive/aware comparison issues
        start = ensure_utc(start)
        end = ensure_utc(end)
        windows = []
        current = start
        
        while current < end:
            window_end = min(current + timedelta(days=days), end)
            windows.append((current, window_end))
            current += timedelta(days=days)  # Non-overlapping windows
        
        return windows
    
    def _detect_phases_in_window(self, start: datetime, end: datetime) -> List[UserPhase]:
        """
        Detect phases within a specific time window.
        Returns list of detected phases (may be multiple clusters per window).
        """
        # Normalize window bounds
        start = ensure_utc(start)
        end = ensure_utc(end)

        # Fetch watch history for window
        watches = self.db.query(TraktWatchHistory).filter(
            TraktWatchHistory.user_id == self.user_id,
            TraktWatchHistory.watched_at >= start,
            TraktWatchHistory.watched_at <= end
        ).order_by(TraktWatchHistory.watched_at.desc()).all()
        
        # Convert to lightweight dicts and expunge to free memory
        watch_data_raw = [
            {
                'trakt_id': w.trakt_id,
                'tmdb_id': w.tmdb_id,
                'title': w.title,
                'media_type': w.media_type,
                'watched_at': w.watched_at,
                'collection_name': getattr(w, 'collection_name', None),
                'genres': w.genres
            }
            for w in watches
        ]
        
        # Expunge all watches from session to free memory
        for w in watches:
            self.db.expunge(w)
        watches.clear()
        
        if len(watch_data_raw) < MIN_CLUSTER_SIZE:
            logger.debug(f"[PhaseDetector] Window {start} to {end}: insufficient watches ({len(watch_data_raw)})")
            return []
        
        # Load embeddings for watched items
        embeddings, watch_data = self._load_embeddings_for_watches(watch_data_raw)
        
        # Clear raw data to free memory
        watch_data_raw.clear()
        
        if len(embeddings) < MIN_CLUSTER_SIZE:
            logger.debug(f"[PhaseDetector] Window {start} to {end}: insufficient embeddings ({len(embeddings)})")
            return []
        
        # Cluster embeddings
        cluster_labels = self._cluster_embeddings(embeddings)
        
        if cluster_labels is None or len(set(cluster_labels)) == 0:
            logger.debug(f"[PhaseDetector] Window {start} to {end}: clustering failed")
            return []
        
        # Analyze each cluster
        phases = []
        unique_labels = set(cluster_labels)
        
        for cluster_id in unique_labels:
            if cluster_id == -1:  # Noise cluster from HDBSCAN
                continue
            
            cluster_mask = cluster_labels == cluster_id
            cluster_watches = [watch_data[i] for i, is_member in enumerate(cluster_mask) if is_member]
            cluster_embeddings = embeddings[cluster_mask]

            # Enrich with collection (franchise) info on-demand (limited calls)
            try:
                self._ensure_collection_info(cluster_watches)
            except Exception as e:
                logger.debug(f"[PhaseDetector] Collection enrichment skipped: {e}")
            
            if len(cluster_watches) < MIN_CLUSTER_SIZE:
                continue
            
            # Compute phase metrics
            phase_metrics = self._compute_phase_metrics(
                cluster_watches,
                cluster_embeddings,
                total_window_watches=len(watches)
            )
            
            # Check score threshold
            if phase_metrics["phase_score"] < PHASE_MIN_SCORE_MINOR:
                logger.debug(f"[PhaseDetector] Cluster score too low: {phase_metrics['phase_score']:.2f}")
                continue
            
            # Determine phase type
            if phase_metrics["phase_score"] >= PHASE_MIN_SCORE_ACTIVE:
                # Compare timezone-aware UTC datetimes
                cutoff_recent = utc_now() - timedelta(days=PHASE_CLOSE_DAYS)
                phase_type = "active" if end >= cutoff_recent else "historical"
            else:
                phase_type = "minor"
            
            # Generate label and icon
            label, icon = self._generate_phase_label(cluster_watches, phase_metrics)
            
            # Generate explanation
            explanation = self._generate_explanation(cluster_watches, phase_metrics, label)
            
            # Select representative posters
            posters = self._select_representative_posters(cluster_watches, count=6)
            
            # Check if phase already exists (similar timeframe and content)
            existing_phase = self._find_similar_phase(cluster_watches, start, end)
            
            if existing_phase:
                # Update existing phase
                self._update_phase(existing_phase, cluster_watches, phase_metrics, label, icon, explanation, posters, phase_type)
                phases.append(existing_phase)
            else:
                # Create new phase
                new_phase = self._create_phase(cluster_watches, phase_metrics, label, icon, explanation, posters, start, end, phase_type)
                phases.append(new_phase)
        
        return phases
    
    def _load_embeddings_for_watches(self, watches: List[Dict]) -> Tuple[np.ndarray, List[Dict]]:
        """
        Load embeddings for watched items from database.
        Returns (embeddings_array, watch_data_list) where watch_data contains watch + metadata.
        Param watches is now a list of dicts (not model objects).
        """
        embeddings_list = []
        watch_data = []
        
        for watch in watches:
            # Try to get embedding from PersistentCandidate
            candidate = None
            tmdb_id = watch.get('tmdb_id')
            media_type = watch.get('media_type')
            
            if tmdb_id:
                candidate = self.db.query(PersistentCandidate).filter(
                    PersistentCandidate.tmdb_id == tmdb_id,
                    PersistentCandidate.media_type == media_type
                ).first()
            
            if not candidate or not candidate.embedding:
                logger.debug(f"[PhaseDetector] No embedding for {watch.get('title')} (tmdb_id={tmdb_id})")
                continue
            
            # Deserialize embedding
            try:
                emb = deserialize_embedding(candidate.embedding)
                embeddings_list.append(emb)
                
                # Store watch + candidate metadata
                watch_data.append({
                    "watch": watch,
                    "candidate": candidate,
                    "tmdb_id": tmdb_id,
                    "trakt_id": watch.get('trakt_id'),
                    "title": watch.get('title'),
                    "genres": self._parse_json(watch.get('genres') or candidate.genres),
                    "keywords": self._parse_json(candidate.keywords),
                    "collection_id": watch.get('collection_name'),  # Note: collection_id stored as collection_name in dict
                    "collection_name": watch.get('collection_name'),
                    "poster_path": candidate.poster_path,
                    "overview": candidate.overview,
                    "runtime": candidate.runtime,
                    "language": candidate.language,
                    # Ensure timezone-aware UTC to avoid comparison errors later
                    "watched_at": ensure_utc(watch.get('watched_at')),
                    "media_type": media_type
                })
            except Exception as e:
                logger.warning(f"[PhaseDetector] Failed to load embedding for {watch.title}: {e}")
                continue
        
        if not embeddings_list:
            return np.array([]), []
        
        embeddings_array = np.vstack(embeddings_list).astype(np.float32)
        return embeddings_array, watch_data

    def _ensure_collection_info(self, cluster_watches: List[Dict]):
        """Fetch TMDB collection info for movies missing it (on-demand, few calls)."""
        # Import async client lazily
        try:
            from app.services.tmdb_client import fetch_tmdb_metadata
        except Exception:
            return
        # Limit API calls
        to_fetch = [w for w in cluster_watches if w.get("media_type") == "movie" and not w.get("collection_id") and w.get("tmdb_id")]
        if not to_fetch:
            return
        max_calls = min(5, len(to_fetch))
        for w in to_fetch[:max_calls]:
            tmdb_id = w.get("tmdb_id")
            try:
                import asyncio
                data = asyncio.run(fetch_tmdb_metadata(int(tmdb_id), media_type='movie'))
                if data and data.get('belongs_to_collection'):
                    coll = data['belongs_to_collection']
                    w["collection_id"] = coll.get('id')
                    w["collection_name"] = coll.get('name')
            except Exception:
                continue
    
    def _cluster_embeddings(self, embeddings: np.ndarray) -> Optional[np.ndarray]:
        """
        Cluster embeddings using HDBSCAN (preferred) or k-means (fallback).
        Returns cluster labels array or None if clustering fails.
        """
        if len(embeddings) < MIN_CLUSTER_SIZE:
            return None
        
        # Try HDBSCAN first (density-based, better for variable cluster sizes)
        if HDBSCAN_AVAILABLE:
            try:
                clusterer = hdbscan.HDBSCAN(
                    min_cluster_size=MIN_CLUSTER_SIZE,
                    min_samples=1,
                    metric='euclidean',  # Embeddings are already normalized
                    cluster_selection_epsilon=0.1
                )
                labels = clusterer.fit_predict(embeddings)
                
                # Check if clustering produced meaningful results
                unique_labels = set(labels)
                if len(unique_labels) > 1:  # At least one cluster beyond noise
                    logger.debug(f"[PhaseDetector] HDBSCAN found {len(unique_labels)} clusters")
                    return labels
            except Exception as e:
                logger.warning(f"[PhaseDetector] HDBSCAN failed: {e}, falling back to k-means")
        
        # Fallback to k-means
        try:
            # Try k=2,3,4 and choose best by silhouette score
            best_labels = None
            best_score = -1
            
            for k in range(2, min(5, len(embeddings))):
                kmeans = KMeans(n_clusters=k, random_state=42, n_init=10)
                labels = kmeans.fit_predict(embeddings)
                
                # Calculate silhouette score
                try:
                    score = silhouette_score(embeddings, labels)
                    if score > best_score:
                        best_score = score
                        best_labels = labels
                except Exception:
                    pass
            
            if best_labels is not None:
                logger.debug(f"[PhaseDetector] K-means clustering complete (silhouette={best_score:.2f})")
                return best_labels
            
        except Exception as e:
            logger.error(f"[PhaseDetector] K-means fallback failed: {e}")
        
        return None
    
    def _compute_phase_metrics(self, cluster_watches: List[Dict], cluster_embeddings: np.ndarray, total_window_watches: int) -> Dict:
        """
        Compute metrics for a phase cluster.
        Returns dict with cohesion, watch_density, franchise_dominance, thematic_consistency, phase_score.
        """
        # Cohesion: average cosine similarity among cluster members
        if len(cluster_embeddings) > 1:
            cos_sim_matrix = cosine_similarity(cluster_embeddings)
            # Get upper triangle (excluding diagonal)
            upper_tri = cos_sim_matrix[np.triu_indices_from(cos_sim_matrix, k=1)]
            cohesion = float(np.mean(upper_tri)) if len(upper_tri) > 0 else 0.5
        else:
            cohesion = 1.0  # Single item = perfect cohesion
        
        # Watch density: fraction of window occupied by this cluster
        watch_count = len(cluster_watches)
        watch_density = watch_count / max(total_window_watches, 1)
        
        # Franchise dominance: fraction from same collection
        collection_ids = [w["collection_id"] for w in cluster_watches if w["collection_id"]]
        franchise_dominance = 0.0
        dominant_collection_id = None
        dominant_collection_name = None
        
        if collection_ids:
            collection_counts = Counter(collection_ids)
            most_common = collection_counts.most_common(1)[0]
            dominant_collection_id = most_common[0]
            franchise_dominance = most_common[1] / watch_count
            
            # Get collection name
            for w in cluster_watches:
                if w["collection_id"] == dominant_collection_id and w["collection_name"]:
                    dominant_collection_name = w["collection_name"]
                    break
        
        # Thematic consistency: agreement on top genre
        all_genres = []
        for w in cluster_watches:
            genres = w.get("genres", [])
            if genres:
                all_genres.extend(genres)
        
        thematic_consistency = 0.0
        dominant_genres = []
        
        if all_genres:
            genre_counts = Counter(all_genres)
            top_genres = genre_counts.most_common(3)
            dominant_genres = [g[0] for g in top_genres]
            # Thematic consistency = fraction of items with top genre
            if top_genres:
                thematic_consistency = top_genres[0][1] / watch_count
        
        # Compute overall phase score
        phase_score = (
            0.35 * cohesion +
            0.25 * watch_density +
            0.20 * franchise_dominance +
            0.20 * thematic_consistency
        )
        
        # Extract dominant keywords
        all_keywords = []
        for w in cluster_watches:
            keywords = w.get("keywords", [])
            if keywords:
                all_keywords.extend(keywords)
        
        keyword_counts = Counter(all_keywords)
        dominant_keywords = [k[0] for k in keyword_counts.most_common(5)]
        
        return {
            "cohesion": cohesion,
            "watch_density": watch_density,
            "franchise_dominance": franchise_dominance,
            "thematic_consistency": thematic_consistency,
            "phase_score": phase_score,
            "dominant_genres": dominant_genres,
            "dominant_keywords": dominant_keywords,
            "dominant_collection_id": dominant_collection_id,
            "dominant_collection_name": dominant_collection_name,
            "item_count": watch_count,
            "movie_count": sum(1 for w in cluster_watches if w["media_type"] == "movie"),
            "show_count": sum(1 for w in cluster_watches if w["media_type"] == "show")
        }
    
    def _generate_phase_label_with_llm(self, cluster_watches: List[Dict], metrics: Dict) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Generate creative phase label and explanation using LLM with ItemLLMProfile + UserTextProfile.
        Returns (label, explanation, icon_emoji) or (None, None, None) if LLM fails.
        """
        try:
            import httpx
            from app.models import ItemLLMProfile, UserTextProfile
            
            # 1. Get top 3 items from cluster
            sorted_watches = sorted(
                cluster_watches,
                key=lambda w: ensure_utc(w.get("watched_at")) or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True
            )[:3]
            
            # 2. Fetch ItemLLMProfile for these items
            item_profiles = []
            for watch in sorted_watches:
                profile = self.db.query(ItemLLMProfile).filter(
                    ItemLLMProfile.tmdb_id == watch["tmdb_id"],
                    ItemLLMProfile.media_type == watch["media_type"]
                ).first()
                
                if profile and profile.profile_text:
                    item_profiles.append({
                        'title': watch['title'],
                        'profile': profile.profile_text[:400]
                    })
            
            # 3. Get user text profile
            user_profile = self.db.query(UserTextProfile).filter_by(
                user_id=self.user_id
            ).first()
            
            user_context = user_profile.summary_text if user_profile else "No profile yet - analyzing viewing patterns"
            
            # 4. Build phase context
            genres_str = ', '.join(metrics.get('dominant_genres', [])[:3])
            keywords_str = ', '.join(metrics.get('dominant_keywords', [])[:5])
            
            # Calculate time span
            watched_dates = [ensure_utc(w["watched_at"]) for w in cluster_watches if w.get("watched_at")]
            if len(watched_dates) > 1:
                days_span = (max(watched_dates) - min(watched_dates)).days
                start_date = min(watched_dates).strftime('%B %d')
                end_date = max(watched_dates).strftime('%B %d, %Y')
            else:
                days_span = 1
                start_date = watched_dates[0].strftime('%B %d, %Y') if watched_dates else "Recently"
                end_date = ""
            
            # 5. Build LLM prompt
            items_text = "\n".join(f"- {item['title']}: {item['profile']}" for item in item_profiles) if item_profiles else "Limited metadata available"
            
            prompt = f"""Analyze this viewing phase and generate a creative, personalized label and explanation:

**User Profile**: {user_context}

**Phase Details**:
- Items Watched: {metrics['item_count']}
- Time Period: {start_date} to {end_date} ({days_span} days)
- Cohesion: {metrics['cohesion']:.2f}/1.0 (how related)
- Watch Density: {metrics['watch_density']:.2f}
- Genres: {genres_str or 'Mixed'}
- Themes: {keywords_str or 'Varied'}

**Representative Content**:
{items_text}

Generate:
1. Creative phase label (3-6 words)
2. Personalized explanation (1-2 sentences why user watched this)
3. Appropriate emoji

**CRITICAL**: Output ONLY valid JSON. No markdown, no explanation, just pure JSON:
{{"label": "Creative Phase Name", "explanation": "Why user watched this", "icon": "🎬"}}
"""
            
            # 6. Call LLM
            with httpx.Client() as client:
                resp = client.post(
                    "http://ollama:11434/api/generate",
                    json={
                        "model": "phi3.5:3.8b-mini-instruct-q4_K_M",
                        "prompt": prompt,
                        "options": {"temperature": 0.7, "num_predict": 150, "num_ctx": 4096},
                        "keep_alive": "24h",
                    },
                    timeout=60.0,
                )
            
            if resp.status_code != 200:
                logger.warning(f"[PhaseDetector] LLM request failed: {resp.status_code}")
                return None, None, None
            
            # 7. Parse response
            data = resp.json()
            output = data.get("response", "").strip()
            
            # Clean JSON
            if "```json" in output:
                output = output.split("```json")[1].split("```")[0].strip()
            elif "```" in output:
                output = output.split("```")[1].split("```")[0].strip()
            output = output.strip('"\'""„" \n\r')
            
            # Parse
            import json as _json
            result = _json.loads(output)
            
            label = result.get('label', '').strip()
            explanation = result.get('explanation', '').strip()
            icon = result.get('icon', '🎬').strip()
            
            # Validate
            if label and 3 <= len(label) <= 80 and explanation and len(explanation) >= 10:
                if len(label) > 60:
                    label = label[:57] + "..."
                if len(explanation) > 200:
                    explanation = explanation[:197] + "..."
                
                logger.info(f"[PhaseDetector] LLM generated: '{label}'")
                return label, explanation, icon
            else:
                logger.warning(f"[PhaseDetector] LLM validation failed")
                return None, None, None
        
        except Exception as e:
            logger.warning(f"[PhaseDetector] LLM phase label failed: {e}")
            return None, None, None
    
    def _generate_phase_label(self, cluster_watches: List[Dict], metrics: Dict) -> Tuple[str, str]:
        """
        Generate AI-powered dynamic label and emoji for phase.
        Returns (label, icon).
        Enhanced to use LLM first, with fallback to rule-based method.
        """
        # Try LLM-based generation first
        llm_label, llm_explanation, llm_icon = self._generate_phase_label_with_llm(cluster_watches, metrics)
        
        if llm_label and llm_explanation:
            # Store explanation for later use
            metrics['_llm_explanation'] = llm_explanation
            return llm_label, llm_icon
        
        # Fallback to rule-based generation
        logger.info("[PhaseDetector] Using rule-based phase label (LLM fallback)")
        
        # Franchise phase
        if metrics["franchise_dominance"] >= FRANCHISE_DOMINANCE_THRESHOLD and metrics["dominant_collection_name"]:
            label = f"{metrics['dominant_collection_name']} Phase"
            icon = "🎬"
            return label, icon

        # Use dynamic naming for non-franchise phases
        try:
            label = self._generate_dynamic_phase_name(cluster_watches, metrics)
        except Exception:
            genres = metrics.get("dominant_genres") or []
            if genres:
                label = f"{genres[0].title()} Phase"
            else:
                label = "Mixed Viewing Phase"

        # Select icon based on dominant genre
        genres = metrics["dominant_genres"]
        icon = self._get_genre_icon(genres[0]) if genres else "📺"

        return label, icon
    
    def _get_genre_icon(self, genre: str) -> str:
        """Map genre to emoji icon."""
        genre_lower = genre.lower()
        for key, emoji in GENRE_EMOJI_MAP.items():
            if key in genre_lower:
                return emoji
        return "🎬"  # Default
    
    def _generate_explanation(self, cluster_watches: List[Dict], metrics: Dict, label: str) -> str:
        """
        Generate "Why this phase?" explanation text.
        Uses LLM explanation if available, otherwise falls back to template.
        """
        # Check if we have LLM-generated explanation
        if '_llm_explanation' in metrics:
            return metrics['_llm_explanation']
        
        # Fallback to rule-based explanation
        item_count = metrics["item_count"]
        genres = metrics["dominant_genres"]
        keywords = metrics["dominant_keywords"][:3]
        
        # Calculate days span
        watched_dates = [ensure_utc(w["watched_at"]) for w in cluster_watches if w.get("watched_at")]
        days_span = (max(watched_dates) - min(watched_dates)).days if len(watched_dates) > 1 else 1
        
        # Build explanation
        genre_text = ", ".join(genres[:2]) if genres else "various genres"
        keyword_text = ", ".join(keywords) if keywords else "diverse themes"
        
        explanation = f"You watched {item_count} {genre_text} titles over {days_span} days. Common themes include {keyword_text}."
        
        return explanation
    
    def _generate_dynamic_phase_name(self, cluster_watches: List[Dict], metrics: Dict) -> str:
            """
            Generate intelligent phase name based on content analysis.
            Uses similar logic to AI list title generation for consistency.
            """
            genres = metrics["dominant_genres"]
            keywords = metrics["dominant_keywords"][:5]
        
            # Count media types
            media_types = {}
            for w in cluster_watches:
                mt = w.get("media_type", "unknown")
                media_types[mt] = media_types.get(mt, 0) + 1
        
            total_items = len(cluster_watches)
            movie_count = media_types.get("movie", 0)
            show_count = media_types.get("show", 0)
        
            title_parts = []
        
            # Priority 1: If there's a dominant keyword that's descriptive, use it
            if keywords:
                # Use first keyword if it's not too generic
                first_kw = keywords[0].title()
                generic_keywords = ["Action", "Drama", "Story", "Film", "Movie", "Show", "Series"]
                if first_kw not in generic_keywords:
                    title_parts.append(first_kw)
        
            # Priority 2: Use genres if no good keyword
            if not title_parts and genres:
                if len(genres) == 1:
                    title_parts.append(genres[0].title())
                else:
                    # Combine up to 2 genres
                    title_parts.append(f"{genres[0].title()} & {genres[1].title()}")
        
            # Add media type suffix if phase is mostly one type
            media_suffix = None
            if movie_count > 0 and show_count == 0:
                media_suffix = "Movies"
            elif show_count > 0 and movie_count == 0:
                media_suffix = "Shows"
            elif movie_count > show_count * 2:
                media_suffix = "Films"
            elif show_count > movie_count * 2:
                media_suffix = "Series"
        
            if media_suffix and not any(media_suffix.lower() in part.lower() for part in title_parts):
                title_parts.append(media_suffix)
        
            # Add phase descriptor if we have enough content
            if not title_parts:
                title_parts.append("Mixed Content")
        
            # Construct final name
            phase_name = " ".join(title_parts)
        
            # Limit length
            if len(phase_name) > 50:
                phase_name = phase_name[:47] + "..."
        
            return phase_name
    
    def _select_representative_posters(self, cluster_watches: List[Dict], count: int = 6) -> List[str]:
        """Select diverse representative posters for phase UI."""
        posters = []
        
        # Sort by watch date (most recent first) and pick diverse items
        # Normalize to UTC-aware for robust sorting
        sorted_watches = sorted(
            cluster_watches,
            key=lambda w: ensure_utc(w.get("watched_at")) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True
        )
        
        for watch in sorted_watches:
            if watch["poster_path"]:
                posters.append(watch["poster_path"])
            if len(posters) >= count:
                break
        
        return posters
    
    def _find_similar_phase(self, cluster_watches: List[Dict], start: datetime, end: datetime) -> Optional[UserPhase]:
        """
        Check if a similar phase already exists (same timeframe, similar content).
        Used to update existing phases instead of creating duplicates.
        """
        # Find phases with overlapping time ranges
        existing = self.db.query(UserPhase).filter(
            UserPhase.user_id == self.user_id,
            UserPhase.start_at <= end,
            UserPhase.end_at >= start
        ).all()
        
        if not existing:
            return None
        
        # Check content similarity (compare tmdb_ids)
        cluster_tmdb_ids = set(w["tmdb_id"] for w in cluster_watches if w["tmdb_id"])
        
        for phase in existing:
            try:
                phase_tmdb_ids = set(json.loads(phase.tmdb_ids))
                overlap = len(cluster_tmdb_ids & phase_tmdb_ids)
                similarity = overlap / max(len(cluster_tmdb_ids), len(phase_tmdb_ids))
                
                if similarity > 0.6:  # 60% overlap = same phase
                    return phase
            except Exception:
                continue
        
        return None
    
    def _create_phase(self, cluster_watches: List[Dict], metrics: Dict, label: str, icon: str,
                     explanation: str, posters: List[str], start: datetime, end: datetime,
                     phase_type: str) -> UserPhase:
        """Create new phase entry in database."""
        tmdb_ids = [w["tmdb_id"] for w in cluster_watches if w["tmdb_id"]]
        trakt_ids = [w["trakt_id"] for w in cluster_watches if w["trakt_id"]]
        media_types = [w["media_type"] for w in cluster_watches]
        
        # Calculate average runtime
        runtimes = [w["runtime"] for w in cluster_watches if w["runtime"]]
        avg_runtime = int(np.mean(runtimes)) if runtimes else None
        
        # Get top language
        languages = [w["language"] for w in cluster_watches if w["language"]]
        top_language = Counter(languages).most_common(1)[0][0] if languages else None
        
        phase = UserPhase(
            user_id=self.user_id,
            label=label,
            icon=icon,
            start_at=start,
            end_at=end if phase_type != "active" else None,
            tmdb_ids=json.dumps(tmdb_ids),
            trakt_ids=json.dumps(trakt_ids),
            media_types=json.dumps(media_types),
            dominant_genres=json.dumps(metrics["dominant_genres"]),
            dominant_keywords=json.dumps(metrics["dominant_keywords"]),
            franchise_id=metrics.get("dominant_collection_id"),
            franchise_name=metrics.get("dominant_collection_name"),
            cohesion=metrics["cohesion"],
            watch_density=metrics["watch_density"],
            franchise_dominance=metrics["franchise_dominance"],
            thematic_consistency=metrics["thematic_consistency"],
            phase_score=metrics["phase_score"],
            item_count=metrics["item_count"],
            movie_count=metrics["movie_count"],
            show_count=metrics["show_count"],
            avg_runtime=avg_runtime,
            top_language=top_language,
            phase_type=phase_type,
            explanation=explanation,
            representative_posters=json.dumps(posters)
        )
        
        self.db.add(phase)
        self.db.commit()
        self.db.refresh(phase)
        
        # Log event
        event = UserPhaseEvent(
            user_id=self.user_id,
            phase_id=phase.id,
            action="created",
            meta=json.dumps({"phase_score": metrics["phase_score"], "label": label})
        )
        self.db.add(event)
        self.db.commit()
        
        logger.info(f"[PhaseDetector] Created phase: {label} (score={metrics['phase_score']:.2f}, type={phase_type})")
        return phase
    
    def _update_phase(self, phase: UserPhase, cluster_watches: List[Dict], metrics: Dict,
                     label: str, icon: str, explanation: str, posters: List[str], phase_type: str):
        """Update existing phase with new data."""
        tmdb_ids = [w["tmdb_id"] for w in cluster_watches if w["tmdb_id"]]
        trakt_ids = [w["trakt_id"] for w in cluster_watches if w["trakt_id"]]
        media_types = [w["media_type"] for w in cluster_watches]
        
        runtimes = [w["runtime"] for w in cluster_watches if w["runtime"]]
        avg_runtime = int(np.mean(runtimes)) if runtimes else None
        
        languages = [w["language"] for w in cluster_watches if w["language"]]
        top_language = Counter(languages).most_common(1)[0][0] if languages else None
        
        phase.label = label
        phase.icon = icon
        phase.tmdb_ids = json.dumps(tmdb_ids)
        phase.trakt_ids = json.dumps(trakt_ids)
        phase.media_types = json.dumps(media_types)
        phase.dominant_genres = json.dumps(metrics["dominant_genres"])
        phase.dominant_keywords = json.dumps(metrics["dominant_keywords"])
        phase.franchise_id = metrics.get("dominant_collection_id")
        phase.franchise_name = metrics.get("dominant_collection_name")
        phase.cohesion = metrics["cohesion"]
        phase.watch_density = metrics["watch_density"]
        phase.franchise_dominance = metrics["franchise_dominance"]
        phase.thematic_consistency = metrics["thematic_consistency"]
        phase.phase_score = metrics["phase_score"]
        phase.item_count = metrics["item_count"]
        phase.movie_count = metrics["movie_count"]
        phase.show_count = metrics["show_count"]
        phase.avg_runtime = avg_runtime
        phase.top_language = top_language
        phase.phase_type = phase_type
        phase.explanation = explanation
        phase.representative_posters = json.dumps(posters)
        
        if phase_type == "active":
            phase.end_at = None
        
        self.db.commit()
        
        # Log event
        event = UserPhaseEvent(
            user_id=self.user_id,
            phase_id=phase.id,
            action="updated",
            meta=json.dumps({"phase_score": metrics["phase_score"], "label": label})
        )
        self.db.add(event)
        self.db.commit()
        
        logger.info(f"[PhaseDetector] Updated phase: {label} (score={metrics['phase_score']:.2f})")
    
    def _detect_future_phase(self) -> Optional[UserPhase]:
        """
        Predict future phase based on very recent trends (last 30 days).
        Lighter analysis to suggest what user might be getting into next.
        """
        logger.debug(f"[PhaseDetector] Detecting future phase for user {self.user_id}")
        
        cutoff = utc_now() - timedelta(days=30)
        recent_watches = self.db.query(TraktWatchHistory).filter(
            TraktWatchHistory.user_id == self.user_id,
            TraktWatchHistory.watched_at >= cutoff
        ).order_by(TraktWatchHistory.watched_at.desc()).all()
        
        if len(recent_watches) < 3:  # Need at least a few recent watches
            return None
        
        # Simple analysis: most common genres + keywords in last 30 days
        all_genres = []
        all_keywords = []
        
        for watch in recent_watches:
            genres = self._parse_json(watch.genres)
            keywords = self._parse_json(watch.keywords)
            if genres:
                all_genres.extend(genres)
            if keywords:
                all_keywords.extend(keywords)
        
        if not all_genres:
            return None
        
        genre_counts = Counter(all_genres)
        keyword_counts = Counter(all_keywords)
        
        top_genre = genre_counts.most_common(1)[0][0]
        top_keywords = [k[0] for k in keyword_counts.most_common(3)]
        
        # Build future phase label
        label = f"Emerging {top_genre.capitalize()} Phase"
        icon = self._get_genre_icon(top_genre)
        explanation = f"Based on your recent viewing, you seem to be getting into {top_genre} content. Common themes: {', '.join(top_keywords)}."
        
        # Create as "future" type (not persisted yet, just returned)
        # This is more of a suggestion/prediction
        # For simplicity, we'll skip persisting future phases for now
        logger.info(f"[PhaseDetector] Future phase detected: {label}")
        return None  # Skip for now, can implement later
    
    def predict_next_phase(self, lookback_days: int = 42) -> Optional[Dict]:
        """
        Predict the next phase using pairwise judgments (if available) + watch history fallback.
        
        Strategy:
        1. Try pairwise judgment analysis (recent training sessions)
        2. Fall back to watch history pattern analysis
        3. Use BGE embeddings to find candidate clusters
        4. Generate prediction with LLM
        
        Returns dict with prediction details (not a persisted UserPhase).
        """
        logger.info(f"[PhaseDetector] Predicting next phase for user {self.user_id}")
        
        # Try pairwise judgment prediction first
        pairwise_prediction = self._predict_from_pairwise_judgments()
        
        if pairwise_prediction:
            logger.info("[PhaseDetector] Using pairwise judgment-based prediction")
            return pairwise_prediction
        
        # Fall back to watch history analysis
        logger.info("[PhaseDetector] Using watch history-based prediction (pairwise fallback)")
        return self._predict_from_watch_history(lookback_days)
    
    def _predict_from_pairwise_judgments(self) -> Optional[Dict]:
        """
        Predict next phase from recent pairwise training sessions.
        Analyzes user preferences revealed through A/B comparisons.
        """
        try:
            from app.models import PairwiseTrainingSession, PairwiseJudgment
            
            # Get recent training sessions (last 30 days)
            cutoff = utc_now() - timedelta(days=30)
            sessions = self.db.query(PairwiseTrainingSession).filter(
                PairwiseTrainingSession.user_id == self.user_id,
                PairwiseTrainingSession.updated_at >= cutoff
            ).order_by(PairwiseTrainingSession.updated_at.desc()).limit(5).all()
            
            if not sessions:
                logger.debug("[PhaseDetector] No recent pairwise sessions")
                return None
            
            # Extract preference patterns from judgments
            preferred_genres = []
            preferred_keywords = []
            preferred_tmdb_ids = []
            
            for session in sessions:
                judgments = self.db.query(PairwiseJudgment).filter_by(
                    session_id=session.id
                ).all()
                
                for judgment in judgments:
                    # Determine winner
                    if judgment.choice == 'A':
                        winner_id = judgment.candidate_a_tmdb_id
                        winner_type = judgment.candidate_a_media_type
                    elif judgment.choice == 'B':
                        winner_id = judgment.candidate_b_tmdb_id
                        winner_type = judgment.candidate_b_media_type
                    else:
                        continue
                    
                    preferred_tmdb_ids.append((winner_id, winner_type))
                    
                    # Get winner metadata
                    winner = self.db.query(PersistentCandidate).filter(
                        PersistentCandidate.tmdb_id == winner_id,
                        PersistentCandidate.media_type == winner_type
                    ).first()
                    
                    if winner:
                        genres = self._parse_json(winner.genres)
                        keywords = self._parse_json(winner.keywords)
                        if genres:
                            preferred_genres.extend(genres)
                        if keywords:
                            preferred_keywords.extend(keywords)
            
            if len(preferred_tmdb_ids) < 3:
                logger.debug("[PhaseDetector] Insufficient pairwise judgments")
                return None
            
            # Analyze preference patterns
            genre_counts = Counter(preferred_genres)
            keyword_counts = Counter(preferred_keywords)
            
            top_genres = [g[0] for g in genre_counts.most_common(3)]
            top_keywords = [k[0] for k in keyword_counts.most_common(5)]
            
            # Find similar unwatched candidates
            candidate_pool = self.db.query(PersistentCandidate).filter(
                PersistentCandidate.active == True
            ).limit(200).all()
            
            # Score candidates using BGE multi-vector search
            from app.services.ai_engine.dual_index_search import hybrid_search
            
            scored_candidates = hybrid_search(
                self.db,
                self.user_id,
                candidate_pool,
                top_k=20,
                bge_weight=0.8,  # Higher weight for pairwise (more reliable signal)
                faiss_weight=0.2
            )
            
            if not scored_candidates:
                return None
            
            # Generate prediction
            prediction = {
                'label': self._generate_prediction_label(top_genres, top_keywords),
                'confidence': 0.75,  # High confidence from pairwise
                'top_genres': top_genres,
                'top_keywords': top_keywords,
                'recommended_items': [
                    {
                        'tmdb_id': item['candidate'].tmdb_id,
                        'title': item['candidate'].title,
                        'poster_path': item['candidate'].poster_path,
                        'score': item['score'],
                        'source': item['source']
                    }
                    for item in scored_candidates[:10]
                ],
                'explanation': f"Based on your recent preferences, you're showing interest in {', '.join(top_genres[:2])} content with themes like {', '.join(top_keywords[:3])}.",
                'source': 'pairwise'
            }
            
            return prediction
        
        except Exception as e:
            logger.warning(f"[PhaseDetector] Pairwise prediction failed: {e}")
            return None
    
    def _predict_from_watch_history(self, lookback_days: int = 42) -> Optional[Dict]:
        """
        Predict next phase from watch history patterns (fallback method).
        """
        cutoff = utc_now() - timedelta(days=lookback_days)
        recent_watches = self.db.query(TraktWatchHistory).filter(
            TraktWatchHistory.user_id == self.user_id,
            TraktWatchHistory.watched_at >= cutoff
        ).order_by(TraktWatchHistory.watched_at.desc()).all()
        
        if len(recent_watches) < 5:
            logger.debug(f"[PhaseDetector] Insufficient recent watches ({len(recent_watches)})")
            return None
        
        # Convert to dicts
        watch_data_raw = [
            {
                'trakt_id': w.trakt_id,
                'tmdb_id': w.tmdb_id,
                'title': w.title,
                'media_type': w.media_type,
                'watched_at': w.watched_at,
                'genres': w.genres,
                'keywords': w.keywords
            }
            for w in recent_watches
        ]
        
        # Load embeddings for recent watches
        embeddings, watch_data = self._load_embeddings_for_watches(watch_data_raw)
        
        if len(embeddings) < 5:
            logger.debug(f"[PhaseDetector] Insufficient embeddings for prediction")
            return None
        
        # Cluster recent watches to find emerging patterns
        cluster_labels = self._cluster_embeddings(embeddings)
        
        if cluster_labels is None:
            logger.debug(f"[PhaseDetector] Clustering failed for prediction")
            return None
        
        # Find the most prominent cluster (excluding noise)
        unique_labels = [l for l in set(cluster_labels) if l != -1]
        
        if not unique_labels:
            logger.debug(f"[PhaseDetector] No valid clusters found")
            return None
        
        # Count items per cluster
        cluster_sizes = {label: np.sum(cluster_labels == label) for label in unique_labels}
        dominant_cluster = max(cluster_sizes, key=cluster_sizes.get)
        
        # Get watches in dominant cluster
        cluster_mask = cluster_labels == dominant_cluster
        cluster_watches = [watch_data[i] for i, is_member in enumerate(cluster_mask) if is_member]
        cluster_embeddings = embeddings[cluster_mask]
        
        if len(cluster_watches) < 3:
            logger.debug(f"[PhaseDetector] Dominant cluster too small ({len(cluster_watches)})")
            return None
        
        # Compute metrics for prediction
        phase_metrics = self._compute_phase_metrics(
            cluster_watches,
            cluster_embeddings,
            total_window_watches=len(recent_watches)
        )
        
        # Generate label and explanation
        label, icon = self._generate_phase_label(cluster_watches, phase_metrics)
        
        # Build prediction object
        prediction = {
            "label": label,
            "icon": icon,
            "predicted_start": utc_now().isoformat(),
            "predicted_end": (utc_now() + timedelta(days=14)).isoformat(),
            "item_count": len(cluster_watches),
            "movie_count": sum(1 for w in cluster_watches if w["media_type"] == "movie"),
            "show_count": sum(1 for w in cluster_watches if w["media_type"] == "show"),
            "confidence": phase_metrics["phase_score"],
            "explanation": f"Based on your recent viewing over the past {lookback_days} days, you're likely entering a {label.lower()}. This prediction is based on {len(cluster_watches)} similar items you've recently watched.",
            "dominant_genres": phase_metrics.get("dominant_genres", []),
            "dominant_keywords": phase_metrics.get("dominant_keywords", []),
            "representative_posters": self._select_representative_posters(cluster_watches, count=6),
            "cohesion": phase_metrics.get("cohesion", 0.0)
        }
        
        logger.info(f"[PhaseDetector] Predicted next phase: {label} (confidence={prediction['confidence']:.2f})")
        return prediction
    
    def _close_stale_phases(self):
        """Close phases that haven't had watches in PHASE_CLOSE_DAYS."""
        cutoff = utc_now() - timedelta(days=PHASE_CLOSE_DAYS)
        
        active_phases = self.db.query(UserPhase).filter(
            UserPhase.user_id == self.user_id,
            UserPhase.end_at.is_(None),  # Currently active
            UserPhase.phase_type == "active"
        ).all()
        
        for phase in active_phases:
            try:
                phase_tmdb_ids = json.loads(phase.tmdb_ids)
                
                # Check if any of these items were watched recently
                recent_watch = self.db.query(TraktWatchHistory).filter(
                    TraktWatchHistory.user_id == self.user_id,
                    TraktWatchHistory.tmdb_id.in_(phase_tmdb_ids),
                    TraktWatchHistory.watched_at >= cutoff
                ).first()
                
                if not recent_watch:
                    # Close this phase
                    phase.end_at = cutoff
                    phase.phase_type = "historical"
                    self.db.commit()
                    
                    # Log event
                    event = UserPhaseEvent(
                        user_id=self.user_id,
                        phase_id=phase.id,
                        action="closed",
                        meta=json.dumps({"reason": "stale", "days_inactive": PHASE_CLOSE_DAYS})
                    )
                    self.db.add(event)
                    self.db.commit()
                    
                    logger.info(f"[PhaseDetector] Closed stale phase: {phase.label}")
            except Exception as e:
                logger.warning(f"[PhaseDetector] Failed to check phase staleness: {e}")
                continue
    
    def _parse_json(self, json_str: Optional[str]) -> List:
        """Safely parse JSON string to list."""
        if not json_str:
            return []
        try:
            result = json.loads(json_str)
            return result if isinstance(result, list) else []
        except Exception:
            return []
    
    def get_current_phase(self) -> Optional[UserPhase]:
        """Get user's current active phase."""
        return self.db.query(UserPhase).filter(
            UserPhase.user_id == self.user_id,
            UserPhase.end_at.is_(None),
            UserPhase.phase_type == "active"
        ).order_by(UserPhase.phase_score.desc()).first()
    
    def get_phase_history(self, limit: int = 10) -> List[UserPhase]:
        """Get user's phase history (closed phases)."""
        return self.db.query(UserPhase).filter(
            UserPhase.user_id == self.user_id,
            UserPhase.end_at.isnot(None)
        ).order_by(UserPhase.start_at.desc()).limit(limit).all()
    
    def _generate_prediction_label(self, genres: List[str], keywords: List[str]) -> str:
        """Generate phase prediction label from genres and keywords."""
        if not genres and not keywords:
            return "Emerging Viewing Phase"
        
        # Prefer descriptive keywords over generic genres
        if keywords:
            first_kw = keywords[0].title()
            generic = ["Action", "Drama", "Story", "Film", "Movie", "Show"]
            if first_kw not in generic:
                return f"Emerging {first_kw} Phase"
        
        # Fall back to genre
        if genres:
            if len(genres) == 1:
                return f"Emerging {genres[0].title()} Phase"
            else:
                return f"{genres[0].title()} & {genres[1].title()} Exploration"
        
        return "Next Viewing Phase"
