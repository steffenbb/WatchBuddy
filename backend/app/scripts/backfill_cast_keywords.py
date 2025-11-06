"""
Backfill script to enrich persistent_candidates with missing cast/keywords/production data.

Identifies candidates with null or empty cast field and fetches comprehensive metadata
from TMDB API, then updates the database with enriched fields.

Usage:
    docker exec -i watchbuddy-backend-1 sh -c "cd /app && PYTHONPATH=/app python app/scripts/backfill_cast_keywords.py"
    
Options:
    --limit N       Process at most N candidates (default: 1000)
    --batch-size N  Batch size for DB updates (default: 50)
    --dry-run       Show what would be updated without making changes
"""
import argparse
import asyncio
import json
import logging
from typing import List, Dict, Optional
from sqlalchemy import text, or_

from app.core.database import SessionLocal
from app.models import PersistentCandidate
from app.services.tmdb_client import fetch_tmdb_metadata, extract_enriched_fields

logging.basicConfig(
    level=logging.INFO,
    format='[%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


async def backfill_missing_enrichments(
    limit: int = 500001,
    batch_size: int = 50,
    dry_run: bool = False
) -> Dict[str, int]:
    """
    Backfill missing cast/keywords/production data for persistent_candidates.
    
    Args:
        limit: Maximum number of candidates to process
        batch_size: Number of candidates to process per batch
        dry_run: If True, show what would be updated without making changes
        
    Returns:
        Dict with statistics: total_checked, needs_enrichment, enriched, failed
    """
    stats = {
        'total_checked': 0,
        'needs_enrichment': 0,
        'enriched': 0,
        'failed': 0,
        'skipped_no_tmdb': 0
    }
    
    db = SessionLocal()
    try:
        # Find candidates with null or empty cast/keywords
        # Check for null cast OR cast='[]' (empty JSON array)
        logger.info("Identifying candidates needing enrichment...")
        
        candidates_query = db.query(PersistentCandidate).filter(
            or_(
                PersistentCandidate.cast.is_(None),
                PersistentCandidate.cast == '[]',
                PersistentCandidate.cast == '',
                PersistentCandidate.keywords.is_(None),
                PersistentCandidate.keywords == '[]',
                PersistentCandidate.keywords == ''
            ),
            PersistentCandidate.active == True,
            PersistentCandidate.tmdb_id.isnot(None)
        ).order_by(PersistentCandidate.popularity.desc())
        
        if limit:
            candidates_query = candidates_query.limit(limit)
        
        candidates = candidates_query.all()
        stats['total_checked'] = len(candidates)
        stats['needs_enrichment'] = len(candidates)
        
        logger.info(f"Found {len(candidates)} candidates needing enrichment")
        
        if dry_run:
            logger.info("DRY RUN - showing first 10 candidates that would be enriched:")
            for i, c in enumerate(candidates[:10]):
                logger.info(f"  {i+1}. {c.title} ({c.year}) - TMDB {c.tmdb_id} - Type: {c.media_type}")
            logger.info(f"... and {max(0, len(candidates) - 10)} more")
            return stats
        
        # Process in batches
        for i in range(0, len(candidates), batch_size):
            batch = candidates[i:i + batch_size]
            logger.info(f"Processing batch {i // batch_size + 1} ({i + 1}-{min(i + batch_size, len(candidates))} of {len(candidates)})")
            
            for candidate in batch:
                try:
                    if not candidate.tmdb_id:
                        stats['skipped_no_tmdb'] += 1
                        continue
                    
                    # Fetch comprehensive metadata from TMDB
                    tmdb_data = await fetch_tmdb_metadata(
                        candidate.tmdb_id,
                        media_type='movie' if candidate.media_type == 'movie' else 'tv'
                    )
                    
                    if not tmdb_data:
                        logger.debug(f"TMDB returned null for {candidate.tmdb_id}")
                        stats['failed'] += 1
                        continue
                    
                    # Extract enriched fields
                    enriched_fields = extract_enriched_fields(
                        tmdb_data,
                        media_type=candidate.media_type
                    )
                    
                    # Update candidate with enriched fields
                    updated_fields = []
                    for field_name, field_value in enriched_fields.items():
                        if hasattr(candidate, field_name):
                            setattr(candidate, field_name, field_value)
                            updated_fields.append(field_name)
                    
                    # Verify we got cast/keywords
                    cast = json.loads(enriched_fields.get('cast', '[]'))
                    keywords = json.loads(enriched_fields.get('keywords', '[]'))
                    
                    logger.info(
                        f"✓ Enriched {candidate.title} ({candidate.year}): "
                        f"{len(cast)} cast, {len(keywords)} keywords, "
                        f"{len(updated_fields)} fields updated"
                    )
                    
                    stats['enriched'] += 1
                    
                    # Small delay for rate limiting
                    await asyncio.sleep(0.1)
                    
                except Exception as e:
                    logger.error(f"Failed to enrich {candidate.title} (TMDB {candidate.tmdb_id}): {e}")
                    stats['failed'] += 1
            
            # Commit batch
            try:
                db.commit()
                logger.info(f"✓ Committed batch {i // batch_size + 1}")
            except Exception as e:
                logger.error(f"Failed to commit batch: {e}")
                db.rollback()
            
            # Rate limit between batches
            await asyncio.sleep(0.5)
        
        logger.info("=" * 60)
        logger.info("Backfill Summary:")
        logger.info(f"  Total checked:        {stats['total_checked']}")
        logger.info(f"  Needs enrichment:     {stats['needs_enrichment']}")
        logger.info(f"  Successfully enriched: {stats['enriched']}")
        logger.info(f"  Failed:               {stats['failed']}")
        logger.info(f"  Skipped (no TMDB ID): {stats['skipped_no_tmdb']}")
        logger.info("=" * 60)
        
        return stats
        
    finally:
        db.close()


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description='Backfill missing cast/keywords/production data for persistent_candidates'
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=1000,
        help='Maximum number of candidates to process (default: 1000)'
    )
    parser.add_argument(
        '--batch-size',
        type=int,
        default=50,
        help='Batch size for DB updates (default: 50)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be updated without making changes'
    )
    
    args = parser.parse_args()
    
    logger.info("Starting backfill of missing cast/keywords/production data...")
    logger.info(f"Settings: limit={args.limit}, batch_size={args.batch_size}, dry_run={args.dry_run}")
    
    # Run async backfill
    asyncio.run(backfill_missing_enrichments(
        limit=args.limit,
        batch_size=args.batch_size,
        dry_run=args.dry_run
    ))


if __name__ == '__main__':
    main()
