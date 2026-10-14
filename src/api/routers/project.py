"""
项目文档接口:
  POST   /api/project/preprocess               上传 .md 做预处理,返回整理结果供预览
  POST   /api/project/confirm                  预览确认后提交入库任务(Celery)
  POST   /api/project/upload                   直接入库(不经预览,兼容旧路径)
  GET    /api/project/upload/{task_id}          轮询任务状态(DB 权威源)
  GET    /api/project/upload/{task_id}/stream   SSE 进度流
  GET    /api/project/docs                      我的项目文档列表(含公共素材)
  GET    /api/project/template                  下载标准模板(与入库校验基准同源)
  DELETE /api/project/docs/{doc_id}             删除(校验归属,越权 404)
  POST   /api/project/docs/batch-delete         批量删除(越权 id 静默跳过并回显)

全部端点需登录。归属判定的权威源是 project_docs 表,不是文件名。

预处理的说明见 src/knowledge/project_normalizer.py 模块说明:
  判断是否整理只有一个标准——是否符合 src/knowledge/templates/project_doc_template.md;
  合模板直接入库,不合则交给大模型按同一份模板重写。
"""
import json
import logging
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.schemas_api import (
    ProjectConfirmIn,
    ProjectDocBatchDeleteIn,
    ProjectDocBatchDeleteResult,
    ProjectDocList,
    ProjectDocOut,
    ProjectPreprocessOut,
    ProjectTemplateOut,
    ProjectUploadAck,
    ProjectUploadStatus,
)
from src.knowledge.doc_template import load_template
from src.knowledge.project_doc import ProjectDocIngestor, ProjectDocParser
from src.knowledge.project_normalizer import normalize_project_doc
from src.api.services.project_service import (
    check_quota,
    delete_project_doc_everywhere,
    delete_project_docs_everywhere,
    sweep_expired_previews,
)
from src.api.sse import SSE_HEADERS, SSE_MEDIA_TYPE
from src.api.task_stream import stream_task_events
from src.auth.security import CurrentUser
from src.config import get_config
from src.database.database import get_session
import src.database.models as models
from src.database.utils.project_docs import (
    get_project_doc,
    list_visible_project_docs,
)
from src.worker.task_store import insert_task
from src.worker.tasks import ingest_project_task
from src.utils.time_convert import to_epoch

_log = logging.getLogger(__name__)

router = APIRouter()
settings = get_config()

# 项目文档是纯 Markdown,只接受 .md
SUPPORTED_EXTENSIONS = {".md"}
# 下载给用户的模板文件名（中文友好，别直接暴露 template md 的工程名）
TEMPLATE_DOWNLOAD_NAME = "项目文档模板.md"


def _validate_upload(file: UploadFile, payload: bytes, user_id: int) -> None:
    """上传前的同步校验（不占队列）：扩展名 / 大小 / 配额"""
    filename = file.filename or "project.md"
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"只支持 Markdown 文件(.md)，收到: {ext or '未知'}")
    if len(payload) > settings.project_doc.max_file_size:
        limit_kb = settings.project_doc.max_file_size // 1024
        raise HTTPException(status_code=413, detail=f"文件超过大小上限 {limit_kb}KB")
    quota_error = check_quota(user_id)
    if quota_error:
        raise HTTPException(status_code=409, detail=quota_error)


