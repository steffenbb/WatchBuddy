"""
Import cast data from a separate CSV file into persistent_candidates table.

Usage:
    docker exec -i watchbuddy-backend-1 sh -c "cd /app && PYTHONPATH=/app python app/scripts/import_cast_data.py /app/data/tmdb_cast.csv"

Expected CSV format:
    id,cast
    550,"Brad Pitt, Edward Norton, Helena Bonham Carter"
    ...
    
Note: 'id' column is the TMDB ID
"""
import sys
import csv
import json
from sqlalchemy import text
from app.core.database import SessionLocal
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def import_cast_data(csv_path: str):
    """
    Import cast data from CSV and update persistent_candidates.
    
    Args:
        csv_path: Path to CSV file with columns: tmdb_id, cast
    """
    db = SessionLocal()
    try:
        logger.info(f"Starting cast data import from {csv_path}")
        
        updated_count = 0
        not_found_count = 0
        error_count = 0
        
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            # Validate required columns - expecting 'id' (tmdb_id) and 'cast'
            if 'id' not in reader.fieldnames or 'cast' not in reader.fieldnames:
                logger.error(f"CSV must contain 'id' and 'cast' columns. Found: {reader.fieldnames}")
                return
            
            batch = []
            batch_size = 500
            
            for idx, row in enumerate(reader, 1):
                try:
                    tmdb_id = row.get('id', '').strip()  # Changed from 'tmdb_id' to 'id'
                    cast_data = row.get('cast', '').strip()
                    
                    if not tmdb_id:
                        continue
                    
                    # Convert tmdb_id to int
                    try:
                        tmdb_id = int(tmdb_id)
                    except ValueError:
                        logger.warning(f"Invalid tmdb_id at row {idx}: {tmdb_id}")
                        error_count += 1
                        continue
                    
                    # Parse cast data - handle different formats
                    cast_list = []
                    if cast_data:
                        # If it's already JSON array format
                        if cast_data.startswith('['):
                            try:
                                cast_list = json.loads(cast_data)
                            except json.JSONDecodeError:
                                # Fall back to comma-separated
                                cast_list = [c.strip() for c in cast_data.strip('[]').split(',') if c.strip()]
                        else:
                            # Comma-separated format
                            cast_list = [c.strip() for c in cast_data.split(',') if c.strip()]
                    
                    # Convert to JSON string for storage
                    cast_json = json.dumps(cast_list) if cast_list else None
                    
                    batch.append({
                        'tmdb_id': tmdb_id,
                        'cast': cast_json
                    })
                    
                    # Process batch
                    if len(batch) >= batch_size:
                        updated, not_found = _update_batch(db, batch)
                        updated_count += updated
                        not_found_count += not_found
                        
                        logger.info(f"Processed {idx} rows: {updated_count} updated, {not_found_count} not found")
                        batch = []
                
                except Exception as e:
                    logger.warning(f"Error processing row {idx}: {str(e)}")
                    error_count += 1
                    continue
            
            # Process remaining batch
            if batch:
                updated, not_found = _update_batch(db, batch)
                updated_count += updated
                not_found_count += not_found
        
        logger.info(f"Cast data import complete!")
        logger.info(f"  Total updated: {updated_count}")
        logger.info(f"  Not found in database: {not_found_count}")
        logger.info(f"  Errors: {error_count}")
        
    except FileNotFoundError:
        logger.error(f"File not found: {csv_path}")
    except Exception as e:
        logger.error(f"Critical error during cast import: {str(e)}", exc_info=True)
    finally:
        db.close()


def _update_batch(db, batch):
    """Update a batch of cast data."""
    updated_count = 0
    not_found_count = 0
    
    try:
        for item in batch:
            # Check both movie and show media types
            result = db.execute(
                text("""
                    UPDATE persistent_candidates 
                        SET "cast" = :cast
                        WHERE tmdb_id = :tmdb_id
                        AND ("cast" IS NULL OR "cast" = '[]')
                    RETURNING id
                """),
                {
                    'tmdb_id': item['tmdb_id'],
                    'cast': item['cast']
                }
            )
            
            if result.rowcount > 0:
                updated_count += result.rowcount
            else:
                # Check if the record exists at all
                exists = db.execute(
                    text("SELECT 1 FROM persistent_candidates WHERE tmdb_id = :tmdb_id LIMIT 1"),
                    {'tmdb_id': item['tmdb_id']}
                ).fetchone()
                
                if not exists:
                    not_found_count += 1
        
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating batch: {str(e)}")
        raise
    
    return updated_count, not_found_count


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python import_cast_data.py <path_to_cast_csv>")
        print("\nExample:")
        print("  docker exec -i watchbuddy-backend-1 sh -c \"cd /app && PYTHONPATH=/app python app/scripts/import_cast_data.py /app/data/tmdb_cast.csv\"")
        sys.exit(1)
    
    csv_path = sys.argv[1]
    import_cast_data(csv_path)
