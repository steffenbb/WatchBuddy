"""
Script to clean up duplicate items in smartlists.
Keeps the most recent version of each duplicate item.
"""
import sys
sys.path.insert(0, '/app')

from app.core.database import SessionLocal
from app.models import ListItem
from sqlalchemy import func
from datetime import datetime

def cleanup_duplicates():
    db = SessionLocal()
    try:
        # Find all smartlists
        smartlist_ids = db.query(ListItem.smartlist_id).distinct().all()
        smartlist_ids = [sid[0] for sid in smartlist_ids if sid[0]]
        
        total_deleted = 0
        
        for smartlist_id in smartlist_ids:
            print(f"\n=== Processing smartlist_id={smartlist_id} ===")
            
            # Get all items in this smartlist
            items = db.query(ListItem).filter(
                ListItem.smartlist_id == smartlist_id
            ).order_by(ListItem.added_at.desc()).all()
            
            # Track seen items by item_id
            seen_item_ids = set()
            items_to_delete = []
            
            for item in items:
                if item.item_id in seen_item_ids:
                    # Duplicate found
                    items_to_delete.append(item)
                    print(f"  Duplicate: id={item.id}, item_id={item.item_id}, title='{item.title}'")
                else:
                    seen_item_ids.add(item.item_id)
            
            # Delete duplicates
            if items_to_delete:
                for item in items_to_delete:
                    db.delete(item)
                db.commit()
                print(f"  Deleted {len(items_to_delete)} duplicates from smartlist {smartlist_id}")
                total_deleted += len(items_to_delete)
            else:
                print(f"  No duplicates found in smartlist {smartlist_id}")
        
        print(f"\n=== SUMMARY ===")
        print(f"Total duplicates deleted: {total_deleted}")
        
        # Show final counts
        print(f"\n=== Final item counts ===")
        from app.models import UserList
        lists = db.query(UserList).all()
        for lst in lists:
            count = db.query(ListItem).filter(ListItem.smartlist_id == lst.id).count()
            print(f"  List {lst.id} ({lst.title}): {count} items")
    
    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
        raise
    finally:
        db.close()

if __name__ == "__main__":
    cleanup_duplicates()
