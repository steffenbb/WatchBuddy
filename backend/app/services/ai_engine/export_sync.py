"""
export_sync.py
- Trakt export helpers for AI lists, with retry/backoff and quota handling.
"""
import time
import logging
from app.services.trakt_client import TraktClient
from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

MAX_RETRIES = 5


def export_to_trakt(user_id: int, ai_list_id: int, items: list, list_name: str, description: str = ""):
    client = TraktClient(user_id=user_id)
    r = get_redis()
    for attempt in range(MAX_RETRIES):
        try:
            # Check Trakt quota (rate limit)
            if not client.can_create_list():
                raise Exception("Trakt quota exceeded")
            trakt_list_id = client.create_list(list_name, description)
            for batch in [items[i:i+100] for i in range(0, len(items), 100)]:
                client.add_items_to_list(trakt_list_id, batch)
            return trakt_list_id
        except Exception as e:
            logger.warning(f"Trakt export failed (attempt {attempt+1}): {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
            else:
                raise
