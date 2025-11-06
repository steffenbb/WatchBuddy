"""Quick script to verify cast/keywords backfill worked."""
from app.core.database import SessionLocal
from app.models import PersistentCandidate
import json

db = SessionLocal()
try:
    # Check the 3 items we know were enriched
    tmdb_ids = [63770, 565770, 980489]
    
    for tmdb_id in tmdb_ids:
        pc = db.query(PersistentCandidate).filter_by(tmdb_id=tmdb_id).first()
        if pc:
            cast = json.loads(pc.cast or '[]')
            keywords = json.loads(pc.keywords or '[]')
            print(f"{pc.title} ({pc.year}):")
            print(f"  Cast: {len(cast)} members - {cast[:3]}")
            print(f"  Keywords: {len(keywords)} - {keywords[:5]}")
            print(f"  Production companies: {pc.production_companies[:100] if pc.production_companies else 'None'}")
            print()
        else:
            print(f"TMDB {tmdb_id} not found")
finally:
    db.close()
