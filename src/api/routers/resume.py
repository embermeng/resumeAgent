"""
简历文件接口:
  POST   /api/resume/parse                     上传文件提交解析任务(Celery)
  GET    /api/resume/parse/{task_id}           轮询任务状态/结果(DB 权威源)
  GET    /api/resume/parse/{task_id}/stream    SSE 进度流(DB 快照+轮询转发)
  GET    /api/resume/current                   我当前的简历(每人仅一份,含正文,无则 204)
  DELETE /api/resume/current                   删除我的简历(任务行 + 解析产物文件)
  GET    /api/resume/supported-extensions      支持的扩展名
契约见 docs/specs/api-contract.md 第 4.3/4.4/4.4b/4.5/4.5b 节。
"""
import logging
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, select
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas_api import (
    ResumeCurrent,
    ResumeParseAck,
    ResumeParseStatus,
    SupportedExtensions,
)
from src.api.sse import SSE_HEADERS, SSE_MEDIA_TYPE
from src.auth.security import CurrentUser
from src.config import get_config
from src.database.database import get_async_session, get_session
import src.database.models as models
from src.knowledge.resume_file_parser import SUPPORTED_EXTENSIONS
from src.utils.time_convert import to_epoch
from src.worker.task_store import insert_task
from src.worker.tasks import parse_resume_task
from src.api.task_stream import stream_task_events

_log = logging.getLogger(__name__)

router = APIRouter()
settings = get_config()


@router.get("/resume/supported-extensions", response_model=SupportedExtensions)
def supported_extensions():
    return SupportedExtensions(extensions=list(SUPPORTED_EXTENSIONS))


@router.post("/resume/parse", response_model=ResumeParseAck, status_code=202)
async def parse_resume(
    current_user: CurrentUser,
    file: UploadFile = File(...),
):
    file_bytes = await file.read()
    filename = file.filename or 'resume'
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"不支持的文件格式: {ext or '未知'}")

    # 字节落盘:消息只带路径字符串(可序列化),不带字节本体
    upload = settings.paths.resume_uploads_dir / f"{uuid4().hex}.upload{ext}"
    upload.write_bytes(file_bytes)

    task_id = uuid4().hex
    insert_task(task_id=task_id, task="parse-resume", user_id=current_user.id, filename=filename)
    # user_id 随消息带给 worker:解析成功要靠它清掉该用户的旧简历,省一次回查 DB
    parse_resume_task.apply_async(args=(str(upload), filename, current_user.id), task_id=task_id)
    return ResumeParseAck(task_id=task_id, status="pending")


@router.get("/resume/parse/{task_id}", response_model=ResumeParseStatus)
async def parse_status(
    task_id: str,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_session)],
):
    '''获取任务状态（轮询）'''
    task_rec = (await db.execute(select(models.BuildTask).where(models.BuildTask.task_id == task_id, models.BuildTask.user_id == current_user.id, models.BuildTask.task == "parse-resume"))).scalars().first()
    if task_rec is None:
        raise HTTPException(status_code=404, detail="task not found")

    content = None
    if task_rec.status == "success" and task_rec.result_path:
        p = Path(task_rec.result_path)
        if p.exists():
            content = p.read_text(encoding="utf-8")
    return ResumeParseStatus(
        task_id=task_rec.task_id,
        status=task_rec.status,
        stage=task_rec.stage,
        percent=task_rec.percent,
        message=task_rec.message,
        filename=task_rec.filename,
        content=content,
        error=task_rec.error,
        created_at=to_epoch(task_rec.created_at),
        finished_at=to_epoch(task_rec.finished_at),
    )


@router.delete("/resume/current", status_code=204)
async def delete_current_resume(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_session)],
):
    """
    删除我的简历:本人全部 parse-resume 行与解析产物一并清除。

    每人只有一份,所以删除针对"我的简历"这个资源而非某个 task_id;
    顺带清掉历史上多份共存时留下的旧行(存量脏数据自愈)。
    幂等:本来就没有简历也返回 204,前端不必区分"删掉了"和"早就没有"。
    """
    rows = (await db.execute(
        select(models.BuildTask).where(and_(
            models.BuildTask.user_id == current_user.id,
            models.BuildTask.task == "parse-resume",
        ))
    )).scalars().all()
    # 先取路径:delete + commit 之后实例过期,再读属性会抛
    paths = [r.result_path for r in rows if r.result_path]
    for r in rows:
        await db.delete(r)
    await db.commit()
    for p in paths:
        Path(p).unlink(missing_ok=True)


@router.get("/resume/parse/{task_id}/stream")
def parse_stream(
    task_id: str,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_session)],
):
    """订阅简历解析任务进度 SSE:DB 快照首帧 + 轮询转发,直到 done 帧"""
    task_rec = db.execute(select(models.BuildTask).where(models.BuildTask.task_id == task_id, models.BuildTask.user_id == current_user.id, models.BuildTask.task == "parse-resume"))
    if task_rec.scalars().first() is None:
        raise HTTPException(status_code=404, detail="task not found")

    return StreamingResponse(stream_task_events(task_id), media_type=SSE_MEDIA_TYPE, headers=SSE_HEADERS)


@router.get("/resume/current", response_model=ResumeCurrent)
async def current_resume(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_async_session)],
):
    """
    我当前的简历:每用户只保留一份,前端挂载时据此自动把正文带入生成上下文。
    没有简历时返回 204 空体(而不是 404:"没传过"是正常状态,不该走错误分支)。
    """
    row = (await db.execute(
        select(models.BuildTask)
        .where(and_(
            models.BuildTask.user_id == current_user.id,
            models.BuildTask.task == "parse-resume",
            models.BuildTask.status == "success",
        ))
        .order_by(models.BuildTask.created_at.desc())
        .limit(1)
    )).scalars().first()
    if row is None:
        return Response(status_code=204)

    content = None
    if row.result_path:
        p = Path(row.result_path)
        if p.exists():
            content = p.read_text(encoding="utf-8")
    return ResumeCurrent(
        filename=row.filename,
        created_at=to_epoch(row.created_at),
        finished_at=to_epoch(row.finished_at),
        content=content,
    )
