"""
知识库构建接口:
  POST /api/knowledge/build                    触发后台构建任务
  GET  /api/knowledge/tasks/{task_id}/stream   订阅任务进度(SSE)
  GET  /api/knowledge/tasks/{task_id}          查询任务状态快照
契约见 docs/specs/api-contract.md 第 4.5/4.6/4.7 节。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from src.api.deps import get_knowledge_service, get_task_manager
from src.api.schemas_api import BuildAck, BuildRequest, TaskStatus
from src.api.services.knowledge_service import KnowledgeService
from src.api.sse import SSE_HEADERS, SSE_MEDIA_TYPE, sse_format
from src.api.task_manager import TaskManager

_log = logging.getLogger(__name__)

router = APIRouter()


@router.post("/knowledge/build", response_model=BuildAck, status_code=202)
def build(
    req: BuildRequest,
    svc: KnowledgeService = Depends(get_knowledge_service),
    tm: TaskManager = Depends(get_task_manager),
):
    """受理构建请求:后台线程执行,立即返回 task_id"""
    def run_fn(progress):
        svc.run(
            req.task,
            force=req.force,
            chunk_size=req.chunk_size,
            chunk_overlap=req.chunk_overlap,
            prune=req.prune,
            progress=progress,
        )

    task_id = tm.submit(req.task, run_fn)
    return BuildAck(task_id=task_id, status="pending")


@router.get("/knowledge/tasks/{task_id}/stream")
def task_stream(task_id: str, tm: TaskManager = Depends(get_task_manager)):
    """订阅任务进度 SSE:重放历史 + 实时推送,直到 done"""
    if tm.get(task_id) is None:
        raise HTTPException(status_code=404, detail="task not found")

    def gen():
        for ev in tm.subscribe(task_id):
            yield sse_format(ev["event"], ev["data"])

    return StreamingResponse(gen(), media_type=SSE_MEDIA_TYPE, headers=SSE_HEADERS)


@router.get("/knowledge/tasks/{task_id}", response_model=TaskStatus)
def task_status(task_id: str, tm: TaskManager = Depends(get_task_manager)):
    """任务状态快照(轮询兜底)"""
    rec = tm.get(task_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="task not found")
    return TaskStatus(
        task_id=rec.task_id,
        task=rec.task,
        status=rec.status,
        stage=rec.stage,
        percent=rec.percent,
        message=rec.message,
        created_at=rec.created_at,
        finished_at=rec.finished_at,
        error=rec.error,
    )
