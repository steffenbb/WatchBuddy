"""
test_tasks.py
- Unit tests for Celery AI tasks (mock DB & Trakt endpoints).
"""
import unittest
from unittest.mock import patch, MagicMock
from app.services.tasks_ai import generate_chat_list

class TestTasksAI(unittest.TestCase):
    @patch("app.services.tasks_ai.SessionLocal")
    @patch("app.services.tasks_ai.generate_chat_list")
    def test_generate_chat_list(self, mock_generate, mock_session):
        mock_generate.return_value = None
        mock_session.return_value = MagicMock()
        try:
            generate_chat_list(1, 1)
        except Exception:
            self.fail("generate_chat_list raised Exception unexpectedly!")

if __name__ == "__main__":
    unittest.main()
