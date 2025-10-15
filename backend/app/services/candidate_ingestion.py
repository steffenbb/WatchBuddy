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
from typing import List, Optional

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models import PersistentCandidate, CandidateIngestionState
from app.utils.logger import logger

from app.services.tmdb_client import discover_movies, discover_tv, get_tmdb_details

MIN_YEAR = 2024
RECENT_DAYS_REFRESH = 90

async def ingest_new_content(media_type: str = 'movies', pages: int = 5, per_page: int = 20) -> int:
    """Ingest new TMDB content newer than last checkpoint (or MIN_YEAR baseline).

    Returns number of upserted rows.
    """
    assert media_type in ('movies','shows')
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
                        manual=False
                    )
                    pc.compute_scores()
                    batch_objs.append(pc)
                    if not new_last_date or release_date > new_last_date:
                        new_last_date = release_date
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
                details = await get_tmdb_details(row.tmdb_id, 'movie' if row.media_type=='movie' else 'tv')
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
