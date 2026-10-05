"""
Celery 具名任务Worker

与线程模型的对照：
- 线程模型: run_fn 闭包捕获 file_bytes/parser/settings（同进程共享内存，无需序列化）
- Celery 模型: 消息只带「任务名 + 可序列化参数」，依赖由 worker 进程内自建

所以本任务签名只收字符串；PDF 字节、parser、config 都在 worker 里现取现建。
"""
import logging
from pathlib import Path
from uuid import uuid4
import time

from src.worker.celery_app import celery_app
from src.worker.task_store import update_task

_log = logging.getLogger(__name__)


@celery_app.task(name="resumeagent.parse_resume", bind=True)
def parse_resume_task(self, upload_path: str, filename: str) -> dict:
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
        return {"result_path": result_path, "filename": filename}
    except Exception as e:
        _log.error("解析简历文件出错: %s", e)
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