@router.post("/project/preprocess", response_model=ProjectPreprocessOut)
async def preprocess_project_doc(
    current_user: CurrentUser,
    file: UploadFile = File(...),
):
    """
    上传并预处理：规则化容错 → 必要时大模型结构化 → 暂存并返回结果供前端预览。

    不直接入库：由用户在预览弹窗确认（可再编辑）后调 /confirm 落库，
    避免系统在用户不知情的情况下改写其文档内容。
    """
    filename = file.filename or "project.md"
    payload = await file.read()
    _validate_upload(file, payload, user_id=current_user.id)

    raw = payload.decode("utf-8", errors="replace")

    # 第 1 层：规则化容错（项目名缺失回退文件名 → 不再直接拒收）
    ingestor = ProjectDocIngestor()
    parsed = ingestor.parse_text(raw, fallback_name=filename)
    if parsed is None:
        raise HTTPException(status_code=400, detail="无法解析文档，也无法确定项目名")

    # 第 2 层：仅当结构缺失才交给大模型，且失败一律回退
    result = normalize_project_doc(parsed["text"], parsed["project_name"], config=settings)

    # 保证最终文本带一级标题：未归一化且原本没标题时补上，
    # 这样后续入库路径无需再区分"项目名从哪来"
    final_text = result.normalized
    if not ProjectDocParser.extract_project_name(final_text):
        final_text = f"# {parsed['project_name']}\n\n{final_text}"

    preview_id = uuid4().hex
    preview_dir = settings.paths.project_previews_dir
    preview_dir.mkdir(parents=True, exist_ok=True)
    # 顺带回收没人确认的旧预览：里面存的是用户文档全文，只靠 confirm 删会无限堆积
    sweep_expired_previews()
    (preview_dir / f"{preview_id}.json").write_text(
        json.dumps({
            "user_id": current_user.id,
            "file_name": filename,
            "project_name": parsed["project_name"],
            "tags": parsed["tags"],
            "text": final_text,
            "normalized": result.applied,
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    return ProjectPreprocessOut(
        preview_id=preview_id,
        project_name=parsed["project_name"],
        file_name=filename,
        original_text=parsed["text"],
        normalized_text=final_text,
        normalized=result.applied,
        reason=result.reason,
        has_resume_section=ProjectDocParser.extract_resume_section(final_text) is not None,
    )


@router.post("/project/confirm", response_model=ProjectUploadAck, status_code=202)
async def confirm_project_doc(
    payload: ProjectConfirmIn,
    current_user: CurrentUser,
):
    """预览确认：把（可能编辑过的）文本落盘并提交入库任务。"""
    preview_path = settings.paths.project_previews_dir / f"{payload.preview_id}.json"
    if not preview_path.exists():
        raise HTTPException(status_code=404, detail="preview not found or expired")
    try:
        preview = json.loads(preview_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"预览数据损坏: {e}")

    # 预览归属校验：不能确认别人的预处理结果
    if preview.get("user_id") != current_user.id:
        raise HTTPException(status_code=404, detail="preview not found or expired")

    text = payload.text if payload.text and payload.text.strip() else preview["text"]
    if not text.strip():
        raise HTTPException(status_code=400, detail="文档内容为空")

    # 字节落盘：消息只带路径字符串（可序列化），不带正文
    upload = settings.paths.resume_uploads_dir / f"{uuid4().hex}.upload.md"
    upload.write_bytes(text.encode("utf-8"))

    task_id = uuid4().hex
    insert_task(task_id=task_id, task="ingest-project",
                user_id=current_user.id, filename=preview["file_name"])
    ingest_project_task.apply_async(args=(str(upload), preview["file_name"],
                                          current_user.id), task_id=task_id)

    # 确认后即失效，避免同一预览被重复提交
    preview_path.unlink(missing_ok=True)
    return ProjectUploadAck(task_id=task_id, status="pending")


@router.post("/project/upload", response_model=ProjectUploadAck, status_code=202)
async def upload_project_doc(
    current_user: CurrentUser,
    file: UploadFile = File(...),
):
    file_bytes = await file.read()
    filename = file.filename or "project.md"
    ext = Path(filename).suffix.lower()
    _validate_upload(file, file_bytes, user_id=current_user.id)

    # 字节落盘:消息只带路径字符串(可序列化),不带字节本体
    upload = settings.paths.resume_uploads_dir / f"{uuid4().hex}.upload{ext}"
    upload.write_bytes(file_bytes)

    task_id = uuid4().hex
    insert_task(task_id=task_id, task="ingest-project",
                user_id=current_user.id, filename=filename)
    ingest_project_task.apply_async(
        args=(str(upload), filename, current_user.id), task_id=task_id)
    return ProjectUploadAck(task_id=task_id, status="pending")


@router.get("/project/upload/{task_id}", response_model=ProjectUploadStatus)
def upload_status(
    task_id: str,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_session)],
):
    """获取上传任务状态（轮询）"""
    rec = db.execute(
        select(models.BuildTask).where(
            models.BuildTask.task_id == task_id,
            models.BuildTask.user_id == current_user.id,
            models.BuildTask.task == "ingest-project",
        )
    ).scalars().first()
    if rec is None:
        raise HTTPException(status_code=404, detail="task not found")

    return ProjectUploadStatus(
        task_id=rec.task_id,
        status=rec.status,
        stage=rec.stage,
        percent=rec.percent,
        message=rec.message,
        filename=rec.filename,
        error=rec.error,
        created_at=to_epoch(rec.created_at),
        finished_at=to_epoch(rec.finished_at),
    )


@router.get("/project/upload/{task_id}/stream")
def upload_stream(
    task_id: str,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_session)],
):
    """订阅上传任务进度 SSE:DB 快照首帧 + 轮询转发,直到 done 帧"""
    rec = db.execute(
        select(models.BuildTask).where(
            models.BuildTask.task_id == task_id,
            models.BuildTask.user_id == current_user.id,
            models.BuildTask.task == "ingest-project",
        )
    ).scalars().first()
    if rec is None:
        raise HTTPException(status_code=404, detail="task not found")

    return StreamingResponse(
        stream_task_events(task_id), media_type=SSE_MEDIA_TYPE, headers=SSE_HEADERS
    )


