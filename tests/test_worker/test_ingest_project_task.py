"""
ingest_project_task 任务体测试。

不起真 worker、不连 broker、不碰真 DB：mock update_state / update_task / 两个 Ingestor。
覆盖契约：
- 进度双通道：update_state(PROGRESS) 给 backend，update_task 给权威 DB
- status 必须在 TaskState 四态内（回归守卫）
- 成功：源 md 落盘到用户目录 + 写分块 + 建 BM25/FAISS 索引
- 无一级标题 → failed，且清掉已落盘的半截产物（不留"上传了但检索不到"的幽灵）
- .upload 无论成功失败都在 finally 清理
- 项目名做文件名 sanitize（含路径分隔符不能写穿目录）
- 只调 process_single，绝不调 process_chunks_dir（会扫到他人文档）
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.api.schemas_api import TaskState
from src.config import get_config
from src.worker import tasks as tasks_module
from src.worker.celery_app import celery_app
from src.worker.tasks import _dest_path, ingest_project_task

DOC = """# 项目甲

> 标签：简历优先

## 一句话简介
一句话介绍。

## 技术栈
Python
"""


class TestTaskRoutes:
    def test_routes_to_project_queue(self):
        """独立队列：不能复用 knowledge（concurrency=1 且跑 build-all，上传会排队）"""
        routes = celery_app.conf.task_routes
        assert routes["resumeagent.ingest_project"]["queue"] == "project"


class TestDestPath:
    def test_strips_path_separators(self):
        """项目名来自文档标题，含 / 时不能写穿目录"""
        name = _dest_path(Path("."), "a/b", 9).name
        assert "/" not in name and "\\" not in name
        assert name.endswith(".md")

    def test_strips_other_illegal_chars(self):
        for ch in ':*?"<>|':
            assert ch not in _dest_path(Path("."), f"项目{ch}名", 9).name

    def test_empty_name_falls_back(self):
        assert _dest_path(Path("."), "...", 9).name.startswith("project-")

    def test_truncates_long_name(self):
        # 主干 100 字 + "-" + 12 位摘要 + ".md"
        assert len(_dest_path(Path("."), "名" * 500, 9).name) <= 116

    def test_sanitizing_collision_does_not_share_path(self):
        """
        "A/B" 与 "A_B" sanitize 后同名。共用一个落盘路径的后果：
        后传的覆盖先传的源 md，而两行 DB 记录（doc_id 不同）指向同一 source_path，
        删任一行会 purge 掉另一行的源文件。
        """
        a = _dest_path(Path("."), "A/B", 9)
        b = _dest_path(Path("."), "A_B", 9)
        assert a != b

    def test_same_name_same_user_is_stable(self):
        """同名项目重复上传必须落到同一路径（覆盖语义，不能越攒越多）"""
        assert _dest_path(Path("."), "项目甲", 9) == _dest_path(Path("."), "项目甲", 9)


@pytest.fixture
def task_env(tmp_path, monkeypatch):
    """产物目录重定向到 tmp"""
    user_dir = tmp_path / "u9"
    chunks = tmp_path / "chunks"
    for d in (user_dir, chunks, tmp_path / "bm25", tmp_path / "vec"):
        d.mkdir()
    monkeypatch.setattr(get_config(), "paths", SimpleNamespace(
        user_projects_dir=lambda uid: tmp_path / f"u{uid}",
        course_chunks_dir=chunks,
        bm25_dbs_dir=tmp_path / "bm25",
        vector_dbs_dir=tmp_path / "vec",
    ))
    upload = tmp_path / "x.upload.md"
    upload.write_bytes(DOC.encode("utf-8"))
    return tmp_path, upload


class TestIngestProjectTask:
    def test_success_flow(self, task_env):
        tmp_path, upload = task_env
        bm25 = MagicMock()
        vec = MagicMock()

        with patch("src.knowledge.ingestion.BM25Ingestor", return_value=bm25), \
                patch("src.knowledge.ingestion.VectorDBIngestor", return_value=vec), \
                patch("src.knowledge.project_doc.upsert_project_doc") as mock_upsert, \
                patch.object(ingest_project_task, "update_state") as mock_state, \
                patch.object(tasks_module, "update_task") as mock_db:
            result = ingest_project_task(str(upload), "x.md", 9)

        assert result["project_name"] == "项目甲"
        assert result["doc_id"].startswith("project-u9-")

        # 源文档落到用户目录（文件名带 doc_id 摘要段防撞名）
        dests = list((tmp_path / "u9").glob("项目甲-*.md"))
        assert len(dests) == 1, dests

        # 分块已写出，且 doc_id 与返回一致
        chunk = tmp_path / "chunks" / f"{result['doc_id']}.json"
        assert chunk.exists()

        # 两个索引都只处理了这一个文档
        bm25.process_single.assert_called_once()
        vec.process_single.assert_called_once()
        bm25.process_chunks_dir.assert_not_called()
        vec.process_chunks_dir.assert_not_called()

        # 登记
        assert mock_upsert.call_count == 1
        assert mock_upsert.call_args.kwargs["user_id"] == 9
        assert mock_upsert.call_args.kwargs["tags"] == ["简历优先"]

        # 进度双通道
        assert mock_state.call_count == 3
        for c in mock_state.call_args_list:
            assert c.kwargs["state"] == "PROGRESS"

        statuses = [c.kwargs.get("status") for c in mock_db.call_args_list]
        assert statuses[0] == "running" and statuses[-1] == "success"
        for s in statuses:
            if s is not None:
                assert s in TaskState.__args__, f"status={s!r} 破坏四态契约"

        assert not upload.exists(), ".upload 必须在 finally 清理"

    def test_missing_h1_uses_filename_and_ingests(self, task_env):
        """
        行为变更：无一级标题不再拒收，改用**原始文件名**兜底。
        此前会直接 failed，用户手写的纯文本说明无法入库。
        """
        tmp_path, upload = task_env
        upload.write_bytes("没有一级标题的文档".encode("utf-8"))

        with patch("src.knowledge.ingestion.BM25Ingestor"), \
                patch("src.knowledge.ingestion.VectorDBIngestor"), \
                patch("src.knowledge.project_doc.upsert_project_doc"), \
                patch.object(ingest_project_task, "update_state"), \
                patch.object(tasks_module, "update_task") as mock_db:
            result = ingest_project_task(str(upload), "我的项目.md", 9)

        assert result["project_name"] == "我的项目"
        assert mock_db.call_args_list[-1].kwargs["status"] == "success"
        # 源文档按项目名落盘（不是按临时上传名）
        assert list((tmp_path / "u9").glob("我的项目-*.md"))

    def test_unusable_filename_still_fails_and_cleans(self, task_env):
        """文件名也不可用时仍需失败，且不留半截产物"""
        tmp_path, upload = task_env
        upload.write_bytes("没有标题".encode("utf-8"))

        with patch("src.knowledge.ingestion.BM25Ingestor"), \
                patch("src.knowledge.ingestion.VectorDBIngestor"), \
                patch("src.knowledge.project_doc.upsert_project_doc"), \
                patch.object(ingest_project_task, "update_state"), \
                patch.object(tasks_module, "update_task") as mock_db:
            with pytest.raises(ValueError):
                ingest_project_task(str(upload), "", 9)

        final = mock_db.call_args_list[-1].kwargs
        assert final["status"] == "failed"
        assert final["status"] in TaskState.__args__

        # 不允许留下源文档或分块
        assert list((tmp_path / "u9").glob("*.md")) == []
        assert list((tmp_path / "chunks").glob("*.json")) == []
        assert not upload.exists()
        assert list((tmp_path / "chunks").glob("*.json")) == []
        assert not upload.exists()

    def test_upload_cleaned_even_when_index_fails(self, task_env):
        """索引阶段失败：源文档与分块都要回滚，.upload 同样清理"""
        tmp_path, upload = task_env
        vec = MagicMock()
        vec.process_single.side_effect = RuntimeError("embedding boom")

        with patch("src.knowledge.ingestion.BM25Ingestor"), \
                patch("src.knowledge.ingestion.VectorDBIngestor", return_value=vec), \
                patch("src.knowledge.project_doc.upsert_project_doc"), \
                patch.object(ingest_project_task, "update_state"), \
                patch.object(tasks_module, "update_task") as mock_db:
            with pytest.raises(RuntimeError):
                ingest_project_task(str(upload), "x.md", 9)

        assert mock_db.call_args_list[-1].kwargs["status"] == "failed"
        assert list((tmp_path / "u9").glob("*.md")) == []
        assert list((tmp_path / "chunks").glob("*.json")) == []
        assert not upload.exists()

    def test_index_receives_doc_data_of_this_doc(self, task_env):
        """传给 process_single 的必须是本次文档的 doc_data（不能是别人的）"""
        tmp_path, upload = task_env
        bm25 = MagicMock()

        with patch("src.knowledge.ingestion.BM25Ingestor", return_value=bm25), \
                patch("src.knowledge.ingestion.VectorDBIngestor"), \
                patch("src.knowledge.project_doc.upsert_project_doc"), \
                patch.object(ingest_project_task, "update_state"), \
                patch.object(tasks_module, "update_task"):
            result = ingest_project_task(str(upload), "x.md", 9)

        doc_data = bm25.process_single.call_args.args[0]
        assert doc_data["metainfo"]["doc_id"] == result["doc_id"]
        assert doc_data["metainfo"]["category"] == "project"
        assert doc_data["metainfo"]["source"] == "项目甲"


class TestStaleSameNameCleanup:
    """
    同项目名的旧副本必须随成功入库一起清掉。
    删除接口只 purge DB 记录里的 source_path，留在磁盘上的旧副本会被
    ProjectDocScanner 扫到 → "已删除的项目"继续参与简历素材挑选。
    """

    def _run(self, upload, side_effect=None):
        vec = MagicMock()
        if side_effect:
            vec.process_single.side_effect = side_effect
        with patch("src.knowledge.ingestion.BM25Ingestor"), \
                patch("src.knowledge.ingestion.VectorDBIngestor", return_value=vec), \
                patch("src.knowledge.project_doc.upsert_project_doc"), \
                patch.object(ingest_project_task, "update_state"), \
                patch.object(tasks_module, "update_task"):
            return ingest_project_task(str(upload), "x.md", 9)

    def test_old_copy_removed_on_success(self, task_env):
        tmp_path, upload = task_env
        old = tmp_path / "u9" / "项目甲.md"          # 旧命名规则留下的副本
        old.write_text("# 项目甲\n\n旧内容\n", encoding="utf-8")

        self._run(upload)

        assert not old.exists()
        assert len(list((tmp_path / "u9").glob("*.md"))) == 1

    def test_other_projects_untouched(self, task_env):
        tmp_path, upload = task_env
        other = tmp_path / "u9" / "项目乙-abcdef.md"
        other.write_text("# 项目乙\n\n别的项目\n", encoding="utf-8")

        self._run(upload)

        assert other.exists()

    def test_failure_keeps_old_copy(self, task_env):
        """入库失败要回滚新文件，此时不能把用户的上一个版本也一起删了"""
        tmp_path, upload = task_env
        old = tmp_path / "u9" / "项目甲.md"
        old.write_text("# 项目甲\n\n旧内容\n", encoding="utf-8")

        with pytest.raises(RuntimeError):
            self._run(upload, side_effect=RuntimeError("embedding boom"))

        assert old.exists()
        assert list((tmp_path / "u9").glob("项目甲-*.md")) == []
