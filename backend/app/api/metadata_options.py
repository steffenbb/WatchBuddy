"""
metadata_options.py

API endpoints for fetching available metadata options (genres, languages, moods, themes, fusions)
from the persistent candidate pool and system configurations.
"""

from fastapi import APIRouter, HTTPException
from typing import Dict, List, Any
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/options/genres")
async def get_available_genres() -> Dict[str, Any]:
    """
    Get all available genres from persistent candidates database.
    Returns both the raw list and a normalized/grouped version for UI display.
    """
    try:
        from app.core.database import SessionLocal
        from sqlalchemy import text
        
        db = SessionLocal()
        try:
            # Query distinct genres from persistent_candidates
            query = text("""
                SELECT DISTINCT jsonb_array_elements_text(genres::jsonb) as genre 
                FROM persistent_candidates 
                WHERE genres IS NOT NULL AND genres != '[]'
                ORDER BY genre
            """)
            result = db.execute(query)
            raw_genres = [row[0] for row in result.fetchall()]
            
            # Normalize and deduplicate genres
            # Map variations to canonical names
            genre_mapping = {
                'Sci-Fi & Fantasy': 'Science Fiction',
                'Action & Adventure': 'Action',
                'War & Politics': 'War',
            }
            
            normalized_genres = set()
            for genre in raw_genres:
                normalized = genre_mapping.get(genre, genre)
                normalized_genres.add(normalized)
            
            # Sort for consistent output
            sorted_genres = sorted(normalized_genres)
            
            # Group genres by category for better UI organization
            genre_categories = {
                "action": ["Action", "Adventure", "War", "Western"],
                "comedy": ["Comedy"],
                "drama": ["Drama", "Romance", "Family"],
                "thriller": ["Thriller", "Crime", "Mystery"],
                "horror": ["Horror"],
                "scifi": ["Science Fiction", "Fantasy"],
                "other": ["Documentary", "History", "Music", "Musical", "Animation", "Kids", 
                         "News", "Reality", "Soap", "Talk", "TV Movie"]
            }
            
            # Build categorized output
            categorized = {}
            uncategorized = []
            for genre in sorted_genres:
                found = False
                for category, genres in genre_categories.items():
                    if genre in genres:
                        if category not in categorized:
                            categorized[category] = []
                        categorized[category].append(genre)
                        found = True
                        break
                if not found:
                    uncategorized.append(genre)
            
            if uncategorized:
                categorized["other"] = categorized.get("other", []) + uncategorized
            
            return {
                "genres": sorted_genres,
                "count": len(sorted_genres),
                "categorized": categorized
            }
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Failed to fetch genres: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch genres: {str(e)}")


@router.get("/options/languages")
async def get_available_languages() -> Dict[str, Any]:
    """
    Get all available languages from persistent candidates database.
    Returns language codes with human-readable names.
    """
    try:
        from app.core.database import SessionLocal
        from sqlalchemy import text
        
        db = SessionLocal()
        try:
            # Query distinct languages with counts
            query = text("""
                SELECT language, COUNT(*) as count 
                FROM persistent_candidates 
                WHERE language IS NOT NULL AND language != ''
                GROUP BY language
                ORDER BY count DESC, language
            """)
            result = db.execute(query)
            language_data = [(row[0], row[1]) for row in result.fetchall()]
            
            # Map common language codes to names
            language_names = {
                'en': 'English',
                'da': 'Danish',
                'sv': 'Swedish',
                'no': 'Norwegian',
                'de': 'German',
                'fr': 'French',
                'es': 'Spanish',
                'it': 'Italian',
                'pt': 'Portuguese',
                'nl': 'Dutch',
                'pl': 'Polish',
                'ru': 'Russian',
                'ja': 'Japanese',
                'ko': 'Korean',
                'zh': 'Chinese',
                'hi': 'Hindi',
                'ar': 'Arabic',
                'tr': 'Turkish',
                'th': 'Thai',
                'vi': 'Vietnamese',
                'id': 'Indonesian',
                'he': 'Hebrew',
                'fi': 'Finnish',
                'cs': 'Czech',
                'ro': 'Romanian',
                'hu': 'Hungarian',
                'el': 'Greek',
                'uk': 'Ukrainian',
                'fa': 'Persian',
                'bn': 'Bengali',
                'ta': 'Tamil',
                'te': 'Telugu',
                'ml': 'Malayalam',
                'kn': 'Kannada',
                'mr': 'Marathi',
            }
            
            # Build response with names and counts
            languages = []
            popular_languages = []
            for code, count in language_data:
                lang_obj = {
                    "code": code,
                    "name": language_names.get(code, code.upper()),
                    "count": count
                }
                languages.append(lang_obj)
                
                # Popular languages (>100 items)
                if count > 100:
                    popular_languages.append(lang_obj)
            
            return {
                "languages": languages,
                "popular": popular_languages,
                "count": len(languages)
            }
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Failed to fetch languages: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch languages: {str(e)}")


