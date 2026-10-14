"""
/api/project/preprocess 与 /api/project/confirm 测试

覆盖：两层预处理的行为、预览归属校验、确认后落库、以及各类同步校验。
LLM 全部 mock，Celery 入队全部拦截，不连真服务、不碰真库。
"""
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import src.database.models  # noqa: F401
from src.api.app import create_app
from src.api.routers import project as project_router
from src.auth.security import get_current_user
from src.config import get_config
from src.database.database import Base, get_session
from src.database.utils import project_docs as pd_utils

ME = 9
OTHER = 10

TIDY = """# 智能问答助手

## 一句话简介
基于大模型的问答系统。

## 简历描述
负责检索链路设计。

## 技术栈
Python, FAISS
"""

MESSY = "我做了个RAG问答项目，用FAISS检索，BM25混合召回，主要负责检索链路。"


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'pp.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(pd_utils, "SessionLocal", Session)

    app = create_app()

    def _override():
        with Session() as s:
            yield s

    app.dependency_overrides[get_session] = _override

    from src.worker import task_store
    monkeypatch.setattr(task_store, "SessionLocal", Session)
    monkeypatch.setattr(project_router, "ingest_project_task", _FakeTask())

    # 落盘目录指向 tmp，避免污染 data/。
    # PathConfig 的目录是只读 property，无 setter，只能整体替换 paths
    previews = tmp_path / "previews"
    uploads = tmp_path / "uploads"
    previews.mkdir()
    uploads.mkdir()
    monkeypatch.setattr(get_config(), "paths", SimpleNamespace(
        project_previews_dir=previews, resume_uploads_dir=uploads))

    yield TestClient(app)
    app.dependency_overrides.clear()
    engine.dispose()


class _FakeTask:
    def __init__(self):
        self.calls = []

    def apply_async(self, *a, **k):
        self.calls.append(k)
        return None


@pytest.fixture
def me(client):
    user = src.database.models.User(id=ME, username="me", email="me@x.com",
                                    password_hash="x", is_superuser=False)
    client.app.dependency_overrides[get_current_user] = lambda: user
    return user


def upload(client, name="a.md", content=TIDY, ctype="text/markdown"):
    return client.post("/api/project/preprocess", files={"file": (name, content, ctype)})


class TestPreprocessValidation:
    def test_rejects_non_md(self, client, me):
        assert upload(client, "a.txt").status_code == 400

    def test_rejects_oversized(self, client, me):
        big = "x" * (project_router.settings.project_doc.max_file_size + 1)
        assert upload(client, "a.md", big).status_code == 413

    def test_unauthenticated_rejected(self, client):
        r = client.post("/api/project/preprocess",
                        files={"file": ("a.md", TIDY, "text/markdown")})
        assert r.status_code in (401, 403)


class TestPreprocessLayer1:
    """第 1 层：规则化容错，零成本、确定性，不调 LLM"""

    def test_missing_h1_uses_filename(self, client, me):
        """
        此前无标题直接拒收；现在回退文件名。
        这里让"模型"故意返回缺一级标题的输出，以验证兜底链路最终生效。
        """
        with _patch_llm("## 技术栈\nPython"):   # 缺 H1 → 归一化回退
            r = upload(client, "智能问答助手.md", "没有标题的纯文本说明。")

        assert r.status_code == 200
        assert r.json()["project_name"] == "智能问答助手"
        assert r.json()["normalized"] is False

    def test_filename_suffix_stripped(self, client, me):
        with _patch_llm("## 技术栈\nPython"):
            r = upload(client, "智能问答助手-项目亮点.md", "没有标题。")
        assert r.json()["project_name"] == "智能问答助手"

    def test_template_conforming_doc_not_normalized(self, client, me):
        """符合项目文档模板 → 跳过第 2 层，避免模型无谓改写原文"""
        r = upload(client, "a.md", TIDY)
        body = r.json()
        assert body["normalized"] is False, "合模板的文档不该被改写"
        assert "无需整理" in body["reason"]
        assert body["has_resume_section"] is True


class TestPreprocessLayer2:
    """第 2 层：结构缺失时交给大模型，失败回退原文"""

    def test_messy_doc_normalized(self, client, me):
        out = "# 智能问答助手\n\n## 技术栈\nFAISS, BM25\n\n## 简历描述\n负责检索链路\n"
        with _patch_llm(out):
            r = upload(client, "智能问答助手.md", MESSY)

        body = r.json()
        assert r.status_code == 200
        assert body["normalized"] is True
        assert body["normalized_text"].startswith("# 智能问答助手")
        assert body["original_text"] == MESSY, "原文必须保留以便用户比对"

    def test_llm_failure_falls_back_gracefully(self, client, me):
        """归一化失败不能导致上传失败——回退原文，用户仍可确认入库"""
        with _patch_llm(side_effect=RuntimeError("api down")):
            r = upload(client, "智能问答助手.md", MESSY)

        body = r.json()
        assert r.status_code == 200
        assert body["normalized"] is False
        assert body["normalized_text"].startswith("# 智能问答助手"), "无标题时要补标题再回退"
        assert "api down" in body["reason"]


