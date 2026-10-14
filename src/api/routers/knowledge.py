"""
知识库构建接口:
  POST /api/knowledge/build                    触发后台构建任务(仅管理员)
  GET  /api/knowledge/tasks/{task_id}/stream   订阅任务进度(SSE)
  GET  /api/knowledge/tasks/{task_id}          查询任务状态快照
  GET  /api/knowledge/tasks                    任务列表分页
契约见 docs/specs/api-contract.md 第 4.5/4.6/4.7 节。

鉴权:全部端点需登录。
- build 是**全局**操作(重扫全部课程 PDF 与所有用户的项目文档、重建索引、产生 embedding 开销),
  故仅限 is_superuser,普通用户 403。
- 任务查询:管理员看全量,普通用户只看自己发起的(历史任务 user_id 为 NULL 的不属于任何人,
  仅管理员可见)。
"""
import logging
from uuid import uuid4
from typing import Annotated, get_args

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas_api import BuildAck, BuildRequest, TaskKind, TaskStatus, TaskList
from src.api.sse import SSE_HEADERS, SSE_MEDIA_TYPE
from src.auth.security import CurrentUser
from src.database.database import get_async_session, get_session
import src.database.models as models
from src.utils.time_convert import to_epoch
from src.worker.task_store import insert_task
from src.worker.tasks import knowledge_build_task
from src.api.task_stream import stream_task_events

_log = logging.getLogger(__name__)

router = APIRouter()


def _visible_tasks(user_id: int, is_superuser: bool):
    """
    任务可见范围过滤条件。
    管理员看全部知识库任务;普通用户只看自己发起的——历史任务 user_id 可能为 NULL
    (改造前 insert_task 未传 user_id),这类不属于任何用户,仅管理员可见。

    build_tasks 是共享表:简历解析(task="parse-resume")、项目文档入库(task="ingest-project")
    以及历史改名前的残留值都往这里写行。出参 TaskStatus.task 是必填 Literal,
    读到不在白名单里的值会让**整个列表** 500(一行脏数据拖垮全站),
    所以这里用"只认知识库任务"的白名单,而不是逐个排除已知外来值。
    白名单直接取 TaskKind 的枚举项,与出参模型同源,新增任务类型也不会再崩。
    """
    base = models.BuildTask.task.in_(get_args(TaskKind))
    if is_superuser:
        return base
    return and_(base, models.BuildTask.user_id == user_id)


@router.post("/knowledge/build", response_model=BuildAck, status_code=202)
def build(
    req: BuildRequest,
    current_user: CurrentUser,
):
    # 全局操作:影响所有用户的数据并消耗 embedding 额度,仅管理员可触发
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="仅管理员可执行知识库构建",
        )
    task_id = uuid4().hex
    insert_task(task_id=task_id, task=req.task, user_id=current_user.id)
    knowledge_build_task.apply_async(args=(
        req.task, req.force, req.chunk_size, req.chunk_overlap, req.prune), task_id=task_id)
    return BuildAck(task_id=task_id, status="pending")


@router.get("/knowledge/tasks/{task_id}/stream")
def task_stream(
    task_id: str,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_session)],
):
    """订阅任务进度 SSE: DB 快照首帧 + 轮询转发，直到 done"""
    task_rec = db.execute(select(models.BuildTask).where(
        models.BuildTask.task_id == task_id,
        _visible_tasks(current_user.id, current_user.is_superuser)))
    if task_rec.scalars().first() is None:
        raise HTTPException(status_code=404, detail="task not found")

    return StreamingResponse(stream_task_events(task_id), media_type=SSE_MEDIA_TYPE, headers=SSE_HEADERS)


@router.get("/knowledge/tasks/{task_id}", response_model=TaskStatus)
async def task_status(
    task_id: str,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_session)],
):
    """任务状态快照(轮询)。"""
    # 查数据库(受可见范围约束)
    result = await db.execute(
        select(models.BuildTask)
        .where(models.BuildTask.task_id == task_id,
               _visible_tasks(current_user.id, current_user.is_superuser))
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
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_session)],
    page: Annotated[int, Query(description="页码", ge=1)] = 1,
    page_size: Annotated[int, Query(description="每页大小", ge=1, le=100)] = 10,
):
    """任务列表分页(降序)。异步路由+AsyncSession。管理员看全量,普通用户只看自己的。"""
    visible = _visible_tasks(current_user.id, current_user.is_superuser)
    total = (await db.execute(
        select(func.count())
        .select_from(models.BuildTask)
        .where(visible))
    ).scalar() or 0
    result = await db.execute(
        select(models.BuildTask)
        .where(visible)
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
