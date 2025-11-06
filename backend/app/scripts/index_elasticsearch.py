"""
index_elasticsearch.py

Script to populate ElasticSearch index from persistent_candidates table.
Run inside backend container with: PYTHONPATH=/app python app/scripts/index_elasticsearch.py
"""
import json
import logging
from sqlalchemy import text

from app.core.database import SessionLocal
from app.services.elasticsearch_client import get_elasticsearch_client

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BATCH_SIZE = 1000


def main():
    """Index all active persistent_candidates into ElasticSearch."""
    
    # Get ElasticSearch client
    es_client = get_elasticsearch_client()
    
    if not es_client.is_connected():
        logger.error("Failed to connect to ElasticSearch. Ensure service is running.")
        return
    
    # Create index with mapping
    logger.info("Creating ElasticSearch index...")
    if not es_client.create_index():
        logger.error("Failed to create index")
        return
    
    # Fetch and index candidates
    db = SessionLocal()
    try:
        # Count total candidates (no longer requiring trakt_id + embedding)
        total = db.execute(text(
            "SELECT COUNT(*) FROM persistent_candidates WHERE active=true"
        )).scalar()
        
        logger.info(f"Total active candidates to index: {total}")
        
        indexed = 0
        offset = 0
        
        while offset < total:
            # Fetch batch (no longer filtering by trakt_id or embedding)
            rows = db.execute(text(
                """
                SELECT 
                    tmdb_id, media_type, title, original_title, year, overview, tagline,
                    genres, keywords, "cast" AS cast_json, created_by, networks,
                    production_companies, production_countries, spoken_languages,
                    popularity, vote_average, vote_count
                FROM persistent_candidates
                WHERE active=true
                ORDER BY id
                OFFSET :off LIMIT :lim
                """
            ), {"off": offset, "lim": BATCH_SIZE}).fetchall()
            
            if not rows:
                break
            
            # Prepare candidates for indexing
            candidates = []
            for row in rows:
                # Helper to parse JSON fields
                def parse_json_field(field_value):
                    if not field_value:
                        return ""
                    try:
                        data = json.loads(field_value)
                        if isinstance(data, list):
                            return " ".join(str(item) for item in data)
                        return str(data)
                    except:
                        return ""
                
                candidate = {
                    "tmdb_id": row.tmdb_id,
                    "media_type": row.media_type,
                    "title": row.title or "",
                    "original_title": row.original_title or "",
                    "year": row.year,
                    "overview": row.overview or "",
                    "tagline": row.tagline or "",
                    "genres": parse_json_field(row.genres),
                    "keywords": parse_json_field(row.keywords),
                    "cast": parse_json_field(row.cast_json),
                    "created_by": parse_json_field(row.created_by),
                    "networks": parse_json_field(row.networks),
                    "production_companies": parse_json_field(row.production_companies),
                    "production_countries": parse_json_field(row.production_countries),
                    "spoken_languages": parse_json_field(row.spoken_languages),
                    "popularity": row.popularity,
                    "vote_average": row.vote_average,
                    "vote_count": row.vote_count
                }
                candidates.append(candidate)
            
            # Index batch
            count = es_client.index_candidates(candidates)
            indexed += count
            offset += len(rows)
            
            logger.info(f"Progress: {indexed}/{total} indexed ({offset}/{total} processed)")
        
        logger.info(f"✅ Indexing complete! Total indexed: {indexed}")
        
        # Show stats
        stats = es_client.get_index_stats()
        logger.info(f"Index stats: {stats}")
        
    finally:
        db.close()


if __name__ == "__main__":
    main()
