"""
project_docs 表的同步 CRUD。

与 dialog.py 同一范式：同步 SessionLocal、写操作吞异常只记日志。
理由：登记/清理是入库的附属动作，DB 不可用时不应阻断主流程
（分块与索引已落盘，后续可回填；但反过来若因 DB 失败导致入库中断则不划算）。
"""
from __future__ import annotations

import logging
from typing import List, Optional

from sqlalchemy import delete, or_, select

import src.database.models as models
from src.database.database import SessionLocal

_log = logging.getLogger(__name__)


def upsert_project_doc(
    doc_id: str,
    project_name: str,
    file_name: str,
    source_path: str,
    user_id: Optional[int] = None,
    tags: Optional[list] = None,
    has_resume_section: bool = False,
    mtime: Optional[float] = None,
) -> bool:
    """按 doc_id 登记或更新一条项目文档记录，返回是否成功"""
    try:
        with SessionLocal() as db:
            rec = db.execute(
                select(models.ProjectDoc).where(models.ProjectDoc.doc_id == doc_id)
            ).scalars().first()
            if rec is None:
                rec = models.ProjectDoc(doc_id=doc_id)
                db.add(rec)
            rec.user_id = user_id
            rec.project_name = project_name
            rec.file_name = file_name
            rec.source_path = source_path
            rec.tags = tags or []
            rec.has_resume_section = has_resume_section
            rec.mtime = mtime
            db.commit()
        return True
    except Exception as e:
        _log.error(f"登记项目文档失败(doc_id={doc_id}): {e}")
        return False


def get_project_doc(doc_id: str) -> Optional[models.ProjectDoc]:
    """按 doc_id 查询；异常返回 None"""
    try:
        with SessionLocal() as db:
            return db.execute(
                select(models.ProjectDoc).where(models.ProjectDoc.doc_id == doc_id)
            ).scalars().first()
    except Exception as e:
        _log.error(f"查询项目文档失败(doc_id={doc_id}): {e}")
        return None


def count_user_project_docs(user_id: int) -> int:
    """该用户的文档数（配额校验用）；异常返回 0（宁可放行也不误拒）"""
    try:
        with SessionLocal() as db:
            return db.execute(
                select(models.ProjectDoc).where(models.ProjectDoc.user_id == user_id)
            ).scalars().all().__len__()
    except Exception as e:
        _log.error(f"统计用户项目文档失败(user_id={user_id}): {e}")
        return 0


def list_visible_project_docs(user_id: Optional[int]) -> List[models.ProjectDoc]:
    """该用户可见的文档 = 公共素材(user_id IS NULL) + 本人"""
    try:
        with SessionLocal() as db:
            rows = db.execute(
                select(models.ProjectDoc).where(
                    or_(
                        models.ProjectDoc.user_id.is_(None),
                        models.ProjectDoc.user_id == user_id,
                    )
                )
            ).scalars().all()
        return list(rows)
    except Exception as e:
        _log.error(f"查询可见项目文档失败(user_id={user_id}): {e}")
        return []


def delete_project_doc(doc_id: str) -> Optional[models.ProjectDoc]:
    """删除记录并返回被删对象（供调用方清理磁盘文件）；不存在返回 None"""
    try:
        with SessionLocal() as db:
            rec = db.execute(
                select(models.ProjectDoc).where(models.ProjectDoc.doc_id == doc_id)
            ).scalars().first()
            if rec is None:
                return None
            db.delete(rec)
            db.commit()
            return rec
    except Exception as e:
        _log.error(f"删除项目文档失败(doc_id={doc_id}): {e}")
        return None


def delete_project_docs_by_ids(user_id: int, doc_ids: List[str]) -> List[models.ProjectDoc]:
    """
    批量删除属于该用户的文档记录并返回被删列表（供磁盘清理）。
    越权/不存在的 doc_id 静默跳过；一次查询过滤归属，一条语句批量删行。
    """
    if not doc_ids:
        return []
    try:
        with SessionLocal() as db:
            rows = db.execute(
                select(models.ProjectDoc).where(
                    models.ProjectDoc.doc_id.in_(doc_ids),
                    models.ProjectDoc.user_id == user_id,
                )
            ).scalars().all()
            docs = list(rows)
            if docs:
                db.execute(
                    delete(models.ProjectDoc).where(
                        models.ProjectDoc.doc_id.in_([d.doc_id for d in docs])
                    )
                )
                db.commit()
            return docs
    except Exception as e:
        _log.error(f"批量删除项目文档失败(user_id={user_id}): {e}")
        return []


def delete_user_project_docs(user_id: int) -> List[models.ProjectDoc]:
    """删除某用户的全部文档记录并返回被删列表（删号前调用，用于清理磁盘文件）"""
    try:
        with SessionLocal() as db:
            rows = db.execute(
                select(models.ProjectDoc).where(models.ProjectDoc.user_id == user_id)
            ).scalars().all()
            docs = list(rows)
            if docs:
                db.execute(
                    delete(models.ProjectDoc).where(models.ProjectDoc.user_id == user_id)
                )
                db.commit()
            return docs
    except Exception as e:
        _log.error(f"删除用户项目文档失败(user_id={user_id}): {e}")
        return []
