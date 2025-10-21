#!/usr/bin/env python3
"""
Auto-import trakt_id mappings on startup (if mappings file exists).

This script is called from database.py after CSV bootstrap completes.
Only updates candidates missing trakt_id (never overwrites existing values).
"""
import csv
from pathlib import Path
from sqlalchemy.orm import Session
from app.models import PersistentCandidate
import logging

def auto_import_trakt_mappings(db: Session, mappings_file: str = "/app/data/trakt_mappings_export.csv") -> int:
    """
    Import trakt_id mappings if file exists and candidates are missing trakt_ids.
    
    Returns: Number of mappings imported
    """

    mappings_path = Path(mappings_file)
    logger = logging.getLogger("auto_import_trakt")
    logging.basicConfig(level=logging.WARNING)

    if not mappings_path.exists():
        logger.warning(f"Trakt mappings file not found: {mappings_file}")
        return 0

    missing_count = db.query(PersistentCandidate).filter(
        PersistentCandidate.trakt_id.is_(None)
    ).count()
    if missing_count == 0:
        logger.warning("No candidates missing trakt_id. Skipping import.")
        return 0

    imported = 0
    logger.warning(f"Starting trakt_id import for {missing_count} candidates.")
    try:
        with mappings_path.open('r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    tmdb_id = int(row['tmdb_id'])
                    media_type = row['media_type']
                    trakt_id = int(row['trakt_id']) if row.get('trakt_id') else None
                    if not trakt_id:
                        continue
                    candidate = db.query(PersistentCandidate).filter(
                        PersistentCandidate.tmdb_id == tmdb_id,
                        PersistentCandidate.media_type == media_type,
                        PersistentCandidate.trakt_id.is_(None)
                    ).first()
                    if candidate:
                        candidate.trakt_id = trakt_id
                        imported += 1
                        if imported % 100 == 0:
                            try:
                                db.commit()
                            except Exception as commit_err:
                                logger.warning(f"Commit error at {imported} imported: {commit_err}")
                                db.rollback()
                                continue
                except Exception as row_err:
                    logger.warning(f"Row import error: {row_err}")
                    continue
        try:
            db.commit()
        except Exception as final_commit_err:
            logger.warning(f"Final commit error: {final_commit_err}")
            db.rollback()
    except Exception as e:
        logger.warning(f"Fatal error during trakt_id import: {e}")
        db.rollback()
        raise e
    logger.warning(f"Trakt_id import complete. Imported {imported} mappings.")
    return imported
