import unittest
from app.services.parser import PromptParser
from app.services.metadata_processing import normalize_prompt, extract_features

class TestPromptParser(unittest.TestCase):
    def test_normalize_prompt(self):
        self.assertEqual(normalize_prompt("  Sci-Fi! 2022?  "), "sci-fi 2022?")
        self.assertEqual(normalize_prompt("Comedy, drama, 1999!"), "comedy, drama, 1999!")

    def test_extract_features(self):
        features = extract_features("comedy drama 1999 2020 sci-fi")
        self.assertIn("genres", features)
        self.assertIn("years", features)
        self.assertIn("sci-fi", features["genres"])
        self.assertIn(1999, features["years"])
        self.assertIn(2020, features["years"])

    def test_prompt_parser(self):
        parser = PromptParser("A thrilling sci-fi movie from 2010")
        q = parser.to_query()
        self.assertIn("sci-fi", q["features"].get("genres", []))
        self.assertIn(2010, q["features"].get("years", []))

if __name__ == "__main__":
    unittest.main()
