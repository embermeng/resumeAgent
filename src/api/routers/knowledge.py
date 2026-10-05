"""
知识库构建接口:
  POST /api/knowledge/build                    触发后台构建任务
  GET  /api/knowledge/tasks/{task_id}/stream   订阅任务进度(SSE)
  GET  /api/knowledge/tasks/{task_id}          查询任务状态快照
契约见 docs/specs/api-contract.md 第 4.5/4.6/4.7 节。
"""
import logging
from uuid import uuid4
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas_api import BuildAck, BuildRequest, TaskStatus, TaskList
from src.api.sse import SSE_HEADERS, SSE_MEDIA_TYPE
from src.database.database import get_async_session, get_session
import src.database.models as models
from src.utils.time_convert import to_epoch
from src.worker.task_store import insert_task
from src.worker.tasks import knowledge_build_task
from src.api.task_stream import stream_task_events

_log = logging.getLogger(__name__)

router = APIRouter()


@router.post("/knowledge/build", response_model=BuildAck, status_code=202)
def build(
    req: BuildRequest,
):
    task_id = uuid4().hex
    insert_task(task_id=task_id, task=req.task)
    knowledge_build_task.apply_async(args=(
        req.task, req.force, req.chunk_size, req.chunk_overlap, req.prune), task_id=task_id)
    return BuildAck(task_id=task_id, status="pending")


@router.get("/knowledge/tasks/{task_id}/stream")
def task_stream(task_id: str, db: Annotated[Session, Depends(get_session)]):
    """订阅任务进度 SSE: DB 快照首帧 + 轮询转发，直到 done"""
    task_rec = db.execute(select(models.BuildTask).where(
        models.BuildTask.task_id == task_id, models.BuildTask.task != "parse-resume"))
    if task_rec.scalars().first() is None:
        raise HTTPException(status_code=404, detail="task not found")

    return StreamingResponse(stream_task_events(task_id), media_type=SSE_MEDIA_TYPE, headers=SSE_HEADERS)


@router.get("/knowledge/tasks/{task_id}", response_model=TaskStatus)
async def task_status(
    task_id: str,
    db: Annotated[AsyncSession, Depends(get_async_session)],
):
    """任务状态快照(轮询)。"""
    # 查数据库
    result = await db.execute(
        select(models.BuildTask)
        .where(models.BuildTask.task_id == task_id, models.BuildTask.task != "parse-resume")
    )
    rec = result.scalars().first()
    if rec is None:
        raise HTTPException(status_code=404, detail="task not found")
    return TaskStatus(
        task_id=rec.task_id,
        task=rec.task,
        status=rec.status,
        stage=rec.stage,
        percent=rec.percent,
        message=rec.message,
        created_at=to_epoch(rec.created_at),
        finished_at=to_epoch(rec.finished_at),
        error=rec.error,
    )



@router.get("/knowledge/tasks", response_model=TaskList)
async def task_list(
    db: Annotated[AsyncSession, Depends(get_async_session)],
    page: Annotated[int, Query(description="页码", ge=1)] = 1,
    page_size: Annotated[int, Query(description="每页大小", ge=1, le=100)] = 10,
):
    """任务列表分页(降序)。异步路由+AsyncSession。"""
    total = (await db.execute(
        select(func.count())
        .select_from(models.BuildTask)
        .where(models.BuildTask.task != "parse-resume"))
    ).scalar() or 0
    result = await db.execute(
        select(models.BuildTask)
        .where(models.BuildTask.task != "parse-resume")
        .order_by(models.BuildTask.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size),
    )
    rows = result.scalars().all()
    task_list = [
        TaskStatus(
            task_id=rec.task_id,
            task=rec.task,
            status=rec.status,
            stage=rec.stage,
            percent=rec.percent,
            message=rec.message,
            created_at=to_epoch(rec.created_at),
            finished_at=to_epoch(rec.finished_at),
            error=rec.error,
        ) for rec in rows]
    return TaskList(tasks=task_list, total=total)
