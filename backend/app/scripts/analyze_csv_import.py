"""
Analyze CSV import to understand why only 12k items were imported from 1.5M rows.
"""
import csv
import sys
from pathlib import Path

def analyze_csv(csv_path, media_type):
    """Analyze a single CSV file."""
    total = 0
    valid_id_title = 0
    no_id = 0
    no_title = 0
    invalid_id = 0
    
    try:
        with open(csv_path, 'r', encoding='utf-8', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                total += 1
                
                tmdb_id = row.get('id')
                title = row.get('title') or row.get('name')
                
                if not tmdb_id:
                    no_id += 1
                    continue
                
                if not title:
                    no_title += 1
                    continue
                
                try:
                    int(tmdb_id)
                except:
                    invalid_id += 1
                    continue
                
                valid_id_title += 1
        
        print(f"\n{media_type.upper()} - {csv_path.name}")
        print(f"  Total rows: {total:,}")
        print(f"  Valid (has id + title): {valid_id_title:,}")
        print(f"  Missing ID: {no_id:,}")
        print(f"  Missing title: {no_title:,}")
        print(f"  Invalid ID format: {invalid_id:,}")
        print(f"  Import rate: {valid_id_title/total*100:.1f}%")
        
        return valid_id_title
    except Exception as e:
        print(f"Error analyzing {csv_path}: {e}")
        return 0

def main():
    data_dir = Path('/app/data')
    
    movie_csv = data_dir / 'TMDB_movie_dataset_v11.csv'
    tv_csv = data_dir / 'TMDB_tv_dataset_v3.csv'
    
    total_valid = 0
    
    if movie_csv.exists():
        total_valid += analyze_csv(movie_csv, 'movies')
    else:
        print(f"Movie CSV not found: {movie_csv}")
    
    if tv_csv.exists():
        total_valid += analyze_csv(tv_csv, 'shows')
    else:
        print(f"TV CSV not found: {tv_csv}")
    
    print(f"\nTOTAL EXPECTED VALID IMPORTS: {total_valid:,}")
    
    # Now check database
    from app.core.database import SessionLocal
    from app.models import PersistentCandidate
    
    db = SessionLocal()
    try:
        actual_count = db.query(PersistentCandidate).count()
        movie_count = db.query(PersistentCandidate).filter_by(media_type='movie').count()
        show_count = db.query(PersistentCandidate).filter_by(media_type='show').count()
        
        print(f"\nACTUAL DATABASE COUNTS:")
        print(f"  Movies: {movie_count:,}")
        print(f"  Shows: {show_count:,}")
        print(f"  Total: {actual_count:,}")
        print(f"\nMISSING: {total_valid - actual_count:,} items ({(total_valid - actual_count)/total_valid*100:.1f}%)")
    finally:
        db.close()

if __name__ == '__main__':
    main()
