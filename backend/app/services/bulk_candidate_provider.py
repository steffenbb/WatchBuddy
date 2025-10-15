"""
bulk_candidate_provider.py

Enhanced candidate provider with mood-based search, filtering, and memory optimization.
Supports obscure/popular filters, watched/unwatched preferences, and bulk search.
"""
import json
import logging
import asyncio
import datetime
import json
import hashlib
from typing import List, Dict, Any, Set, Optional
from app.core.database import SessionLocal
from app.models import UserList, MediaMetadata, ListItem, CandidateCache
from app.services.trakt_client import TraktClient
from app.services.tmdb_client import fetch_tmdb_metadata, fetch_tmdb_metadata_with_fallback

logger = logging.getLogger(__name__)

class BulkCandidateProvider:
    """Enhanced candidate provider with mood-based search and filtering."""
    
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.trakt_client = TraktClient(user_id=user_id)
        self.db = SessionLocal()
    
    def _generate_cache_key(self, media_type: str, discovery: str, **params) -> str:
        """Generate a cache key for candidate search parameters."""
        # Include key parameters that affect candidate search results
        cache_params = {
            'media_type': media_type,
            'discovery': discovery,
            'genres': sorted(params.get('genres', [])) if params.get('genres') else None,
            'languages': sorted(params.get('languages', [])) if params.get('languages') else None,
            'min_year': params.get('min_year'),
            'max_year': params.get('max_year'),
            'min_rating': params.get('min_rating'),
            'search_keywords': sorted(params.get('search_keywords', [])) if params.get('search_keywords') else None,
        }
        # Remove None values
        cache_params = {k: v for k, v in cache_params.items() if v is not None}
        
        # Create hash of parameters
        cache_str = json.dumps(cache_params, sort_keys=True)
        return hashlib.md5(cache_str.encode()).hexdigest()
    async def _get_cached_candidates(self, cache_key: str) -> Optional[List[Dict[str, Any]]]:
        """Retrieve candidates from cache if available and not expired."""
        try:
            cached = (
                self.db.query(CandidateCache)
                .filter(
                    CandidateCache.cache_key == cache_key,
                    CandidateCache.expires_at > datetime.datetime.utcnow()
                )
                .first()
            )
            if not cached:
                return None

            # Update last accessed
            cached.last_accessed = datetime.datetime.utcnow()
            self.db.commit()
            
            # Parse and return cached data
            return json.loads(cached.candidate_data)
        except Exception as e:
            logger.warning(f"Failed to retrieve cached candidates: {e}")
            self.db.rollback()
            return None
    
    async def _cache_candidates(self, cache_key: str, media_type: str, discovery: str, 
                               candidates: List[Dict[str, Any]], cache_hours: int = 6):
        """Cache candidates with expiry."""
        try:
            expires_at = datetime.datetime.utcnow() + datetime.timedelta(hours=cache_hours)
            
            # Remove existing cache entry
            self.db.query(CandidateCache).filter(CandidateCache.cache_key == cache_key).delete()
            
            # Create new cache entry
            cache_entry = CandidateCache(
                cache_key=cache_key,
                media_type=media_type,
                discovery_type=discovery,
                candidate_data=json.dumps(candidates),
                item_count=len(candidates),
                expires_at=expires_at
            )
            
            self.db.add(cache_entry)
            self.db.commit()
            
            logger.info(f"Cached {len(candidates)} candidates for {cache_hours} hours")
            
        except Exception as e:
            logger.warning(f"Failed to cache candidates: {e}")
            self.db.rollback()
    
    async def _cleanup_expired_cache(self):
        """Remove expired cache entries."""
        try:
            expired_count = (
                self.db.query(CandidateCache)
                .filter(CandidateCache.expires_at < datetime.datetime.utcnow())
                .delete()
            )
            if expired_count > 0:
                self.db.commit()
                logger.info(f"Cleaned up {expired_count} expired cache entries")
        except Exception as e:
            logger.warning(f"Failed to cleanup cache: {e}")
            self.db.rollback()

    async def _finalize_candidates(self, candidates: List[Dict[str, Any]], limit: int, exclude_ids: Set[int],
                                  existing_list_ids: Optional[Set[int]], enrich_with_tmdb: bool,
                                  genres: Optional[List[str]], languages: Optional[List[str]],
                                  min_year: Optional[int], max_year: Optional[int], min_rating: Optional[float],
                                  genre_mode: str = "any") -> List[Dict[str, Any]]:
        """Finalize cached candidates with current filters and enrichment."""
        # Apply current exclusions and filters
        filtered = self._apply_filters(
            candidates, exclude_ids, genres, languages, min_year, max_year, min_rating, genre_mode=genre_mode
        )
        # Strict media_type filter: only allow items matching the first candidate's media_type (movie/show)
        if filtered:
            expected_type = candidates[0].get('media_type', 'movie') if candidates else 'movie'
            filtered = [item for item in filtered if item.get('media_type') == expected_type]
        
        # Prefer new content if existing list provided
        if existing_list_ids:
            new_items = [item for item in filtered if item.get('trakt_id') not in existing_list_ids]
            old_items = [item for item in filtered if item.get('trakt_id') in existing_list_ids]
            if len(new_items) >= limit:
                filtered = new_items[:limit]
            else:
                filtered = new_items + old_items[:max(0, limit - len(new_items))]
        else:
            filtered = filtered[:limit]
        
        # Apply current enrichment if requested
        if enrich_with_tmdb and filtered:
            from app.services.tmdb_client import get_tmdb_api_key
            tmdb_api_key = None
            try:
                tmdb_api_key = await get_tmdb_api_key()
            except Exception:
                pass
                
            if tmdb_api_key:
                enriched = await self._enrich_with_aggressive_metadata(filtered, candidates[0].get('media_type', 'movies'), genres, languages)
                final_filtered = self._apply_post_enrichment_filters(enriched, genres, languages, genre_mode=genre_mode)
                return self._ensure_downstream_fields(final_filtered[:limit], candidates[0].get('media_type', 'movies'))
        
        return self._ensure_downstream_fields(filtered[:limit], candidates[0].get('media_type', 'movies'))

    async def _cleanup_expired_cache(self):
        """Remove expired cache entries."""
        try:
            expired_count = (
                self.db.query(CandidateCache)
                .filter(CandidateCache.expires_at < datetime.datetime.utcnow())
                .delete()
            )
            if expired_count > 0:
                self.db.commit()
                logger.info(f"Cleaned up {expired_count} expired cache entries")
        except Exception as e:
            logger.warning(f"Failed to cleanup cache: {e}")
            self.db.rollback()

    def _ensure_downstream_fields(self, items: List[Dict[str, Any]], media_type: str) -> List[Dict[str, Any]]:
        """Ensure items include fields expected by downstream services and always have a title.
        - Adds 'trakt_id' from ids.trakt
        - Adds 'media_type' as 'movie' or 'show'
        - Ensures 'title' is present, fetching from MediaMetadata if needed
        Skips items without a title after all attempts.
        """
        singular_type = media_type[:-1] if media_type.endswith('s') else media_type
        ensured = []
        from app.core.database import SessionLocal
        from app.models import MediaMetadata
        db = SessionLocal()
        try:
            for it in items or []:
                try:
                    # Support nested Trakt search result structure
                    inner = None
                    if isinstance(it, dict):
                        inner = it.get('movie') or it.get('show')
                    
                    # Ensure trakt_id: only set when a real numeric Trakt ID is present
                    if 'trakt_id' not in it or it.get('trakt_id') is None:
                        trakt_id = None
                        ids = it.get('ids') if isinstance(it.get('ids'), dict) else None
                        if not ids and inner and isinstance(inner, dict):
                            ids = inner.get('ids') if isinstance(inner.get('ids'), dict) else None
                        if ids:
                            trakt_id = ids.get('trakt')
                        # Only propagate valid integer trakt_id values
                        if isinstance(trakt_id, int):
                            it['trakt_id'] = trakt_id
                        elif isinstance(trakt_id, str) and trakt_id.isdigit():
                            it['trakt_id'] = int(trakt_id)
                        else:
                            # Do not set a surrogate string into trakt_id; leave None and rely on tmdb_id/item_id downstream
                            pass
                    # Ensure media_type
                    mt = it.get('media_type') or it.get('type')
                    if not mt and inner and isinstance(inner, dict):
                        # Fall back to provided media_type context
                        mt = singular_type
                    if mt not in ('movie', 'show'):
                        mt = singular_type
                    it['media_type'] = mt
                    # Ensure title
                    title = it.get('title')
                    if not title and inner and isinstance(inner, dict):
                        title = inner.get('title')
                        if title:
                            it['title'] = title
                    if not title:
                        # Try to fetch from MediaMetadata
                        trakt_id = it.get('trakt_id')
                        ids_for_lookup = it.get('ids') if isinstance(it.get('ids'), dict) else None
                        if not ids_for_lookup and inner and isinstance(inner, dict):
                            ids_for_lookup = inner.get('ids') if isinstance(inner.get('ids'), dict) else None
                        tmdb_id = (ids_for_lookup or {}).get('tmdb')
                        meta = None
                        if trakt_id:
                            meta = db.query(MediaMetadata).filter_by(trakt_id=trakt_id).first()
                        if not meta and tmdb_id:
                            meta = db.query(MediaMetadata).filter_by(tmdb_id=tmdb_id).first()
                        if meta and meta.title:
                            it['title'] = meta.title
                        else:
                            continue  # Skip items with no title
                    ensured.append(it)
                except Exception:
                    # Be resilient; don't let a malformed item crash the pipeline
                    continue
        finally:
            db.close()
        return ensured
    
    def _extract_genres_from_title(self, title: str) -> List[str]:
        """Extract genre hints from list title for fusion mode lists.
        
        Handles compound genres like:
        - Romcoms -> Romance + Comedy
        - Crime documentaries -> Crime + Documentary
        - Action thrillers -> Action + Thriller
        """
        if not title:
            return []
        
        title_lower = title.lower()
        
        # Compound genre patterns (check these first for specificity)
        compound_patterns = {
            'romcom': ['romance', 'comedy'],
            'rom-com': ['romance', 'comedy'],
            'romantic comedy': ['romance', 'comedy'],
            'romantic comedies': ['romance', 'comedy'],
            'action thriller': ['action', 'thriller'],
            'action-thriller': ['action', 'thriller'],
            'crime drama': ['crime', 'drama'],
            'crime thriller': ['crime', 'thriller'],
            'crime documentary': ['crime', 'documentary'],
            'crime documentaries': ['crime', 'documentary'],
            'true crime': ['crime', 'documentary'],
            'horror comedy': ['horror', 'comedy'],
            'horror-comedy': ['horror', 'comedy'],
            'sci-fi thriller': ['science fiction', 'thriller'],
            'sci-fi horror': ['science fiction', 'horror'],
            'psychological thriller': ['thriller', 'mystery'],
            'psychological horror': ['horror', 'thriller'],
            'dark comedy': ['comedy', 'drama'],
            'fantasy adventure': ['fantasy', 'adventure'],
            'war drama': ['war', 'drama'],
            'historical drama': ['drama', 'history'],
            'biographical drama': ['drama', 'history'],
            'biopic': ['drama', 'history'],
            'superhero': ['action', 'adventure', 'science fiction'],
            'space opera': ['science fiction', 'adventure'],
        }
        
        # Single genre mapping
        genre_mapping = {
            'thriller': ['thriller', 'mystery'],
            'horror': ['horror'],
            'comedy': ['comedy'],
            'romance': ['romance'],
            'action': ['action', 'adventure'],
            'drama': ['drama'],
            'sci-fi': ['science fiction'],
            'scifi': ['science fiction'],
            'science fiction': ['science fiction'],
            'fantasy': ['fantasy'],
            'animation': ['animation'],
            'animated': ['animation'],
            'documentary': ['documentary'],
            'documentaries': ['documentary'],
            'crime': ['crime'],
            'war': ['war'],
            'western': ['western'],
            'musical': ['music'],
            'music': ['music'],
            'family': ['family'],
            'adventure': ['adventure'],
            'mystery': ['mystery'],
            'suspense': ['thriller', 'mystery'],
            'noir': ['crime', 'thriller'],
            'slasher': ['horror'],
            'zombie': ['horror'],
            'vampire': ['horror', 'fantasy'],
        }
        
        extracted_genres = []
        
        # Check compound patterns first (more specific)
        for compound, genres in compound_patterns.items():
            if compound in title_lower:
                extracted_genres.extend(genres)
        
        # Then check single genre keywords
        if not extracted_genres:  # Only if no compound match
            for genre_keyword, genre_list in genre_mapping.items():
                if genre_keyword in title_lower:
                    extracted_genres.extend(genre_list)
        
        # Remove duplicates while preserving order
        return list(dict.fromkeys(extracted_genres))

    async def get_candidates(
        self,
        media_type: str = "movies",
        limit: int = 200,
        mood: Optional[str] = None,  # deprecated: use discovery for obscure/popular; mood is semantic mood elsewhere
        discovery: Optional[str] = None,  # "obscure", "very_obscure", "popular", "mainstream", "balanced"
        include_watched: bool = False,
        genres: Optional[List[str]] = None,
        languages: Optional[List[str]] = None,
        min_year: Optional[int] = None,
        max_year: Optional[int] = None,
        min_rating: Optional[float] = None,
        search_keywords: Optional[List[str]] = None,
        enrich_with_tmdb: bool = True,
        genre_mode: str = "any",  # "any" (OR) or "all" (AND)
        existing_list_ids: Optional[Set[int]] = None,  # trakt_ids already in the list, for freshness
        list_title: Optional[str] = None,  # List title for fusion mode genre extraction
        fusion_mode: bool = False,  # Enable fusion mode logic
        exclude_ids: Optional[Set] = None,  # NEW: Exclude these trakt/tmdb/item_ids from candidates
        persistent_only: bool = False  # NEW: Only use persistent DB, skip TMDB fallback
    ) -> List[Dict[str, Any]]:
        """
        Fetch enhanced candidates with mood-based filtering and search.
        Args:
            media_type: "movies" or "shows"
            limit: Maximum number of candidates to return
            mood: Deprecated discovery strategy (kept for backward compatibility)
            discovery: Content discovery strategy (obscure/popular/balanced)
            include_watched: Whether to include user's watched items
            genres: Genre filters
            min_year/max_year: Year range filters
            min_rating: Minimum rating filter
            search_keywords: Keywords to search for
            enrich_with_tmdb: Fetch TMDB metadata
            genre_mode: "any" (OR) or "all" (AND) for genre filtering
            list_title: Title of the list (for genre extraction in fusion mode)
            fusion_mode: Whether this is a fusion-powered list
            exclude_ids: Set of trakt_ids or tmdb_ids to exclude from candidates for diversity filtering
        """
        from app.services.trakt_client import TraktAPIError, TraktAuthError, TraktNetworkError, TraktUnavailableError
        logger.warning(f"[MEDIA_TYPE_DIAGNOSTIC] get_candidates CALLED for media_type={media_type}, limit={limit}, languages={languages}, genres={genres}")
        try:
            # Attempt primary sourcing from PersistentCandidate store for speed.
            try:
                from app.models import PersistentCandidate
                # Base query with cheap indexed filters only (allow any non-null title, skip empty/whitespace at Python level)
                q = self.db.query(PersistentCandidate).filter(
                    PersistentCandidate.active == True,
                    PersistentCandidate.media_type == ("movie" if media_type == "movies" else "show"),
                    (PersistentCandidate.is_adult == False),
                    PersistentCandidate.title != None
                )
                if languages:
                    q = q.filter(PersistentCandidate.language.in_([l.lower() for l in languages]))
                if min_year:
                    q = q.filter(PersistentCandidate.year >= min_year)
                if max_year:
                    q = q.filter(PersistentCandidate.year <= max_year)
                if min_rating is not None:
                    try:
                        q = q.filter(PersistentCandidate.vote_average >= float(min_rating))
                    except Exception:
                        pass

                # Diversity: exclude items present in other lists
                if exclude_ids:
                    q = q.filter(~PersistentCandidate.trakt_id.in_(exclude_ids))
                # Discovery weighting: adjust ordering heuristics
                if discovery in ("popular","mainstream"):
                    q = q.order_by(PersistentCandidate.mainstream_score.desc(), PersistentCandidate.popularity.desc())
                elif discovery in ("obscure","very_obscure"):
                    q = q.order_by(PersistentCandidate.obscurity_score.desc())
                else:
                    # balanced / default: blend freshness and mainstream
                    from sqlalchemy import desc
                    q = q.order_by(desc(PersistentCandidate.freshness_score), desc(PersistentCandidate.mainstream_score))

                # Helper: normalize genres for in-memory check (handles both TMDB and user input formats)
                def _norm(g: str) -> str:
                    if not g:
                        return ''
                    g = g.strip().lower()
                    # TMDB-style genres → simplified forms (comprehensive mapping)
                    mappings = {
                        # Action variants
                        'action & adventure': 'action',
                        'action and adventure': 'action',
                        'action/adventure': 'action',
                        'adventure': 'action',
                        
                        # Sci-Fi variants
                        'sci-fi & fantasy': 'sci-fi',
                        'sci-fi and fantasy': 'sci-fi',
                        'sci-fi/fantasy': 'sci-fi',
                        'science fiction': 'sci-fi',
                        'scifi': 'sci-fi',
                        'fantasy': 'sci-fi',
                        
                        # War & Politics → Drama/Thriller
                        'war & politics': 'drama',
                        'war and politics': 'drama',
                        'war/politics': 'drama',
                        'war': 'drama',
                        'politics': 'drama',
                        
                        # Crime → Mystery/Thriller
                        'crime': 'mystery',
                        
                        # Kids & Family → Family/Animation
                        'kids': 'family',
                        'family': 'family',
                        
                        # News & Documentary
                        'news': 'documentary',
                        
                        # Soap → Drama
                        'soap': 'drama',
                        
                        # Reality & Talk shows
                        'reality': 'documentary',
                        'talk': 'documentary',
                        
                        # Western → Action/Drama
                        'western': 'action',
                    }
                    return mappings.get(g, g)

                required_genres = [_norm(g) for g in (genres or [])]

                def _genres_match(parsed: list) -> bool:
                    if not required_genres:
                        return True
                    have = set(_norm(x) for x in parsed if isinstance(x, str))
                    if not have:
                        return False
                    
                    # Expand "crime" to also match "thriller" and "mystery" searches
                    if 'crime' in have:
                        have.add('thriller')
                        have.add('mystery')
                    
                    # Allow "thriller" or "mystery" filter to match "crime" genre
                    if any(g in required_genres for g in ['thriller', 'mystery']):
                        if 'crime' in have:
                            return True
                    
                    if genre_mode == 'all':
                        return all(r in have for r in required_genres)
                    return any(r in have for r in required_genres)

                # First pass: moderate fetch cap, early-exit once enough matches
                fetch_cap = min(max(limit * 3, limit), 6000)
                stored_candidates = []
                rows = q.limit(fetch_cap).all()
                for row in rows:
                    try:
                        # Robustly parse genres JSON (can be malformed or non-list)
                        if row.genres:
                            try:
                                parsed_genres = json.loads(row.genres)
                                if not isinstance(parsed_genres, list):
                                    parsed_genres = []
                            except Exception:
                                parsed_genres = []
                        else:
                            parsed_genres = []
                        # In-memory genre filter
                        if not _genres_match(parsed_genres):
                            continue
                        # STRICT: skip if title is missing/null/empty
                        if not row.title or not isinstance(row.title, str) or not row.title.strip():
                            continue
                        # Exclude items in exclude_ids
                        cid = row.trakt_id or row.tmdb_id or row.id
                        if exclude_ids and cid in exclude_ids:
                            continue
                        item = {
                            'title': row.title,
                            'year': row.year,
                            'ids': {'tmdb': row.tmdb_id, 'trakt': row.trakt_id},
                            'media_type': row.media_type,
                            'language': row.language,
                            'obscurity_score': row.obscurity_score,
                            'mainstream_score': row.mainstream_score,
                            'freshness_score': row.freshness_score,
                            'tmdb_data': {
                                'popularity': row.popularity,
                                'vote_average': row.vote_average,
                                'vote_count': row.vote_count,
                                'genres': parsed_genres,
                                'poster_path': row.poster_path,
                                'backdrop_path': row.backdrop_path,
                                'overview': row.overview
                            },
                            'scoring_features': {
                                'tmdb_popularity': row.popularity,
                                'tmdb_rating': row.vote_average,
                                'tmdb_votes': row.vote_count,
                                'has_overview': bool(row.overview),
                                'has_poster': bool(row.poster_path),
                                'genre_count': len(parsed_genres)
                            },
                            '_from_persistent_store': True
                        }
                        stored_candidates.append(item)
                        # Short-circuit as soon as we have enough to fulfill the request
                        if len(stored_candidates) >= limit:
                            logger.info(f"Serving {len(stored_candidates)} candidates from persistent store (pre-limit {limit})")
                            return stored_candidates[:limit]
                    except Exception as row_err:
                        # Skip bad rows rather than failing the entire request
                        logger.debug(f"Skipping malformed persistent_candidate row tmdb_id={getattr(row, 'tmdb_id', None)}: {row_err}")
                # If we have some but not enough, broaden within DB and try again quickly
                if len(stored_candidates) < limit:
                    # Second pass: larger cap without min_rating constraint (but still exclude adult)
                    q2 = self.db.query(PersistentCandidate).filter(
                        PersistentCandidate.active == True,
                        PersistentCandidate.media_type == ("movie" if media_type == "movies" else "show"),
                        (PersistentCandidate.is_adult == False)
                    )
                    if languages:
                        # Keep language filter strict - no automatic broadening
                        q2 = q2.filter(PersistentCandidate.language.in_([l.lower() for l in languages]))
                    if min_year:
                        q2 = q2.filter(PersistentCandidate.year >= max(1900, min_year - 5))
                    if max_year:
                        q2 = q2.filter(PersistentCandidate.year <= max_year + 2)
                    if discovery in ("popular","mainstream"):
                        q2 = q2.order_by(PersistentCandidate.mainstream_score.desc(), PersistentCandidate.popularity.desc())
                    elif discovery in ("obscure","very_obscure"):
                        q2 = q2.order_by(PersistentCandidate.obscurity_score.desc())
                    else:
                        from sqlalchemy import desc
                        q2 = q2.order_by(desc(PersistentCandidate.freshness_score), desc(PersistentCandidate.mainstream_score))
                    rows2 = q2.limit(min(limit * 6, 8000)).all()
                    for row in rows2:
                        try:
                            parsed_genres = []
                            if row.genres:
                                try:
                                    parsed = json.loads(row.genres)
                                    parsed_genres = parsed if isinstance(parsed, list) else []
                                except Exception:
                                    parsed_genres = []
                            if not _genres_match(parsed_genres):
                                continue
                            item = {
                                'title': row.title,
                                'year': row.year,
                                'ids': {'tmdb': row.tmdb_id, 'trakt': row.trakt_id},
                                'media_type': row.media_type,
                                'language': row.language,
                                'obscurity_score': row.obscurity_score,
                                'mainstream_score': row.mainstream_score,
                                'freshness_score': row.freshness_score,
                                'tmdb_data': {
                                    'popularity': row.popularity,
                                    'vote_average': row.vote_average,
                                    'vote_count': row.vote_count,
                                    'genres': parsed_genres,
                                    'poster_path': row.poster_path,
                                    'backdrop_path': row.backdrop_path,
                                    'overview': row.overview
                                },
                                'scoring_features': {
                                    'tmdb_popularity': row.popularity,
                                    'tmdb_rating': row.vote_average,
                                    'tmdb_votes': row.vote_count,
                                    'has_overview': bool(row.overview),
                                    'has_poster': bool(row.poster_path),
                                    'genre_count': len(parsed_genres)
                                },
                                '_from_persistent_store': True
                            }
                            stored_candidates.append(item)
                            if len(stored_candidates) >= limit:
                                return stored_candidates[:limit]
                        except Exception:
                            continue
                if stored_candidates:
                    logger.info(f"Found {len(stored_candidates)} candidates from persistent store")
                    # If we have enough from persistent store, return them
                    if len(stored_candidates) >= limit:
                        logger.info(f"Serving {len(stored_candidates)} candidates from persistent store (sufficient)")
                        return stored_candidates[:limit]
                    else:
                        logger.info(f"Persistent store has {len(stored_candidates)}/{limit} candidates, will supplement with TMDB discovery")
                        # DON'T return early - continue to TMDB discovery to fill the gap
            except Exception as e_pc:
                logger.debug(f"PersistentCandidate sourcing failed or not available: {e_pc}")
                stored_candidates = []  # Ensure it's defined for later merge
            # For fusion mode lists without explicit genres, extract from title
            if fusion_mode and list_title and not genres:
                title_genres = self._extract_genres_from_title(list_title)
                if title_genres:
                    genres = title_genres
                    genre_mode = "any"  # Be more lenient for title-based genres
                    logger.info(f"Fusion mode: extracted genres {title_genres} from title '{list_title}'")
            
            # Check cache first for expensive bulk operations
            discovery_mode = discovery or mood or "balanced"
            cache_key = self._generate_cache_key(
                media_type, discovery_mode,
                genres=genres, languages=languages, min_year=min_year, max_year=max_year,
                min_rating=min_rating, search_keywords=search_keywords
            )
            
            # Only use cache for ultra_discovery when persistent store doesn't fully satisfy
            # (cache bypassed for normal operations since PersistentCandidate handles it)
            use_cache = discovery_mode == "ultra_discovery" and limit >= 1000
            cached_candidates = None
            if use_cache:
                cached_candidates = await self._get_cached_candidates(cache_key)
                if cached_candidates:
                    logger.info(f"Using {len(cached_candidates)} cached candidates for {discovery_mode}")
                    return await self._finalize_candidates(
                        cached_candidates, limit, exclude_ids=await self._get_excluded_ids(media_type, include_watched),
                        existing_list_ids=existing_list_ids, enrich_with_tmdb=enrich_with_tmdb,
                        genres=genres, languages=languages, min_year=min_year, max_year=max_year,
                        min_rating=min_rating, genre_mode=genre_mode
                    )
            excluded_ids = await self._get_excluded_ids(media_type, include_watched)
            logger.info(f"Excluding {len(excluded_ids)} items based on filters")

            # If we have language filters, prioritize TMDB discover API for targeted search
            # This is much more efficient than generic searches
            candidates = list(stored_candidates) if stored_candidates else []
            # Defensive filter: ensure only correct media_type is returned
            expected_type = "movie" if media_type == "movies" else "show"
            candidates = [c for c in candidates if c.get("media_type") == expected_type]
            
            # Track if we used language-specific discovery (to avoid generic searches later)
            used_language_discovery = False
            
            # Only fetch from TMDB if persistent_only is False
            if not persistent_only and languages and len(candidates) < limit:
                needed = limit - len(candidates)
                logger.info(f"Using TMDB discover API for media_type={media_type}, language={languages}, need {needed} more candidates")
                tmdb_candidates = await self._fetch_tmdb_first_candidates(
                    media_type, languages, genres, search_keywords, needed * 3  # Fetch 3x for filtering margin
                )
                candidates.extend(tmdb_candidates)
                candidates = self._deduplicate(candidates)
                logger.info(f"After TMDB language discovery for {media_type}: {len(candidates)} total candidates")
                used_language_discovery = True
                
                # If we have enough now, skip the generic discovery
                if len(candidates) >= limit:
                    logger.info(f"TMDB language discovery provided sufficient candidates ({len(candidates)}/{limit}), skipping generic discovery")
                else:
                    logger.info(f"Still need more candidates from language discovery ({len(candidates)}/{limit})")

            # When language filters are active and we used language discovery, skip generic searches
            # This prevents mixing non-Danish content into Danish lists, etc.
            if used_language_discovery and languages:
                logger.info(f"Language-specific filters active ({languages}), skipping generic discovery to maintain language purity")
                target_candidates = len(candidates)  # Don't fetch more
            else:
                # Aggressively fetch candidates until we have at least 5x the requested limit for better filtering
                target_candidates = max(len(candidates), min(5 * limit, 8000))  # Don't reduce if we already have some
            
            # Auto-upgrade to ultra_discovery for large candidate pools
            if limit >= 1000 or target_candidates >= 3000:
                logger.info(f"Large candidate pool requested ({limit} items, {target_candidates} target), upgrading to ultra_discovery")
                discovery_mode = "ultra_discovery"
            
            # Only do generic discovery if we don't have enough candidates yet
            attempts = 0
            max_attempts = 8 if discovery_mode == "ultra_discovery" else 6  # More attempts for ultra mode
            
            while len(candidates) < target_candidates and attempts < max_attempts:
                needed = target_candidates - len(candidates)
                
                # For ultra_discovery, make larger batches to be more efficient
                batch_size = needed if discovery_mode == "ultra_discovery" else min(needed, 500)
                
                batch = await self._fetch_by_discovery(
                    media_type, discovery_mode, batch_size, search_keywords,
                    genres, languages, min_year, max_year, min_rating
                )
                candidates.extend(batch)
                candidates = self._deduplicate(candidates)
                attempts += 1
                logger.info(f"Bulk search progress: {len(candidates)}/{target_candidates} candidates after {attempts} attempts")
                
                # For ultra_discovery, if we get a good batch, we might be done early
                if discovery_mode == "ultra_discovery" and len(batch) >= needed // 2:
                    logger.info("Ultra discovery found sufficient candidates in single batch")
                    break
                    
                if len(batch) == 0:
                    logger.info("No more new candidates found, breaking early.")
                    break

            # If we have genres but no search keywords, enhance with genre-based search
            if genres and not search_keywords and len(candidates) < target_candidates:
                logger.info("Enhancing candidates with genre-based comprehensive search (bulk mode)")
                remaining_needed = target_candidates - len(candidates)
                
                # Use multiple genre-based strategies for better coverage
                genre_sources = [
                    self._fetch_genre_based_candidates(media_type, genres, remaining_needed // 3),
                    self._fetch_decade_based_candidates(media_type, remaining_needed // 3),
                    self._fetch_global_content(media_type, remaining_needed // 3),
                ]
                
                genre_results = await asyncio.gather(*genre_sources, return_exceptions=True)
                for result in genre_results:
                    if isinstance(result, list):
                        candidates.extend(result)
                        
                candidates = self._deduplicate(candidates)
                logger.info(f"After genre enhancement: {len(candidates)} total candidates")

            # Filter out excluded items and apply additional filters
            filtered = self._apply_filters(
                candidates, excluded_ids, genres, languages, min_year, max_year, min_rating, genre_mode=genre_mode
            )

            # If we have too few results after filtering, try a more lenient approach
            if len(filtered) < limit // 3:  # If less than 1/3 of requested amount
                logger.info(f"Only {len(filtered)} items after strict filtering, applying lenient filters")
                lenient_filtered = self._apply_filters(
                    candidates, excluded_ids, genres, None, 
                    min_year - 10 if min_year else None, 
                    max_year + 5 if max_year else None, 
                    min_rating - 1.0 if min_rating and min_rating > 1.0 else None, 
                    genre_mode="any"
                )
                if len(lenient_filtered) > len(filtered):
                    filtered = lenient_filtered
                    logger.info(f"Lenient filtering resulted in {len(filtered)} items")

            # Prefer new/novel content if possible
            if existing_list_ids:
                # Split into new and already-in-list
                new_items = [item for item in filtered if item.get('trakt_id') not in existing_list_ids]
                old_items = [item for item in filtered if item.get('trakt_id') in existing_list_ids]
                # If enough new items, use them; else fill with old
                if len(new_items) >= limit:
                    filtered = new_items[:limit]
                else:
                    filtered = new_items + old_items[:max(0, limit - len(new_items))]
            else:
                filtered = filtered[:limit]

            # TMDB enrichment: always attempt if we have candidates, even without explicit request
            if filtered:
                from app.services.tmdb_client import get_tmdb_api_key
                tmdb_api_key = None
                try:
                    tmdb_api_key = await get_tmdb_api_key()
                except Exception:
                    pass
                
                if tmdb_api_key:
                    # Enhanced metadata enrichment with aggressive retry for missing data
                    logger.info(f"Starting enrichment for {len(filtered)} filtered candidates")
                    enriched = await self._enrich_with_aggressive_metadata(filtered, media_type, genres, languages)
                    logger.info(f"Phase: after_enrichment count={len(enriched)}")
                    
                    # Apply final filtering after TMDB enrichment
                    final_filtered = self._apply_post_enrichment_filters(enriched, genres, languages, genre_mode=genre_mode)
                    logger.info(f"Phase: after_post_filter count={len(final_filtered)}")

                    # If we still don't have enough candidates after enrichment, be more lenient
                    if len(final_filtered) < max(limit // 2, 50):
                        logger.info(f"Post-enrichment yielded only {len(final_filtered)} candidates, applying lenient filtering")
                        lenient_filtered = self._apply_post_enrichment_filters(
                            enriched, 
                            genres=genres if genres and len(genres) <= 2 else (genres[:2] if genres else None),  # Reduce genre requirements
                            languages=languages, 
                            genre_mode="any"  # Switch to any-match for genres
                        )
                        if len(lenient_filtered) > len(final_filtered):
                            final_filtered = lenient_filtered
                            logger.info(f"Lenient post-enrichment filtering increased to {len(final_filtered)} candidates")

                    # Strict language filtering: do NOT broaden to other Nordic languages unless explicitly configured
                    # This preserves language purity for lists like Danish-only.
                    # If a future list needs fallback, it should pass explicit languages in filters.

                    # Ensure downstream fields exist
                    final_result = self._ensure_downstream_fields(final_filtered[:limit], media_type)
                    logger.info(f"Phase: final_result count={len(final_result)} (limit={limit})")
                    
                    # Save newly discovered candidates to persistent DB for future use
                    # This includes both selected and unselected candidates from TMDB
                    try:
                        saved_count = await self._save_discovered_candidates_to_db(enriched, media_type)
                        if saved_count > 0:
                            logger.info(f"Added {saved_count} new candidates to persistent pool")
                    except Exception as e:
                        logger.warning(f"Could not save discovered candidates: {e}")
                    
                    # Cache the raw candidates (before final limit) for expensive operations
                    if use_cache and len(candidates) >= 1000:
                        await self._cache_candidates(cache_key, media_type, discovery_mode, candidates, cache_hours=6)
                        # Cleanup old cache entries occasionally
                        if hash(cache_key) % 10 == 0:  # 10% chance
                            await self._cleanup_expired_cache()
                    
                    return final_result
                else:
                    logger.info("TMDB not configured, attempting Trakt metadata enhancement")
                    # Even without TMDB, try to get better Trakt metadata for items that need it
                    enhanced_filtered = await self._enhance_with_trakt_metadata(filtered, media_type, genres)
                    return self._ensure_downstream_fields(enhanced_filtered[:limit], media_type)
            else:
                # If enrichment not requested, ensure fallback fields
                for it in filtered:
                    if 'tmdb_data' not in it and 'cached_metadata' not in it:
                        it['tmdb_data'] = None
                return self._ensure_downstream_fields(filtered[:limit], media_type)

        except TraktAuthError as e:
            logger.error(f"Trakt authentication error: {e}")
            raise RuntimeError("Trakt account is not authorized. Please reauthorize your Trakt account.")
        except TraktUnavailableError as e:
            logger.error(f"Trakt API unavailable: {e}")
            raise RuntimeError("Trakt API is currently offline or rate limited. Please try again later.")
        except TraktNetworkError as e:
            logger.error(f"Network error with Trakt: {e}")
            raise RuntimeError("Network error connecting to Trakt API. Please check your connection or try again later.")
        except TraktAPIError as e:
            logger.error(f"Trakt API error: {e}")
            raise RuntimeError(f"Trakt API error: {e}")
        except Exception as e:
            logger.error(f"Failed to fetch candidates: {e}")
            raise RuntimeError("An unexpected error occurred while fetching recommendations. Please try again later.")
        finally:
            self.db.close()
    
    async def _fetch_by_discovery(
        self, 
        media_type: str, 
        discovery: Optional[str], 
        limit: int,
        search_keywords: Optional[List[str]] = None,
        genres: Optional[List[str]] = None,
        languages: Optional[List[str]] = None,
        min_year: Optional[int] = None,
        max_year: Optional[int] = None,
        min_rating: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """Fetch candidates based on discovery strategy (obscure/popular/balanced/deep_discovery)."""
        candidates = []
        
        if discovery == "obscure" or discovery == "very_obscure":
            # Use search with niche keywords and lesser-known content
            candidates.extend(await self._fetch_obscure_content(media_type, limit, search_keywords))
        elif discovery == "popular" or discovery == "mainstream":
            # Focus on trending and popular content
            # If languages specified, use TMDB-first for better language targeting
            if languages:
                logger.info(f"Popular discovery with languages {languages}: using TMDB-first strategy for {limit} items")
                candidates.extend(await self._fetch_tmdb_first_candidates(
                    media_type, languages, genres, search_keywords, limit
                ))
                logger.info(f"TMDB-first returned {len(candidates)} candidates")
            else:
                candidates.extend(await self._fetch_trending(media_type, limit // 2))
                candidates.extend(await self._fetch_popular(media_type, limit // 2))
        elif discovery == "deep_discovery":
            # Deep discovery: combine obscure, trending, popular, recommendations, and search for max diversity
            sources = [
                self._fetch_obscure_content(media_type, limit // 5, search_keywords),
                self._fetch_trending(media_type, limit // 5),
                self._fetch_popular(media_type, limit // 5),
                self._fetch_recommendations(media_type, limit // 5),
            ]
            if search_keywords:
                sources.append(self._fetch_search_results(media_type, search_keywords, limit // 5))
            results = await asyncio.gather(*sources, return_exceptions=True)
            for result in results:
                if isinstance(result, list):
                    candidates.extend(result)
            # Shuffle for variety
            import random
            random.shuffle(candidates)
        elif discovery == "ultra_discovery":
            # Ultra discovery: Use ALL available sources for maximum candidate pool (5000+)
            logger.info("Using ultra discovery mode - fetching from all available sources")
            source_limit = max(50, limit // 10)  # Each source contributes at least 50 items
            
            sources = [
                # Core content sources
                self._fetch_trending(media_type, source_limit),
                self._fetch_popular(media_type, source_limit),
                self._fetch_recommendations(media_type, source_limit),
                self._fetch_obscure_content(media_type, source_limit, search_keywords),
                
                # Genre-based discovery
                self._fetch_genre_based_candidates(media_type, genres or ["action", "comedy", "drama"], source_limit),
                
                # Decade-based discovery for variety
                self._fetch_decade_based_candidates(media_type, source_limit),
                self._fetch_comprehensive_decade_search(media_type, source_limit),
                
                # Global content discovery
                self._fetch_global_content(media_type, source_limit),
                
                # Alphabet-based search for comprehensive coverage
                self._fetch_alphabet_search(media_type, source_limit),
                
                # Fallback strategies
                self._fetch_fallback_candidates(media_type, source_limit, genres),
            ]
            
            # Add keyword searches if provided
            if search_keywords:
                sources.append(self._fetch_search_results(media_type, search_keywords, source_limit))
            
            # Add TMDB-first search for language-specific content
            if languages:
                sources.append(self._fetch_tmdb_first_candidates(
                    media_type, languages, genres, search_keywords, source_limit
                ))
            
            # Execute all sources in parallel for efficiency
            logger.info(f"Executing {len(sources)} content sources in parallel for ultra discovery")
            results = await asyncio.gather(*sources, return_exceptions=True)
            
            for i, result in enumerate(results):
                if isinstance(result, list):
                    candidates.extend(result)
                    logger.debug(f"Source {i+1} contributed {len(result)} candidates")
                elif isinstance(result, Exception):
                    logger.warning(f"Source {i+1} failed: {result}")
            
            # Remove duplicates and shuffle for variety
            candidates = self._deduplicate(candidates)
            import random
            random.shuffle(candidates)
            logger.info(f"Ultra discovery gathered {len(candidates)} unique candidates from {len(sources)} sources")
        else:
            # Balanced approach - mix of sources
            sources = [
                self._fetch_trending(media_type, limit // 4),
                self._fetch_popular(media_type, limit // 4),
                self._fetch_recommendations(media_type, limit // 4),
            ]
            
            # Add search results if keywords provided
            if search_keywords:
                sources.append(self._fetch_search_results(media_type, search_keywords, limit // 4))
            
            results = await asyncio.gather(*sources, return_exceptions=True)
            for result in results:
                if isinstance(result, list):
                    candidates.extend(result)
        
        # TMDB-first search: Use TMDB's superior search for language-specific queries
        # This is especially beneficial for non-English content like Danish movies/shows
        if languages and (search_keywords or genres):
            logger.info(f"Using TMDB-first search for languages: {languages}")
            tmdb_candidates = await self._fetch_tmdb_first_candidates(
                media_type, languages, genres, search_keywords, limit // 3
            )
            candidates.extend(tmdb_candidates)
            candidates = self._deduplicate(candidates)
        
        # Fallback: If we don't have enough candidates, search with broader terms
        candidates = self._deduplicate(candidates)
        if len(candidates) < limit // 2:  # If we have less than half the requested amount
            logger.info(f"Only found {len(candidates)} candidates, searching for more with fallback strategies")
            fallback_candidates = await self._fetch_fallback_candidates(media_type, limit - len(candidates), genres)
            candidates.extend(fallback_candidates)
            candidates = self._deduplicate(candidates)
        
        return candidates
    
    async def _fetch_obscure_content(
        self, 
        media_type: str, 
        limit: int, 
        search_keywords: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Fetch obscure/under-the-radar content using search."""
        candidates = []
        
        # Define obscure search terms by genre/mood
        obscure_terms = {
            "movies": [
                "independent", "indie film", "arthouse", "foreign film", 
                "film festival", "avant-garde", "experimental", "cult",
                "low budget", "underground", "neo-noir", "psychological thriller"
            ],
            "shows": [
                "limited series", "anthology", "foreign series", "indie series",
                "psychological drama", "experimental", "avant-garde", "cult series",
                "miniseries", "international"
            ]
        }
        
        # Use provided keywords or default obscure terms
        search_terms = search_keywords if search_keywords else obscure_terms.get(media_type, [])
        
        if search_terms:
            candidates.extend(await self._fetch_search_results(media_type, search_terms, limit))
        
        return candidates[:limit]
    
    async def _fetch_fallback_candidates(
        self,
        media_type: str,
        limit: int,
        genres: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Ultra-aggressive fallback search to ensure we meet the requested limit."""
        fallback_candidates = []
        remaining_needed = limit
        
        logger.info(f"Starting aggressive fallback search for {limit} {media_type} candidates")
        
        # Strategy 1: Massive genre-based search
        if genres and remaining_needed > 0:
            genre_candidates = await self._fetch_genre_based_candidates(media_type, genres, remaining_needed)
            fallback_candidates.extend(genre_candidates)
            remaining_needed = limit - len(self._deduplicate(fallback_candidates))
            logger.info(f"After genre search: {len(fallback_candidates)} candidates, need {remaining_needed} more")
        
        # Strategy 2: Popular/trending with much higher limits
        if remaining_needed > 0:
            try:
                # Fetch with much higher limits
                trending = await self._fetch_trending(media_type, min(100, remaining_needed))
                popular = await self._fetch_popular(media_type, min(100, remaining_needed))
                fallback_candidates.extend(trending)
                fallback_candidates.extend(popular)
                
                # Massive award search
                award_search_terms = ["award winning", "oscar", "golden globe", "critically acclaimed", 
                                    "film festival", "best picture", "nominated", "cannes", "sundance",
                                    "bafta", "emmy", "winner", "acclaimed", "festival"]
                award_candidates = await self._search_by_keywords(media_type, award_search_terms, remaining_needed // 2)
                fallback_candidates.extend(award_candidates)
                
                remaining_needed = limit - len(self._deduplicate(fallback_candidates))
                logger.info(f"After trending/awards: {len(fallback_candidates)} candidates, need {remaining_needed} more")
                
            except Exception as e:
                logger.warning(f"Fallback trending/popular failed: {e}")
        
        # Strategy 3: Ultra-broad decade and style search
        if remaining_needed > 0:
            decade_candidates = await self._fetch_comprehensive_decade_search(media_type, remaining_needed)
            fallback_candidates.extend(decade_candidates)
            remaining_needed = limit - len(self._deduplicate(fallback_candidates))
            logger.info(f"After decade search: {len(fallback_candidates)} candidates, need {remaining_needed} more")
        
        # Strategy 4: Language-agnostic global search (especially for Danish/foreign content)
        if remaining_needed > 0:
            global_candidates = await self._fetch_global_content(media_type, remaining_needed, genres)
            fallback_candidates.extend(global_candidates)
            remaining_needed = limit - len(self._deduplicate(fallback_candidates))
            logger.info(f"After global search: {len(fallback_candidates)} candidates, need {remaining_needed} more")
        
        # Strategy 5: TMDB discover-based fill for language/genre gaps (map back to Trakt)
        if remaining_needed > 0:
            try:
                logger.info(f"Attempting TMDB discover fill for {remaining_needed} items")
                tmdb_fill = await self._tmdb_discover_fill(media_type, remaining_needed, genres)
                fallback_candidates.extend(tmdb_fill)
                remaining_needed = limit - len(self._deduplicate(fallback_candidates))
                logger.info(f"After TMDB discover: {len(fallback_candidates)} candidates, need {remaining_needed} more")
            except Exception as e:
                logger.warning(f"TMDB discover fill failed: {e}")

        # Strategy 6: Last resort - alphabet soup search
        if remaining_needed > 0:
            alphabet_candidates = await self._fetch_alphabet_search(media_type, remaining_needed)
            fallback_candidates.extend(alphabet_candidates)
            logger.info(f"After alphabet search: {len(fallback_candidates)} total candidates")
        
        final_candidates = self._deduplicate(fallback_candidates)[:limit]
        logger.info(f"Final fallback result: {len(final_candidates)} candidates for requested {limit}")
        return final_candidates
    
    async def _fetch_genre_based_candidates(
        self,
        media_type: str,
        genres: List[str],
        limit: int
    ) -> List[Dict[str, Any]]:
        """Fetch candidates using comprehensive genre-based search."""
        candidates = []
        
        # Massively expanded genre keyword mapping for aggressive bulk search
        genre_search_terms = {
            "action": ["action", "adventure", "superhero", "martial arts", "war", "spy", "heist", 
                      "chase", "explosive", "adrenaline", "fast paced", "intense", "fighting",
                      "combat", "battle", "military", "soldier", "warrior", "assassin", "agent",
                      "rescue", "escape", "pursuit", "weapons", "guns", "swords", "violence"],
            "comedy": ["comedy", "funny", "humor", "satire", "parody", "romantic comedy", 
                      "dark comedy", "slapstick", "witty", "hilarious", "lighthearted", "amusing",
                      "entertaining", "laugh", "joke", "comic", "cheerful", "upbeat", "fun",
                      "silly", "absurd", "quirky", "eccentric", "playful", "charming"],
            "drama": ["drama", "family", "biographical", "historical", "character study", 
                     "emotional", "heartfelt", "touching", "powerful", "moving", "intense drama",
                     "serious", "deep", "profound", "meaningful", "tragic", "sad", "tears",
                     "relationships", "human", "personal", "intimate", "realistic", "social"],
            "sci-fi": ["science fiction", "futuristic", "space", "alien", "robot", "cyberpunk", 
                      "dystopian", "time travel", "artificial intelligence", "virtual reality",
                      "technology", "future", "spacecraft", "galaxy", "planet", "universe",
                      "android", "cyborg", "mutation", "experiment", "laboratory", "invention"],
            "horror": ["horror", "scary", "supernatural", "zombie", "vampire", "monster", 
                      "psychological horror", "slasher", "haunted", "terrifying", "nightmare",
                      "ghost", "demon", "evil", "dark", "creepy", "disturbing", "frightening",
                      "blood", "gore", "death", "murder", "killer", "psycho", "twisted"],
            "romance": ["romance", "love", "romantic", "relationship", "wedding", "dating", 
                       "love story", "passion", "heartbreak", "soulmate", "couple", "kiss",
                       "marriage", "boyfriend", "girlfriend", "attraction", "chemistry",
                       "valentine", "romantic comedy", "love triangle", "affair", "devotion",
                       "romantisk", "kærlighed"],
            "thriller": ["thriller", "suspense", "mystery", "crime", "detective", "conspiracy", 
                        "psychological thriller", "noir", "investigation", "tense", "dangerous",
                        "pursuit", "hunt", "chase", "escape", "fugitive", "criminal", "police",
                        "FBI", "murder mystery", "serial killer", "kidnapping", "blackmail",
                        "Nordic noir", "krimi", "whodunit", "paranoia", "hostage"],
            "documentary": ["documentary", "biography", "nature", "history", "investigative", 
                           "educational", "real life", "true story", "behind the scenes", "factual",
                           "wildlife", "science", "politics", "culture", "society", "environment",
                           "exploration", "interview", "archive", "historical", "current events"]
        }
        
        # Collect ALL search terms for provided genres (no limits for bulk search)
        search_terms = []
        for genre in genres:  # Use ALL genres, not just 3
            genre_lower = genre.lower()
            if genre_lower in genre_search_terms:
                # Add ALL terms per genre for maximum bulk discovery
                search_terms.extend(genre_search_terms[genre_lower])
        
        # Add extensive cross-genre combinations for better diversity
        if len(genres) >= 2:
            for i, genre1 in enumerate(genres[:4]):  # More genre combinations
                for genre2 in genres[i+1:4]:
                    search_terms.extend([f"{genre1} {genre2}", f"{genre2} {genre1}"])
                    # Explicit romantic comedy terms
                    if {genre1.lower(), genre2.lower()} == {"romance", "comedy"}:
                        search_terms.extend(["romantic comedy", "romcom", "rom-com", "romantisk komedie"])
        
        # Add international variants for each genre
        for genre in genres[:3]:
            search_terms.extend([
                f"international {genre}", f"foreign {genre}", f"european {genre}",
                f"american {genre}", f"british {genre}", f"independent {genre}"
            ])
        
        # Search with all collected terms
        if search_terms:
            candidates = await self._search_by_keywords(media_type, search_terms, limit)
        
        return candidates
    
    async def _fetch_decade_based_candidates(
        self,
        media_type: str,
        limit: int
    ) -> List[Dict[str, Any]]:
        """Fetch candidates using decade-based search terms."""
        candidates = []
        
        # Search by decades and eras
        decade_terms = [
            "90s movies", "2000s", "2010s", "modern", "contemporary", "recent",
            "classic", "retro", "vintage", "timeless", "cult classic",
            "indie", "independent", "foreign", "international", "arthouse"
        ]
        
        try:
            decade_candidates = await self._search_by_keywords(media_type, decade_terms, limit)
            candidates.extend(decade_candidates)
        except Exception as e:
            logger.warning(f"Decade-based search failed: {e}")
        
        return candidates
    
    async def _fetch_comprehensive_decade_search(
        self,
        media_type: str,
        limit: int
    ) -> List[Dict[str, Any]]:
        """Ultra-comprehensive decade and style search for bulk discovery."""
        candidates = []
        
        # Massive decade/era search terms
        comprehensive_terms = [
            # Decades
            "1970s", "1980s", "1990s", "2000s", "2010s", "2020s",
            "70s", "80s", "90s", "00s", "10s", "20s",
            
            # Eras and movements
            "classic", "golden age", "new wave", "modern", "contemporary",
            "retro", "vintage", "nostalgic", "period", "historical",
            
            # Production styles
            "indie", "independent", "arthouse", "auteur", "experimental",
            "mainstream", "commercial", "blockbuster", "low budget",
            
            # Geographic/cultural
            "american", "british", "european", "asian", "international",
            "foreign", "world cinema", "subtitled", "dubbed",
            
            # Quality indicators
            "acclaimed", "celebrated", "masterpiece", "influential",
            "groundbreaking", "iconic", "legendary", "essential",
            
            # Popularity
            "popular", "hit", "successful", "bestselling", "top rated",
            "fan favorite", "cult", "underground", "hidden gem",
            
            # Festival circuit
            "cannes", "sundance", "venice", "berlin", "toronto",
            "festival", "competition", "premiere", "selection"
        ]
        
        try:
            candidates = await self._search_by_keywords(media_type, comprehensive_terms, limit)
        except Exception as e:
            logger.warning(f"Comprehensive decade search failed: {e}")
        
        return candidates
    
    async def _fetch_global_content(
        self,
        media_type: str,
        limit: int,
        genres: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Search for global/international content, especially useful for Danish/foreign requests."""
        candidates = []
        
        # Global search terms - especially important for non-English content
        global_terms = [
            # Languages and regions
            "danish", "dansk", "denmark", "dk", "københavn", "aarhus", "odense",
            "scandinavian", "skandinavisk", "nordic", "nordisk",
            "german", "french", "italian", "spanish", "dutch", "swedish", "norwegian", "finnish",
            "international", "foreign", "subtitled", "world cinema",
            
            # International genres/styles
            "european cinema", "nordic noir", "scandinavian", "arthouse",
            "international drama", "foreign film", "world movie",
            
            # Production origins
            "denmark", "sweden", "norway", "germany", "france", "italy",
            "netherlands", "belgium", "switzerland", "austria",
            
            # Danish creators, awards, networks
            "Mads Mikkelsen", "Nicolas Winding Refn", "Lars von Trier", "Susanne Bier",
            "Trine Dyrholm", "Ulrich Thomsen", "Sidse Babett Knudsen",
            "Bodil Awards", "Robertprisen", "Nordic Council Film Prize",
            "DR", "DR Drama", "TV 2 Danmark", "Viaplay", "Zentropa", "Nordisk Film",
            "Nordic noir", "krimi", "romantisk komedie"
        ]
        
        # Add genre-specific international terms
        if genres:
            for genre in genres:
                genre_lower = genre.lower()
                global_terms.extend([
                    f"{genre_lower} international", f"european {genre_lower}",
                    f"scandinavian {genre_lower}", f"foreign {genre_lower}"
                ])
        
        try:
            candidates = await self._search_by_keywords(media_type, global_terms, limit)
        except Exception as e:
            logger.warning(f"Global content search failed: {e}")
        
        return candidates

    async def _tmdb_discover_fill(self, media_type: str, limit: int, genres: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Use TMDB discover to find relevant items for sparse regions/languages and map back to Trakt.
        Focus on Nordic languages first to improve Danish coverage. This is a best-effort fill.
        """
        from app.services.tmdb_client import discover_movies, discover_tv
        mapped: List[Dict[str, Any]] = []
        seen: Set[int] = set()

        # Prefer Nordic languages for Danish lists; expand to nearby to ensure enough volume
        languages = ["da", "sv", "no", "fi", "de", "fr"]
        pages_per_lang = 2
        for lang in languages:
            try:
                for page in range(1, pages_per_lang + 1):
                    data = None
                    if media_type == "movies":
                        data = await discover_movies(original_language=lang, page=page)
                    else:
                        data = await discover_tv(original_language=lang, page=page)
                    results = (data or {}).get("results") or []
                    for r in results:
                        tmdb_id = r.get("id")
                        if not tmdb_id:
                            continue
                        # Map TMDB -> Trakt
                        try:
                            mapped_results = await self.trakt_client.search_by_tmdb_id(
                                tmdb_id, "movie" if media_type == "movies" else "show"
                            )
                        except Exception:
                            mapped_results = []
                        for mr in mapped_results or []:
                            item = mr.get("movie") if media_type == "movies" else mr.get("show")
                            if not item:
                                continue
                            trakt_id = (item.get("ids") or {}).get("trakt")
                            if trakt_id and trakt_id not in seen:
                                # Annotate with language for downstream filters
                                item["language"] = r.get("original_language") or lang
                                mapped.append(item)
                                seen.add(trakt_id)
                                if len(mapped) >= limit:
                                    return mapped[:limit]
            except Exception:
                continue
        return mapped[:limit]
    
    async def _fetch_alphabet_search(
        self,
        media_type: str,
        limit: int
    ) -> List[Dict[str, Any]]:
        """Last resort: search by common title patterns and words."""
        candidates = []
        
        # Common title words and patterns
        common_words = [
            "the", "a", "an", "my", "our", "one", "two", "three", "last", "first",
            "new", "old", "big", "little", "great", "good", "bad", "best", "worst",
            "love", "life", "death", "war", "peace", "time", "night", "day",
            "home", "house", "man", "woman", "girl", "boy", "king", "queen",
            "world", "city", "story", "tale", "game", "play", "show", "movie"
        ]
        
        # Single letter searches (surprisingly effective for discovery)
        letters = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j']
        
        # Combine both strategies
        search_terms = common_words[:15] + letters  # Limited to prevent API spam
        
        try:
            candidates = await self._search_by_keywords(media_type, search_terms, limit)
        except Exception as e:
            logger.warning(f"Alphabet search failed: {e}")
        
        return candidates
    
    async def _fetch_search_results(
        self, 
        media_type: str, 
        keywords: List[str], 
        limit: int
    ) -> List[Dict[str, Any]]:
        """Enhanced search results using multiple search strategies and fields."""
        candidates = []
        
        # Strategy 1: Direct title/keyword search
        title_candidates = await self._search_by_keywords(media_type, keywords, limit // 3)
        candidates.extend(title_candidates)
        
        # Strategy 2: Genre and taste profile based search
        taste_candidates = await self._search_by_taste_profile(media_type, keywords, limit // 3)
        candidates.extend(taste_candidates)
        
        # Strategy 3: Related content search using taglines/overviews
        related_candidates = await self._search_related_content(media_type, keywords, limit // 3)
        candidates.extend(related_candidates)
        
        return self._deduplicate(candidates)[:limit]
    
    async def _search_by_keywords(
        self,
        media_type: str,
        keywords: List[str],
        limit: int
    ) -> List[Dict[str, Any]]:
        """Aggressive bulk search by keywords with high volume."""
        candidates = []
        
        if not keywords:
            return candidates
            
        # Much more aggressive search - aim for 50+ results per keyword for bulk discovery
        per_keyword_limit = max(50, limit // max(1, len(keywords)))
        
        # Process keywords in batches to avoid overwhelming the API
        batch_size = 5
        for i in range(0, min(len(keywords), 40), batch_size):  # Process up to 40 keywords
            batch_keywords = keywords[i:i + batch_size]
            search_tasks = []
            
            for keyword in batch_keywords:
                # Search with higher limits for bulk discovery
                search_tasks.append(self._search_with_term(media_type, keyword, per_keyword_limit))
            
            if search_tasks:
                results = await asyncio.gather(*search_tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, list):
                        candidates.extend(result)
                        
                # Log progress for bulk operations
                if len(candidates) % 100 == 0 and len(candidates) > 0:
                    logger.info(f"Bulk search progress: {len(candidates)} candidates found")
            
            # Short delay between batches to be respectful to API
            if i + batch_size < len(keywords):
                await asyncio.sleep(0.1)
        
        return candidates
    
    async def _search_by_taste_profile(
        self,
        media_type: str,
        base_keywords: List[str],
        limit: int
    ) -> List[Dict[str, Any]]:
        """Search based on user's taste profile and genre preferences."""
        candidates = []
        
        # Get user's watch history for taste analysis
        try:
            watched_history = await self.trakt_client.get_my_history(media_type=media_type, limit=50)
            taste_keywords = self._extract_taste_keywords(watched_history)
        except Exception as e:
            logger.warning(f"Failed to get watch history for taste profile: {e}")
            taste_keywords = []
        
        # Combine base keywords with taste-derived keywords
        enhanced_keywords = list(set(base_keywords + taste_keywords))[:10]
        
        # Search with enhanced keyword set
        if enhanced_keywords:
            taste_candidates = await self._search_by_keywords(media_type, enhanced_keywords, limit)
            candidates.extend(taste_candidates)
        
        return candidates
    
    async def _search_related_content(
        self,
        media_type: str,
        keywords: List[str],
        limit: int
    ) -> List[Dict[str, Any]]:
        """Search for related content using expanded search terms."""
        candidates = []
        
        # Generate related search terms based on genres and themes
        related_terms = self._generate_related_search_terms(keywords)
        
        # Search with related terms
        if related_terms:
            related_candidates = await self._search_by_keywords(media_type, related_terms, limit)
            candidates.extend(related_candidates)
        
        return candidates
    
    def _extract_taste_keywords(self, watched_history: List[Dict[str, Any]]) -> List[str]:
        """Extract keywords from user's watch history to understand taste profile."""
        keywords = set()
        
        for item in watched_history[:20]:  # Analyze recent 20 items
            try:
                # Extract from different sources
                content = None
                if 'movie' in item:
                    content = item['movie']
                elif 'show' in item:
                    content = item['show']
                elif 'episode' in item and 'show' in item['episode']:
                    content = item['episode']['show']
                
                if content:
                    # Extract genres
                    genres = content.get('genres', [])
                    for genre in genres[:3]:  # Top 3 genres
                        keywords.add(genre.lower())
                    
                    # Extract from title words
                    title = content.get('title', '')
                    if title:
                        # Extract meaningful words from title
                        title_words = [w.lower().strip() for w in title.split() 
                                     if len(w) > 3 and w.lower() not in ['the', 'and', 'for', 'with']]
                        keywords.update(title_words[:2])  # Max 2 words per title
                    
                    # Extract from overview if available
                    overview = content.get('overview', '')
                    if overview:
                        overview_keywords = self._extract_keywords_from_text(overview)
                        keywords.update(overview_keywords[:2])
            
            except Exception:
                continue
        
        return list(keywords)[:15]  # Return top 15 taste keywords
    
    def _generate_related_search_terms(self, base_keywords: List[str]) -> List[str]:
        """Generate related search terms based on genres and themes."""
        related_terms = set()
        
        # Genre expansion mapping
        genre_expansions = {
            'action': ['adventure', 'thriller', 'superhero', 'martial arts', 'spy'],
            'comedy': ['humor', 'satire', 'parody', 'romantic comedy', 'dark comedy'],
            'drama': ['family', 'biographical', 'historical', 'social', 'character study'],
            'horror': ['supernatural', 'psychological', 'zombie', 'vampire', 'monster'],
            'sci-fi': ['futuristic', 'space', 'alien', 'dystopian', 'cyberpunk'],
            'romance': ['love story', 'romantic', 'relationship', 'wedding', 'dating'],
            'thriller': ['suspense', 'mystery', 'crime', 'detective', 'conspiracy'],
            'documentary': ['biography', 'nature', 'history', 'investigative', 'educational']
        }
        
        # Theme expansion mapping
        theme_expansions = {
            'family': ['friendship', 'coming of age', 'heartwarming', 'feel good'],
            'war': ['military', 'conflict', 'battle', 'historical'],
            'music': ['musical', 'concert', 'band', 'artist'],
            'sports': ['competition', 'team', 'championship', 'athlete'],
            'travel': ['adventure', 'journey', 'road trip', 'exploration']
        }
        
        for keyword in base_keywords:
            keyword_lower = keyword.lower()
            
            # Add genre expansions
            if keyword_lower in genre_expansions:
                related_terms.update(genre_expansions[keyword_lower][:3])
            
            # Add theme expansions
            if keyword_lower in theme_expansions:
                related_terms.update(theme_expansions[keyword_lower][:2])
            
            # Add the original keyword
            related_terms.add(keyword_lower)
        
        return list(related_terms)[:12]  # Return top 12 related terms
    
    def _extract_keywords_from_text(self, text: str) -> List[str]:
        """Extract meaningful keywords from overview/description text."""
        import re
        
        # Common words to filter out
        stop_words = {
            'the', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with',
            'by', 'from', 'up', 'about', 'into', 'through', 'during', 'before',
            'after', 'above', 'below', 'between', 'among', 'through', 'during',
            'before', 'after', 'above', 'below', 'up', 'down', 'out', 'off', 'over',
            'under', 'again', 'further', 'then', 'once', 'here', 'there', 'when',
            'where', 'why', 'how', 'all', 'any', 'both', 'each', 'few', 'more',
            'most', 'other', 'some', 'such', 'only', 'own', 'same', 'so', 'than',
            'too', 'very', 'can', 'will', 'just', 'should', 'now', 'his', 'her',
            'their', 'them', 'they', 'she', 'him', 'this', 'that', 'these', 'those'
        }
        
        # Extract words, filter stop words and short words
        words = re.findall(r'\b[a-zA-Z]{4,}\b', text.lower())
        keywords = [w for w in words if w not in stop_words]
        
        # Return unique keywords, limited count
        return list(set(keywords))[:8]

    # -------- Language Normalization Helper --------
    def _normalize_language(self, item: Dict[str, Any]):
        """Ensure language appears on both root and nested movie/show dict.
        Chooses first non-empty from: item.language, nested.language, tmdb_data.original_language, cached_metadata.language."""
        if not isinstance(item, dict):
            return
        candidates = []
        if item.get('language'): candidates.append(item.get('language'))
        for key in ('movie','show'):
            if key in item and isinstance(item[key], dict):
                if item[key].get('language'):
                    candidates.append(item[key]['language'])
        td = item.get('tmdb_data') or {}
        if isinstance(td, dict) and td.get('original_language'):
            candidates.append(td.get('original_language'))
        cd = item.get('cached_metadata') or {}
        if isinstance(cd, dict) and cd.get('language'):
            candidates.append(cd.get('language'))
        lang = next((l for l in candidates if l), None)
        if not lang:
            return
        item['language'] = lang
        for key in ('movie','show'):
            if key in item and isinstance(item[key], dict):
                item[key].setdefault('language', lang)
    
    async def _search_with_term(
        self,
        media_type: str,
        term: str,
        limit: int
    ) -> List[Dict[str, Any]]:
        """Aggressive search with Trakt API for maximum bulk discovery."""
        try:
            # Use Trakt's search API which searches across title, tagline, overview, etc.
            search_type = "movie" if media_type == "movies" else "show"
            
            # For bulk operations, always request the maximum from Trakt API
            api_limit = min(100, max(50, limit))  # At least 50, up to 100 per term
            results = await self.trakt_client.search(term, search_type, api_limit)
            
            if not results:
                # Try variations of the search term for better coverage
                variations = self._generate_search_variations(term)
                for variation in variations[:3]:  # Try up to 3 variations
                    try:
                        results = await self.trakt_client.search(variation, search_type, api_limit)
                        if results:
                            break
                    except Exception:
                        continue

            candidates: List[Dict[str, Any]] = []
            for result in (results or [])[:limit]:
                content = result.get(search_type)
                if not content:
                    continue
                # Preserve score
                content['search_score'] = result.get('score', 0)
                # Normalize language if present inside content
                self._normalize_language(content)
                candidates.append(content)
            return candidates
            
        except Exception as e:
            logger.warning(f"Search failed for term '{term}': {e}")
            return []
    
    def _generate_search_variations(self, term: str) -> List[str]:
        """Generate search term variations for better coverage."""
        variations = []
        
        # Add plural/singular variations
        if term.endswith('s') and len(term) > 3:
            variations.append(term[:-1])  # Remove 's'
        else:
            variations.append(term + 's')   # Add 's'
        
        # Add common prefixes/suffixes for broader matching
        if len(term) > 4:
            variations.extend([
                f"the {term}",
                f"{term} movie" if "movie" not in term.lower() else term,
                f"{term} film" if "film" not in term.lower() else term,
            ])
        
        return variations
    
    async def _get_excluded_ids(self, media_type: str, include_watched: bool) -> Set[int]:
        """Get Trakt IDs to exclude based on user preferences."""
        excluded = set()
        
        if not include_watched:
            try:
                # Get watched history from Trakt
                watched = await self.trakt_client.get_my_history(
                    media_type=media_type, 
                    limit=2000  # Increased for better filtering
                )
                
                for item in watched:
                    content = item.get(media_type[:-1]) if media_type.endswith('s') else item.get(media_type)
                    if content and content.get('ids', {}).get('trakt'):
                        excluded.add(content['ids']['trakt'])
                
                logger.info(f"Excluding {len(excluded)} watched {media_type}")
                
            except Exception as e:
                logger.warning(f"Failed to fetch watched history: {e}")
        
        # Add items from existing lists if user doesn't want duplicates
        try:
            from app.models import UserList as UserListModel, ListItem as ListItemModel
            existing_items = (
                self.db.query(ListItemModel)
                .select_from(ListItemModel)
                .join(UserListModel, ListItemModel.smartlist_id == UserListModel.id)
                .filter(UserListModel.user_id == self.user_id)
                .all()
            )
            for item in existing_items:
                try:
                    if item.item_id.isdigit():
                        excluded.add(int(item.item_id))
                except (ValueError, AttributeError):
                    continue
            logger.info(f"Excluding {len(existing_items)} items from existing lists")
        except Exception as e:
            logger.warning(f"Failed to fetch existing list items: {e}")
        
        return excluded
    
    
    async def _fetch_trending(self, media_type: str, limit: int) -> List[Dict[str, Any]]:
        """Fetch trending items from Trakt."""
        try:
            items = await self.trakt_client.get_trending(media_type=media_type, limit=limit)
            return self._normalize_trakt_response(items, media_type)
        except Exception as e:
            logger.warning(f"Failed to fetch trending {media_type}: {e}")
            return []
    
    async def _fetch_popular(self, media_type: str, limit: int) -> List[Dict[str, Any]]:
        """Fetch popular items from Trakt."""
        try:
            items = await self.trakt_client.get_popular(media_type=media_type, limit=limit)
            return self._normalize_trakt_response(items, media_type)
        except Exception as e:
            logger.warning(f"Failed to fetch popular {media_type}: {e}")
            return []
    
    async def _fetch_recommendations(self, media_type: str, limit: int) -> List[Dict[str, Any]]:
        """Fetch personalized recommendations from Trakt."""
        try:
            items = await self.trakt_client.get_recommendations(media_type=media_type, limit=limit)
            return self._normalize_trakt_response(items, media_type)
        except Exception as e:
            logger.warning(f"Failed to fetch recommendations for {media_type}: {e}")
            return []
    
    def _normalize_trakt_response(self, items: List[Dict[str, Any]], media_type: str) -> List[Dict[str, Any]]:
        """Normalize Trakt API responses that may wrap items."""
        normalized = []
        item_key = media_type[:-1] if media_type.endswith('s') else media_type  # "movie" or "show"
        
        for item in items or []:
            if isinstance(item, dict):
                # Some endpoints return {"movie": {...}} or {"show": {...}}
                if item_key in item:
                    content = item[item_key]
                else:
                    content = item
                
                # Ensure we have required fields
                if content.get('ids', {}).get('trakt'):
                    content['type'] = item_key  # Add type for consistency
                    normalized.append(content)
        
        return normalized
    
    def _deduplicate(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove duplicate items based on Trakt ID or TMDB ID (fallback)."""
        seen = set()
        filtered = []
        
        for item in items:
            # Check for IDs at root level first (for backward compatibility)
            trakt_id = item.get('ids', {}).get('trakt')
            tmdb_id = item.get('ids', {}).get('tmdb')
            
            # If not found, check inside nested movie/show structure (Trakt API format)
            if not trakt_id and not tmdb_id:
                if 'movie' in item and isinstance(item.get('movie'), dict):
                    trakt_id = item['movie'].get('ids', {}).get('trakt')
                    tmdb_id = item['movie'].get('ids', {}).get('tmdb')
                elif 'show' in item and isinstance(item.get('show'), dict):
                    trakt_id = item['show'].get('ids', {}).get('trakt')
                    tmdb_id = item['show'].get('ids', {}).get('tmdb')
            
            # Use Trakt ID if available, otherwise use TMDB ID
            # Prefix TMDB IDs with 't' to avoid collision with Trakt IDs
            unique_id = trakt_id if trakt_id else f"tmdb_{tmdb_id}" if tmdb_id else None
            
            if not unique_id or unique_id in seen:
                continue
            
            seen.add(unique_id)
            filtered.append(item)
        
        return filtered
    
    def _apply_filters(
        self, 
        items: List[Dict[str, Any]], 
        excluded_ids: Set[int],
        genres: Optional[List[str]] = None,
        languages: Optional[List[str]] = None,
        min_year: Optional[int] = None,
        max_year: Optional[int] = None,
        min_rating: Optional[float] = None
        , genre_mode: str = "any"
    ) -> List[Dict[str, Any]]:
        """Apply all filters to candidate items."""
        filtered = []
        
        for item in items:
            # Support both flat and nested Trakt structures from search results
            inner = None
            if isinstance(item, dict):
                inner = item.get('movie') or item.get('show')

            # IDs (prefer flat, then nested)
            ids_obj = item.get('ids') if isinstance(item.get('ids'), dict) else None
            trakt_id = ids_obj.get('trakt') if ids_obj else None
            tmdb_id = ids_obj.get('tmdb') if ids_obj else None
            if (trakt_id is None and tmdb_id is None) and inner and isinstance(inner, dict):
                inner_ids = inner.get('ids') if isinstance(inner.get('ids'), dict) else None
                if inner_ids:
                    trakt_id = inner_ids.get('trakt')
                    tmdb_id = inner_ids.get('tmdb')
            
            # Accept items with either Trakt ID or TMDB ID
            if not trakt_id and not tmdb_id:
                continue
            
            # Skip excluded items (check both Trakt and TMDB IDs)
            if excluded_ids:
                if trakt_id in excluded_ids or tmdb_id in excluded_ids:
                    continue
            
            # Apply year filters
            year = item.get('year')
            if year is None and inner and isinstance(inner, dict):
                year = inner.get('year')
            if min_year and (not year or year < min_year):
                continue
            if max_year and (not year or year > max_year):
                continue
            
            # Apply rating filter (using Trakt rating if available)
            rating = item.get('rating')
            if rating is None and inner and isinstance(inner, dict):
                rating = inner.get('rating')
            if min_rating and (not rating or rating < min_rating):
                continue
            
            # Apply language filters
            if languages:
                item_language = item.get('language')
                if not item_language and inner and isinstance(inner, dict):
                    item_language = inner.get('language')
                # Be lenient: if no language info, don't exclude (could be Danish content without proper tagging)
                if item_language and item_language not in languages:
                    continue
            
            # Apply genre filters (basic check - enhanced with TMDB data later)
            if genres:
                item_genres = item.get('genres', [])
                if (not item_genres) and inner and isinstance(inner, dict):
                    item_genres = inner.get('genres', [])
                # If no matching genres found in basic check, still include item for metadata enhancement
                # We'll be aggressive about finding metadata for items missing genre info
                if not item_genres:
                    # Mark item as needing metadata enhancement
                    item['_needs_metadata_enhancement'] = True
                else:
                    genre_set = set(g.lower() for g in item_genres)
                    filter_set = set(g.lower() for g in genres)
                    if genre_mode == "all":
                        # Require all genres to be present
                        if not filter_set.issubset(genre_set):
                            # Still include but mark for enhancement
                            item['_needs_metadata_enhancement'] = True
                    else:
                        # Require any genre to match
                        if not genre_set.intersection(filter_set):
                            # Still include but mark for enhancement
                            item['_needs_metadata_enhancement'] = True
            
            filtered.append(item)
        
        return filtered
    
    def _apply_post_enrichment_filters(
        self, 
        items: List[Dict[str, Any]], 
        genres: Optional[List[str]] = None,
        languages: Optional[List[str]] = None
        , genre_mode: str = "any"
    ) -> List[Dict[str, Any]]:
        """Apply strict filtering after TMDB enrichment for better accuracy."""
        if not genres and not languages:
            return items
        
        filtered = []
        for item in items:
            # Support nested Trakt search result structure { 'movie': {...}} or { 'show': {...}}
            inner = None
            if isinstance(item, dict):
                inner = item.get('movie') or item.get('show')

            # Apply strict language filtering with enriched metadata
            if languages:
                # Check both original Trakt language and TMDB language
                item_language = item.get('language')
                if not item_language and inner and isinstance(inner, dict):
                    item_language = inner.get('language')
                tmdb_language = None
                
                # Get language from TMDB data or cached metadata
                if item.get('tmdb_data'):
                    tmdb_language = item['tmdb_data'].get('original_language')
                elif item.get('cached_metadata'):
                    tmdb_language = item['cached_metadata'].get('language')
                # Also inspect nested tmdb_data if present under inner
                if not tmdb_language and inner and isinstance(inner, dict):
                    nested_tmdb = inner.get('tmdb_data') or inner.get('cached_metadata')
                    if isinstance(nested_tmdb, dict):
                        tmdb_language = nested_tmdb.get('original_language') or nested_tmdb.get('language')
                
                # Item passes if either language matches
                # STRICT MODE: If we have language filters, items MUST match
                language_match = False
                if item_language and item_language in languages:
                    language_match = True
                elif tmdb_language and tmdb_language in languages:
                    language_match = True
                # REMOVED: Lenient fallback when no language info
                # Items without language metadata are now excluded when filters are active
                
                if not language_match:
                    continue
            
            # Apply strict genre filtering with enriched metadata
            if genres:
                # Collect genres from both Trakt and TMDB
                all_genres = set()
                
                # Add Trakt genres
                trakt_genres = item.get('genres', [])
                if trakt_genres:
                    all_genres.update([g.lower() for g in trakt_genres])
                # Include nested genres if present
                if inner and isinstance(inner, dict):
                    nested_genres = inner.get('genres') or []
                    if isinstance(nested_genres, list):
                        all_genres.update([g.lower() for g in nested_genres if isinstance(g, str)])
                    elif isinstance(nested_genres, str):
                        try:
                            parsed = json.loads(nested_genres)
                            if isinstance(parsed, list):
                                all_genres.update([g.lower() for g in parsed if isinstance(g, str)])
                        except Exception:
                            pass
                
                # Add TMDB genres
                if item.get('tmdb_data') and item['tmdb_data'].get('genres'):
                    tmdb_genres = item['tmdb_data']['genres']
                    # tmdb_data.genres is already a list of strings from _merge_trakt_tmdb
                    if isinstance(tmdb_genres, list):
                        all_genres.update([g.lower() for g in tmdb_genres if isinstance(g, str)])
                    elif isinstance(tmdb_genres, str):
                        try:
                            parsed_genres = json.loads(tmdb_genres)
                            all_genres.update([g.lower() for g in parsed_genres if isinstance(g, str)])
                        except:
                            pass
                elif item.get('cached_metadata') and item['cached_metadata'].get('genres'):
                    cached_genres = item['cached_metadata']['genres']
                    if isinstance(cached_genres, str):
                        try:
                            cached_genres = json.loads(cached_genres)
                        except:
                            cached_genres = []
                    if isinstance(cached_genres, list):
                        all_genres.update([g.lower() for g in cached_genres])
                # Nested TMDB genre info
                if inner and isinstance(inner, dict):
                    nested_tmdb = inner.get('tmdb_data') or inner.get('cached_metadata')
                    if nested_tmdb and isinstance(nested_tmdb, dict):
                        ng = nested_tmdb.get('genres')
                        if isinstance(ng, list):
                            all_genres.update([g.lower() for g in ng if isinstance(g, str)])
                        elif isinstance(ng, str):
                            try:
                                parsed = json.loads(ng)
                                if isinstance(parsed, list):
                                    all_genres.update([g.lower() for g in parsed if isinstance(g, str)])
                            except Exception:
                                pass
                
                filter_set = set(g.lower() for g in genres)
                
                # Define unwanted genres that should be excluded even if they match criteria
                unwanted_genres = {'animation', 'family', 'kids', 'children', 'cartoon', 'anime'}
                
                # First check: exclude items with unwanted genres
                if all_genres.intersection(unwanted_genres):
                    continue
                
                # Second check: ensure item has required genres
                # STRICT MODE: Items without genre metadata are excluded when genre filters are active
                if not all_genres:
                    # No genre metadata - exclude when filters are active
                    continue
                else:
                    if genre_mode == "all":
                        if not filter_set.issubset(all_genres):
                            continue
                    else:
                        if not all_genres.intersection(filter_set):
                            continue
            
            filtered.append(item)
        
        return filtered
    
    async def _enrich_with_metadata(self, items: List[Dict[str, Any]], media_type: str) -> List[Dict[str, Any]]:
        """Enrich items with cached or fresh TMDB metadata."""
        if not items:
            return items
        
        logger.info(f"Enriching {len(items)} items with metadata")
        enriched = []
        
        for item in items:
            trakt_id = item.get('ids', {}).get('trakt')
            if not trakt_id:
                enriched.append(item)
                continue
            
            # Check database cache first
            cached_metadata = self.db.query(MediaMetadata).filter_by(
                trakt_id=trakt_id, 
                media_type=media_type[:-1] if media_type.endswith('s') else media_type
            ).first()
            
            if cached_metadata and self._is_metadata_fresh(cached_metadata):
                # Use cached data
                enriched_item = self._merge_with_cached_metadata(item, cached_metadata)
            else:
                # Fetch fresh TMDB data
                enriched_item = await self._fetch_and_cache_metadata(item, media_type)
            
            enriched.append(enriched_item)
        
        logger.info(f"Successfully enriched {len(enriched)} items")
        return enriched
    
    def _is_metadata_fresh(self, metadata: MediaMetadata, max_age_days: int = 7) -> bool:
        """Check if cached metadata is still fresh."""
        if not metadata.last_updated:
            return False
        
        import datetime
        age = datetime.datetime.utcnow() - metadata.last_updated
        return age.days < max_age_days
    
    def _merge_with_cached_metadata(self, item: Dict[str, Any], metadata: MediaMetadata) -> Dict[str, Any]:
        """Merge item with cached metadata."""
        enriched = item.copy()
        
        enriched['cached_metadata'] = {
            'overview': metadata.overview,
            'poster_path': metadata.poster_path,
            'backdrop_path': metadata.backdrop_path,
            'genres': json.loads(metadata.genres) if metadata.genres else [],
            'keywords': json.loads(metadata.keywords) if metadata.keywords else [],
            'language': metadata.language,
            'rating': metadata.rating,
            'votes': metadata.votes,
            'popularity': metadata.popularity,
        }
        
        # Add scoring features
        enriched['scoring_features'] = {
            'has_overview': bool(metadata.overview),
            'has_poster': bool(metadata.poster_path),
            'tmdb_popularity': metadata.popularity or 0.0,
            'tmdb_rating': metadata.rating or 0.0,
            'tmdb_votes': metadata.votes or 0,
            'genre_count': len(json.loads(metadata.genres) if metadata.genres else []),
            'keyword_count': len(json.loads(metadata.keywords) if metadata.keywords else []),
            'cached': True
        }
        
        return enriched
    
    async def _fetch_and_cache_metadata(self, item: Dict[str, Any], media_type: str) -> Dict[str, Any]:
        """Fetch TMDB metadata and cache in database. Return item even if TMDB fetch fails."""
        item_ids = item.get('ids', {})
        trakt_id = item_ids.get('trakt')
        
        if not trakt_id:
            # For pseudo-items (TMDB-only), attempt to fetch rich metadata directly from TMDB
            # This enriches the item even without DB caching (since we lack trakt_id for cache key)
            tmdb_id = item_ids.get('tmdb')
            if tmdb_id:
                try:
                    await asyncio.sleep(0.05)  # Rate limit respect
                    item_type = media_type[:-1] if media_type.endswith('s') else media_type
                    from app.services.tmdb_client import fetch_tmdb_metadata
                    tmdb_data = await fetch_tmdb_metadata(tmdb_id, item_type)
                    if tmdb_data:
                        # Enrich pseudo-item with full TMDB metadata
                        logger.debug(f"Enriching pseudo-item {item.get('title')} with direct TMDB fetch")
                        # Update or create tmdb_data block
                        item['tmdb_data'] = {
                            'id': tmdb_data.get('id'),
                            'original_language': tmdb_data.get('original_language'),
                            'genres': [g.get('name') for g in tmdb_data.get('genres', []) if isinstance(g, dict) and g.get('name')],
                            'keywords': [k.get('name') for k in tmdb_data.get('keywords', {}).get('keywords', []) if isinstance(k, dict) and k.get('name')],
                            'popularity': tmdb_data.get('popularity'),
                            'vote_average': tmdb_data.get('vote_average'),
                            'vote_count': tmdb_data.get('vote_count'),
                            'overview': tmdb_data.get('overview'),
                            'poster_path': tmdb_data.get('poster_path'),
                            'backdrop_path': tmdb_data.get('backdrop_path'),
                        }
                        # Propagate to nested structure
                        for key in ('movie', 'show'):
                            if key in item and isinstance(item[key], dict):
                                item[key]['tmdb_data'] = item['tmdb_data']
                                item[key]['genres'] = item['tmdb_data']['genres']
                                item[key]['overview'] = item['tmdb_data']['overview']
                        # Normalize language
                        self._normalize_language(item)
                        return item
                except Exception as e:
                    logger.debug(f"Failed to fetch TMDB metadata for pseudo-item {tmdb_id}: {e}")
            
            # Fallback if TMDB fetch failed: still propagate language from any existing tmdb_data block
            tmdb_block = item.get('tmdb_data') or {}
            lang = tmdb_block.get('original_language') or item.get('language')
            if lang:
                item.setdefault('language', lang)
                for key in ('movie','show'):
                    if key in item and isinstance(item[key], dict):
                        item[key].setdefault('language', lang)
            logger.debug(f"No Trakt ID for item: {item.get('title', 'Unknown')}, returning without full enrichment")
            return item
        
        try:
            # Add delay to respect rate limits
            await asyncio.sleep(0.1)
            
            # Prepare fallback lookup data
            lookup_ids = {
                'tmdb': item_ids.get('tmdb'),
                'imdb': item.get('ids', {}).get('imdb'),
                'title': item.get('title'),
                'year': item.get('year')
            }
            
            item_type = media_type[:-1] if media_type.endswith('s') else media_type
            tmdb_data = await fetch_tmdb_metadata_with_fallback(lookup_ids, item_type)
            
            if tmdb_data:
                # Cache in database
                await self._cache_metadata(item, tmdb_data, item_type)
                # Merge with item
                merged = self._merge_trakt_tmdb(item, tmdb_data)
                # Propagate language
                lang = tmdb_data.get('original_language')
                if lang:
                    merged.setdefault('language', lang)
                    for key in ('movie','show'):
                        if key in merged and isinstance(merged[key], dict):
                            merged[key].setdefault('language', lang)
                return merged
            else:
                logger.debug(f"No TMDB data found via any lookup method for: {item.get('title', 'Unknown')}")
                return item
                
        except Exception as e:
            logger.debug(f"Failed to fetch TMDB data for {item.get('title', 'Unknown')}: {e}, keeping item without enrichment")
            return item
    
    async def _cache_metadata(self, item: Dict[str, Any], tmdb_data: Dict[str, Any], media_type: str):
        """Cache metadata in database."""
        try:
            trakt_id = item.get('ids', {}).get('trakt')
            tmdb_id = item.get('ids', {}).get('tmdb')
            imdb_id = item.get('ids', {}).get('imdb')
            
            # Check if exists
            existing = self.db.query(MediaMetadata).filter_by(trakt_id=trakt_id).first()
            
            metadata_dict = {
                'trakt_id': trakt_id,
                'tmdb_id': tmdb_id,
                'imdb_id': imdb_id,
                'media_type': media_type,
                'title': item.get('title', ''),
                'year': item.get('year'),
                'overview': tmdb_data.get('overview', ''),
                'poster_path': tmdb_data.get('poster_path'),
                'backdrop_path': tmdb_data.get('backdrop_path'),
                'genres': json.dumps([g.get('name') for g in tmdb_data.get('genres', [])]),
                'keywords': json.dumps([k.get('name') for k in tmdb_data.get('keywords', {}).get('keywords', [])]),
                'language': tmdb_data.get('original_language'),
                'rating': tmdb_data.get('vote_average'),
                'votes': tmdb_data.get('vote_count'),
                'popularity': tmdb_data.get('popularity'),
                'last_updated': datetime.datetime.utcnow()
            }
            
            if existing:
                for key, value in metadata_dict.items():
                    if key != 'trakt_id':  # Don't update primary key
                        setattr(existing, key, value)
            else:
                metadata = MediaMetadata(**metadata_dict)
                self.db.add(metadata)
            
            self.db.commit()
            
        except Exception as e:
            logger.warning(f"Failed to cache metadata: {e}")
            self.db.rollback()
    
    def _merge_trakt_tmdb(self, trakt_item: Dict[str, Any], tmdb_data: Dict[str, Any]) -> Dict[str, Any]:
        """Merge Trakt and TMDB data into a unified item."""
        merged = trakt_item.copy()
        
        # Add TMDB data under a separate key to preserve Trakt structure
        merged['tmdb_data'] = {
            'overview': tmdb_data.get('overview', ''),
            'poster_path': tmdb_data.get('poster_path'),
            'backdrop_path': tmdb_data.get('backdrop_path'),
            'popularity': tmdb_data.get('popularity', 0.0),
            'vote_average': tmdb_data.get('vote_average', 0.0),
            'vote_count': tmdb_data.get('vote_count', 0),
            'genres': [g.get('name') for g in tmdb_data.get('genres', [])],
            'keywords': [k.get('name') for k in tmdb_data.get('keywords', {}).get('keywords', [])],
            'runtime': tmdb_data.get('runtime'),
            'budget': tmdb_data.get('budget'),
            'revenue': tmdb_data.get('revenue'),
        }
        
        # Enhance with computed features for scoring
        merged['scoring_features'] = {
            'has_overview': bool(tmdb_data.get('overview', '').strip()),
            'has_poster': bool(tmdb_data.get('poster_path')),
            'tmdb_popularity': tmdb_data.get('popularity', 0.0),
            'tmdb_rating': tmdb_data.get('vote_average', 0.0),
            'tmdb_votes': tmdb_data.get('vote_count', 0),
            'genre_count': len(tmdb_data.get('genres', [])),
            'keyword_count': len(tmdb_data.get('keywords', {}).get('keywords', [])),
            'cached': False
        }
        
        return merged

    async def _fetch_tmdb_first_candidates(
        self, 
        media_type: str, 
        languages: Optional[List[str]] = None,
        genres: Optional[List[str]] = None,
        keywords: Optional[List[str]] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        TMDB-first search strategy with superior language and genre filtering.
        Searches TMDB first for better results, then maps back to Trakt for consistency.
        """
        from app.services.tmdb_client import discover_movies, discover_tv, search_multi, search_movies, search_tv
        logger.info(f"[TMDB-FIRST] Enter for media_type={media_type}, languages={languages}, limit={limit}")

        candidates = []
        seen_tmdb_ids = set()
        
        # Strategy 1: Adaptive TMDB discover with language + on-the-fly genre pre-filtering
        # We keep fetching pages per language until we reach a target threshold (limit * 1.25)
        target = int(limit * 1.25)
        genre_filter_set = set(g.lower() for g in (genres or [])) if genres else None
        if languages:
            for lang in languages[:3]:
                if len(candidates) >= target:
                    break
                try:
                    pages_fetched = 0
                    max_pages = 8  # upper bound per language to avoid runaway API usage
                    while pages_fetched < max_pages and len(candidates) < target:
                        page = pages_fetched + 1
                        if media_type == "movies":
                            data = await discover_movies(original_language=lang, page=page)
                        else:
                            data = await discover_tv(original_language=lang, page=page)

                        pages_fetched += 1
                        results = (data or {}).get("results") or []
                        if not results:
                            # Break early if TMDB stops returning items (end pages)
                            if (data or {}).get("total_pages") and page >= (data or {}).get("total_pages"):
                                break
                            continue

                        for item in results:
                            tmdb_id = item.get("id")
                            if not tmdb_id or tmdb_id in seen_tmdb_ids:
                                continue

                            # Lightweight genre pre-check using genre ids/names if present to reduce mapping cost
                            if genre_filter_set:
                                # Use 'genre_ids' if available; fallback to mapping after conversion
                                genre_ids = item.get('genre_ids')
                                if genre_ids and isinstance(genre_ids, list):
                                    # We can't map ids -> names without full TMDB genre list here; accept for now
                                    pass  # Defer actual genre validation until enrichment
                            
                            # Ensure original_language is present in item before mapping
                            if not item.get('original_language'):
                                item['original_language'] = lang
                            
                            trakt_item = await self._tmdb_to_trakt_format(item, media_type)
                            if not trakt_item:
                                logger.warning(f"CONVERSION FAILED: TMDB {tmdb_id} ({item.get('title') or item.get('name')}) returned None from _tmdb_to_trakt_format")
                                continue

                            # Normalize language (redundant safety; _tmdb_to_trakt_format should handle this)
                            try:
                                self._normalize_language(trakt_item)
                                has_trakt = bool(trakt_item.get('movie', {}).get('ids', {}).get('trakt') or trakt_item.get('show', {}).get('ids', {}).get('trakt'))
                                logger.debug(f"CONVERSION SUCCESS: TMDB {tmdb_id} → Trakt format, has_trakt_id={has_trakt}")
                            except Exception as e:
                                logger.warning(f"NORMALIZATION FAILED for TMDB {tmdb_id}: {e}")
                                continue
                            
                            seen_tmdb_ids.add(tmdb_id)
                            candidates.append(trakt_item)

                        # Short delay to respect rate limits
                        await asyncio.sleep(0.05)
                except Exception as e:
                    logger.debug(f"TMDB discover failed for language {lang}: {e}")
                    continue
        
        # Strategy 2: If we have keywords, use TMDB search (language-aware)
        # We iterate languages (if provided) to retrieve localized results first
        if keywords and len(candidates) < target:
            search_languages = languages[:3] if languages else ["en-US"]
            for lang in search_languages:
                if len(candidates) >= target:
                    break
                for keyword in keywords[:5]:  # Limit keywords for performance
                    if len(candidates) >= target:
                        break
                    try:
                        if media_type == "movies":
                            data = await search_movies(keyword, page=1, language=lang)
                        else:
                            data = await search_tv(keyword, page=1, language=lang)

                        if data and data.get("results"):
                            for item in data["results"]:
                                tmdb_id = item.get("id")
                                if tmdb_id and tmdb_id not in seen_tmdb_ids:
                                    seen_tmdb_ids.add(tmdb_id)
                                    # Ensure original_language present
                                    if not item.get('original_language'):
                                        item['original_language'] = lang
                                    trakt_item = await self._tmdb_to_trakt_format(item, media_type)
                                    if trakt_item:
                                        self._normalize_language(trakt_item)
                                        candidates.append(trakt_item)
                        
                    except Exception as e:
                        logger.debug(f"TMDB search failed for keyword '{keyword}' (lang {lang}): {e}")
                        continue
        
        # Strategy 3: Multi-search if we still need more results (also language-aware where possible)
        # TMDB multi endpoint supports a 'language' parameter; we iterate languages for better localization
        if keywords and len(candidates) < target:
            multi_languages = languages[:2] if languages else ["en-US"]
            for lang in multi_languages:
                if len(candidates) >= target:
                    break
                for keyword in keywords[:3]:
                    if len(candidates) >= target:
                        break
                    try:
                        data = await search_multi(keyword, page=1, language=lang)
                        if data and data.get("results"):
                            for item in data["results"]:
                                # Filter for movies/TV only
                                if item.get("media_type") not in ["movie", "tv"]:
                                    continue

                                tmdb_id = item.get("id")
                                if tmdb_id and tmdb_id not in seen_tmdb_ids:
                                    seen_tmdb_ids.add(tmdb_id)
                                    # Convert TV to our standard format
                                    item_media_type = "movies" if item.get("media_type") == "movie" else "shows"
                                    # Ensure original_language present
                                    if not item.get('original_language'):
                                        item['original_language'] = lang
                                    trakt_item = await self._tmdb_to_trakt_format(item, item_media_type)
                                    if trakt_item:
                                        self._normalize_language(trakt_item)
                                        candidates.append(trakt_item)
                    except Exception as e:
                        logger.debug(f"TMDB multi-search failed for keyword '{keyword}' (lang {lang}): {e}")
                        continue

        # Strategy 4: If we still have a language constraint and are under target, expand keyword surface
        # by generating language-specific composite queries (e.g., append language code or common regional terms)
        if languages and keywords and len(candidates) < target:
            expanded_keywords: List[str] = []
            base_kw_sample = keywords[:8]
            for kw in base_kw_sample:
                kw_lower = kw.lower()
                for lang in languages[:3]:
                    # Composite patterns that often yield localized titles on TMDB
                    expanded_keywords.extend([
                        f"{kw_lower} {lang}",
                        f"{lang} {kw_lower}",
                        f"{kw_lower} film" if media_type == "movies" else f"{kw_lower} tv",  # localized 'film / tv'
                        f"{kw_lower} movie",
                        f"{kw_lower} series" if media_type != "movies" else f"{kw_lower} cinema",
                    ])
            # Deduplicate expanded list while preserving order
            seen_exp = set()
            expanded_keywords = [k for k in expanded_keywords if not (k in seen_exp or seen_exp.add(k))]

            if expanded_keywords and len(candidates) < target:
                logger.info(f"Language-aware expansion generated {len(expanded_keywords)} extra keywords (remaining need {limit - len(candidates)})")
                for lang in languages[:2]:
                    if len(candidates) >= target:
                        break
                    for ek in expanded_keywords:
                        if len(candidates) >= target:
                            break
                        try:
                            if media_type == "movies":
                                data = await search_movies(ek, page=1, language=lang)
                            else:
                                data = await search_tv(ek, page=1, language=lang)
                            if data and data.get("results"):
                                for item in data["results"]:
                                    tmdb_id = item.get("id")
                                    if tmdb_id and tmdb_id not in seen_tmdb_ids:
                                        seen_tmdb_ids.add(tmdb_id)
                                        # Ensure original_language present
                                        if not item.get('original_language'):
                                            item['original_language'] = lang
                                        trakt_item = await self._tmdb_to_trakt_format(item, media_type)
                                        if trakt_item:
                                            self._normalize_language(trakt_item)
                                            candidates.append(trakt_item)
                        except Exception as e:
                            logger.debug(f"Expanded TMDB search failed for '{ek}' (lang {lang}): {e}")
                            continue

        # NOTE: Deliberately NOT relaxing language constraint with a language-less fallback
        # per user directive. Instead, we rely solely on expanded, language-aware strategies above.

        logger.info(f"TMDB-first search gathered {len(candidates)} raw candidates (target={target}, final limit={limit})")
        final_candidates = candidates[:limit]
        logger.warning(f"_fetch_tmdb_first_candidates RETURNING {len(final_candidates)} candidates for {media_type}")
        if final_candidates:
            sample = final_candidates[0]
            logger.warning(f"Sample candidate structure: keys={list(sample.keys())}, has_movie={bool(sample.get('movie'))}, has_show={bool(sample.get('show'))}")
        return final_candidates

    async def _tmdb_to_trakt_format(self, tmdb_item: Dict[str, Any], media_type: str) -> Optional[Dict[str, Any]]:
        """
        Convert TMDB item to Trakt-compatible format and map back to Trakt for IDs.
        This ensures we have Trakt IDs for database consistency.
        """
        try:
            tmdb_id = tmdb_item.get("id")
            title = tmdb_item.get("title") or tmdb_item.get("name", "Unknown")
            logger.warning(f"_tmdb_to_trakt_format START: {media_type} ID={tmdb_id} title={title} has_genre_ids={bool(tmdb_item.get('genre_ids'))} has_genres={bool(tmdb_item.get('genres'))}")
            
            if not tmdb_id:
                logger.warning(f"_tmdb_to_trakt_format ABORT: No tmdb_id for {title}")
                return None
            
            # Try to find the corresponding Trakt item
            search_type = "movie" if media_type == "movies" else "show"
            trakt_results = []
            try:
                trakt_results = await self.trakt_client.search_by_tmdb_id(tmdb_id, search_type)
            except Exception as e:
                logger.debug(f"Trakt search failed for TMDB {tmdb_id}: {e}, will create TMDB-only item")
            
            if trakt_results:
                # Use the Trakt result as base (has proper IDs)
                trakt_item = trakt_results[0]
                
                # Enhance with TMDB data we already have
                if search_type == "movie" and "movie" in trakt_item:
                    trakt_item["movie"]["tmdb_popularity"] = tmdb_item.get("popularity", 0)
                    trakt_item["movie"]["tmdb_vote_average"] = tmdb_item.get("vote_average", 0)
                    trakt_item["movie"].setdefault('language', tmdb_item.get('original_language'))
                elif search_type == "show" and "show" in trakt_item:
                    trakt_item["show"]["tmdb_popularity"] = tmdb_item.get("popularity", 0)
                    trakt_item["show"]["tmdb_vote_average"] = tmdb_item.get("vote_average", 0)
                    trakt_item["show"].setdefault('language', tmdb_item.get('original_language'))
                # Normalize language at root as well
                self._normalize_language(trakt_item)
                
                return trakt_item
            else:
                # If no Trakt mapping found, create a minimal Trakt-like structure
                # This allows us to use TMDB-only content if needed
                logger.debug(f"No Trakt mapping for TMDB {tmdb_id}, creating TMDB-only item")
                title = tmdb_item.get("title") or tmdb_item.get("name", "Unknown")
                year = None
                try:
                    if tmdb_item.get("release_date"):
                        year = int(tmdb_item["release_date"][:4])
                    elif tmdb_item.get("first_air_date"):
                        year = int(tmdb_item["first_air_date"][:4])
                except (ValueError, TypeError, IndexError):
                    pass  # Year parsing failed, leave as None
                
                # Handle genres: TMDB discover returns genre_ids, not genre objects
                genres = []
                try:
                    if tmdb_item.get('genres'):
                        # Full TMDB API response has genre objects
                        genres = [g.get('name') for g in tmdb_item.get('genres', []) if isinstance(g, dict) and g.get('name')]
                    elif tmdb_item.get('genre_ids'):
                        # TMDB discover response has genre_ids - map them to names
                        # We'll use a simplified mapping for common genre IDs
                        genre_id_map = {
                            # Movies
                            28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy", 80: "Crime",
                            99: "Documentary", 18: "Drama", 10751: "Family", 14: "Fantasy", 36: "History",
                            27: "Horror", 10402: "Music", 9648: "Mystery", 10749: "Romance", 878: "Science Fiction",
                            10770: "TV Movie", 53: "Thriller", 10752: "War", 37: "Western",
                            # TV
                            10759: "Action & Adventure", 10762: "Kids", 10763: "News", 10764: "Reality",
                            10765: "Sci-Fi & Fantasy", 10766: "Soap", 10767: "Talk", 10768: "War & Politics"
                        }
                        genres = [genre_id_map.get(gid, f"Genre_{gid}") for gid in tmdb_item.get('genre_ids', [])]
                except Exception as e:
                    logger.debug(f"Genre processing failed for TMDB {tmdb_id}: {e}")
                    genres = []
                
                base_item = {
                    "title": title,
                    "year": year,
                    "ids": {
                        "tmdb": tmdb_id,
                        "trakt": None  # Will be None for TMDB-only items
                    },
                    "tmdb_popularity": tmdb_item.get("popularity", 0),
                    "tmdb_vote_average": tmdb_item.get("vote_average", 0),
                    "overview": tmdb_item.get("overview", ""),
                    "language": tmdb_item.get("original_language")
                }
                
                if search_type == "movie":
                    wrapper = {"movie": base_item}
                else:
                    wrapper = {"show": base_item}
                # Add tmdb_data subset for downstream filters
                wrapper_key = 'movie' if search_type == 'movie' else 'show'
                wrapper[wrapper_key]['tmdb_data'] = {
                    'id': tmdb_id,
                    'original_language': tmdb_item.get('original_language'),
                    'genres': genres,  # Use the mapped genres
                    'popularity': tmdb_item.get('popularity'),
                    'vote_average': tmdb_item.get('vote_average'),
                    'vote_count': tmdb_item.get('vote_count')
                }
                self._normalize_language(wrapper)
                logger.debug(f"Created TMDB-only item: {title} ({year}), genres={genres}, lang={tmdb_item.get('original_language')}")
                return wrapper
                    
        except Exception as e:
            logger.warning(f"Failed to convert TMDB item {tmdb_item.get('id', 'unknown')}: {e}")
            return None

    async def _enrich_with_aggressive_metadata(
        self, 
        items: List[Dict[str, Any]], 
        media_type: str,
        required_genres: Optional[List[str]] = None,
        required_languages: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Aggressively enrich items with metadata, especially those marked as needing enhancement."""
        if not items:
            return items
        
        logger.info(f"Aggressive metadata enrichment for {len(items)} items")
        enriched = []
        
        # Separate items that definitely need enhancement vs those that might benefit
        needs_enhancement = [item for item in items if item.get('_needs_metadata_enhancement')]
        regular_items = [item for item in items if not item.get('_needs_metadata_enhancement')]
        
        logger.info(f"Priority enhancement for {len(needs_enhancement)} items, regular enrichment for {len(regular_items)}")
        
        # First, do regular enrichment for items that already have some metadata
        if regular_items:
            regular_enriched = await self._enrich_with_metadata(regular_items, media_type)
            enriched.extend(regular_enriched)
        
        # Then, aggressively enhance items that need metadata
        if needs_enhancement:
            enhanced_items = await self._aggressive_individual_enhancement(
                needs_enhancement, media_type, required_genres, required_languages
            )
            enriched.extend(enhanced_items)
        
        return enriched
    
    async def _aggressive_individual_enhancement(
        self,
        items: List[Dict[str, Any]],
        media_type: str,
        required_genres: Optional[List[str]] = None,
        required_languages: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Individually enhance items that lack critical metadata by searching Trakt/TMDB directly."""
        enhanced = []
        
        for item in items:
            try:
                trakt_id = item.get('ids', {}).get('trakt')
                tmdb_id = item.get('ids', {}).get('tmdb')
                title = item.get('title', '')
                
                enhanced_item = item.copy()
                found_metadata = False
                
                # Strategy 1: Enhanced multi-ID TMDB lookup
                if tmdb_id or item.get('ids', {}).get('imdb'):
                    try:
                        await asyncio.sleep(0.1)  # Rate limit
                        item_type = media_type[:-1] if media_type.endswith('s') else media_type
                        
                        # Prepare fallback lookup data
                        lookup_ids = {
                            'tmdb': tmdb_id,
                            'imdb': item.get('ids', {}).get('imdb'),
                            'title': title,
                            'year': item.get('year')
                        }
                        
                        tmdb_data = await fetch_tmdb_metadata_with_fallback(lookup_ids, item_type)
                        if tmdb_data:
                            enhanced_item = self._merge_trakt_tmdb(enhanced_item, tmdb_data)
                            found_metadata = True
                            logger.debug(f"Found TMDB metadata for {title}")
                    except Exception as e:
                        logger.debug(f"Direct TMDB lookup failed for {title}: {e}")
                
                # Strategy 2: Search Trakt for individual item details if we have Trakt ID
                if not found_metadata and trakt_id:
                    try:
                        search_type = "movie" if media_type == "movies" else "show"
                        # Use a method that gets full details instead of search results
                        detailed_item = await self._get_trakt_item_details(trakt_id, search_type)
                        if detailed_item and detailed_item.get('genres'):
                            enhanced_item['genres'] = detailed_item['genres']
                            if detailed_item.get('language'):
                                enhanced_item['language'] = detailed_item['language']
                            found_metadata = True
                            logger.debug(f"Found detailed Trakt metadata for {title}")
                    except Exception as e:
                        logger.debug(f"Trakt details lookup failed for {title}: {e}")
                
                # Strategy 3: Search by title if we still don't have metadata
                if not found_metadata and title:
                    try:
                        search_type = "movie" if media_type == "movies" else "show"
                        search_results = await self.trakt_client.search(title, search_type, limit=5)
                        
                        # Find the best match
                        for result in search_results or []:
                            result_item = result.get(search_type) or result
                            if result_item and result_item.get('title', '').lower() == title.lower():
                                if result_item.get('genres'):
                                    enhanced_item['genres'] = result_item['genres']
                                if result_item.get('language'):
                                    enhanced_item['language'] = result_item['language']
                                # Update IDs if we found better ones
                                if result_item.get('ids'):
                                    enhanced_item['ids'].update(result_item['ids'])
                                found_metadata = True
                                logger.debug(f"Found metadata via title search for {title}")
                                break
                    except Exception as e:
                        logger.debug(f"Title search failed for {title}: {e}")
                
                # Clean up enhancement marker
                enhanced_item.pop('_needs_metadata_enhancement', None)
                enhanced.append(enhanced_item)
                
                if found_metadata:
                    # Small delay between successful enrichments
                    await asyncio.sleep(0.05)
                    
            except Exception as e:
                logger.warning(f"Individual enhancement failed for item: {e}")
                # Still include the item even if enhancement failed
                item_copy = item.copy()
                item_copy.pop('_needs_metadata_enhancement', None)
                enhanced.append(item_copy)
        
        logger.info(f"Aggressive enhancement completed for {len(enhanced)} items")
        return enhanced

    async def _save_discovered_candidates_to_db(self, candidates: List[Dict[str, Any]], media_type: str) -> int:
        """Save TMDB-discovered candidates that don't exist in persistent DB yet.
        
        This ensures we build up the persistent candidate pool over time with newly
        discovered content from TMDB searches. Returns number of candidates saved.
        """
        saved_count = 0
        try:
            from app.models import PersistentCandidate
            import datetime as dt
            import json
            
            for candidate in candidates:
                # Skip if it came from persistent store (already in DB)
                if candidate.get('_from_persistent_store'):
                    continue
                
                # Get TMDB ID
                tmdb_id = candidate.get('ids', {}).get('tmdb')
                if not tmdb_id:
                    continue
                
                # Check if already exists
                existing = self.db.query(PersistentCandidate).filter_by(tmdb_id=tmdb_id).first()
                if existing:
                    continue
                
                # Extract metadata from candidate
                title = candidate.get('title')
                if not title:
                    continue
                
                year = candidate.get('year')
                language = candidate.get('language', '').lower()
                trakt_id = candidate.get('ids', {}).get('trakt')
                
                # Extract TMDB data
                tmdb_data = candidate.get('tmdb_data') or {}
                genres = tmdb_data.get('genres', [])
                if genres and isinstance(genres, list):
                    genres_json = json.dumps(genres)
                else:
                    genres_json = None
                
                # Determine release date and year
                release_date = None
                if media_type == 'movies' and tmdb_data.get('release_date'):
                    release_date = tmdb_data['release_date']
                elif media_type in ('shows', 'show') and tmdb_data.get('first_air_date'):
                    release_date = tmdb_data['first_air_date']
                
                if not year and release_date:
                    try:
                        year = int(release_date[:4])
                    except:
                        pass
                
                # Create new persistent candidate
                pc = PersistentCandidate(
                    tmdb_id=tmdb_id,
                    trakt_id=trakt_id,
                    media_type='movie' if media_type == 'movies' else 'show',
                    title=title,
                    original_title=tmdb_data.get('original_title') or tmdb_data.get('original_name'),
                    year=year,
                    release_date=release_date,
                    language=language,
                    popularity=tmdb_data.get('popularity', 0.0),
                    vote_average=tmdb_data.get('vote_average', 0.0),
                    vote_count=tmdb_data.get('vote_count', 0),
                    overview=tmdb_data.get('overview') or candidate.get('overview'),
                    poster_path=tmdb_data.get('poster_path'),
                    backdrop_path=tmdb_data.get('backdrop_path'),
                    genres=genres_json,
                    manual=False,
                    active=True
                )
                
                # Compute scores
                pc.compute_scores()
                
                # Add to session
                self.db.add(pc)
                saved_count += 1
                
                # Commit in batches of 50 to avoid memory issues
                if saved_count % 50 == 0:
                    self.db.commit()
                    logger.info(f"Saved batch of 50 discovered candidates (total: {saved_count})")
            
            # Final commit for remaining items
            if saved_count > 0:
                self.db.commit()
                logger.info(f"Saved {saved_count} newly discovered candidates to persistent DB")
            
        except Exception as e:
            logger.warning(f"Failed to save discovered candidates to DB: {e}")
            self.db.rollback()
        
        return saved_count

    async def _map_trakt_ids_for_saved_candidates(self, limit: int = 50) -> int:
        """Background task: Map Trakt IDs for persistent candidates that don't have them yet.
        
        This runs asynchronously after discovery to avoid blocking sync operations.
        Returns number of candidates updated with Trakt IDs.
        """
        updated_count = 0
        try:
            from app.models import PersistentCandidate
            
            # Find candidates without Trakt IDs (limit to avoid overwhelming Trakt API)
            candidates_without_trakt = self.db.query(PersistentCandidate).filter(
                PersistentCandidate.active == True,
                PersistentCandidate.trakt_id == None,
                PersistentCandidate.tmdb_id != None
            ).limit(limit).all()
            
            if not candidates_without_trakt:
                return 0
            
            logger.info(f"Mapping Trakt IDs for {len(candidates_without_trakt)} candidates...")
            
            for candidate in candidates_without_trakt:
                try:
                    # Search Trakt by TMDB ID
                    search_type = "movie" if candidate.media_type == "movie" else "show"
                    trakt_results = await self.trakt_client.search_by_tmdb_id(candidate.tmdb_id, search_type)
                    
                    if trakt_results and len(trakt_results) > 0:
                        trakt_item = trakt_results[0]
                        # Extract Trakt ID from result
                        if search_type == "movie" and "movie" in trakt_item:
                            trakt_id = trakt_item["movie"].get("ids", {}).get("trakt")
                        elif search_type == "show" and "show" in trakt_item:
                            trakt_id = trakt_item["show"].get("ids", {}).get("trakt")
                        else:
                            trakt_id = trakt_item.get("ids", {}).get("trakt")
                        
                        if trakt_id:
                            candidate.trakt_id = trakt_id
                            updated_count += 1
                            
                            # Commit in small batches
                            if updated_count % 10 == 0:
                                self.db.commit()
                                logger.debug(f"Mapped {updated_count} Trakt IDs so far...")
                    
                    # Rate limiting
                    await asyncio.sleep(0.1)
                    
                except Exception as e:
                    logger.debug(f"Failed to map Trakt ID for TMDB {candidate.tmdb_id}: {e}")
                    continue
            
            # Final commit
            if updated_count > 0:
                self.db.commit()
                logger.info(f"Successfully mapped {updated_count} Trakt IDs")
            
        except Exception as e:
            logger.warning(f"Trakt ID mapping failed: {e}")
            self.db.rollback()
        
        return updated_count
    
    async def _get_trakt_item_details(self, trakt_id: int, item_type: str) -> Optional[Dict[str, Any]]:
        """Get detailed item information from Trakt API."""
        try:
            # Trakt API endpoints for detailed item info
            endpoint = f"{item_type}s/{trakt_id}"
            response = await self.trakt_client._make_request("GET", endpoint)
            return response
        except Exception as e:
            logger.debug(f"Failed to get Trakt details for {item_type} {trakt_id}: {e}")
            return None
    
    async def _enhance_with_trakt_metadata(
        self,
        items: List[Dict[str, Any]],
        media_type: str,
        required_genres: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Enhance items using only Trakt metadata when TMDB is not available."""
        enhanced = []
        
        for item in items:
            enhanced_item = item.copy()
            
            # If item needs metadata and we have a Trakt ID, try to get details
            if item.get('_needs_metadata_enhancement') and item.get('ids', {}).get('trakt'):
                try:
                    trakt_id = item['ids']['trakt']
                    search_type = "movie" if media_type == "movies" else "show"
                    detailed_item = await self._get_trakt_item_details(trakt_id, search_type)
                    
                    if detailed_item:
                        # Merge in additional metadata
                        if detailed_item.get('genres') and not enhanced_item.get('genres'):
                            enhanced_item['genres'] = detailed_item['genres']
                        if detailed_item.get('language') and not enhanced_item.get('language'):
                            enhanced_item['language'] = detailed_item['language']
                        if detailed_item.get('overview') and not enhanced_item.get('overview'):
                            enhanced_item['overview'] = detailed_item['overview']
                        
                        logger.debug(f"Enhanced {enhanced_item.get('title', 'Unknown')} with Trakt details")
                except Exception as e:
                    logger.debug(f"Trakt enhancement failed: {e}")
            
            # Clean up enhancement marker
            enhanced_item.pop('_needs_metadata_enhancement', None)
            
            # Ensure tmdb_data field for compatibility
            if 'tmdb_data' not in enhanced_item:
                enhanced_item['tmdb_data'] = None
                
            enhanced.append(enhanced_item)
        
        return enhanced