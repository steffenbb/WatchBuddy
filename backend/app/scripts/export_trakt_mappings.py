#!/usr/bin/env python3
"""
Export Trakt ID mappings and enriched metadata from persistent_candidates.

This script exports all candidates with trakt_id to a CSV file for safe backup
and import on fresh installs. Run this after enriching the DB with Trakt IDs
via the metadata builder.

Usage:
    docker exec -i watchbuddy-backend-1 sh -c "cd /app && PYTHONPATH=/app python app/scripts/export_trakt_mappings.py"

Output: backend/data/trakt_mappings_export.csv
"""
import sys
import csv
from pathlib import Path
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.core.database import SessionLocal
from app.models import PersistentCandidate

def export_trakt_mappings(output_file: str = "/app/data/trakt_mappings_export.csv"):
    """Export all persistent candidates with trakt_id to CSV."""
    db = SessionLocal()
    
    try:
        print(f"[Export] Querying persistent_candidates with trakt_id...")
        
        # Query all candidates with trakt_id
        candidates = db.query(PersistentCandidate).filter(
            PersistentCandidate.trakt_id.isnot(None)
        ).all()
        
        total = len(candidates)
        print(f"[Export] Found {total} candidates with trakt_id")
        
        if total == 0:
            print("[Export] No candidates with trakt_id found. Run metadata builder first.")
            return
        
        # Minimal export: only critical mapping fields
        fieldnames = ['tmdb_id', 'media_type', 'trakt_id']
        
        # Write CSV
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()
            
            for i, candidate in enumerate(candidates, 1):
                row = {
                    'tmdb_id': candidate.tmdb_id,
                    'media_type': candidate.media_type,
                    'trakt_id': candidate.trakt_id
                }
                writer.writerow(row)
                
                if i % 1000 == 0:
                    print(f"[Export] Processed {i}/{total} ({i/total*100:.1f}%)")
        
        file_size = output_path.stat().st_size / (1024 * 1024)  # MB
        print(f"\n[Export] ✅ Successfully exported {total} mappings")
        print(f"[Export] Output: {output_path} ({file_size:.2f} MB)")
        print(f"[Export] Timestamp: {datetime.now().isoformat()}")
        
    except Exception as e:
        print(f"[Export] ❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    export_trakt_mappings()
