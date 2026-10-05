"""
Celery 应用实例
- broker:        任务消息队列（LPUSH/BRPOP）
- result_backend: 任务状态与结果存储（key-value + TTL）
"""

from celery import Celery

from src.config import get_config

settings = get_config()

celery_app = Celery("resumeagent")

celery_app.conf.update(
    broker_url=settings.redis.url,
    result_backend=settings.redis.url,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    result_expires=3600,
    # worker 启动时扫描注册具名任务
    include=["src.worker.tasks"],
    task_routes={
        "resumeagent.parse_resume": {"queue": "resume"},
        # Step B 才实现,先占位
        "resumeagent.knowledge_build": {"queue": "knowledge"},
    },
    task_acks_late=True,
)
