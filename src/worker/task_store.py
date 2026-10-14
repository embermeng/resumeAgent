"""
    Task Store Module
    将后台任务信息存储到数据库中
"""
from datetime import datetime, UTC
from sqlalchemy import and_, update, select
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


def purge_other_resumes(user_id: int, keep_task_id: str) -> list[str]:
    """
    删除该用户除 keep_task_id 之外的全部简历解析行,返回被删行的产物路径(文件由调用方删)。

    每个用户只保留一份简历:新简历解析成功后旧的才被顶替——放在成功之后而非提交时,
    是为了让"新文件解析失败"不至于把用户原来的简历也一起弄丢。
    清理范围含 pending/failed 的历史行,否则失败的旧行会永远留在表里。
    """
    try:
        with SessionLocal() as db:
            rows = db.execute(select(models.BuildTask).where(
                and_(
                    models.BuildTask.user_id == user_id,
                    models.BuildTask.task == "parse-resume",
                    models.BuildTask.task_id != keep_task_id,
                )
            )).scalars().all()
            stale_paths = [r.result_path for r in rows if r.result_path]
            for r in rows:
                db.delete(r)
            db.commit()
            return stale_paths
    except Exception:
        _log.exception("旧简历行清理失败: user_id=%s keep_task_id=%s", user_id, keep_task_id)
        return []
