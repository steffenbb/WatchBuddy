from app.core.database import SessionLocal
from app.models import PersistentCandidate
import json

def main():
    db = SessionLocal()
    try:
        candidates = db.query(PersistentCandidate).filter(PersistentCandidate.language == 'da').all()
        out = [
            {
                'trakt_id': c.trakt_id,
                'tmdb_id': c.tmdb_id,
                'title': c.title
            }
            for c in candidates
        ]
        print(json.dumps(out, ensure_ascii=False, indent=2))
    finally:
        db.close()

if __name__ == "__main__":
    main()
