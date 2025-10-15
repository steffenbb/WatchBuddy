"""
DEPRECATED MODULE
-----------------
This legacy candidate provider is superseded by `bulk_candidate_provider.py` and is
no longer used in production paths. It remains temporarily for reference.
Please use `app.services.bulk_candidate_provider.BulkCandidateProvider` instead.
"""
import warnings as _warnings
_warnings.warn(
    "app.services.candidate_provider is deprecated. Use BulkCandidateProvider instead.",
    DeprecationWarning,
    stacklevel=2,
)
from typing import List, Dict, Any, Set
import asyncio
from app.core.database import SessionLocal
from app.models import UserList
from app.services.trakt_client import TraktClient
from app.services.tmdb_client import fetch_tmdb_metadata, merge_tmdb_trakt

async def _get_user_existing(db, user_id: int) -> Set[int]:
    existing: Set[int] = set()
    # For now, we store only list definitions; if you later persist items, join them here
    # Placeholder returns empty set
    return existing

async def _get_user_watched(trakt: TraktClient, media_type: str = "movies", limit: int = 1000) -> Set[int]:
    watched = await trakt.get_my_history(media_type=media_type, limit=limit)
    ids: Set[int] = set()
    for it in watched:
        entry = it.get(media_type[:-1]) if media_type.endswith('s') else it.get(media_type)
        if entry and entry.get('ids', {}).get('trakt'):
            ids.add(entry['ids']['trakt'])
    return ids

async def fetch_candidates(user_id: int, list_filters: Dict[str, Any] | None = None, media_type: str = "movies", limit: int = 200, enrich_tmdb: bool = True) -> List[Dict[str, Any]]:
    trakt = TraktClient(user_id=user_id)
    db = SessionLocal()
    try:
        # Gather sources in parallel
        trending_coro = trakt.get_trending(media_type=media_type, limit=limit//2)
        popular_coro = trakt.get_popular(media_type=media_type, limit=limit//2)
        recs_coro = trakt.get_recommendations(media_type=media_type, limit=min(100, limit))
        watched_coro = _get_user_watched(trakt, media_type=media_type, limit=1000)

        trending, popular, recs, watched_ids = await asyncio.gather(trending_coro, popular_coro, recs_coro, watched_coro)

        # Flatten items from different endpoints
        def normalize_list(items):
            # Some endpoints wrap in {movie: {...}} structure
            norm = []
            for it in items or []:
                if isinstance(it, dict) and 'movie' in it:
                    norm.append(it['movie'])
                elif isinstance(it, dict) and 'show' in it:
                    norm.append(it['show'])
                else:
                    norm.append(it)
            return norm

        pool = normalize_list(trending) + normalize_list(popular) + normalize_list(recs)

        # Deduplicate by trakt id
        by_trakt: Dict[int, Dict[str, Any]] = {}
        for it in pool:
            tid = it.get('ids', {}).get('trakt')
            if not tid:
                continue
            by_trakt[tid] = it

        # Exclude watched and (future) existing list items
        existing_ids = await _get_user_existing(db, user_id)
        candidates = [it for tid, it in by_trakt.items() if tid not in watched_ids and tid not in existing_ids]

        # Enrich with TMDB metadata only if configured
        if enrich_tmdb:
            from app.services.tmdb_client import get_tmdb_api_key
            tmdb_api_key = None
            try:
                tmdb_api_key = await get_tmdb_api_key()
            except Exception as e:
                pass
            if tmdb_api_key:
                async def enrich_one(it):
                    tmdb_id = it.get('ids', {}).get('tmdb')
                    mtype = 'movie' if it.get('title') else 'show'
                    data = None
                    if tmdb_id:
                        data = await fetch_tmdb_metadata(tmdb_id, media_type=mtype)
                    return merge_tmdb_trakt(it, data) if data else it

                # Limit concurrency to be gentle
                sem = asyncio.Semaphore(10)
                async def guarded(it):
                    async with sem:
                        return await enrich_one(it)
                candidates = await asyncio.gather(*(guarded(it) for it in candidates[:limit]))
            else:
                # TMDB not configured, fallback: ensure all items have tmdb field
                for it in candidates:
                    if 'tmdb' not in it:
                        it['tmdb'] = None
        else:
            # If enrichment not requested, ensure fallback fields
            for it in candidates:
                if 'tmdb' not in it:
                    it['tmdb'] = None

        return candidates[:limit]
    finally:
        db.close()