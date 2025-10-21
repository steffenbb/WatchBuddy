"""
EmbeddingService using sentence-transformers (CPU-only).
Provides encode_text and encode_texts(batch_size=64). Ensures model is lazy-loaded and closed as needed.
Converts vectors to float16 for FAISS storage. Uses del and gc.collect after encoding batches.
"""
import numpy as np
import gc
from typing import List, Optional

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

class EmbeddingService:
    def __init__(self, model_name: str = MODEL_NAME, local_model_dir: Optional[str] = None):
        self.model_name = model_name
        self.local_model_dir = local_model_dir or "app/models_cache/all-MiniLM-L6-v2"
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            if SentenceTransformer is None:
                raise ImportError("sentence-transformers not installed")
            # Load from local snapshot directory directly (bundled with Docker image)
            import os
            snapshot_path = "/app/app/models_cache/all-MiniLM-L6-v2/models--sentence-transformers--all-MiniLM-L6-v2/snapshots/c9745ed1d9f207416be6d2e6f8de32d1f16199bf"
            
            if os.path.exists(snapshot_path):
                # Load directly from the snapshot directory (offline mode)
                self._model = SentenceTransformer(snapshot_path, device="cpu")
            else:
                # Fallback to online download
                self._model = SentenceTransformer(self.model_name, device="cpu")

    def encode_text(self, text: str) -> np.ndarray:
        self._ensure_model()
        emb = self._model.encode([text], show_progress_bar=False, convert_to_numpy=True, normalize_embeddings=True)
        arr = emb.astype(np.float16)
        del emb
        gc.collect()
        return arr[0]

    def encode_texts(self, texts: List[str], batch_size: int = 64) -> np.ndarray:
        self._ensure_model()
        embs = self._model.encode(texts, batch_size=batch_size, show_progress_bar=True, convert_to_numpy=True, normalize_embeddings=True)
        arr = embs.astype(np.float16)
        del embs
        gc.collect()
        return arr
