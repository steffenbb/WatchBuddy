"""
query_enhancer.py

Fast query enhancement for natural language search.
Extracts mood, genre, people, and theme keywords without LLM.
"""
import re
import logging
from typing import Dict, List, Set, Optional

logger = logging.getLogger(__name__)

# Mood keywords mapping
MOOD_KEYWORDS = {
    'dark': ['dark', 'bleak', 'grim', 'noir', 'disturbing', 'sinister', 'gritty'],
    'light': ['light', 'fun', 'cheerful', 'upbeat', 'bright', 'lighthearted', 'feel-good', 'feelgood'],
    'tense': ['tense', 'suspenseful', 'gripping', 'intense', 'edge-of-seat', 'nail-biting'],
    'atmospheric': ['atmospheric', 'moody', 'ambient', 'ethereal', 'dreamy', 'surreal'],
    'cerebral': ['cerebral', 'intellectual', 'thought-provoking', 'philosophical', 'complex'],
    'emotional': ['emotional', 'moving', 'touching', 'heartfelt', 'poignant', 'tearjerker'],
    'funny': ['funny', 'hilarious', 'comedy', 'humorous', 'witty', 'laugh'],
    'scary': ['scary', 'terrifying', 'frightening', 'horror', 'creepy', 'eerie']
}

# Theme keywords
THEME_KEYWORDS = {
    'psychological': ['psychological', 'psycho', 'mind', 'mental'],
    'crime': ['crime', 'criminal', 'heist', 'theft', 'detective', 'investigation'],
    'family': ['family', 'family-drama', 'familial', 'domestic'],
    'romance': ['romantic', 'love', 'relationship'],
    'action': ['action', 'explosive', 'adrenaline'],
    'mystery': ['mystery', 'whodunit', 'enigma', 'puzzle'],
    'sci-fi': ['sci-fi', 'science-fiction', 'futuristic', 'space', 'cyberpunk'],
    'fantasy': ['fantasy', 'magical', 'mystical'],
    'historical': ['historical', 'period', 'costume'],
    'war': ['war', 'military', 'combat', 'battlefield'],
    'western': ['western', 'cowboy', 'frontier'],
    'political': ['political', 'politics', 'conspiracy'],
    'survival': ['survival', 'apocalypse', 'post-apocalyptic'],
}

# Regional keywords
REGION_KEYWORDS = {
    'nordic': ['nordic', 'scandinavian', 'scandi', 'danish', 'swedish', 'norwegian', 'icelandic'],
    'asian': ['asian', 'japanese', 'korean', 'chinese', 'taiwanese'],
    'european': ['european', 'french', 'german', 'italian', 'spanish'],
    'british': ['british', 'uk', 'english', 'bbc'],
    'american': ['american', 'usa', 'hollywood'],
}

# Common actor/director patterns
PEOPLE_PATTERNS = [
    # Common patterns for people names
    r'\b([A-Z][a-z]+\s[A-Z][a-z]+)\b',  # "Christopher Nolan"
    r'\b(mads\smikkelsen)\b',
    r'\b(bong\sjoon-ho)\b',
    r'\b(park\schan-wook)\b',
    r'\b(denis\svilleneuve)\b',
    r'\b(david\sfincher)\b',
]


