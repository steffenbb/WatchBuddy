"""
metadata_processing.py (AI Engine)
- Compose candidate text from PersistentCandidate fields for embedding/TF-IDF.
"""
import json
from typing import Dict, Any, List, Optional
import re


def normalize_prompt(prompt: str) -> str:
    """Normalize prompt while preserving semantic meaning.
    
    Note: Lowercase conversion happens AFTER entity extraction in parse_prompt,
    so we preserve capitalization here for proper name recognition.
    """
    # Preserve original case for entity extraction (done in parser.py)
    # Only strip whitespace and collapse multiple spaces
    prompt = prompt.strip()
    prompt = re.sub(r"\s+", " ", prompt)
    # Convert to lowercase for matching (case-insensitive)
    prompt = prompt.lower()
    # Remove only truly disruptive punctuation, keep apostrophes for contractions
    prompt = re.sub(r"[^\w\s.,!?\-']", "", prompt)
    return prompt


def compose_text_for_embedding(candidate: Dict[str, Any], extra_fields: Optional[List[str]] = None) -> str:
    field_names = [
        # Core
        "title", "original_title", "overview", "tagline", "media_type",
        # Taxonomy
        "genres", "keywords", "production_companies", "production_countries", "spoken_languages",
        # Credits
        "cast", "director", "writers", "created_by",
        # Dates & runtime
        "year", "release_date", "runtime", "status",
        # TV-specific
        "networks", "number_of_seasons", "number_of_episodes", "episode_run_time",
        "first_air_date", "last_air_date", "in_production",
        # Popularity & ratings
        "popularity", "vote_average", "vote_count",
        # Financials
        "revenue", "budget",
        # Locale
        "language",
        # Additional metadata
        "homepage",
    ]
    parts: List[str] = []
    for name in field_names:
        val = candidate.get(name, "")
        # Handle JSON array fields
        if name in ("genres", "keywords", "production_companies", "production_countries", 
                    "spoken_languages", "cast", "writers", "created_by", "networks", "episode_run_time"):
            try:
                if isinstance(val, str):
                    val = ", ".join(json.loads(val)) if val else ""
                elif isinstance(val, list):
                    val = ", ".join(str(v) for v in val)
            except Exception:
                pass
        # Convert boolean to string for TV production status
        if name == "in_production" and val is not None:
            val = "Currently in production" if val else "Series completed"
        parts.append(str(val))
    if extra_fields:
        for f in extra_fields:
            parts.append(str(candidate.get(f, "")))
    return ". ".join([p for p in parts if p]).strip()
