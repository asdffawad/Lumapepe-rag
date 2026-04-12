"""
Celery App 配置
"""
import os
from celery import Celery
from celery.schedules import crontab


def make_celery():
    celery = Celery(
        "rag_customer_service",
        broker=os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/1"),
        backend=os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/2"),
        include=["celery_app.tasks"],
    )

    celery.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="Asia/Shanghai",
        enable_utc=True,
        task_track_started=True,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        task_routes={
            "celery_app.tasks.doc_parser.*": {"queue": "documents"},
            "celery_app.tasks.chunker.*": {"queue": "documents"},
            "celery_app.tasks.embed_task.*": {"queue": "embedding"},
            "celery_app.tasks.vector_index.*": {"queue": "embedding"},
        },
        task_annotations={
            "celery_app.tasks.embed_task.embed_batch": {
                "rate_limit": "100/m"
            }
        },
        beat_schedule={
            "cleanup-old-logs": {
                "task": "celery_app.tasks.cleanup_old_logs",
                "schedule": 86400.0,
            },
            "refresh-hot-questions": {
                "task": "celery_app.tasks.refresh_hot_questions",
                "schedule": 3600.0,
            },
            "daily-incremental-ingest": {
                "task": "celery_app.tasks.incremental_ingest.daily_incremental_ingest",
                "schedule": crontab(hour=0, minute=0),   # 每日 00:00 执行
            },
        },
    )
    return celery


celery_app = make_celery()
