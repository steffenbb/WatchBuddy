"""
explain.py

Deterministic, template-based explanation generator for SmartList and scoring results.
No LLMs or stochastic models are used. Explanations are based on scoring features, user mood, and list criteria.
"""

from typing import Dict, Any, List

class ExplainEngine:
    def __init__(self):
        pass

    def explain_smartlist(self, item: Dict[str, Any], features: Dict[str, float], mood: str = None) -> str:
        parts = []
        if features.get("keyword_score"):
            parts.append(f"Matches your keywords with a score of {features['keyword_score']:.2f}.")
        if features.get("mood_score") and mood:
            parts.append(f"Fits your current mood: {mood} (score {features['mood_score']:.2f}).")
        if features.get("recency_score"):
            parts.append(f"Recently released or trending (recency score {features['recency_score']:.2f}).")
        if features.get("popularity_score"):
            parts.append(f"Popular among users (popularity score {features['popularity_score']:.2f}).")
        if not parts:
            return "Selected based on your SmartList criteria."
        return " ".join(parts)

    def explain_list(self, item: Dict[str, Any], features: Dict[str, float]) -> str:
        # For general lists, do not mention mood
        parts = []
        if features.get("keyword_score"):
            parts.append(f"Matches your keywords with a score of {features['keyword_score']:.2f}.")
        if features.get("recency_score"):
            parts.append(f"Recently released or trending (recency score {features['recency_score']:.2f}).")
        if features.get("popularity_score"):
            parts.append(f"Popular among users (popularity score {features['popularity_score']:.2f}).")
        if not parts:
            return "Selected based on your list criteria."
        return " ".join(parts)

    def explain(self, item: Dict[str, Any], features: Dict[str, float], list_type: str = "smartlist", mood: str = None) -> str:
        if list_type == "smartlist":
            return self.explain_smartlist(item, features, mood)
        else:
            return self.explain_list(item, features)


def generate_explanation(features: Dict[str, float]) -> str:
    """Backward-compatible helper used by scoring_engine."""
    parts = []
    if features.get("similarity_score") is not None:
        parts.append(f"Content similarity {features['similarity_score']:.2f}.")
    if features.get("mood_score") is not None:
        parts.append(f"Mood match {features['mood_score']:.2f}.")
    if features.get("novelty_score") is not None:
        parts.append(f"Novelty {features['novelty_score']:.2f}.")
    if not parts:
        return "Selected based on your criteria."
    return " ".join(parts)
