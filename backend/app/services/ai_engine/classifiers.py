"""
classifiers.py
- Tone and theme detectors for AI recommendation lists (zero-shot or SBERT-based).
"""
from typing import List, Dict
import numpy as np
from .moods_themes_map import MOOD_THEME_LABELS
from app.services.ai_engine.embeddings import EmbeddingService

# Simple keyword map for fast matching
MOOD_KEYWORDS = {
    "uplifting": ["uplifting", "inspiring", "hopeful"],
    "dark": ["dark", "grim", "bleak"],
    "romantic": ["romantic", "love", "passion"],
    "funny": ["funny", "humorous", "comedy", "hilarious"],
    "suspenseful": ["suspenseful", "tense", "thrilling"],
    # ... add more as needed ...
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
