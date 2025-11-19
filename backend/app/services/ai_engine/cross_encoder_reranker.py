from typing import Dict, List, Optional, Tuple

try:
    from sentence_transformers import CrossEncoder  # type: ignore
except Exception:  # pragma: no cover
    CrossEncoder = None  # type: ignore


class CrossEncoderReranker:
    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"):
        self.model_name = model_name
        self._model = None

    def ensure(self):
        if self._model is None:
            if CrossEncoder is None:
                raise RuntimeError("CrossEncoder not available")
            self._model = CrossEncoder(self.model_name)

    def score(self, query: str, texts: List[str], batch_size: int = 64) -> List[float]:
        self.ensure()
        assert self._model is not None
        pairs = [(query, t) for t in texts]
        scores = self._model.predict(pairs, batch_size=batch_size).tolist()
        # Normalize to 0..1 if range unknown; most CE models return 0..1 already
        out: List[float] = []
        for s in scores:
            try:
                sc = float(s)
                if sc < 0.0 or sc > 1.0:
                    sc = max(0.0, min(1.0, sc))
                out.append(sc)
            except Exception:
                out.append(0.0)
        return out
