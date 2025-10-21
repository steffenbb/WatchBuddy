import unittest
import numpy as np
from app.services.scorer import CandidateScorer
from app.services.diversifier import mmr_select

def make_candidate(idx, score, mood=None):
    c = {"title": f"Movie {idx}", "final_score": score, "semantic_score": score}
    if mood is not None:
        c["mood_vector"] = mood
    return c

class TestScorerDiversifier(unittest.TestCase):
    def test_scorer(self):
        query_emb = np.array([1.0, 0.0])
        candidate_embs = np.array([[1.0, 0.0], [0.0, 1.0]])
        candidates = [make_candidate(1, 0.9), make_candidate(2, 0.1)]
        scorer = CandidateScorer(query_emb)
        scored = scorer.score_candidates(candidates, candidate_embs)
        self.assertGreater(scored[0]["final_score"], scored[1]["final_score"])

    def test_mmr(self):
        candidates = [make_candidate(i, 1.0 - i*0.1) for i in range(5)]
        embs = np.eye(5)
        selected = mmr_select(candidates, embs, top_k=3)
        self.assertEqual(len(selected), 3)
        titles = [c["title"] for c in selected]
        self.assertIn("Movie 0", titles)

if __name__ == "__main__":
    unittest.main()
