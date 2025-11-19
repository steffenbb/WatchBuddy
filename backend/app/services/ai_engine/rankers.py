from typing import Any, Dict, List, Sequence

class BaseRanker:
    def rank(self, items: List[Dict[str, Any]], context: Dict[str, Any] | None = None) -> List[int]:
        raise NotImplementedError

class ClassicRanker(BaseRanker):
    def __init__(self, score_key: str = "final_score"):
        self.score_key = score_key

    def rank(self, items: List[Dict[str, Any]], context: Dict[str, Any] | None = None) -> List[int]:
        order = sorted(range(len(items)), key=lambda i: float(items[i].get(self.score_key, 0.0)), reverse=True)
        return order

class LLMRanker(BaseRanker):
    """Simple ranker that respects a judge score on the item if present, falling back to classic score.
    Expects 'judge_score' or reuses 'final_score'.
    """
    def __init__(self, primary_key: str = "judge_score", fallback_key: str = "final_score"):
        self.primary_key = primary_key
        self.fallback_key = fallback_key

    def rank(self, items: List[Dict[str, Any]], context: Dict[str, Any] | None = None) -> List[int]:
        def _score(i: int) -> float:
            it = items[i]
            if self.primary_key in it and it[self.primary_key] is not None:
                try:
                    return float(it[self.primary_key])
                except Exception:
                    pass
            try:
                return float(it.get(self.fallback_key, 0.0))
            except Exception:
                return 0.0
        order = sorted(range(len(items)), key=_score, reverse=True)
        return order
