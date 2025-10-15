from app.core.database import SessionLocal
from app.models import PersistentCandidate

def main():
    db = SessionLocal()
    try:
        count = db.query(PersistentCandidate).filter(PersistentCandidate.language == 'da').count()
        print(f"Danish candidates: {count}")
    finally:
        db.close()

if __name__ == "__main__":
    main()
