"""
explainability.py (AI Engine)
- Deterministic explanation templates and meta builder.
"""
from typing import Dict, Any


def build_explanation_meta(candidate: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "similarity_type": "semantic" if candidate.get("semantic_sim", 0) >= candidate.get("tfidf_sim", 0) else "tfidf",
        "genre_overlap": candidate.get("genre_overlap", 0.0),
        "mood_score": candidate.get("mood_score", 0.0),
        "novelty_score": candidate.get("novelty", 0.0),
    }


def generate_explanation(candidate: Dict[str, Any]) -> str:
    bits = []
    if candidate.get("semantic_sim", 0) > 0.6:
        bits.append("high semantic match")
    if candidate.get("tfidf_sim", 0) > 0.4:
        bits.append("strong text similarity")
    if candidate.get("novelty", 0) > 0.6:
        bits.append("novel pick")
    if not bits:
        bits.append("relevant to your prompt")
    return ", ".join(bits).capitalize() + "."
