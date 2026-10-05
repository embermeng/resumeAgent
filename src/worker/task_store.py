"""
    Task Store Module
    将后台任务信息存储到数据库中
"""
from datetime import datetime, UTC
from sqlalchemy import update, select
import logging

import src.database.models as models
from src.database.database import SessionLocal

_log = logging.getLogger(__name__)


def insert_task(task_id: str, task: str, user_id: int | None = None, filename: str | None = None) -> None:
    try:
        new_record = models.BuildTask(
            task_id=task_id,
            task=task,
            user_id=user_id,
            filename=filename,
            status="pending",
            stage="queued",
            percent=0,
            message="排队中"
        )
        with SessionLocal() as db:
            db.add(new_record)
            db.commit()
    except Exception:
        _log.exception("build_task插入失败: task_id=%s", task_id)


def update_task(task_id: str, **fields) -> None:
    try:
        # finished_at 需要 float epoch → datetime 转换
        if "finished_at" in fields:
            fields['finished_at'] = datetime.fromtimestamp(
                fields["finished_at"], tz=UTC)
        if not fields:
            return
        with SessionLocal() as db:
            db.execute(update(models.BuildTask).where(
                models.BuildTask.task_id == task_id).values(fields))
            db.commit()
    except Exception:
        _log.exception("build_task更新失败: task_id=%s", task_id)


def get_task(task_id: str) -> models.BuildTask | None:
    try:
        with SessionLocal() as db:
            res = db.execute(select(models.BuildTask).where(
                models.BuildTask.task_id == task_id))
            return res.scalars().first()
    except Exception:
        _log.exception("build_task查询失败: task_id=%s", task_id)
