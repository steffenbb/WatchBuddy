from fastapi import APIRouter, HTTPException, Body
from typing import Optional, Dict, Any, List
from app.core.database import SessionLocal
from app.models import UserList, ListItem, PersistentCandidate
from app.services.scoring_engine import ScoringEngine
from app.utils.timezone import utc_now
import json
import logging
import re

router = APIRouter()
logger = logging.getLogger(__name__)

# Import the mood keyword mapping from scoring engine to ensure consistency
MOOD_KEYWORD_MAPPING = {
    # Cozy/comfort moods
    "cozy": {"happy": 0.6, "thoughtful": 0.3, "romantic": 0.1},
    "comfort": {"happy": 0.6, "thoughtful": 0.3, "romantic": 0.1},
    "feel-good": {"happy": 0.9, "excited": 0.1},
    "feel good": {"happy": 0.9, "excited": 0.1},
    "feelgood": {"happy": 0.9, "excited": 0.1},
    "uplifting": {"happy": 0.8, "excited": 0.2},
    "heartwarming": {"happy": 0.7, "romantic": 0.3},
    
    # Dark/intense moods
    "dark": {"tense": 0.6, "scared": 0.3, "thoughtful": 0.1},
    "intense": {"tense": 0.7, "excited": 0.3},
    "gritty": {"tense": 0.7, "sad": 0.3},
    "serious": {"thoughtful": 0.7, "tense": 0.3},
    
    # Exciting moods
    "exciting": {"excited": 0.9, "happy": 0.1},
    "thrilling": {"excited": 0.8, "tense": 0.2},
    "action-packed": {"excited": 0.9, "tense": 0.1},
    "adventurous": {"excited": 0.7, "curious": 0.3},
    
    # Scary moods
    "scary": {"scared": 0.9, "tense": 0.1},
    "horror": {"scared": 0.9, "tense": 0.1},
    "creepy": {"scared": 0.8, "tense": 0.2},
    
    # Funny moods
    "funny": {"happy": 0.9, "excited": 0.1},
    "hilarious": {"happy": 0.95, "excited": 0.05},
    "comedy": {"happy": 0.9, "excited": 0.1},
    "lighthearted": {"happy": 0.8, "thoughtful": 0.2},
    
    # Romantic moods
    "romantic": {"romantic": 0.9, "happy": 0.1},
    "love": {"romantic": 0.8, "happy": 0.2},
    "passionate": {"romantic": 0.7, "excited": 0.3},
    
    # Thoughtful moods
    "thoughtful": {"thoughtful": 0.9, "curious": 0.1},
    "contemplative": {"thoughtful": 0.8, "sad": 0.2},
    "philosophical": {"thoughtful": 0.9, "curious": 0.1},
    "cerebral": {"thoughtful": 0.8, "curious": 0.2},
    
    # Sad moods
    "sad": {"sad": 0.9, "thoughtful": 0.1},
    "melancholic": {"sad": 0.8, "thoughtful": 0.2},
    "tragic": {"sad": 0.9, "thoughtful": 0.1},
    "emotional": {"sad": 0.6, "romantic": 0.2, "thoughtful": 0.2},
}


def generate_dynamic_title(filters: Dict[str, Any], prompt: str) -> str:
    """
    Generate a descriptive title based on parsed filters rather than using raw prompt.
    """
    parts = []
    
    # Add mood/feeling
    if "mood" in filters and filters["mood"]:
        mood_str = ", ".join(filters["mood"]).title()
        parts.append(mood_str)
    
    # Add genres
    if "genres" in filters and filters["genres"]:
        genre_str = " & ".join(filters["genres"][:3])  # Max 3 genres
        parts.append(genre_str)
    
    # Add media type
    media_types = filters.get("media_types", [])
    if media_types:
        if media_types == ["movie"]:
            parts.append("Movies")
        elif media_types == ["show"]:
            parts.append("Shows")
        else:
            parts.append("Movies & Shows")
    
    # Add anchor reference if present
    if "similar_to_title" in filters:
        parts.append(f"like {filters['similar_to_title'].title()}")
    
    # Add year constraint
    year_from = filters.get("year_from")
    year_to = filters.get("year_to")
    if year_from and year_to:
        parts.append(f"({year_from}-{year_to})")
    elif year_from:
        parts.append(f"(from {year_from})")
    elif year_to:
        parts.append(f"(until {year_to})")
    
    # Add language
    if "languages" in filters and filters["languages"]:
        lang_str = ", ".join(filters["languages"]).upper()
        parts.append(f"[{lang_str}]")
    
    # Build final title
    if parts:
        title = " ".join(parts)
        # Limit to 100 chars
        if len(title) > 100:
            title = title[:97] + "..."
        return title
    else:
        # Fallback to prompt
        return prompt[:100] if len(prompt) <= 100 else prompt[:97] + "..."


