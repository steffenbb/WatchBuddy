"""
Utility to extract available genres and languages from persistent_candidates table.
Returns genres and languages with >500 items each.
"""
from app.core.database import SessionLocal
from app.models import PersistentCandidate
import json

def get_genres_and_languages(min_count=500):
    db = SessionLocal()
    try:
        # Genre extraction
        genre_counts = {}
        lang_counts = {}
        for c in db.query(PersistentCandidate).filter(PersistentCandidate.trakt_id.isnot(None)):
            # Genres
            try:
                genres = json.loads(c.genres) if c.genres else []
                for g in genres:
                    genre_counts[g] = genre_counts.get(g, 0) + 1
            except Exception:
                pass
            # Languages
            lang = c.language or c.original_title or None
            if lang:
                lang_counts[lang] = lang_counts.get(lang, 0) + 1
        # Filter by min_count
        genres_filtered = [g for g, cnt in genre_counts.items() if cnt >= min_count]
        langs_filtered = [l for l, cnt in lang_counts.items() if cnt >= min_count]
        return genres_filtered, langs_filtered
    finally:
        db.close()

if __name__ == "__main__":
    genres, langs = get_genres_and_languages()
    print("Genres:", genres)
    print("Languages:", langs)