class TestConfirm:
    def _preview(self, client, me, content=TIDY, name="a.md"):
        r = upload(client, name, content)
        assert r.status_code == 200
        return r.json()

    def test_confirm_enqueues_task(self, client, me):
        prev = self._preview(client, me)
        r = client.post("/api/project/confirm", json={"preview_id": prev["preview_id"]})

        assert r.status_code == 202
        body = r.json()
        assert body["status"] == "pending"
        task = project_router.ingest_project_task
        assert len(task.calls) == 1
        assert task.calls[0]["args"][2] == ME

    def test_preview_consumed_after_confirm(self, client, me):
        """同一预览不能重复提交"""
        prev = self._preview(client, me)
        client.post("/api/project/confirm", json={"preview_id": prev["preview_id"]})
        again = client.post("/api/project/confirm", json={"preview_id": prev["preview_id"]})
        assert again.status_code == 404

    def test_edited_text_is_written(self, client, me):
        """用户在预览里改过内容后，落盘的应是编辑后的文本"""
        prev = self._preview(client, me)
        edited = "# 智能问答助手\n\n## 简历描述\n用户手改的版本。\n"
        r = client.post("/api/project/confirm",
                        json={"preview_id": prev["preview_id"], "text": edited})
        assert r.status_code == 202

        upload_path = Path(project_router.ingest_project_task.calls[0]["args"][0])
        assert upload_path.read_text(encoding="utf-8") == edited

    def test_unknown_preview_404(self, client, me):
        assert client.post("/api/project/confirm",
                           json={"preview_id": "nope"}).status_code == 404

    def test_other_users_preview_404(self, client, me):
        """不能确认别人的预处理结果"""
        prev = self._preview(client, me)

        other = src.database.models.User(id=OTHER, username="o", email="o@x.com",
                                         password_hash="x")
        client.app.dependency_overrides[get_current_user] = lambda: other
        r = client.post("/api/project/confirm", json={"preview_id": prev["preview_id"]})
        assert r.status_code == 404

    def test_empty_edited_text_falls_back_to_preview(self, client, me):
        """用户清空了编辑框时回退到整理结果，绝不入库空文档"""
        prev = self._preview(client, me)
        r = client.post("/api/project/confirm",
                        json={"preview_id": prev["preview_id"], "text": "   "})
        assert r.status_code == 202

        upload_path = Path(project_router.ingest_project_task.calls[0]["args"][0])
        assert upload_path.read_text(encoding="utf-8").strip(), "不能入库空文档"


class TestPreviewSweep:
    """
    预处理暂存里是**用户文档全文**，只有 confirm 成功才 unlink。
    用户关掉预览弹窗就再没人来收 → 必须有过期回收，否则无限堆积。
    """

    @staticmethod
    def _aged(preview_dir, name, age_seconds):
        path = preview_dir / name
        path.write_text(json.dumps({"user_id": ME, "text": "旧预览全文"}),
                        encoding="utf-8")
        old = time.time() - age_seconds
        os.utime(path, (old, old))
        return path

    def test_expired_preview_removed_on_next_preprocess(self, client, me):
        preview_dir = get_config().paths.project_previews_dir
        stale = self._aged(preview_dir, "stale.json", 25 * 3600)

        r = upload(client, "a.md", TIDY)

        assert r.status_code == 200
        assert not stale.exists(), "过期预览未被回收"
        # 本次生成的预览不能顺手被删掉
        fresh = preview_dir / f"{r.json()['preview_id']}.json"
        assert fresh.exists()

    def test_fresh_preview_kept(self, client, me):
        preview_dir = get_config().paths.project_previews_dir
        recent = self._aged(preview_dir, "recent.json", 60)

        assert upload(client, "a.md", TIDY).status_code == 200
        assert recent.exists(), "未过期的预览被误删"

    def test_sweep_returns_removed_count(self, client, me):
        from src.api.services.project_service import sweep_expired_previews

        preview_dir = get_config().paths.project_previews_dir
        self._aged(preview_dir, "a.json", 100)
        self._aged(preview_dir, "b.json", 100)
        self._aged(preview_dir, "c.json", 1)

        assert sweep_expired_previews(ttl_seconds=50) == 2
        assert (preview_dir / "c.json").exists()

    def test_sweep_survives_missing_dir(self, client, me, tmp_path):
        """目录不存在不能让上传链路崩掉"""
        from src.api.services.project_service import sweep_expired_previews

        get_config().paths.project_previews_dir = tmp_path / "not-exist"
        assert sweep_expired_previews() == 0


@contextmanager
def _patch_llm(content=None, side_effect=None):
    """拦截 APIProcessor：要么返回指定内容，要么抛异常"""
    with patch("src.api_client.APIProcessor") as MockAPI:
        inst = MockAPI.return_value
        if side_effect is not None:
            inst.send_message.side_effect = side_effect
        else:
            inst.send_message.return_value = {"content": content}
        yield MockAPI
