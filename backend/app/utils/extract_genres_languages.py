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
        # Query distinct genres from persistent_candidates using JSON extraction
        genre_query = text("""
            SELECT DISTINCT jsonb_array_elements_text(genres::jsonb) as genre 
            FROM persistent_candidates 
            WHERE genres IS NOT NULL AND genres != '[]'
            ORDER BY genre
        """)
        genre_result = db.execute(genre_query)
        raw_genres = [row[0] for row in genre_result.fetchall()]
        
        # Normalize genres (same mapping as metadata_options)
        genre_mapping = {
            'Sci-Fi & Fantasy': 'Science Fiction',
            'Action & Adventure': 'Action',
            'War & Politics': 'War',
        }
        
        normalized_genres = set()
        for genre in raw_genres:
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

