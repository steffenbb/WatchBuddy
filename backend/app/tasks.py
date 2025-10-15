"""
DEPRECATED MODULE
-----------------
This legacy Celery tasks module has been replaced by `app.services.tasks`.
It remains as a compatibility shim to avoid import errors in older docs/scripts.

Recommended: import tasks from `app.services.tasks` instead.
"""

# Re-export tasks from the canonical module so any legacy imports continue to work
from app.services.tasks import *  # noqa: F401,F403
