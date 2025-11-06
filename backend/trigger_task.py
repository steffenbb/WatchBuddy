from app.core.celery_app import celery_app

celery_app.send_task("compute_user_overview_task", args=[1])
print("Task triggered")
