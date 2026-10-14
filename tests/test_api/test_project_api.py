"""
/api/project/* 接口测试
覆盖：上传校验与入队、配额、列表可见性、删除、越权防护、未登录拦截

DB 用文件版 SQLite 覆盖同步 get_session（与 tests/test_auth/conftest.py 同思路），
Celery 的 apply_async 全部 mock，不依赖真实 Redis。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import src.database.models  # noqa: F401  注册全部表
from src.api.app import create_app
from src.api.routers import project as project_router
from src.auth.security import get_current_user
from src.config import get_config
from src.database.database import Base, get_session

ME_ID = 9
OTHER_ID = 10


@pytest.fixture
def client(tmp_path, monkeypatch):
    dbfile = tmp_path / "project_api.db"
    engine = create_engine(f"sqlite:///{dbfile}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    def _override():
        with TestSession() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _override

    # 服务层/CRUD 走的是模块级 SessionLocal（不是可注入的 get_session），
    # 必须一并替换，否则测试会读到开发库的真实数据。
    from src.database.utils import project_docs as pd_utils
    from src.retrieval import visibility
    from src.worker import task_store
    monkeypatch.setattr(pd_utils, "SessionLocal", TestSession)
    monkeypatch.setattr(visibility, "SessionLocal", TestSession)
    # 受理端点先 insert_task 落库再 delay(见 celery-insert-delay 约定),
    # task_store 用的也是模块级 SessionLocal:不替换就会把 task="ingest-project"、
    # filename="a.md" 的 pending 行写进开发库,且因 apply_async 被打桩永不消费,
    # 这些行会永远停在"排队中"污染任务列表。
    monkeypatch.setattr(task_store, "SessionLocal", TestSession)

    # 上传落盘到临时目录，别污染真实 data/
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    real = get_config()
    fake_settings = type("S", (), {})()
    fake_settings.project_doc = real.project_doc
    fake_settings.paths = type("P", (), {})()
    fake_settings.paths.resume_uploads_dir = uploads
    monkeypatch.setattr(project_router, "settings", fake_settings)

    yield TestClient(app)

    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def me(client):
    """当前登录用户"""
    user = src.database.models.User(id=ME_ID, username="me", email="me@x.com",
                                    password_hash="x", is_superuser=False)
    client.app.dependency_overrides[get_current_user] = lambda: user
    return user


def _seed_doc(doc_id, user_id, project_name, source_path=None, tags=None):
    from src.database.utils.project_docs import upsert_project_doc
    upsert_project_doc(
        doc_id=doc_id, project_name=project_name, file_name=f"{doc_id}.md",
        source_path=source_path or f"/tmp/{doc_id}.md", user_id=user_id,
        tags=tags or [], has_resume_section=False, mtime=1.0,
    )


class TestUpload:
    def test_rejects_non_markdown(self, client, me):
        r = client.post("/api/project/upload", files={"file": ("a.txt", b"# P", "text/plain")})
        assert r.status_code == 400
        assert ".md" in r.json()["detail"]

    def test_rejects_oversized_file(self, client, me):
        real = get_config()
        big = b"x" * (real.project_doc.max_file_size + 1)
        r = client.post("/api/project/upload", files={"file": ("a.md", big, "text/markdown")})
        assert r.status_code == 413

    def test_enqueues_task_and_returns_202(self, client, me, monkeypatch):
        from unittest.mock import MagicMock
        fake_task = MagicMock()
        monkeypatch.setattr(project_router, "ingest_project_task", fake_task)

        r = client.post("/api/project/upload",
                        files={"file": ("a.md", "# 项目甲\n\n## 简介\n内容", "text/markdown")})
        assert r.status_code == 202
        body = r.json()
        assert body["status"] == "pending"
        assert body["task_id"]
        fake_task.apply_async.assert_called_once()
        # 消息只带路径字符串与 user_id，不带字节本体
        args = fake_task.apply_async.call_args.kwargs["args"]
        assert args[2] == ME_ID

    def test_rejects_when_over_quota(self, client, me, monkeypatch):
        from unittest.mock import MagicMock
        monkeypatch.setattr(project_router, "ingest_project_task", MagicMock())
        limit = get_config().project_doc.max_docs_per_user
        for i in range(limit):
            _seed_doc(f"project-u{ME_ID}-f{i}", ME_ID, f"项目{i}")

        r = client.post("/api/project/upload",
                        files={"file": ("a.md", "# 新项目", "text/markdown")})
        assert r.status_code == 409
        assert "上限" in r.json()["detail"]

    def test_requires_auth(self, client):
        """未登录不得上传——依赖未覆盖时走真实鉴权"""
        r = client.post("/api/project/upload",
                        files={"file": ("a.md", "# P", "text/markdown")})
        assert r.status_code in (401, 403)


class TestListDocs:
    def test_returns_mine_and_public_only(self, client, me):
        _seed_doc("project-u9-mine", ME_ID, "我的项目")
        _seed_doc("project-u10-other", OTHER_ID, "他人的项目")
        _seed_doc("project-public", None, "公共示例")

        r = client.get("/api/project/docs")
        assert r.status_code == 200
        body = r.json()
        names = {d["project_name"] for d in body["docs"]}
        assert names == {"我的项目", "公共示例"}
        assert body["total"] == 2

    def test_flags_public_and_resume_section(self, client, me):
        from src.database.utils.project_docs import upsert_project_doc
        upsert_project_doc(doc_id="project-public", project_name="公共示例",
                           file_name="p.md", source_path="/tmp/p.md", user_id=None,
                           tags=["简历优先"], has_resume_section=True, mtime=1.0)
        _seed_doc("project-u9-mine", ME_ID, "我的项目")

        body = client.get("/api/project/docs").json()
        by_name = {d["project_name"]: d for d in body["docs"]}
        assert by_name["公共示例"]["is_public"] is True
        assert by_name["我的项目"]["is_public"] is False
        assert by_name["公共示例"]["has_resume_section"] is True
        assert by_name["公共示例"]["tags"] == ["简历优先"]


class TestTemplateDownload:
    """下载的模板必须与入库校验基准、LLM 下发格式同源"""

    def test_returns_content_and_filename(self, client, me):
        from src.knowledge.doc_template import load_template

        r = client.get("/api/project/template")
        assert r.status_code == 200
        body = r.json()
        assert body["file_name"].endswith(".md")
        assert body["content"] == load_template()

    def test_content_is_the_validation_baseline(self, client, me):
        """
        照着模板写就应该直接通过校验——这是"同源"最有价值的性质：
        用户填完上传不会被大模型改写。
        """
        from src.knowledge.doc_template import matches_template

        content = client.get("/api/project/template").json()["content"]
        assert matches_template(content) is True

    def test_template_has_required_sections(self, client, me):
        content = client.get("/api/project/template").json()["content"]
        assert "## 一句话简介" in content
        assert "## 简历描述" in content

    def test_requires_auth(self, client):
        r = client.get("/api/project/template")
        assert r.status_code in (401, 403)


class TestDeleteDoc:
    def test_deletes_own_doc_and_disk_artifacts(self, client, me, tmp_path):
        src = tmp_path / "src.md"
        src.write_text("# 甲", encoding="utf-8")
        _seed_doc("project-u9-mine", ME_ID, "我的项目", source_path=str(src))

        r = client.delete("/api/project/docs/project-u9-mine")
        assert r.status_code == 204
        assert not src.exists(), "源文件应被清理"
        assert client.get("/api/project/docs").json()["total"] == 0

    def test_cannot_delete_other_users_doc(self, client, me):
        _seed_doc("project-u10-other", OTHER_ID, "他人的项目")
        r = client.delete("/api/project/docs/project-u10-other")
        assert r.status_code == 404, "越权删除必须 404，且不泄露存在性"
        # 文档仍在
        assert any(d["doc_id"] == "project-u10-other"
                   for d in client.get("/api/project/docs").json()["docs"]) is False
        from src.database.utils.project_docs import get_project_doc
        assert get_project_doc("project-u10-other") is not None, "他人文档不应被删"

    def test_cannot_delete_public_doc(self, client, me):
        _seed_doc("project-public", None, "公共示例")
        assert client.delete("/api/project/docs/project-public").status_code == 404

    def test_delete_missing_returns_404(self, client, me):
        assert client.delete("/api/project/docs/not-exist").status_code == 404
