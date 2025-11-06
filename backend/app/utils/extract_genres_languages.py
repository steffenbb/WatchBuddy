"""
Utility to extract available genres and languages from persistent_candidates table.
Uses the same logic as metadata_options API for consistency.
"""
from app.core.database import SessionLocal
from sqlalchemy import text
from typing import List, Tuple
import json

def get_genres_and_languages(min_count=0):
    """
    Get available genres and languages from persistent candidates.
    
    Args:
        min_count: Minimum number of items required for a genre/language to be included.
                   Default 0 returns all available options.
    
    Returns:
        Tuple of (genres_list, languages_list)
    """
    db = SessionLocal()
    try:
        # Fetch raw genre strings and normalize in Python to tolerate non-JSON formats
        # Some rows contain JSON arrays (e.g., '["Drama","Action"]'), others contain
        # comma-separated text (e.g., 'Drama, Action') or single tokens (e.g., 'Drama').
        genre_rows = db.execute(text("""
            SELECT genres
            FROM persistent_candidates
            WHERE genres IS NOT NULL AND TRIM(genres) != ''
        """)).fetchall()

        raw_genre_items = []
        for (gval,) in genre_rows:
            if gval is None:
                continue
            if isinstance(gval, (list, tuple)):
                # Already a sequence
                for item in gval:
                    if item:
                        raw_genre_items.append(str(item))
                continue
            text_val = str(gval).strip()
            if not text_val:
                continue
            # Try JSON array first
            parsed = None
            if text_val.startswith('[') and text_val.endswith(']'):
                try:
                    parsed = json.loads(text_val)
                except Exception:
                    parsed = None
            if isinstance(parsed, list):
                for item in parsed:
                    if item is not None:
                        raw_genre_items.append(str(item))
                continue
            # Fallback: split by comma
            parts = [p.strip() for p in text_val.split(',') if p.strip()]
            if parts:
                raw_genre_items.extend(parts)
        
        # Normalize genres (same mapping as metadata_options)
        genre_mapping = {
            'Sci-Fi & Fantasy': 'Science Fiction',
            'Action & Adventure': 'Action',
            'War & Politics': 'War',
        }
        
        normalized_genres = set()
        for genre in raw_genre_items:
            normalized = genre_mapping.get(genre, genre)
            normalized_genres.add(normalized)
        
        genres_filtered = sorted(normalized_genres)
        
        # Query distinct languages with counts
        lang_query = text("""
            SELECT language, COUNT(*) as count 
            FROM persistent_candidates 
            WHERE language IS NOT NULL AND language != ''
            GROUP BY language
            HAVING COUNT(*) >= :min_count
            ORDER BY count DESC, language
        """)
        lang_result = db.execute(lang_query, {"min_count": min_count})
        
        # Extract language codes
        langs_filtered = [row[0] for row in lang_result.fetchall()]
        
        return genres_filtered, langs_filtered
    finally:
        db.close()

if __name__ == "__main__":
    genres, langs = get_genres_and_languages()
    print("Genres:", genres)
    print("Languages:", langs)

