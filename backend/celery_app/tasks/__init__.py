"""
Celery 任务模块
"""
from celery_app import celery_app

__all__ = ["celery_app", "daily_incremental_ingest"]
