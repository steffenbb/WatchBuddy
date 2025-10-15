"""Manually trigger background tasks for testing."""
from app.core.celery_app import celery_app

print("Triggering background tasks...")

# Trigger ingestion tasks
print("\n1. Ingesting new movies...")
result1 = celery_app.send_task("app.services.tasks.ingest_new_movies")
print(f"   Task ID: {result1.id}")

print("\n2. Ingesting new shows...")
result2 = celery_app.send_task("app.services.tasks.ingest_new_shows")
print(f"   Task ID: {result2.id}")

print("\n3. Building Trakt metadata...")
result3 = celery_app.send_task("app.services.tasks.build_metadata", kwargs={"user_id": 1, "force": False})
print(f"   Task ID: {result3.id}")

print("\nTasks queued. Check worker logs with:")
print("  docker logs -f watchbuddy-celery-1")
