#!/usr/bin/env python3
"""
Import Trakt ID mappings and merge with existing persistent_candidates.

This script reads the exported CSV (trakt_mappings_export.csv) and updates
persistent_candidates with trakt_id and other enriched fields. Only updates
fields that are NULL or missing - never overwrites existing data.

Usage:
    docker exec -i watchbuddy-backend-1 sh -c "cd /app && PYTHONPATH=/app python app/scripts/import_trakt_mappings.py"

Expected file: backend/data/trakt_mappings_export.csv
"""
import sys
import csv
from pathlib import Path
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.core.database import SessionLocal
from app.models import PersistentCandidate
from sqlalchemy import and_

def import_trakt_mappings(input_file: str = "/app/data/trakt_mappings_export.csv"):
    """Import trakt_id mappings and merge with existing candidates."""
    input_path = Path(input_file)
    
    if not input_path.exists():
        print(f"[Import] ❌ File not found: {input_path}")
        print(f"[Import] Run export_trakt_mappings.py first to generate this file.")
        return
    
    db = SessionLocal()
    
    try:
        print(f"[Import] Reading {input_path}...")
        
        with open(input_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        
        total = len(rows)
        print(f"[Import] Found {total} mappings in CSV")
        
        updated = 0
        skipped = 0
        errors = 0
        
        for i, row in enumerate(rows, 1):
            try:
                tmdb_id = int(row['tmdb_id'])
                media_type = row['media_type']
                trakt_id = int(row['trakt_id']) if row.get('trakt_id') else None
                
                # Find existing candidate by tmdb_id + media_type (unique constraint)
                candidate = db.query(PersistentCandidate).filter(
                    and_(
                        PersistentCandidate.tmdb_id == tmdb_id,
                        PersistentCandidate.media_type == media_type
                    )
                ).first()
                
                if not candidate:
                    skipped += 1
                    if i % 1000 == 0:
                        print(f"[Import] Progress: {i}/{total} ({i/total*100:.1f}%) | Updated: {updated} | Skipped: {skipped}")
                    continue
                
                # Only update trakt_id if missing
                if trakt_id and not candidate.trakt_id:
                    candidate.trakt_id = trakt_id
                    updated += 1
                    
                    if updated % 100 == 0:
                        db.commit()  # Commit every 100 updates
                
                if i % 1000 == 0:
                    print(f"[Import] Progress: {i}/{total} ({i/total*100:.1f}%) | Updated: {updated} | Skipped: {skipped}")
                
            except Exception as e:
                errors += 1
                if errors <= 10:  # Only log first 10 errors
                    print(f"[Import] Error on row {i}: {e}")
        
        # Final commit
        db.commit()
        
        print(f"\n[Import] ✅ Completed!")
        print(f"[Import] Total rows: {total}")
        print(f"[Import] Updated: {updated}")
        print(f"[Import] Skipped (not found in DB): {skipped}")
        print(f"[Import] Errors: {errors}")
        print(f"[Import] Timestamp: {datetime.now().isoformat()}")
        
    except Exception as e:
        print(f"[Import] ❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    import_trakt_mappings()
