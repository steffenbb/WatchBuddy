import sys
from app.core.database import SessionLocal
from app.models import PersistentCandidate

if len(sys.argv) < 2:
    print('Usage: check_candidate.py <tmdb_id>')
    sys.exit(1)

tmdb_id = int(sys.argv[1])

db = SessionLocal()
try:
    c = db.query(PersistentCandidate).filter(PersistentCandidate.tmdb_id == tmdb_id).all()
    if not c:
        print('Not found in DB')
    else:
        for candidate in c:
            print('tmdb_id=', candidate.tmdb_id, 'media_type=', candidate.media_type, 'active=', candidate.active, 'title=', candidate.title)
finally:
    db.close()