def parse_chat_prompt(prompt: str) -> Dict[str, Any]:
    """
    Parse natural language prompt into structured filters.
    Enhanced to use metadata_options for validation.
    """
    logger.debug(f"Parsing chat prompt: {prompt}")
    filters = {}
    
    # Genre extraction (e.g. "thriller", "comedy", "comedies", "action movies")
    # First try explicit "genre:" syntax
    genre_match = re.findall(r"genre[s]?:?\s*([\w\s,&+-]+)", prompt, re.I)
    genres = []
    if genre_match:
        raw_genres = [g.strip() for g in genre_match[0].split(",") if g.strip()]
        for g in raw_genres:
            g_lower = g.lower()
            # Map common variations
            if g_lower in ['sci-fi', 'scifi', 'science fiction']:
                genres.append('Science Fiction')
            elif g_lower == 'romcom':
                genres.extend(['Romance', 'Comedy'])
            else:
                # Capitalize first letter of each word
                genres.append(g.title())
    
    # Also detect genre keywords directly in text (more flexible)
    prompt_lower = prompt.lower()
    genre_keywords = {
        # Comedy variations
        'comedy': 'Comedy', 'comedies': 'Comedy', 'funny': 'Comedy', 'hilarious': 'Comedy', 'comic': 'Comedy',
        'romcom': ['Romance', 'Comedy'], 'rom-com': ['Romance', 'Comedy'], 'romantic comedy': ['Romance', 'Comedy'],
        
        # Action variations
        'action': 'Action', 
        
        # Thriller variations
        'thriller': 'Thriller', 'thrillers': 'Thriller', 'suspense': 'Thriller', 'suspenseful': 'Thriller',
        
        # Drama variations
        'drama': 'Drama', 'dramas': 'Drama', 'dramatic': 'Drama',
        
        # Horror variations
        'horror': 'Horror', 'scary': 'Horror', 'terrifying': 'Horror',
        
        # Romance variations
        'romance': 'Romance', 'romantic': 'Romance', 'love story': 'Romance', 'love stories': 'Romance',
        
        # Sci-Fi variations
        'sci-fi': 'Science Fiction', 'scifi': 'Science Fiction', 'sci fi': 'Science Fiction',
        'science fiction': 'Science Fiction', 'sf': 'Science Fiction',
        
        # Fantasy variations
        'fantasy': 'Fantasy', 'fantasies': 'Fantasy', 'fantastical': 'Fantasy',
        
        # Crime variations
        'crime': 'Crime', 'crimes': 'Crime', 'criminal': 'Crime', 'heist': 'Crime',
        
        # Mystery variations
        'mystery': 'Mystery', 'mysteries': 'Mystery', 'whodunit': 'Mystery', 'detective': 'Mystery',
        
        # Adventure variations
        'adventure': 'Adventure', 'adventures': 'Adventure', 'adventurous': 'Adventure',
        
        # Animation variations
        'animation': 'Animation', 'animated': 'Animation', 'anime': 'Animation', 'cartoon': 'Animation',
        
        # Documentary variations
        'documentary': 'Documentary', 'documentaries': 'Documentary', 'docu': 'Documentary', 'docuseries': 'Documentary',
        
        # Western variations
        'western': 'Western', 'westerns': 'Western', 'cowboy': 'Western',
        
        # War variations
        'war': 'War', 'wartime': 'War', 'military': 'War',
        
        # Musical variations
        'musical': 'Music', 'musicals': 'Music',
        
        # Additional genres
        'biography': 'Biography', 'biopic': 'Biography', 'biographical': 'Biography',
        'historical': 'History', 'history': 'History',
        'family': 'Family', 'kids': 'Family', 'children': 'Family',
        'sport': 'Sport', 'sports': 'Sport',
    }
    
    for keyword, genre_value in genre_keywords.items():
        # Use word boundaries to avoid false matches (e.g., "fact" matching "action")
        if re.search(rf'\b{re.escape(keyword)}\b', prompt_lower):
            # Handle cases where genre_value is a list (e.g., romcom -> [Romance, Comedy])
            if isinstance(genre_value, list):
                for g in genre_value:
                    if g not in genres:
                        genres.append(g)
            else:
                if genre_value not in genres:
                    genres.append(genre_value)
    
    if genres:
        filters["genres"] = genres
    
    # Language extraction (e.g. "in Danish", "language: da")
    lang_match = re.findall(r"language[s]?:?\s*([\w,\s]+)|in\s+([A-Za-z]+)", prompt, re.I)
    langs = []
    for m in lang_match:
        for l in m:
            if l:
                langs += [x.strip().lower() for x in l.split(",") if x.strip()]
    if langs:
        # Map language names to codes
        lang_map = {
            'danish': 'da', 'english': 'en', 'swedish': 'sv', 'norwegian': 'no',
            'german': 'de', 'french': 'fr', 'spanish': 'es', 'italian': 'it'
        }
        filters["languages"] = [lang_map.get(l.lower(), l) for l in langs]
    
    # Year extraction (e.g. "from 2010 to 2020" or "after 2000")
    year_match = re.findall(r"from (\d{4})\s+to\s+(\d{4})", prompt)
    if year_match:
        filters["year_from"] = int(year_match[0][0])
        filters["year_to"] = int(year_match[0][1])
    after_match = re.findall(r"after (\d{4})", prompt)
    if after_match:
        filters["year_from"] = int(after_match[0])
    before_match = re.findall(r"before (\d{4})", prompt)
    if before_match:
        filters["year_to"] = int(before_match[0])
    
    # Mood extraction (e.g. "mood: cozy", "uplifting content")
    mood_match = re.findall(r"mood:?\s*([\w,\s\-]+)", prompt, re.I)
    if mood_match:
        raw_moods = [m.strip().lower() for m in mood_match[0].split(",") if m.strip()]
        filters["mood"] = raw_moods
    
    # Additional mood keywords - use all keywords from MOOD_KEYWORD_MAPPING for consistency
    # This handles mood words appearing naturally in the prompt (not after "mood:")
    prompt_lower = prompt.lower()
    for mood_word in MOOD_KEYWORD_MAPPING.keys():
        # Use word boundaries to avoid false matches
        # Also try without hyphens (e.g., "feel-good" vs "feel good")
        mood_pattern = mood_word.replace('-', r'[\s\-]?')  # Allow hyphen or space or neither
        if re.search(rf'\b{mood_pattern}\b', prompt_lower):
            # Normalize the mood word for storage
            normalized_mood = mood_word.replace(' ', '-').replace('--', '-')  # "feel good" -> "feel-good"
            if normalized_mood not in filters.get("mood", []):
                filters.setdefault("mood", []).append(normalized_mood)
    
    # Similar to / like anchor extraction (e.g. "similar to The Dark Knight", "movies like Inception")
    # Try multiple patterns
    similar_patterns = [
        r"similar to ([a-zA-Z0-9\s\-:'.]+?)(?:,|\.|;| but| and| prefer| after| before|$)",
        r"like ([a-zA-Z0-9\s\-:'.]+?)(?:,|\.|;| but| and| prefer| after| before|$)",
        r"as good as ([a-zA-Z0-9\s\-:'.]+?)(?:,|\.|;| but| and| prefer| after| before|$)",
    ]
    for pattern in similar_patterns:
        similar_match = re.findall(pattern, prompt, re.I)
        if similar_match:
            # Clean up the match - remove common connecting words at the end
            anchor = similar_match[0].strip()
            # Remove trailing "the", "a", "an" if they're artifacts
            anchor = re.sub(r'\s+(the|a|an)$', '', anchor, flags=re.I)
            filters["similar_to_title"] = anchor
            break
    
    # Media type extraction (e.g. "movies", "shows", "TV shows")
    media_types = []
    prompt_lower = prompt.lower()
    # Check for explicit mentions
    if re.search(r'\bmovies?\b', prompt_lower) and not re.search(r'\bshows?\b|\btv\b|\bseries\b', prompt_lower):
        media_types = ['movie']
    elif re.search(r'\bshows?\b|\btv shows?\b|\bseries\b|\btv series\b', prompt_lower) and not re.search(r'\bmovies?\b', prompt_lower):
        media_types = ['show']
    elif re.search(r'\bmovies?\b', prompt_lower) and re.search(r'\bshows?\b|\btv\b|\bseries\b', prompt_lower):
        media_types = ['movie', 'show']  # Both mentioned
    # If nothing mentioned, default to both
    if media_types:
        filters["media_types"] = media_types
    
    # Discovery/obscurity extraction - user preference for popular vs obscure content
    # Default to mainstream/popular for chat lists to get well-known recommendations
    discovery = "mainstream"  # Default
    
    # Obscure/hidden gem indicators
    if re.search(r'\b(obscure|hidden gem|under the radar|less known|unknown|indie|independent|undiscovered|lesser known|deep cut)\b', prompt_lower):
        discovery = "obscure"
    elif re.search(r'\bvery (obscure|unknown|indie)\b', prompt_lower):
        discovery = "very_obscure"
    # Popular/mainstream indicators (explicit override)
    elif re.search(r'\b(popular|mainstream|well known|well-known|famous|blockbuster|hit|big)\b', prompt_lower):
        discovery = "mainstream"
    # Balanced approach
    elif re.search(r'\b(balanced|mix|variety|diverse)\b', prompt_lower):
        discovery = "balanced"
    
    filters["discovery"] = discovery
    logger.info(f"[CHAT_PROMPT] Detected discovery mode: {discovery}")
    
    # Search keywords (fallback)
    filters["search_query"] = prompt
    
    return filters


