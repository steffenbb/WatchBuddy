from app.core.database import SessionLocal
from app.models import PersistentCandidate, ListItem
import json

def main():
    db = SessionLocal()
    try:
        # Fetch all Danish persistent candidates
        danish_candidates = db.query(PersistentCandidate).filter(
            PersistentCandidate.language == 'da'
        ).all()
        
        danish_trakt_ids = {c.trakt_id for c in danish_candidates if c.trakt_id}
        danish_tmdb_ids = {c.tmdb_id for c in danish_candidates if c.tmdb_id}
        
        print(f"Total Danish candidates: {len(danish_candidates)}")
        print(f"Danish candidates with Trakt IDs: {len(danish_trakt_ids)}")
        print(f"Danish candidates with TMDB IDs: {len(danish_tmdb_ids)}")
        print()
        
        # Fetch list items for Danish list (ID 21)
        list_items = db.query(ListItem).filter(ListItem.smartlist_id == 21).all()
        list_trakt_ids = {item.trakt_id for item in list_items if item.trakt_id}
        
        print(f"Total items in Danish list: {len(list_items)}")
        print(f"Unique Trakt IDs in list: {len(list_trakt_ids)}")
        print()
        
        # Find which Danish candidates are in the list
        danish_in_list = danish_trakt_ids.intersection(list_trakt_ids)
        danish_not_in_list = danish_trakt_ids - list_trakt_ids
        
        print(f"Danish candidates currently in list: {len(danish_in_list)}")
        print(f"Danish candidates NOT in list: {len(danish_not_in_list)}")
        print()
        
        # Find non-Danish items in the list
        non_danish_in_list = list_trakt_ids - danish_trakt_ids
        print(f"Non-Danish items in list (should be removed): {len(non_danish_in_list)}")
        print()
        
        # List Danish candidates not in list
        print("Danish candidates NOT in the list:")
        for c in danish_candidates:
            if c.trakt_id and c.trakt_id in danish_not_in_list:
                print(f"  - {c.title} (Trakt: {c.trakt_id}, TMDB: {c.tmdb_id})")
        
        print()
        print("Danish candidates currently IN the list:")
        for c in danish_candidates:
            if c.trakt_id and c.trakt_id in danish_in_list:
                print(f"  - {c.title} (Trakt: {c.trakt_id}, TMDB: {c.tmdb_id})")
                
    finally:
        db.close()

if __name__ == "__main__":
    main()
