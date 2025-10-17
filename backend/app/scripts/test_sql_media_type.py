"""Test if SQL media_type filter actually works."""
from app.models import PersistentCandidate
from app.core.database import SessionLocal

db = SessionLocal()
try:
    # Test query with media_type = "movie"
    q = db.query(PersistentCandidate).filter(
        PersistentCandidate.media_type == "movie",
        PersistentCandidate.genres.like('%Comedy%'),
        PersistentCandidate.genres.like('%Action%')
    ).limit(10)
    
    print("="*80)
    print("SQL Query:")
    print(str(q))
    print("="*80)
    
    results = q.all()
    print(f"\nFound {len(results)} movies:")
    for c in results:
        print(f"  - {c.title} ({c.year}) - media_type: {c.media_type}")
        
    print("\n" + "="*80)
    print("Testing with 'show' instead:")
    print("="*80)
    
    q2 = db.query(PersistentCandidate).filter(
        PersistentCandidate.media_type == "show",
        PersistentCandidate.genres.like('%Comedy%'),
        PersistentCandidate.genres.like('%Action%')
    ).limit(10)
    
    results2 = q2.all()
    print(f"\nFound {len(results2)} shows:")
    for c in results2:
        print(f"  - {c.title} ({c.year}) - media_type: {c.media_type}")
finally:
    db.close()
