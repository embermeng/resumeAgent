"""
Celery 具名任务Worker

与线程模型的对照：
- 线程模型: run_fn 闭包捕获 file_bytes/parser/settings（同进程共享内存，无需序列化）
- Celery 模型: 消息只带「任务名 + 可序列化参数」，依赖由 worker 进程内自建

所以本任务签名只收字符串；PDF 字节、parser、config 都在 worker 里现取现建。
"""
import json
import logging
import re
from pathlib import Path
from uuid import uuid4
import time

from src.worker.celery_app import celery_app
from src.worker.task_store import purge_other_resumes, update_task

_log = logging.getLogger(__name__)

# 项目名来自文档一级标题，可能含路径分隔符等非法文件名字符
_UNSAFE_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]')


def _safe_stem(project_name: str) -> str:
    """把项目名转成安全的文件名主干（保留可读性）"""
    cleaned = _UNSAFE_FILENAME_CHARS.sub("_", project_name).strip().strip(".")
    return (cleaned or "project")[:100]


def _dest_path(user_dir: Path, project_name: str, user_id: int) -> Path:
    """
    源文档落盘路径 = 项目名主干 + doc_id 摘要段。

    只用项目名会撞车：不同项目名经非法字符替换/截断后可能得到同一个文件名
    （"A/B" 与 "A_B"、超 100 字后同前缀）。后传的会覆盖先传的源 md，而两行
    DB 记录（doc_id 按项目名哈希，互不相同）指向同一个 source_path——
    删任一行会 purge 掉另一行的源文件，重建索引也会把 A 的 doc_id 写成 B 的内容。
    带上 doc_id 摘要段后文件名与 doc_id 一一对应，不可能撞。
    """
    from src.knowledge.project_doc import ProjectDocParser

    digest = ProjectDocParser.make_doc_id(project_name, user_id).rsplit("-", 1)[-1]
    return user_dir / f"{_safe_stem(project_name)}-{digest}.md"


def _purge_same_name_stale(user_dir: Path, project_name: str, keep: Path) -> int:
    """
    删掉同目录下**同项目名**的其它 md（旧命名规则或改名留下的副本），返回删除数。

    不删的后果：删除接口只 purge DB 记录里的 source_path，旧副本会留在磁盘上被
    ProjectDocScanner 扫到，等于"已删除的项目"继续参与简历素材挑选。
    """
    from src.knowledge.project_doc import ProjectDocParser

    removed = 0
    for md in user_dir.glob("*.md"):
        if md == keep:
            continue
        try:
            name = ProjectDocParser.extract_project_name(
                ProjectDocParser.clean_markdown(md.read_text(encoding="utf-8")))
        except Exception as e:
            _log.warning("读取项目文档失败，跳过同名清理 %s: %s", md.name, e)
            continue
        if name != project_name:
            continue
        try:
            md.unlink()
            removed += 1
        except Exception as e:
            _log.warning("清理同名旧副本失败 %s: %s", md.name, e)
    return removed


@celery_app.task(name="resumeagent.parse_resume", bind=True)
def parse_resume_task(self, upload_path: str, filename: str, user_id: int | None = None) -> dict:
    """
    解析上传的简历文件为 Markdown 并落盘结果。
    进度经 self.update_state 上报为 PROGRESS 态（meta 带 stage/percent/message）；
    """
    from src.config import get_config
    from src.knowledge.resume_file_parser import ResumeFileParser
    settings = get_config()

    task_id = self.request.id
    try:
        meta={"status": "running", "stage": "parsing", "percent": 50, "message": "MinerU解析中"}
        self.update_state(state="PROGRESS", meta=meta)
        update_task(task_id, **meta)

        # 开始干活
        parser = ResumeFileParser()
        data = Path(upload_path).read_bytes()
        text = parser.parse(filename, data) # 开启后台解析任务

        meta={"stage": "saving", "percent": 90, "message": "解析完成，写入结果"}
        self.update_state(state="PROGRESS", meta=meta)
        update_task(task_id, **meta)
        result_path = f"{settings.paths.resume_uploads_dir}/{uuid4().hex}.md"
        with open(result_path, "w", encoding="utf-8") as f:
            f.write(text)
        update_task(task_id, status="success", percent=100, result_path=result_path, finished_at=time.time())
        # 每用户只保留一份简历:新简历生效后,旧的行与产物才让位(失败时走不到这里,旧的留着)
        if user_id is not None:
            for stale_path in purge_other_resumes(user_id, task_id):
                Path(stale_path).unlink(missing_ok=True)
        return {"result_path": result_path, "filename": filename}
    except Exception as e:
        _log.error("解析简历文件出错: %s", e)
        update_task(task_id, status="failed", error=str(e), finished_at=time.time())
        raise
    finally:
        Path(upload_path).unlink(missing_ok=True)


