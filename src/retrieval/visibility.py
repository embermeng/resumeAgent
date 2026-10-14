"""
项目文档可见性

多用户隔离的唯一入口：把「用户」翻译成「该用户可见的 doc_id 白名单」，
交给检索器的 doc_ids 参数下传。检索层本身不做用户判断。

为什么走白名单而不是检索后过滤：
- FAISS 是纯向量库，不支持属性过滤，只能在召回前用 doc_ids 限定候选；
- 检索后过滤会在 top_n 裁剪之后才生效，既漏结果又有泄露风险；
- 缓存 key（retrieval_cache.build_key）已含 doc_ids，白名单天然让缓存按用户分片，
  不会串号。

权威源是 project_docs 表，不是文件名前缀——后者只用于日志可读性。
"""
import logging
from pathlib import Path
from typing import List, Optional, Tuple

from sqlalchemy import or_, select

import src.database.models as models
from src.database.database import SessionLocal

_log = logging.getLogger(__name__)

# 项目文档的 doc_id 前缀（见 ProjectDocParser.make_doc_id）：公共课程/面试分块是纯 hex
PROJECT_DOC_PREFIX = "project-"


def visible_project_doc_ids(user_id: Optional[int]) -> List[str]:
    """
    该用户可见的项目分块 doc_id 白名单 = 公共素材(user_id IS NULL) + 本人文档。

    user_id 为 None（未登录 / CLI 未指定用户）时只返回公共素材。
    DB 异常时返回空列表——宁可检索不到，也不能回退成"不过滤"导致跨用户泄露。
    """
    try:
        with SessionLocal() as db:
            rows = db.execute(
                select(models.ProjectDoc.doc_id).where(
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


def visible_tagged_project_ids(
    user_id: Optional[int], tag: str
) -> List[Tuple[str, str]]:
    """
    该用户可见、且带指定标签的项目文档 [(doc_id, project_name), ...]。

    tags 是 JSON 列，直接取回在 Python 侧过滤——单用户文档数有上限（见 ProjectDocConfig），
    数据量小，不必引入 JSONB 查询。
    """
    try:
        with SessionLocal() as db:
            rows = db.execute(
                select(models.ProjectDoc.doc_id, models.ProjectDoc.project_name,
                       models.ProjectDoc.tags).where(
                    or_(
                        models.ProjectDoc.user_id.is_(None),
                        models.ProjectDoc.user_id == user_id,
                    )
                )
            ).all()
        return [
            (doc_id, name)
            for doc_id, name, tags in rows
            if tags and tag in tags
        ]
    except Exception as e:
        _log.error(f"查询标签项目文档失败(user_id={user_id}, tag={tag}): {e}")
        return []


def visible_doc_ids(user_id: Optional[int], chunks_dir=None) -> List[str]:
    """
    知识检索的完整可见范围 = 公共分块（课程/面试，doc_id 不带 project- 前缀）
                            + 该用户可见的项目分块。

    为什么不能只传 visible_project_doc_ids：检索器的 doc_ids 是覆盖**全部**文档的
    硬白名单，只给项目文档会把公共课程知识一并切掉，知识问答直接变空。

    任何知识检索（含 category=None 的全库检索与分层检索的降级分支）都必须带这个白名单，
    否则他人私有项目分块会进入自己的上下文；且 doc_ids=None 时缓存 key 对所有用户相同，
    泄露结果还会被 Redis 缓存后分发给所有人。
    """
    if chunks_dir is None:
        from src.config import get_config
        chunks_dir = get_config().paths.course_chunks_dir

    try:
        public = [
            p.stem for p in Path(chunks_dir).glob("*.json")
            if not p.stem.startswith(PROJECT_DOC_PREFIX)
        ]
    except Exception as e:
        _log.error(f"扫描公共分块失败(chunks_dir={chunks_dir}): {e}")
        public = []

    return public + visible_project_doc_ids(user_id)
