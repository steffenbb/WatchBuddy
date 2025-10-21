"""
test_faiss_build_search.py
- Unit tests for FAISS index build and search helpers.
"""
import unittest
import numpy as np
from app.services.faiss_index import train_build_ivfpq, load_index, search_index
import tempfile
import os

class TestFaissIndex(unittest.TestCase):
    def test_train_and_search(self):
        # Create dummy embeddings and mapping
        embeddings = np.random.rand(1000, 384).astype(np.float32)
        mapping = {i: 100000 + i for i in range(1000)}
        with tempfile.TemporaryDirectory() as tmpdir:
            index_path = os.path.join(tmpdir, "faiss_index.bin")
            mapping_path = os.path.join(tmpdir, "faiss_map.json")
            train_build_ivfpq(embeddings, mapping, 384, index_path, nlist=16, m=4, nbits=8)
            index, loaded_mapping = load_index(index_path, mapping_path)
            query = embeddings[0]
            D, I = search_index(index, query, top_k=5)
            self.assertEqual(I.shape[1], 5)
            self.assertIn(I[0][0], loaded_mapping)

if __name__ == "__main__":
    unittest.main()
