"""
简历文件接口:
  POST /api/resume/parse                上传文件解析为 Markdown
  GET  /api/resume/supported-extensions 支持的扩展名
契约见 docs/specs/api-contract.md 第 4.3/4.4 节。
"""
import logging
import threading
from functools import lru_cache
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_resume_parser, get_task_manager
from src.api.schemas_api import ResumeParseAck, ResumeParseStatus, SupportedExtensions
from src.api.sse import SSE_HEADERS, SSE_MEDIA_TYPE, sse_format
from src.api.task_manager import TaskManager
from src.auth.security import CurrentUser
from src.config import get_config
from src.database.database import get_async_session
import src.database.models as models
from src.knowledge.resume_file_parser import SUPPORTED_EXTENSIONS, ResumeFileParser
from src.utils.time_convert import to_epoch

_log = logging.getLogger(__name__)

router = APIRouter()
settings = get_config()


@lru_cache
def _parse_semaphore():
    return threading.Semaphore(settings.semaphore.resume_parse_max_concurrency)


@router.get("/resume/supported-extensions", response_model=SupportedExtensions)
def supported_extensions():
    return SupportedExtensions(extensions=list(SUPPORTED_EXTENSIONS))


@router.post("/resume/parse", response_model=ResumeParseAck, status_code=202)
async def parse_resume(
    current_user: CurrentUser,
    file: UploadFile = File(...),
    parser: ResumeFileParser = Depends(get_resume_parser),
    tm: TaskManager = Depends(get_task_manager),
):
    file_bytes = await file.read()
    filename = file.filename or 'resume'
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"不支持的文件格式: {ext or '未知'}")

    def run_fn(progress):
        progress("queued", "排队等待解析槽位", 5)
        with _parse_semaphore():
            progress("parsing", "MinerU解析中", 50)
            text = parser.parse(filename, file_bytes)
            out = settings.paths.resume_uploads_dir / \
                f"{uuid4().hex}.md"  # 独立 uuid 命名(不用 task_id,避免改 RunFn 签名)
            out.write_text(text, encoding="utf-8")
            progress("parsing", "解析完成,写入结果", 90)
            return str(out)

    task_id = tm.submit("parse-resume", run_fn,
                        user_id=current_user.id, filename=filename)
    return ResumeParseAck(task_id=task_id, status="pending")


@router.get("/resume/parse/{task_id}", response_model=ResumeParseStatus)
async def parse_status(
    task_id: str,
    current_user: CurrentUser,
    tm: Annotated[TaskManager, Depends(get_task_manager)],
    db: Annotated[AsyncSession, Depends(get_async_session)],
):
    '''获取任务状态（轮询）'''
    task_rec = tm.get(task_id)
    if task_rec is None:
        # 存在内存里的任务找不到，去数据库里找
        task_rec = (await db.execute(select(models.BuildTask).where(models.BuildTask.task_id == task_id))).scalars().first()
    if task_rec is None or task_rec.user_id != current_user.id or task_rec.task != "parse-resume":
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


@router.get("/resume/parse/{task_id}/stream")
def parse_stream(
    task_id: str,
    current_user: CurrentUser,
    tm: TaskManager = Depends(get_task_manager)
):
    """订阅简历解析任务进度 SSE:重放历史 + 实时推送,直到 done"""
    rec = tm.get(task_id)
    if rec is None or rec.user_id != current_user.id or rec.task != "parse-resume":
        raise HTTPException(status_code=404, detail="task not found")

    def gen():
        for ev in tm.subscribe(task_id):
            yield sse_format(ev["event"], ev["data"])

    return StreamingResponse(gen(), media_type=SSE_MEDIA_TYPE, headers=SSE_HEADERS)
