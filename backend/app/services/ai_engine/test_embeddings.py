"""
test_embeddings.py
- Unit tests for EmbeddingService (sentence-transformers, CPU-only).
"""
import unittest
from app.services.ai_engine.embeddings import EmbeddingService

class TestEmbeddingService(unittest.TestCase):
    def test_encode_text(self):
        embedder = EmbeddingService()
        vec = embedder.encode_text("hello world")
        self.assertEqual(len(vec.shape), 1)
        self.assertEqual(vec.dtype.name, "float32")

    def test_encode_texts(self):
        embedder = EmbeddingService()
        vecs = embedder.encode_texts(["hello", "world"], batch_size=2)
        self.assertEqual(len(vecs.shape), 2)
        self.assertEqual(vecs.dtype.name, "float32")
        self.assertEqual(vecs.shape[0], 2)

if __name__ == "__main__":
    unittest.main()
