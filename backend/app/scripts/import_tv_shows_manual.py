"""
Manual CSV import script to complete TV shows import.
"""
import csv
import json
import logging
from pathlib import Path
from app.core.database import SessionLocal
from app.models import PersistentCandidate

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def import_tv_shows():
    """Import TV shows from CSV, skipping existing ones."""
    db = SessionLocal()
    
    # Get existing TMDB IDs to skip
    logger.info("Loading existing show TMDB IDs...")
    existing_ids = set()
    for row in db.query(PersistentCandidate.tmdb_id).filter_by(media_type='show').all():
        existing_ids.add(row[0])
    
    logger.info(f"Found {len(existing_ids)} existing shows, will skip these")
    
    csv_path = Path('/app/data/TMDB_tv_dataset_v3.csv')
    
    batch = []
    total_rows = 0
    skipped = 0
    imported = 0
    errors = 0
    
    try:
        with csv_path.open('r', encoding='utf-8', newline='') as f:
            reader = csv.DictReader(f)
            
            for row in reader:
                total_rows += 1
                
                # Progress logging
                if total_rows % 10000 == 0:
                    logger.info(f"Processed {total_rows} rows, imported {imported}, skipped {skipped}, errors {errors}")
                
                # Get TMDB ID
                tmdb_id = row.get('id')
                if not tmdb_id:
                    errors += 1
                    continue
                
                try:
                    tmdb_id_int = int(tmdb_id)
                except:
                    errors += 1
                    continue
                
                # Skip if already exists
                if tmdb_id_int in existing_ids:
                    skipped += 1
                    continue
                
                # Get title
                title = row.get('name')
                if not title:
                    errors += 1
                    continue
                
                # Parse other fields
                language = (row.get('original_language') or '').lower()[:5]
                
                release_date = row.get('first_air_date')
                year = None
                if release_date and len(release_date) >= 4:
                    try:
                        year = int(release_date[:4])
                    except:
                        pass
                
                # Parse genres
                genres_raw = row.get('genres') or ''
                try:
                    if genres_raw and genres_raw.startswith('['):
                        genres_list = json.loads(genres_raw)
                    else:
                        genres_list = [g.strip() for g in genres_raw.split(',') if g.strip()]
                except:
                    genres_list = [g.strip() for g in genres_raw.split(',') if g.strip()]
                
                # Parse popularity and votes
                try:
                    popularity = float(row.get('popularity') or 0.0)
                except:
                    popularity = 0.0
                
                try:
                    vote_average = float(row.get('vote_average') or 0.0)
                except:
                    vote_average = 0.0
                
                try:
                    vote_count = int(float(row.get('vote_count') or 0))
                except:
                    vote_count = 0
                
                # Handle adult flag
                adult_val = row.get('adult', 'False')
                is_adult = str(adult_val).lower() in ('1', 'true', 't', 'yes', 'y', 'True')
                
                # Create candidate
                pc = PersistentCandidate(
                    tmdb_id=tmdb_id_int,
                    trakt_id=None,
                    imdb_id=row.get('imdb_id'),
                    media_type='show',
                    title=title.strip(),
                    original_title=row.get('original_name'),
                    year=year,
                    release_date=release_date,
                    language=language,
                    genres=json.dumps(genres_list) if genres_list else None,
                    keywords=None,  # TV CSV doesn't have keywords
                    overview=row.get('overview') or '',
                    popularity=popularity,
                    vote_average=vote_average,
                    vote_count=vote_count,
                    poster_path=row.get('poster_path'),
                    backdrop_path=row.get('backdrop_path'),
                    is_adult=is_adult,
                    manual=True
                )
                pc.compute_scores()
                batch.append(pc)
                imported += 1
                
                # Commit in batches
                if len(batch) >= 500:
                    try:
                        db.bulk_save_objects(batch)
                        db.commit()
                        batch = []
                    except Exception as e:
                        logger.error(f"Batch commit error: {e}")
                        db.rollback()
                        batch = []
                        errors += len(batch)
            
            # Final batch
            if batch:
                try:
                    db.bulk_save_objects(batch)
                    db.commit()
                except Exception as e:
                    logger.error(f"Final batch commit error: {e}")
                    db.rollback()
        
        logger.info(f"\n=== Import Complete ===")
        logger.info(f"Total rows processed: {total_rows}")
        logger.info(f"Imported: {imported}")
        logger.info(f"Skipped (existing): {skipped}")
        logger.info(f"Errors: {errors}")
        
    finally:
        db.close()

if __name__ == '__main__':
    import_tv_shows()