@router.post("/chat/parse-prompt")
async def chat_parse_prompt(prompt: str = Body(..., embed=True)):
    """Parse a natural language prompt into structured filters."""
    logger.info(f"Received chat prompt for parsing: {prompt}")
    filters = parse_chat_prompt(prompt)
    logger.debug(f"Parsed prompt result: {filters}")
    return {"filters": filters}


@router.post("/chat/generate-list")
async def chat_generate_list(
    prompt: str = Body(..., embed=True),
    user_id: int = Body(1, embed=True),
    item_limit: int = Body(30, embed=True)
):
    """
    Generate a new chat-based list from a natural language prompt.
    Uses persistent candidates and advanced scoring with semantic anchoring.
    """
    logger.info(f"Generating chat list for prompt: {prompt}")
    db = SessionLocal()
    try:
        filters = parse_chat_prompt(prompt)
        logger.debug(f"Parsed filters for chat list: {filters}")
        
        # Query persistent candidates only
        q = db.query(PersistentCandidate).filter(PersistentCandidate.trakt_id.isnot(None))
        
        # Apply media type filter if specified
        if "media_types" in filters and filters["media_types"]:
            q = q.filter(PersistentCandidate.media_type.in_(filters["media_types"]))
        
        if "genres" in filters and filters["genres"]:
            for g in filters["genres"]:
                q = q.filter(PersistentCandidate.genres.ilike(f"%{g}%"))
        
        if "languages" in filters and filters["languages"]:
            q = q.filter(PersistentCandidate.language.in_(filters["languages"]))
        
        if "year_from" in filters:
            q = q.filter(PersistentCandidate.year >= filters["year_from"])
        
        if "year_to" in filters:
            q = q.filter(PersistentCandidate.year <= filters["year_to"])
        
        # NEW: Actor filtering - check if any actor appears in cast field (JSON array)
        if "actors" in filters and filters["actors"]:
            # Build SQL pattern for each actor (case-insensitive substring match in JSON array)
            for actor in filters["actors"]:
                q = q.filter(PersistentCandidate.cast.ilike(f"%{actor}%"))
        
        # NEW: Studio filtering - check if any studio appears in production_companies field
        if "studios" in filters and filters["studios"]:
            for studio in filters["studios"]:
                q = q.filter(PersistentCandidate.production_companies.ilike(f"%{studio}%"))
        
        # Query all matching candidates from the full persistent DB (no limit)
        candidates = q.all()
        logger.info(f"Fetched {len(candidates)} persistent candidates for chat list generation")
        
        if not candidates:
            raise HTTPException(status_code=404, detail="No candidates found matching prompt filters")
        
        # Convert to dicts for scoring engine
        candidate_dicts = []
        for c in candidates:
            genres = []
            cast = []
            production_companies = []
            try:
                if c.genres:
                    genres = json.loads(c.genres) if isinstance(c.genres, str) else c.genres
                if c.cast:
                    cast = json.loads(c.cast) if isinstance(c.cast, str) else c.cast
                if c.production_companies:
                    production_companies = json.loads(c.production_companies) if isinstance(c.production_companies, str) else c.production_companies
            except:
                pass
            
            candidate_dicts.append({
                'ids': {'trakt': c.trakt_id, 'tmdb': c.tmdb_id},
                'type': c.media_type,
                'trakt_id': c.trakt_id,
                'tmdb_id': c.tmdb_id,
                'title': c.title,
                'year': c.year,
                'rating': c.vote_average,
                'votes': c.vote_count,
                'genres': genres,
                'cast': cast,
                'production_companies': production_companies,
                'overview': c.overview,
                'popularity': c.popularity,
                'language': c.language,
                'obscurity_score': c.obscurity_score,
                'mainstream_score': c.mainstream_score,
                'freshness_score': c.freshness_score,
            })
        
        # If 'similar_to_title' anchor is present, boost semantic similarity
        anchor_title = filters.get("similar_to_title")
        anchor_text = None
        if anchor_title:
            # Find candidate with matching/similar title
            anchor_cand = next((c for c in candidates if anchor_title.lower() in (c.title or '').lower()), None)
            if anchor_cand:
                anchor_text = anchor_cand.title + " " + (anchor_cand.overview or "")
                logger.info(f"Found semantic anchor: {anchor_cand.title}")
            else:
                anchor_text = anchor_title
                logger.info(f"Using prompt-provided anchor: {anchor_title}")
        
        # Score candidates (now with actors/studios in filters)
        scoring_engine = ScoringEngine()
        user_ctx = {"id": user_id}
        scored = scoring_engine.score_candidates(
            user_ctx,
            candidate_dicts,
            list_type="chat",
            item_limit=item_limit,
            filters=filters,
            semantic_anchor=anchor_text
        )
        logger.info(f"Scored {len(scored)} candidates for chat list")
        
        # Build top items
        top_items = []
        for s in scored[:item_limit]:
            tid = s.get('trakt_id')
            mtype = s.get('media_type')
            if not tid or not mtype:
                continue
            explanation = s.get('explanation_text') or f"Matched prompt: {prompt}"
            if anchor_title and s.get('components', {}).get('semantic_sim', 0) > 0.3:
                explanation += f"; similar to {anchor_title}"
            top_items.append({
                'trakt_id': tid,
                'media_type': mtype,
                'title': s.get('title'),
                'score': s.get('final_score', 0.0),
                'explanation': explanation
            })
        
        # Create UserList and ListItems with dynamic title
        dynamic_title = generate_dynamic_title(filters, prompt)
        user_list = UserList(
            user_id=user_id,
            title=dynamic_title,
            filters=json.dumps(filters),
            list_type="chat",
            item_limit=item_limit,
            sync_status="created",
            created_at=utc_now()
        )
        db.add(user_list)
        db.commit()
        db.refresh(user_list)
        logger.info(f"Created UserList with id={user_list.id}, title='{user_list.title}'")
        
        for c in top_items:
            li = ListItem(
                smartlist_id=user_list.id,
                item_id=str(c['trakt_id']),
                title=c.get('title'),
                score=c.get('score', 0.0),
                trakt_id=c['trakt_id'],
                media_type=c['media_type'],
                explanation=c.get('explanation'),
                added_at=utc_now()
            )
            db.add(li)
        db.commit()
        logger.info(f"Inserted {len(top_items)} ListItems for UserList id={user_list.id}")
        
        return {
            "id": user_list.id,
            "title": user_list.title,
            "filters": filters,
            "item_count": len(top_items),
            "items": top_items
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to generate chat list: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to generate chat list: {str(e)}")
    finally:
        db.close()
