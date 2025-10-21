import unittest
from app.services.explainability import generate_explanation

class TestExplainability(unittest.TestCase):
    def test_generate_explanation(self):
        candidate = {"semantic_score": 0.8, "mood_score": 0.6, "genres": "comedy, drama"}
        query = {"features": {"genres": ["comedy"]}}
        explanation = generate_explanation(candidate, query)
        self.assertIn("high semantic match", explanation.lower())
        self.assertIn("genre overlap", explanation.lower())

if __name__ == "__main__":
    unittest.main()
