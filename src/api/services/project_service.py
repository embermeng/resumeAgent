"""
项目文档服务层

职责：磁盘产物清理（purge）、配额校验与预处理暂存回收。路由层只做 HTTP/SSE 转换
与鉴权判断，具体规则收敛在这里，便于 Celery worker 与 API 复用。
"""
import logging
import os
import time
from pathlib import Path
from typing import List, Optional

from src.config import get_config
from src.database.utils.project_docs import (
    count_user_project_docs,
    delete_project_doc,
    delete_project_docs_by_ids,
)

_log = logging.getLogger(__name__)

# 预处理暂存的最长存活时间：预览弹窗里用户可以慢慢看，但不会跨天
PREVIEW_TTL_SECONDS = int(os.getenv("PROJECT_PREVIEW_TTL", str(24 * 3600)))


def sweep_expired_previews(
    ttl_seconds: int = PREVIEW_TTL_SECONDS, now: Optional[float] = None
) -> int:
    """
    删除过期的预处理暂存 JSON，返回删除数。

    暂存里是**用户文档全文**，而只有 confirm 成功才会 unlink——用户关掉预览弹窗
    就再没人来收，不扫会无限堆积。挂在 preprocess 入口顺带做，不必另起定时任务。
    清理失败只记日志，绝不影响上传主流程。
    """
    now = time.time() if now is None else now
    preview_dir = get_config().paths.project_previews_dir
    try:
        candidates = list(Path(preview_dir).glob("*.json"))
    except Exception as e:
        _log.warning(f"扫描预处理暂存目录失败 {preview_dir}: {e}")
        return 0

    removed = 0
    for path in candidates:
        try:
            if now - path.stat().st_mtime < ttl_seconds:
                continue
            path.unlink()
            removed += 1
        except Exception as e:
            _log.warning(f"清理过期预览失败 {path.name}: {e}")
    if removed:
        _log.info(f"已清理过期预处理暂存 {removed} 份")
    return removed


def purge_disk_artifacts(source_path: Optional[str], doc_id: Optional[str]) -> int:
    """
    删除一个项目文档在磁盘上的全部产物：源 md / 分块 JSON / faiss / pkl。
    逐项删除、单项失败不影响其余（无法做到跨文件系统原子），返回成功删除数。
    """
    config = get_config()
    targets = []
    if source_path:
        targets.append(Path(source_path))
    if doc_id:
        targets += [
            config.paths.course_chunks_dir / f"{doc_id}.json",
            config.paths.vector_dbs_dir / f"{doc_id}.faiss",
            config.paths.bm25_dbs_dir / f"{doc_id}.pkl",
        ]

    removed = 0
    for path in targets:
        try:
            if path.exists():
                path.unlink()
                removed += 1
        except Exception as e:
            _log.error(f"删除产物失败 {path}: {e}")
    return removed


def delete_project_doc_everywhere(doc_id: str) -> bool:
    """
    完整删除一个项目文档：先取记录（行删了就没 doc_id 了）-> 清磁盘 -> 删 DB 行。
    返回是否删除成功（记录不存在返回 False）。
    """
    rec = delete_project_doc(doc_id)
    if rec is None:
        return False
    purge_disk_artifacts(rec.source_path, rec.doc_id)
    _log.info(f"已删除项目文档: {rec.project_name} ({doc_id})")
    return True


def delete_project_docs_everywhere(user_id: int, doc_ids: List[str]) -> List[str]:
    """
    批量删除属于该用户的项目文档（DB 行 + 磁盘产物）。
    越权/不存在的 doc_id 静默跳过，返回实际删除成功的 doc_id 列表。
    """
    docs = delete_project_docs_by_ids(user_id, doc_ids)
    deleted = []
    for rec in docs:
        purge_disk_artifacts(rec.source_path, rec.doc_id)
        deleted.append(rec.doc_id)
        _log.info(f"已删除项目文档: {rec.project_name} ({rec.doc_id})")
    return deleted


def check_quota(user_id: int) -> Optional[str]:
    """返回拒绝对话（None 表示通过）。新增文档前调用，防止 embedding 成本失控。"""
    limit = get_config().project_doc.max_docs_per_user
    if count_user_project_docs(user_id) >= limit:
        return f"项目文档数量已达上限（{limit} 份），请先删除后再上传"
    return None