@router.get("/options/moods")
async def get_available_moods() -> Dict[str, Any]:
    """
    Get all available mood axes from the mood service.
    These are used for mood-based recommendations and filtering.
    """
    try:
        from app.services.mood import MOOD_AXES
        
        # Extended mood descriptions for UI
        mood_descriptions = {
            "happy": "Uplifting, feel-good, cheerful content",
            "sad": "Melancholic, emotional, tearjerker content",
            "excited": "Action-packed, thrilling, adrenaline-pumping",
            "scared": "Suspenseful, terrifying, nerve-wracking",
            "romantic": "Love stories, romantic, heartwarming",
            "tense": "Edge-of-your-seat, suspenseful, gripping",
            "curious": "Thought-provoking, mysterious, intriguing",
            "thoughtful": "Deep, philosophical, contemplative"
        }
        
        # Additional moods for expanded options
        extended_moods = [
            "cozy", "intense", "uplifting", "dark", "melancholic", "adventurous",
            "nostalgic", "inspiring", "bittersweet", "whimsical"
        ]
        
        moods_with_descriptions = [
            {
                "mood": mood,
                "description": mood_descriptions.get(mood, f"{mood.title()} content")
            }
            for mood in MOOD_AXES
        ]
        
        extended_moods_with_descriptions = [
            {
                "mood": mood,
                "description": f"{mood.title()} mood"
            }
            for mood in extended_moods
        ]
        
        return {
            "moods": MOOD_AXES,
            "extended_moods": extended_moods,
            "all_moods": MOOD_AXES + extended_moods,
            "descriptions": moods_with_descriptions + extended_moods_with_descriptions,
            "count": len(MOOD_AXES) + len(extended_moods)
        }
    except Exception as e:
        logger.error(f"Failed to fetch moods: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch moods: {str(e)}")


@router.get("/options/themes")
async def get_available_themes() -> Dict[str, Any]:
    """
    Get all available themes for theme-based lists.
    These are curated theme combinations used in dynamic lists.
    """
    try:
        # Expanded theme options
        themes = [
            {"name": "trending", "description": "Currently popular and trending content"},
            {"name": "noir", "description": "Dark, moody, classic noir aesthetics"},
            {"name": "witty crime", "description": "Clever crime stories with humor"},
            {"name": "dark thriller", "description": "Intense, dark psychological thrillers"},
            {"name": "epic saga", "description": "Grand, sweeping narratives"},
            {"name": "indie gems", "description": "Hidden independent film treasures"},
            {"name": "cult classics", "description": "Beloved cult favorite films"},
            {"name": "mindbenders", "description": "Complex, twist-heavy plots"},
            {"name": "underrated", "description": "Overlooked quality content"},
            {"name": "nostalgia", "description": "Throwback favorites"},
            {"name": "arthouse", "description": "Artistic, experimental cinema"},
            {"name": "crowd-pleasers", "description": "Universally loved films"},
        ]
        
        return {
            "themes": [t["name"] for t in themes],
            "descriptions": themes,
            "count": len(themes)
        }
    except Exception as e:
        logger.error(f"Failed to fetch themes: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch themes: {str(e)}")


@router.get("/options/fusions")
async def get_available_fusions() -> Dict[str, Any]:
    """
    Get all available fusion combinations for fusion-based lists.
    These are genre combinations that create unique recommendation blends.
    """
    try:
        # Expanded fusion options
        fusions = [
            {"name": "sci-fi + thriller", "genres": ["Science Fiction", "Thriller"], "description": "Futuristic suspense"},
            {"name": "comedy + crime", "genres": ["Comedy", "Crime"], "description": "Funny heist and detective stories"},
            {"name": "romance + adventure", "genres": ["Romance", "Adventure"], "description": "Epic love stories"},
            {"name": "drama + mystery", "genres": ["Drama", "Mystery"], "description": "Emotional whodunits"},
            {"name": "horror + comedy", "genres": ["Horror", "Comedy"], "description": "Scary-funny mashups"},
            {"name": "action + comedy", "genres": ["Action", "Comedy"], "description": "Action-packed laughs"},
            {"name": "sci-fi + horror", "genres": ["Science Fiction", "Horror"], "description": "Terrifying futures"},
            {"name": "fantasy + adventure", "genres": ["Fantasy", "Adventure"], "description": "Magical quests"},
            {"name": "crime + thriller", "genres": ["Crime", "Thriller"], "description": "Intense criminal plots"},
            {"name": "romance + comedy", "genres": ["Romance", "Comedy"], "description": "Romantic comedies"},
            {"name": "war + drama", "genres": ["War", "Drama"], "description": "Powerful war stories"},
            {"name": "western + action", "genres": ["Western", "Action"], "description": "Wild west shootouts"},
        ]
        
        return {
            "fusions": [f["name"] for f in fusions],
            "descriptions": fusions,
            "count": len(fusions)
        }
    except Exception as e:
        logger.error(f"Failed to fetch fusions: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch fusions: {str(e)}")


@router.get("/options/all")
async def get_all_options() -> Dict[str, Any]:
    """
    Get all available metadata options in one consolidated response.
    Useful for frontend initialization.
    """
    try:
        genres = await get_available_genres()
        languages = await get_available_languages()
        moods = await get_available_moods()
        themes = await get_available_themes()
        fusions = await get_available_fusions()
        
        return {
            "genres": genres,
            "languages": languages,
            "moods": moods,
            "themes": themes,
            "fusions": fusions
        }
    except Exception as e:
        logger.error(f"Failed to fetch all options: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch all options: {str(e)}")
