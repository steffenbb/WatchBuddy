"""
mood_extractor.py

Extract mood, tone, and theme tags from ItemLLMProfile for ElasticSearch indexing.
Fast keyword-based extraction without LLM inference.
"""
import re
import logging
from typing import List, Set
from app.models import ItemLLMProfile

logger = logging.getLogger(__name__)

# Mood indicators (from ItemLLMProfile text)
MOOD_INDICATORS = {
    'dark': ['dark', 'bleak', 'grim', 'noir', 'disturbing', 'sinister', 'gritty', 'somber', 'brooding'],
    'light': ['light', 'fun', 'cheerful', 'upbeat', 'bright', 'lighthearted', 'whimsical', 'playful'],
    'tense': ['tense', 'suspenseful', 'gripping', 'intense', 'edge-of-your-seat', 'nail-biting', 'thrilling'],
    'atmospheric': ['atmospheric', 'moody', 'ambient', 'ethereal', 'dreamy', 'surreal', 'haunting', 'evocative'],
    'cerebral': ['cerebral', 'intellectual', 'thought-provoking', 'philosophical', 'complex', 'intricate'],
    'emotional': ['emotional', 'moving', 'touching', 'heartfelt', 'poignant', 'tearjerker', 'sentimental'],
    'funny': ['funny', 'hilarious', 'comedic', 'humorous', 'witty', 'laugh-out-loud'],
    'scary': ['scary', 'terrifying', 'frightening', 'horrifying', 'creepy', 'eerie', 'chilling'],
    'epic': ['epic', 'grand', 'sweeping', 'vast', 'monumental', 'spectacular'],
    'intimate': ['intimate', 'personal', 'introspective', 'quiet', 'subtle', 'nuanced'],
}

# Tone indicators
TONE_INDICATORS = {
    'serious': ['serious', 'grave', 'solemn', 'dramatic', 'weighty'],
    'satirical': ['satirical', 'satire', 'satirizes', 'parody', 'ironic'],
    'comedic': ['comedic', 'comic', 'humorous', 'funny', 'lighthearted'],
    'romantic': ['romantic', 'romance', 'love', 'passionate'],
    'melancholic': ['melancholic', 'melancholy', 'sad', 'wistful', 'bittersweet'],
    'hopeful': ['hopeful', 'optimistic', 'uplifting', 'inspiring'],
    'cynical': ['cynical', 'pessimistic', 'nihilistic', 'bleak'],
    'whimsical': ['whimsical', 'fanciful', 'quirky', 'offbeat'],
}

# Theme indicators
THEME_INDICATORS = {
    'psychological': ['psychological', 'psyche', 'mental', 'mind', 'consciousness'],
    'crime': ['crime', 'criminal', 'heist', 'theft', 'detective', 'investigation', 'murder'],
    'family': ['family', 'familial', 'domestic', 'parental', 'sibling', 'family-drama'],
    'romance': ['romantic', 'love', 'relationship', 'affair'],
    'revenge': ['revenge', 'vengeance', 'retribution', 'payback'],
    'survival': ['survival', 'survive', 'apocalypse', 'post-apocalyptic'],
    'redemption': ['redemption', 'redemptive', 'forgiveness', 'atonement'],
    'identity': ['identity', 'self-discovery', 'transformation', 'coming-of-age'],
    'power': ['power', 'corruption', 'politics', 'control', 'authority'],
    'morality': ['moral', 'morality', 'ethics', 'conscience', 'right-and-wrong'],
    'class': ['class', 'wealth', 'poverty', 'inequality', 'social-commentary'],
    'isolation': ['isolation', 'loneliness', 'solitude', 'alienation'],
    'obsession': ['obsession', 'obsessive', 'compulsion', 'fixation'],
}


class MoodExtractor:
    """Extract mood, tone, and theme tags from ItemLLMProfile summaries."""
    
    def __init__(self):
        # Build reverse lookups for fast matching
        self.mood_lookup = self._build_lookup(MOOD_INDICATORS)
        self.tone_lookup = self._build_lookup(TONE_INDICATORS)
        self.theme_lookup = self._build_lookup(THEME_INDICATORS)
    
    def _build_lookup(self, indicator_map: dict) -> dict:
        """Build reverse lookup from keywords to categories."""
        lookup = {}
        for category, keywords in indicator_map.items():
            for kw in keywords:
                lookup[kw.lower()] = category
        return lookup
    
    def extract_from_profile(self, item_profile: ItemLLMProfile) -> dict:
        """
        Extract mood, tone, and theme tags from ItemLLMProfile.
        
        Args:
            item_profile: ItemLLMProfile object with summary_text
            
        Returns:
            Dict with mood_tags, tone_tags, themes lists
        """
        if not item_profile or not item_profile.summary_text:
            return {
                'mood_tags': [],
                'tone_tags': [],
                'themes': []
            }
        
        text = item_profile.summary_text.lower()
        
        # Extract words (preserve hyphens for compound terms)
        words = set(re.findall(r'\b[\w-]+\b', text))
        
        # Match moods
        moods = set()
        for word in words:
            # Try direct match
            if word in self.mood_lookup:
                moods.add(self.mood_lookup[word])
            # Try without hyphens
            elif word.replace('-', '') in self.mood_lookup:
                moods.add(self.mood_lookup[word.replace('-', '')])
        
        # Match tones
        tones = set()
        for word in words:
            if word in self.tone_lookup:
                tones.add(self.tone_lookup[word])
            elif word.replace('-', '') in self.tone_lookup:
                tones.add(self.tone_lookup[word.replace('-', '')])
        
        # Match themes
        themes = set()
        for word in words:
            if word in self.theme_lookup:
                themes.add(self.theme_lookup[word])
            elif word.replace('-', '') in self.theme_lookup:
                themes.add(self.theme_lookup[word.replace('-', '')])
        
        result = {
            'mood_tags': sorted(list(moods)),
            'tone_tags': sorted(list(tones)),
            'themes': sorted(list(themes))
        }
        
        logger.debug(f"Extracted from profile: {result}")
        return result
    
    def extract_from_text(self, text: str) -> dict:
        """
        Extract tags from raw text (for candidates without ItemLLMProfile).
        Uses overview + genres as fallback.
        """
        if not text:
            return {
                'mood_tags': [],
                'tone_tags': [],
                'themes': []
            }
        
        text_lower = text.lower()
        words = set(re.findall(r'\b[\w-]+\b', text_lower))
        
        moods = set()
        tones = set()
        themes = set()
        
        for word in words:
            if word in self.mood_lookup:
                moods.add(self.mood_lookup[word])
            if word in self.tone_lookup:
                tones.add(self.tone_lookup[word])
            if word in self.theme_lookup:
                themes.add(self.theme_lookup[word])
        
        return {
            'mood_tags': sorted(list(moods)),
            'tone_tags': sorted(list(tones)),
            'themes': sorted(list(themes))
        }


# Singleton instance
_extractor = None

def get_mood_extractor() -> MoodExtractor:
    """Get or create singleton MoodExtractor instance."""
    global _extractor
    if _extractor is None:
        _extractor = MoodExtractor()
    return _extractor
