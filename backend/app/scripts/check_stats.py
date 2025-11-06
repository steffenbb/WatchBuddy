#!/usr/bin/env python3
"""Check persistent_candidates and FAISS statistics."""

from app.core.database import SessionLocal
from app.models import PersistentCandidate
import os

def main():
    db = SessionLocal()
    try:
        # Database stats
        print("=" * 60)
        print("PERSISTENT CANDIDATES DATABASE STATISTICS")
        print("=" * 60)
        
        total = db.query(PersistentCandidate).count()
        with_trakt = db.query(PersistentCandidate).filter(
            PersistentCandidate.trakt_id.isnot(None)
        ).count()
        with_keywords = db.query(PersistentCandidate).filter(
            PersistentCandidate.keywords.isnot(None),
            PersistentCandidate.keywords != ''
        ).count()
        
        print(f"\nTotal items:           {total:>10,}")
        print(f"With Trakt ID:         {with_trakt:>10,}  ({with_trakt/total*100:>5.1f}%)")
        print(f"Missing Trakt ID:      {total-with_trakt:>10,}  ({(total-with_trakt)/total*100:>5.1f}%)")
        print(f"With Keywords:         {with_keywords:>10,}  ({with_keywords/total*100:>5.1f}%)")
        print(f"Missing Keywords:      {total-with_keywords:>10,}  ({(total-with_keywords)/total*100:>5.1f}%)")
        
        # Check for cast column
        try:
            # Check if cast column exists
            result = db.execute("SELECT column_name FROM information_schema.columns WHERE table_name='persistent_candidates' AND column_name LIKE '%cast%'")
            cast_cols = [row[0] for row in result]
            if cast_cols:
                print(f"\nCast columns found: {', '.join(cast_cols)}")
                # Try to count
                for col in cast_cols:
                    with_cast = db.execute(f"SELECT COUNT(*) FROM persistent_candidates WHERE {col} IS NOT NULL AND {col} != ''").scalar()
                    print(f"With {col}:          {with_cast:>10,}  ({with_cast/total*100:>5.1f}%)")
            else:
                print("\nNo cast columns found in persistent_candidates table")
        except Exception as e:
            print(f"\nCould not check cast columns: {e}")
        
        # FAISS index stats
        print("\n" + "=" * 60)
        print("FAISS INDEX STATISTICS")
        print("=" * 60)
        
        faiss_dir = "/app/data/ai"
        if os.path.exists(faiss_dir):
            index_file = os.path.join(faiss_dir, "faiss_index.bin")
            id_map_file = os.path.join(faiss_dir, "faiss_id_map.pkl")
            
            if os.path.exists(index_file):
                import faiss
                import pickle
                
                # Load FAISS index
                index = faiss.read_index(index_file)
                print(f"\nFAISS index entries:   {index.ntotal:>10,}")
                
                # Load ID map
                if os.path.exists(id_map_file):
                    with open(id_map_file, 'rb') as f:
                        id_map = pickle.load(f)
                    print(f"ID map entries:        {len(id_map):>10,}")
                else:
                    print("ID map file not found")
            else:
                print("\nFAISS index file not found")
        else:
            print(f"\nFAISS directory not found: {faiss_dir}")
        
        print("\n" + "=" * 60)
        
    finally:
        db.close()

if __name__ == "__main__":
    main()