@celery_app.task(name="resumeagent.ingest_project", bind=True)
def ingest_project_task(self, upload_path: str, filename: str, user_id: int) -> dict:
    """
    项目文档上传入库：落盘源md -> 分块 -> 建 BM25/FAISS 索引。

    与 parse_resume_task 同范式：消息只带路径字符串，依赖在 worker 内自建。
    只处理单个文档，绝不调用 process_chunks_dir（它会遍历全部 chunks 并 prune，
    会扫到他人文档）。
    """
    from src.config import get_config
    from src.knowledge.ingestion import BM25Ingestor, VectorDBIngestor
    from src.knowledge.project_doc import ProjectDocIngestor

    task_id = self.request.id
    dest = None
    doc_id = None
    try:
        meta = {"status": "running", "stage": "parsing", "percent": 10,
                "message": "解析项目文档"}
        self.update_state(state="PROGRESS", meta=meta)
        update_task(task_id, **meta)

        config = get_config()
        ingestor = ProjectDocIngestor()

        # 先解析校验，避免脏文件落盘。
        # 兜底名必须用**原始文件名**——upload_path 是 `{uuid}.upload.md`，
        # 拿它回退会把一串 uuid 变成项目名。
        parsed = ingestor.parse_text(
            Path(upload_path).read_text(encoding="utf-8"), fallback_name=filename)
        if parsed is None:
            raise ValueError("无法确定项目名（文档无一级标题且文件名不可用）")

        meta = {"stage": "saving", "percent": 40, "message": "写入分块"}
        self.update_state(state="PROGRESS", meta=meta)
        update_task(task_id, **meta)

        # 源文档落盘到该用户目录（同名项目覆盖）
        user_dir = config.paths.user_projects_dir(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        dest = _dest_path(user_dir, parsed["project_name"], user_id)
        dest.write_bytes(Path(upload_path).read_bytes())

        doc_id = ingestor.ingest_file(dest, config.paths.course_chunks_dir, user_id=user_id)
        if doc_id is None:
            raise ValueError("项目文档入库失败")

        meta = {"stage": "indexing", "percent": 70, "message": "构建向量索引"}
        self.update_state(state="PROGRESS", meta=meta)
        update_task(task_id, **meta)

        chunk_path = config.paths.course_chunks_dir / f"{doc_id}.json"
        with open(chunk_path, "r", encoding="utf-8") as f:
            doc_data = json.load(f)

        BM25Ingestor().process_single(doc_data, config.paths.bm25_dbs_dir)
        VectorDBIngestor(
            embedding_provider=config.embedding.provider,
            embedding_model=config.embedding.model,
        ).process_single(doc_data, config.paths.vector_dbs_dir)

        # 索引全部建好之后才清同名旧副本：中途失败要回滚 dest，
        # 过早清理会让用户把上一个版本一起丢掉
        stale = _purge_same_name_stale(user_dir, parsed["project_name"], dest)
        if stale:
            _log.info("已清理 %d 份同名旧副本: %s", stale, parsed["project_name"])

        update_task(task_id, status="success", percent=100,
                    result_path=str(dest), finished_at=time.time())
        return {"doc_id": doc_id, "project_name": parsed["project_name"]}
    except Exception as e:
        _log.error("项目文档入库出错: %s", e)
        # 失败时清掉已落盘的半截产物：没有索引的分块会被 refresh 跳过，
        # 留着只会让用户看到"上传了但检索不到"的幽灵文档
        config = get_config()
        for stale in (dest, config.paths.course_chunks_dir / f"{doc_id}.json" if doc_id else None):
            if stale is None:
                continue
            try:
                Path(stale).unlink(missing_ok=True)
            except Exception:
                pass
        update_task(task_id, status="failed", error=str(e), finished_at=time.time())
        raise
    finally:
        Path(upload_path).unlink(missing_ok=True)


@celery_app.task(name="resumeagent.knowledge_build", bind=True)
def knowledge_build_task(self, task: str, force: bool, chunk_size: int, chunk_overlap: int, prune: bool) -> dict:
    """
    知识库构建任务。
    进度经 self.update_state 上报为 PROGRESS 态（meta 带 stage/percent/message）；
    """
    from src.api.services.knowledge_service import KnowledgeService

    task_id = self.request.id
    try:
        meta={"status": "running", "stage": "queued", "percent": 0, "message": "知识库开始构建"}
        self.update_state(state="PROGRESS", meta=meta)
        update_task(task_id, **meta)

        # 开始干活
        def progress(stage: str, message: str, percent: float):
            meta = {"stage": stage, "message": message, "percent": percent}
            self.update_state(state="PROGRESS", meta=meta)
            update_task(task_id, **meta)

        builder = KnowledgeService()
        builder.run(task, force, chunk_size, chunk_overlap, prune, progress=progress)

        update_task(task_id, status="success", percent=100, finished_at=time.time())
        return
    except Exception as e:
        _log.error("构建知识库出错: %s", e)
        update_task(task_id, status="failed", error=str(e), finished_at=time.time())
        raise