@router.get("/project/template", response_model=ProjectTemplateOut)
def get_project_template(current_user: CurrentUser):
    """
    返回标准模板，供用户下载后按格式填写。

    与入库校验基准、LLM 下发的标准格式是同一份文件，因此"照着写的文档"
    能通过校验直接入库，不会经过大模型改写。
    """
    return ProjectTemplateOut(
        file_name=TEMPLATE_DOWNLOAD_NAME, content=load_template()
    )


@router.get("/project/docs", response_model=ProjectDocList)
def list_project_docs(current_user: CurrentUser):
    """我的项目文档列表 = 公共素材 + 我上传的"""
    docs = list_visible_project_docs(current_user.id)
    return ProjectDocList(
        docs=[
            ProjectDocOut(
                doc_id=d.doc_id,
                project_name=d.project_name,
                file_name=d.file_name,
                tags=d.tags or [],
                has_resume_section=d.has_resume_section,
                is_public=d.user_id is None,
                created_at=to_epoch(d.created_at),
                updated_at=to_epoch(d.updated_at),
            )
            for d in docs
        ],
        total=len(docs),
        max_docs=settings.project_doc.max_docs_per_user,
    )


@router.delete("/project/docs/{doc_id}", status_code=204)
def delete_project_doc_endpoint(doc_id: str, current_user: CurrentUser):
    """删除项目文档。越权（他人的/公共素材）统一返回 404，不泄露存在性。"""
    rec = get_project_doc(doc_id)
    if rec is None or rec.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="project doc not found")

    if not delete_project_doc_everywhere(doc_id):
        raise HTTPException(status_code=404, detail="project doc not found")


@router.post("/project/docs/batch-delete", response_model=ProjectDocBatchDeleteResult)
def multi_delete_project_docs_endpoint(
    payload: ProjectDocBatchDeleteIn, current_user: CurrentUser
):
    """
    批量删除项目文档。不用 DELETE+body（代理可能丢 body）而用 POST；
    越权/不存在的 id 不报错、静默跳过并回显在 not_found_ids，
    与单条删除「不泄露存在性」的策略一致。
    """
    doc_ids = list(dict.fromkeys(payload.doc_ids))  # 保序去重
    deleted = delete_project_docs_everywhere(current_user.id, doc_ids)
    deleted_set = set(deleted)
    return ProjectDocBatchDeleteResult(
        deleted_ids=deleted,
        not_found_ids=[doc_id for doc_id in doc_ids if doc_id not in deleted_set],
    )