class QueryEnhancer:
    """Fast query enhancement using keyword extraction."""
    
    def __init__(self):
        # Pre-compile patterns
        self.people_regex = [re.compile(p, re.IGNORECASE) for p in PEOPLE_PATTERNS]
        
        # Build reverse lookup for fast matching
        self.mood_lookup = self._build_lookup(MOOD_KEYWORDS)
        self.theme_lookup = self._build_lookup(THEME_KEYWORDS)
        self.region_lookup = self._build_lookup(REGION_KEYWORDS)
    
    def _build_lookup(self, keyword_map: Dict[str, List[str]]) -> Dict[str, str]:
        """Build reverse lookup from keywords to categories."""
        lookup = {}
        for category, keywords in keyword_map.items():
            for kw in keywords:
                lookup[kw.lower()] = category
        return lookup
    
    def enhance(self, query: str) -> Dict[str, any]:
        """
        Extract structured information from natural language query.
        
        Args:
            query: Search query string
            
        Returns:
            Dict with extracted moods, themes, regions, people, and cleaned query
        """
        if not query:
            return {
                'original': query,
                'cleaned': query,
                'moods': [],
                'themes': [],
                'regions': [],
                'people': [],
                'media_type': None
            }
        
        query_lower = query.lower()
        words = set(re.findall(r'\b\w+\b', query_lower))
        
        # Extract moods
        moods = set()
        for word in words:
            if word in self.mood_lookup:
                moods.add(self.mood_lookup[word])
        
        # Extract themes
        themes = set()
        for word in words:
            if word in self.theme_lookup:
                themes.add(self.theme_lookup[word])
        
        # Extract regions
        regions = set()
        for word in words:
            if word in self.region_lookup:
                regions.add(self.region_lookup[word])
        
        # Extract people (actor/director names)
        people = set()
        for pattern in self.people_regex:
            matches = pattern.findall(query)
            people.update(matches)
        
        # Detect media type preference
        media_type = None
        if 'movie' in query_lower or 'film' in query_lower:
            media_type = 'movie'
        elif 'show' in query_lower or 'series' in query_lower or 'tv' in query_lower:
            media_type = 'show'
        
        # Clean query by removing extracted keywords
        cleaned = query
        for mood_kw in self.mood_lookup.keys():
            cleaned = re.sub(r'\b' + re.escape(mood_kw) + r'\b', '', cleaned, flags=re.IGNORECASE)
        for region_kw in self.region_lookup.keys():
            cleaned = re.sub(r'\b' + re.escape(region_kw) + r'\b', '', cleaned, flags=re.IGNORECASE)
        
        # Remove multiple spaces
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        
        result = {
            'original': query,
            'cleaned_query': cleaned if cleaned else query,  # Fallback to original if everything removed
            'moods': sorted(list(moods)),
            'themes': sorted(list(themes)),
            'regions': sorted(list(regions)),
            'people': sorted(list(people)),
            'media_type': media_type
        }
        
        logger.debug(f"Query enhancement: {result}")
        return result
    
    def build_es_filters(self, enhanced: Dict[str, any]) -> Dict[str, any]:
        """
        Build ElasticSearch filters from enhanced query.
        
        Returns dict with 'must' and 'should' clauses for ES query.
        """
        must = []
        should = []
        
        # Media type filter (hard filter)
        if enhanced.get('media_type'):
            must.append({
                'term': {'media_type': enhanced['media_type']}
            })
        
        # Mood boosting (soft filter)
        for mood in enhanced.get('moods', []):
            should.append({
                'match': {
                    'mood_tags': {
                        'query': mood,
                        'boost': 2.5
                    }
                }
            })
        
        # Theme boosting (soft filter)
        for theme in enhanced.get('themes', []):
            should.append({
                'match': {
                    'themes': {
                        'query': theme,
                        'boost': 2.0
                    }
                }
            })
        
        # Region boosting (soft filter)
        for region in enhanced.get('regions', []):
            should.append({
                'match': {
                    'production_countries': {
                        'query': region,
                        'boost': 1.5
                    }
                }
            })
            should.append({
                'match': {
                    'spoken_languages': {
                        'query': region,
                        'boost': 1.5
                    }
                }
            })
        
        # People boosting (high priority)
        for person in enhanced.get('people', []):
            should.append({
                'match': {
                    'cast': {
                        'query': person,
                        'boost': 3.0
                    }
                }
            })
            should.append({
                'match': {
                    'created_by': {
                        'query': person,
                        'boost': 3.0
                    }
                }
            })
        
        # Return just the should clauses (boost filters)
        # Must clauses (media_type) already handled by caller
        return should


# Singleton instance
_enhancer = None

def get_query_enhancer() -> QueryEnhancer:
    """Get or create singleton QueryEnhancer instance."""
    global _enhancer
    if _enhancer is None:
        _enhancer = QueryEnhancer()
    return _enhancer
