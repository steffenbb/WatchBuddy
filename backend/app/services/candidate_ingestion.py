"""Candidate ingestion & refresh services.

Responsible for:
 1. Incrementally ingesting new TMDB (and optionally Trakt-mapped) items >= last checkpoint / since 2024.
 2. Refreshing vote_count and vote_average for recently released content (<= 90 days) to keep scores current.

Design notes:
 - We favor TMDB for breadth & metadata; Trakt mapping can be deferred (lazy) to reduce API pressure.
 - Use CandidateIngestionState for per media_type release_date checkpoints (YYYY-MM-DD).
 - Rate limiting: lightweight async sleeps between page fetches; stop early on sparse results.
 - Compute derived scores using PersistentCandidate.compute_scores().
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
from typing import List, Optional

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.redis_client import get_redis_sync
from app.models import PersistentCandidate, CandidateIngestionState
from app.utils.logger import logger

from app.services.tmdb_client import discover_movies, discover_tv, fetch_tmdb_metadata, search_multi
from app.services.trakt_client import TraktClient

MIN_YEAR = 2024
RECENT_DAYS_REFRESH = 90

def _update_worker_status(media_type: str, status: str, error: Optional[str] = None, items_processed: int = 0):
    """Update worker status in Redis."""
    try:
        redis = get_redis_sync()
        # Normalize media_type to singular form for consistency with API
        normalized_type = "movie" if "movie" in media_type else "show"
        key = f"worker_status:{normalized_type}"
        data = {
            "status": status,  # "running", "completed", "error"
            "last_run": dt.datetime.utcnow().isoformat(),
            "items_processed": items_processed,
            "error": error
        }
        redis.set(key, json.dumps(data), ex=86400)  # Expire after 24 hours
    except Exception as e:
        logger.warning(f"Failed to update worker status: {e}")

async def ingest_new_content(media_type: str = 'movies', pages: int = 5, per_page: int = 20) -> int:
    """Ingest new TMDB content newer than last checkpoint (or MIN_YEAR baseline).

    Returns number of upserted rows.
    """
    assert media_type in ('movies','shows')
    _update_worker_status(media_type, "running")
    
    db: Session = SessionLocal()
    inserted = 0
    
    # Send start notification to all users
    from app.api.notifications import send_notification
    try:
        await send_notification(1, f"Finding new {media_type}...", "info")
    except Exception:
        pass
    
    try:
        state = db.query(CandidateIngestionState).filter_by(media_type=media_type).one_or_none()
        last_date = None
        if state and state.last_release_date:
            last_date = state.last_release_date
        cutoff_year = MIN_YEAR
        new_last_date = last_date
        fetch_fn = discover_movies if media_type == 'movies' else discover_tv
        for page in range(1, pages + 1):
            try:
                data = await fetch_fn(page=page)
            except Exception as e:
                logger.debug(f"Ingest {media_type} page {page} failed: {e}")
                await asyncio.sleep(0.5)
                continue
            results = (data or {}).get('results') or []
            if not results:
                break
            batch_objs: List[PersistentCandidate] = []
            for item in results:
                release_date = item.get('release_date') or item.get('first_air_date')
                if not release_date:
                    continue
                year = None
                try:
                    year = int(release_date[:4])
                except Exception:
                    continue
                if year < cutoff_year:
                    # Skip old content (bootstrap CSV handled historical corpus)
                    continue
                if last_date and release_date <= last_date:
                    # Already ingested previously
                    continue
                tmdb_id = item.get('id')
                if not tmdb_id:
                    continue
                title = item.get('title') or item.get('name')
                if not title:
                    continue
                
                # Build or update existing record
                existing: Optional[PersistentCandidate] = db.query(PersistentCandidate).filter_by(tmdb_id=tmdb_id).one_or_none()
                if existing:
                    # Light update of fast fields only if missing popularity/votes
                    updated = False
                    for field, src_key in [('popularity','popularity'), ('vote_average','vote_average'), ('vote_count','vote_count')]:
                        val = item.get(src_key)
                        if val is not None and getattr(existing, field) != val:
                            setattr(existing, field, val)
                            updated = True
                    if updated:
                        existing.last_refreshed = dt.datetime.utcnow()
                        existing.compute_scores()
                else:
                    # Fetch comprehensive metadata for new items
                    tmdb_metadata = None
                    enriched_fields = {}
                    try:
                        from app.services.tmdb_client import fetch_tmdb_metadata, extract_enriched_fields
                        tmdb_metadata = await fetch_tmdb_metadata(tmdb_id, 'movie' if media_type=='movies' else 'tv')
                        if tmdb_metadata:
                            enriched_fields = extract_enriched_fields(tmdb_metadata, 'movie' if media_type=='movies' else 'show')
                    except Exception as e:
                        logger.debug(f"Failed to fetch enriched metadata for {tmdb_id}: {e}")
                    
                    pc = PersistentCandidate(
                        tmdb_id=tmdb_id,
                        trakt_id=None,
                        media_type='movie' if media_type=='movies' else 'show',
                        title=title,
                        original_title=item.get('original_title') or item.get('original_name'),
                        year=year,
                        release_date=release_date,
                        language=(item.get('original_language') or '').lower(),
                        popularity=item.get('popularity') or 0.0,
                        vote_average=item.get('vote_average') or 0.0,
                        vote_count=item.get('vote_count') or 0,
                        overview=item.get('overview'),
                        poster_path=item.get('poster_path'),
                        backdrop_path=item.get('backdrop_path'),
                        manual=False,
                        # Add enriched fields if available
                        **enriched_fields
                    )
                    pc.compute_scores()
                    batch_objs.append(pc)
                    if not new_last_date or release_date > new_last_date:
                        new_last_date = release_date
                    
                    # Add small delay after fetching detailed metadata to respect rate limits
                    if tmdb_metadata:
                        await asyncio.sleep(0.05)
            if batch_objs:
                db.bulk_save_objects(batch_objs)
                db.commit()
                inserted += len(batch_objs)
            # Rate limit pacing
            await asyncio.sleep(0.25)
        # Update checkpoint
        if not state:
            state = CandidateIngestionState(media_type=media_type, last_release_date=new_last_date)
            db.add(state)
        else:
            if new_last_date and (not state.last_release_date or new_last_date > state.last_release_date):
                state.last_release_date = new_last_date
        state.last_run_at = dt.datetime.utcnow()
        db.commit()
        logger.info(f"Ingested {inserted} new {media_type} candidates (checkpoint={state.last_release_date})")
        
        # Update worker status to completed
        _update_worker_status(media_type, "completed", items_processed=inserted)
        
        # Send completion notification
        from app.api.notifications import send_notification
        if inserted > 0:
            await send_notification(1, f"Added {inserted} new {media_type} to library", "success")
        else:
            await send_notification(1, f"No new {media_type} found", "info")
        
        return inserted
    except Exception as e:
        logger.warning(f"ingest_new_content failed for {media_type}: {e}")
        db.rollback()
        
        # Update worker status to error
        _update_worker_status(media_type, "error", error=str(e), items_processed=inserted)
        
        # Send error notification
        from app.api.notifications import send_notification
        try:
            await send_notification(1, f"Failed to fetch new {media_type}", "error")
        except Exception:
            pass
        
        return inserted
    finally:
        db.close()

async def refresh_recent_votes(media_type: str = 'movies', days: int = RECENT_DAYS_REFRESH, batch_limit: int = 400) -> int:
    """Refresh vote_count & vote_average for recently released items (<= days).
    Uses TMDB details endpoint for higher accuracy. Returns number of rows updated.
    """
    assert media_type in ('movies','shows')
    db: Session = SessionLocal()
    updated = 0
    
    # Send start notification
    from app.api.notifications import send_notification
    try:
        await send_notification(1, f"Refreshing ratings for recent {media_type}...", "info")
    except Exception:
        pass
    
    try:
        cutoff = dt.datetime.utcnow() - dt.timedelta(days=days)
        # Filter by release_date year/ freshness_score or inserted_at recency
        q = db.query(PersistentCandidate).filter(
            PersistentCandidate.media_type == ('movie' if media_type=='movies' else 'show'),
            PersistentCandidate.inserted_at >= cutoff - dt.timedelta(days=30)  # slightly broader window
        ).order_by(PersistentCandidate.inserted_at.desc()).limit(batch_limit)
        rows: List[PersistentCandidate] = q.all()
        for row in rows:
            try:
                details = await fetch_tmdb_metadata(row.tmdb_id, 'movie' if row.media_type=='movie' else 'tv')
                if not details:
                    continue
                changed = False
                for f in ('popularity','vote_average','vote_count'):   
                    val = details.get(f)
                    if val is not None and getattr(row, f) != val:
                        setattr(row, f, val)
                        changed = True
                if changed:
                    row.last_refreshed = dt.datetime.utcnow()
                    row.compute_scores()
                    updated += 1
                await asyncio.sleep(0.05)
            except Exception:
                continue
        if updated:
            db.commit()
        logger.info(f"Refreshed votes for {updated} recent {media_type} candidates")
        
        # Send completion notification
        from app.api.notifications import send_notification
        if updated > 0:
            await send_notification(1, f"Updated ratings for {updated} {media_type}", "success")
        
        return updated
    except Exception as e:
        logger.warning(f"refresh_recent_votes failed: {e}")
        db.rollback()
        return updated
    finally:
        db.close()


async def ingest_via_search_multi(media_type: str = 'movies', duration_minutes: int = 12, language: str = 'en-US') -> int:
    """Time-boxed ingestion using TMDB search/multi.

    Strategy:
    - Iterate across alphabet letters and pages using a Redis cursor (per media_type)
    - For each result (movie/tv): if not in DB by tmdb_id, attempt to resolve Trakt ID via TraktClient.search_by_tmdb_id
    - Insert minimal PersistentCandidate rows and compute scores
    - Time-boxed to avoid blocking sync pipeline (default 10 minutes)
    """

    # Enforce 10 minute max duration
    if duration_minutes is None or duration_minutes > 10:
        duration_minutes = 10
    assert media_type in ('movies', 'shows')
    _update_worker_status(media_type, "running")

    db: Session = SessionLocal()
    inserted = 0
    start_time = dt.datetime.utcnow()
    deadline = start_time + dt.timedelta(minutes=duration_minutes)

    # Redis cursor state
    r = get_redis_sync()
    cursor_key = f"ingest_cursor:{media_type}"
    try:
        cursor_raw = r.get(cursor_key)
        cursor = json.loads(cursor_raw) if cursor_raw else {"letter_index": 0, "page": 1}
    except Exception:
        cursor = {"letter_index": 0, "page": 1}

    letters = [chr(c) for c in range(ord('a'), ord('z') + 1)]
    # Helper to advance cursor
    def advance_cursor(cur: dict):
        prev_page = cur["page"]
        prev_letter = cur["letter_index"]
        cur["page"] += 1
        if cur["page"] > 500:  # increased from 50 for better coverage
            cur["page"] = 1
            cur["letter_index"] = (cur["letter_index"] + 1) % len(letters)
            logger.debug(f"Cursor advanced to next letter: {letters[cur['letter_index']]} (from {letters[prev_letter]}), page reset to 1")
        else:
            logger.debug(f"Cursor advanced to page {cur['page']} (from {prev_page}) for letter {letters[cur['letter_index']]}")
        return cur

    try:
        # Notify start
        from app.api.notifications import send_notification
        try:
            await send_notification(1, f"Ingesting new {media_type} via search...", "info")
        except Exception:
            pass

        while dt.datetime.utcnow() < deadline:
            query = letters[cursor["letter_index"]]
            page = cursor["page"]

            logger.debug(f"Fetching TMDB multi-search for query='{query}', page={page}, language={language}")
            data = await search_multi(query=query, page=page, language=language)
            results = (data or {}).get('results') or []
            if not results:
                logger.debug(f"No results for query='{query}', page={page}. Advancing cursor.")
                cursor = advance_cursor(cursor)
                continue

            # Filter only movies or tv per current run
            desired_tmdb_items = []
            tmdb_type = 'movie' if media_type == 'movies' else 'tv'
            for item in results:
                if item.get('media_type') != tmdb_type:
                    continue
                tmdb_id = item.get('id')
                if not tmdb_id:
                    continue
                desired_tmdb_items.append((tmdb_id, item))
                if not desired_tmdb_items:
                    logger.debug(f"No desired TMDB items for query='{query}', page={page}, type={tmdb_type}. Advancing cursor.")
                    cursor = advance_cursor(cursor)
                    continue

            # Dedup against DB in bulk
            tmdb_ids = [tid for tid, _ in desired_tmdb_items]
            existing = db.query(PersistentCandidate.tmdb_id).filter(PersistentCandidate.tmdb_id.in_(tmdb_ids)).all()
            existing_ids = {row[0] for row in existing}

            # Resolve Trakt IDs and build new candidates
            client = TraktClient(user_id=1)
            new_objs: List[PersistentCandidate] = []
            for tmdb_id, item in desired_tmdb_items:
                if tmdb_id in existing_ids:
                    continue
                # Resolve trakt id best-effort
                trakt_id: Optional[int] = None
                try:
                    results = await client.search_by_tmdb_id(tmdb_id, media_type='movie' if tmdb_type == 'movie' else 'show')
                    if results:
                        if tmdb_type == 'movie':
                            trakt_id = results[0].get('movie', {}).get('ids', {}).get('trakt')
                        else:
                            trakt_id = results[0].get('show', {}).get('ids', {}).get('trakt')
                except Exception:
                    trakt_id = None

                title = item.get('title') or item.get('name') or ""
                year = None
                rd = item.get('release_date') or item.get('first_air_date')
                if rd:
                    try:
                        year = int(rd[:4])
                    except Exception:
                        year = None

                pc = PersistentCandidate(
                    tmdb_id=tmdb_id,
                    trakt_id=trakt_id,
                    media_type='movie' if tmdb_type == 'movie' else 'show',
                    title=title,
                    original_title=item.get('original_title') or item.get('original_name'),
                    year=year,
                    release_date=rd,
                    language=(item.get('original_language') or '').lower(),
                    popularity=item.get('popularity') or 0.0,
                    vote_average=item.get('vote_average') or 0.0,
                    vote_count=item.get('vote_count') or 0,
                    overview=item.get('overview'),
                    poster_path=item.get('poster_path'),
                    backdrop_path=item.get('backdrop_path'),
                    manual=False
                )
                pc.compute_scores()
                new_objs.append(pc)

            if new_objs:
                db.bulk_save_objects(new_objs)
                db.commit()
                inserted += len(new_objs)
                logger.info(f"Inserted {len(new_objs)} new candidates for query='{query}', page={page}, type={tmdb_type}")
                _update_worker_status(media_type, "running", items_processed=inserted)

            # Advance cursor for next page
            cursor = advance_cursor(cursor)

            # Small async yield
            await asyncio.sleep(0.15)

        # Save cursor state
        try:
            r.set(cursor_key, json.dumps(cursor), ex=60 * 60 * 24)
        except Exception:
            pass

        logger.info(f"Search-multi ingest completed for {media_type}: inserted={inserted}, cursor={cursor}")
        _update_worker_status(media_type, "completed", items_processed=inserted)

        # Notify completion
        from app.api.notifications import send_notification
        if inserted:
            await send_notification(1, f"Added {inserted} new {media_type} via search", "success")
        return inserted
    except Exception as e:
        logger.warning(f"ingest_via_search_multi failed for {media_type}: {e}")
        db.rollback()
        _update_worker_status(media_type, "error", error=str(e), items_processed=inserted)
        return inserted
    finally:
        db.close()
