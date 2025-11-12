"""
classifiers.py
- Tone and theme detectors for AI recommendation lists (zero-shot or SBERT-based).
"""
from typing import List, Dict
import numpy as np
from .moods_themes_map import MOOD_THEME_LABELS
from app.services.ai_engine.embeddings import EmbeddingService

# Comprehensive mood/tone keyword map with extensive synonyms and variations
MOOD_KEYWORDS = {
    "uplifting": ["uplifting", "inspiring", "hopeful", "feel-good", "feelgood", "heartwarming", "optimistic", "positive", "motivational", "cheerful", "joyful", "bright"],
    "dark": ["dark", "grim", "bleak", "noir", "gritty", "sinister", "moody", "brooding", "shadowy", "ominous", "foreboding", "menacing", "disturbing"],
    "romantic": ["romantic", "love", "passion", "passionate", "romance", "love story", "heartfelt", "tender", "sweet", "affectionate", "dreamy"],
    "funny": ["funny", "humorous", "comedy", "hilarious", "comedic", "laugh", "witty", "amusing", "lighthearted", "playful", "silly", "goofy", "satirical", "sarcastic"],
    "suspenseful": ["suspenseful", "tense", "thrilling", "nail-biting", "edge of your seat", "gripping", "intense", "nerve-wracking", "heart-pounding", "pulse-pounding"],
    "sad": ["sad", "melancholy", "depressing", "tearjerker", "tear-jerker", "emotional", "poignant", "bittersweet", "tragic", "sorrowful", "heartbreaking", "somber"],
    "scary": ["scary", "frightening", "terrifying", "spooky", "creepy", "eerie", "chilling", "bone-chilling", "horrifying", "nightmare", "haunting"],
    "action-packed": ["action-packed", "explosive", "adrenaline", "high-octane", "fast-paced", "thrilling action", "intense action", "nonstop", "non-stop"],
    "thought-provoking": ["thought-provoking", "philosophical", "cerebral", "intellectual", "deep", "contemplative", "reflective", "introspective", "mind-bending", "psychological"],
    "whimsical": ["whimsical", "quirky", "offbeat", "eccentric", "charming", "magical", "fantastical", "imaginative", "dreamlike", "surreal"],
    "epic": ["epic", "grand", "sweeping", "large-scale", "monumental", "spectacular", "majestic", "ambitious"],
    "intimate": ["intimate", "personal", "small-scale", "character-driven", "quiet", "subtle", "nuanced", "understated"],
    "nostalgic": ["nostalgic", "retro", "vintage", "throwback", "classic", "old-school", "reminiscent"],
    "mysterious": ["mysterious", "enigmatic", "cryptic", "puzzling", "intriguing", "mystifying", "secretive"],
    "violent": ["violent", "brutal", "graphic", "gory", "bloody", "visceral", "savage", "ruthless"],
    "peaceful": ["peaceful", "calming", "serene", "tranquil", "relaxing", "soothing", "meditative", "zen"],
    "chaotic": ["chaotic", "frantic", "hectic", "wild", "anarchic", "crazy", "unpredictable", "messy"],
    "empowering": ["empowering", "strong", "powerful", "confident", "bold", "fierce", "badass", "triumphant"],
    "cozy": ["cozy", "comforting", "warm", "homey", "snug", "hygge", "comfortable", "inviting"],
}

def detect_tone_keywords(prompt: str) -> List[str]:
    found = []
    for mood, keywords in MOOD_KEYWORDS.items():
        for kw in keywords:
            if kw in prompt.lower():
                found.append(mood)
    return found

def sbert_tone_vector(prompt: str) -> np.ndarray:
    """SBERT-based similarity to mood/theme labels."""
    embedder = EmbeddingService()
    prompt_emb = embedder.encode_text(prompt)
    label_embs = embedder.encode_texts(MOOD_THEME_LABELS)
    sims = np.dot(label_embs, prompt_emb) / (np.linalg.norm(label_embs, axis=1) * np.linalg.norm(prompt_emb) + 1e-8)
    sims = np.maximum(sims, 0)
    tone_vec = sims / (np.sum(sims) + 1e-8)
    return tone_vec
