"""
inspect_celery.py

Utility to print the registered Celery tasks inside the running image/container.
Run with: PYTHONPATH=/app python app/scripts/inspect_celery.py
"""
from app.core.celery_app import celery_app

def main():
    keys = sorted(celery_app.tasks.keys())
    tasks = [k for k in keys if k.startswith("app.services.tasks.") or k.startswith("generate_") or k.startswith("refresh_")]
    print("Registered tasks (subset):")
    for k in tasks:
        print(" -", k)

if __name__ == "__main__":
    main()
