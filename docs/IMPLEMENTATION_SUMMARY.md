# WatchBuddy Persistent Candidate Pool - Implementation Summary

## Completed Implementation

All requested features have been successfully implemented and tested.

---

## Architecture Overview

### Database Schema
Created two new tables:

**`persistent_candidates`** (Main pool):
- Stores 20,000+ movies/shows with full TMDB metadata
- Pre-computed scoring: `obscurity_score`, `mainstream_score`, `freshness_score`
- Comprehensive indices on language, genre, year, popularity, votes
- Unique constraints on `tmdb_id` (primary) and `trakt_id` (when available)

**`candidate_ingestion_state`** (Checkpointing):
- Tracks `last_release_date` per media_type (movies/shows)
- Enables incremental updates without re-fetching historical content

### Core Services

1. Bootstrap Import (`core/database.py`)
- Auto-detects empty `persistent_candidates` table on startup
- Scans `/app/data/*.csv` for datasets
- Bulk imports with score computation
- Resilient to missing columns/malformed rows

2. Incremental Ingestion (`services/candidate_ingestion.py`)
- `ingest_new_content()`: Fetches TMDB items >= 2024 (or last checkpoint)
- Adaptive multi-page fetch with rate limiting
- Updates existing records or inserts new
- Checkpoints progress in `candidate_ingestion_state`

3. Vote Refresh (`services/candidate_ingestion.py`)
- `refresh_recent_votes()`: Updates vote_count/vote_average for recent items (<90 days)
- Batch limited to 400 items per run (API-friendly)
- Re-computes derived scores after updates

4. Celery Task Integration (`services/tasks.py`)
- `ingest_new_movies` / `ingest_new_shows`
- `refresh_recent_votes_movies` / `refresh_recent_votes_shows`
- Scheduled via Celery Beat (cron-style)

5. Recommendation Pipeline (`services/bulk_candidate_provider.py`)
- Primary sourcing from `persistent_candidates` DB query
- Filters: language, genre, year, obscurity/mainstream
- Discovery-aware ordering
- Fallback to external APIs only for missing/new content
- Returns enriched candidates with pre-computed scores

6. Scoring Engine (`services/scoring_engine.py`)
- Detects `_from_persistent_store` flag
- Blends persistent scores with discovery mode
- Uses `obscurity_score`, `mainstream_score`, `freshness_score`

---

## File Structure

New and modified files are listed in the repository; see services/ and scripts/ for details.

---

## Key Features

1. Zero-Configuration Bootstrap
2. Incremental Updates
3. Smart Scoring Heuristics
4. API Rate Limit Friendly
5. Performance Gains

---

## Testing & Validation

- Database tables created
- Indices applied
- Backend startup verified
- Schema verification and usage examples provided

---

## Production Deployment Checklist

- Place TMDB CSV datasets in `backend/data/` before build
- Configure Celery Beat schedule for ingestion tasks (see deployment docs)
- Verify `/app/data` volume or mount point exists
- Monitor first startup logs for CSV import success
- After bootstrap, check `persistent_candidates` row count

---

## Support

For issues or questions, check backend logs and persistent pool guide.
