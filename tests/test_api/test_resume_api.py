"""简历解析接口测试(Celery 版):/api/resume/* 五端点 + 受理竞态守卫 + 坑①回归。

架构:端点受理后 apply_async 投递(被 patch 拦截,不连真 broker),DB(build_tasks)
是唯一权威状态源——受理时 insert pending 行,worker 更新,端点只读。
SSE 走 task_stream 适配器(DB 快照首帧 + 轮询转发)。

覆盖契约 docs/specs/api-contract.md §4.3(提交)/§4.4(状态)/§4.4b(删除)/§4.5(流)/§4.5b(我的当前简历),以及:
  - 受理竞态:先 insert 后 delay(投递那一刻 pending 行必须已存在)
  - 消息只带可序列化字符串参数(路径+文件名),字节落盘
  - IDOR:跨用户查询/订阅统一 404
  - SSE 404 必须在 StreamingResponse 之前
  - 每人一份简历:当前简历取最近一条成功行,无简历走 204 而非 404
  - 坑①回归:parse-resume 不得污染 knowledge 列表/状态(Step B 后 knowledge 也只读 DB,过滤在查询层)
"""
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

import src.api.routers.resume as resume_module
import src.database.models as models  # noqa: F401  注册全部表到 Base.metadata
import src.worker.task_store as task_store
from src.api.app import create_app
from src.auth.security import get_current_user
from src.database.database import Base, get_async_session, get_session
from tests.test_api.helpers import event_names, parse_sse


def _mk_user(uid=1):
    """假用户:端点只用到 current_user.id"""
    u = MagicMock()
    u.id = uid
    return u


@pytest.fixture
def env(tmp_path, monkeypatch):
    """测试环境:
    - resume.settings → 假(resume_uploads_dir=tmp_path,.upload 不污染真实目录)
    - SQLite 文件库三方共享:异步 override(状态端点)/同步 override(stream 404 校验)/
      task_store.SessionLocal(受理 insert)
    - apply_async 全程被 patch 拦截(不连真 broker)
    """
    fake_settings = MagicMock()
    fake_settings.paths.resume_uploads_dir = tmp_path
    monkeypatch.setattr(resume_module, "settings", fake_settings)

    dbfile = tmp_path / "resume_test.db"
    sync_engine = create_engine(
        f"sqlite:///{dbfile}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine)
    SyncSession = sessionmaker(bind=sync_engine)
    monkeypatch.setattr(task_store, "SessionLocal", SyncSession)

    async_engine = create_async_engine(
        f"sqlite+aiosqlite:///{dbfile}", connect_args={"check_same_thread": False})
    TestSession = async_sessionmaker(
        async_engine, expire_on_commit=False, class_=AsyncSession)

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _mk_user(1)

    def _override_sync():
        with Session(sync_engine) as s:
            yield s

    async def _override_async():
        async with TestSession() as session:
            yield session

    app.dependency_overrides[get_session] = _override_sync
    app.dependency_overrides[get_async_session] = _override_async

    client = TestClient(app)

    def set_user(uid):
        app.dependency_overrides[get_current_user] = lambda: _mk_user(uid)

    def seed_task(**kw):
        """手动往 SQLite 插一条 BuildTask(模拟 worker 已推进的状态)"""
        with SyncSession() as s:
            s.add(models.BuildTask(**kw))
            s.commit()

    def db_row(task_id):
        with SyncSession() as s:
            return s.query(models.BuildTask).filter_by(task_id=task_id).first()

    with patch.object(resume_module.parse_resume_task, "apply_async") as mock_apply:
        yield SimpleNamespace(
            app=app, client=client, tmp_path=tmp_path,
            apply_async=mock_apply, set_user=set_user,
            seed_task=seed_task, db_row=db_row,
        )

    async_engine.sync_engine.dispose()
    sync_engine.dispose()


class TestSupportedExtensions:
    def test_returns_four(self, env):
        r = env.client.get("/api/resume/supported-extensions")
        assert r.status_code == 200
        assert set(r.json()["extensions"]) == {".md", ".txt", ".docx", ".pdf"}


class TestParseSubmit:
    def test_202_ack_db_row_and_serializable_dispatch(self, env):
        r = env.client.post(
            "/api/resume/parse",
            files={"file": ("resume.md", b"# content", "text/markdown")},
        )
        assert r.status_code == 202
        body = r.json()
        assert body["status"] == "pending"
        tid = body["task_id"]

        # 投递契约:apply_async 显式带 task_id(与回执一致),args 只含可序列化参数
        env.apply_async.assert_called_once()
        kw = env.apply_async.call_args.kwargs
        assert kw["task_id"] == tid
        path_arg, name_arg, uid_arg = kw["args"]
        assert isinstance(path_arg, str) and isinstance(name_arg, str)
        assert name_arg == "resume.md"
        # 第三参 user_id:worker 成功回写后据此清掉该用户的旧简历(每人只留一份)
        assert uid_arg == 1

        # DB 行已就位:pending + queued/0 初值 + 归属 + 文件名
        row = env.db_row(tid)
        assert (row.status, row.task, row.user_id, row.filename) == \
            ("pending", "parse-resume", 1, "resume.md")
        assert (row.stage, row.percent) == ("queued", 0)

        # 字节落盘(消息传路径不传本体),落在 resume_uploads_dir
        upload = Path(path_arg)
        assert upload.parent == env.tmp_path
        assert upload.read_bytes() == b"# content"

    def test_insert_before_delay_race_guard(self, env):
        """经典竞态回归:投递那一刻 DB 必须已有 pending 行(先 insert 再 delay)。
        反序则 worker 可能抢先 update(0 行静默丢失),insert 又把状态压回 pending。"""
        seen = {}

        def _check(*args, **kw):
            seen["row_exists"] = env.db_row(kw["task_id"]) is not None

        env.apply_async.side_effect = _check
        r = env.client.post(
            "/api/resume/parse",
            files={"file": ("resume.md", b"x", "text/markdown")},
        )
        assert r.status_code == 202
        assert seen["row_exists"] is True

    def test_unsupported_extension_400(self, env):
        r = env.client.post(
            "/api/resume/parse",
            files={"file": ("bad.xyz", b"x", "application/octet-stream")},
        )
        assert r.status_code == 400
        assert "不支持" in r.json()["detail"]
        env.apply_async.assert_not_called()          # 白名单在投递前拦
        assert not list(env.tmp_path.glob("*.upload*"))  # 也不留落盘垃圾

    def test_missing_file_422(self, env):
        assert env.client.post("/api/resume/parse").status_code == 422

    def test_requires_auth_401(self):
        # 不 override get_current_user:无 token → oauth2_scheme(auto_error) → 401
        client = TestClient(create_app())
        r = client.post(
            "/api/resume/parse",
            files={"file": ("resume.md", b"x", "text/markdown")},
        )
        assert r.status_code == 401


class TestParseStatus:
    def test_success_returns_content(self, env):
        out = env.tmp_path / "r1.md"
        out.write_text("# 张三的简历", encoding="utf-8")
        env.seed_task(task_id="t-ok", task="parse-resume", status="success",
                      user_id=1, filename="resume.md", result_path=str(out),
                      finished_at=datetime.now(UTC))
        r = env.client.get("/api/resume/parse/t-ok")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "success"
        assert body["content"] == "# 张三的简历"   # 从 result_path 文件读回
        assert body["filename"] == "resume.md"
        assert "task" not in body                  # ResumeParseStatus 不含 task 字段

    def test_running_no_content(self, env):
        env.seed_task(task_id="t-run", task="parse-resume", status="running",
                      user_id=1, stage="parsing", percent=50, message="MinerU解析中")
        body = env.client.get("/api/resume/parse/t-run").json()
        assert body["status"] == "running"
        assert body["content"] is None
        assert body["percent"] == 50

    def test_not_found_404(self, env):
        assert env.client.get("/api/resume/parse/nope").status_code == 404

    def test_cross_user_404(self, env):
        # IDOR:user2 的任务 user1 查 → 404(不泄露存在性)
        env.seed_task(task_id="t-2", task="parse-resume", status="success", user_id=2)
        env.set_user(1)
        assert env.client.get("/api/resume/parse/t-2").status_code == 404


class TestCurrentResume:
    """GET /api/resume/current:每人一份简历,前端挂载即取回正文自动带入上下文。"""

    def test_returns_newest_success_with_content(self, env):
        now = datetime.now(UTC)
        old_md = env.tmp_path / "old.md"
        old_md.write_text("# 旧简历", encoding="utf-8")
        new_md = env.tmp_path / "new.md"
        new_md.write_text("# 新简历", encoding="utf-8")
        env.seed_task(task_id="t-old", task="parse-resume", status="success", user_id=1,
                      filename="old.docx", result_path=str(old_md),
                      created_at=now - timedelta(minutes=5), finished_at=now - timedelta(minutes=4))
        env.seed_task(task_id="t-new", task="parse-resume", status="success", user_id=1,
                      filename="new.docx", result_path=str(new_md),
                      created_at=now, finished_at=now)
        r = env.client.get("/api/resume/current")
        assert r.status_code == 200
        body = r.json()
        # 每人只留一份由 worker 成功回写时清理;存量多行时取最近一条
        assert body["filename"] == "new.docx"
        assert body["content"] == "# 新简历"
        assert isinstance(body["created_at"], float)
        # 简历是单数资源:删除走 DELETE /resume/current,这里不外泄 task_id
        assert "task_id" not in body

    def test_excludes_other_users_and_knowledge_tasks(self, env):
        now = datetime.now(UTC)
        md = env.tmp_path / "mine.md"
        md.write_text("# 我的", encoding="utf-8")
        env.seed_task(task_id="t-mine", task="parse-resume", status="success", user_id=1,
                      filename="mine.docx", result_path=str(md), created_at=now)
        env.seed_task(task_id="t-other", task="parse-resume", status="success", user_id=2,
                      filename="other.docx", created_at=now + timedelta(minutes=1))
        env.seed_task(task_id="t-legacy", task="parse-resume", status="success",
                      filename="legacy.docx", created_at=now + timedelta(minutes=2))  # 无归属
        env.seed_task(task_id="t-build", task="build-all", status="success", user_id=1,
                      created_at=now + timedelta(minutes=3))
        body = env.client.get("/api/resume/current").json()
        assert body["filename"] == "mine.docx"
        assert body["content"] == "# 我的"

    def test_unfinished_latest_falls_back_to_last_success(self, env):
        """重传正在解析或失败时,已有的那份仍要能用(否则用户当下就没简历了)。"""
        now = datetime.now(UTC)
        md = env.tmp_path / "keep.md"
        md.write_text("# 仍可用", encoding="utf-8")
        env.seed_task(task_id="t-ok", task="parse-resume", status="success", user_id=1,
                      filename="keep.docx", result_path=str(md), created_at=now)
        env.seed_task(task_id="t-run", task="parse-resume", status="running", user_id=1,
                      filename="wip.docx", created_at=now + timedelta(minutes=1))
        env.seed_task(task_id="t-bad", task="parse-resume", status="failed", user_id=1,
                      filename="bad.docx", created_at=now + timedelta(minutes=2))
        body = env.client.get("/api/resume/current").json()
        assert body["filename"] == "keep.docx"

    def test_missing_result_file_content_null(self, env):
        env.seed_task(task_id="t-gone", task="parse-resume", status="success", user_id=1,
                      filename="gone.docx",
                      result_path=str(env.tmp_path / "not-here.md"),
                      created_at=datetime.now(UTC))
        body = env.client.get("/api/resume/current").json()
        assert body["filename"] == "gone.docx"
        assert body["content"] is None

    def test_no_resume_204_empty_body(self, env):
        r = env.client.get("/api/resume/current")
        assert r.status_code == 204
        assert r.content == b""

    def test_requires_auth_401(self):
        assert TestClient(create_app()).get("/api/resume/current").status_code == 401


class TestDeleteCurrentResume:
    """DELETE /api/resume/current:删我的简历 = 本人行 + 产物全清,删完即无。"""

    def test_removes_rows_and_files(self, env):
        out = env.tmp_path / "r1.md"
        out.write_text("# 张三的简历", encoding="utf-8")
        stale = env.tmp_path / "stale.md"
        stale.write_text("# 上一版", encoding="utf-8")
        env.seed_task(task_id="t-cur", task="parse-resume", status="success", user_id=1,
                      filename="resume.docx", result_path=str(out))
        # 存量多行(单份约束上线前传的)一并清掉,否则删掉最新一条后又"复活"成旧的
        env.seed_task(task_id="t-old", task="parse-resume", status="success", user_id=1,
                      filename="old.docx", result_path=str(stale))
        assert env.client.delete("/api/resume/current").status_code == 204
        assert env.db_row("t-cur") is None and env.db_row("t-old") is None
        assert not out.exists() and not stale.exists()
        assert env.client.get("/api/resume/current").status_code == 204

    def test_missing_file_still_deletes_row(self, env):
        """产物已被外部清理时删除仍要成功——留着一个取不到正文的行更糟。"""
        env.seed_task(task_id="t-gone", task="parse-resume", status="success", user_id=1,
                      result_path=str(env.tmp_path / "gone.md"))
        assert env.client.delete("/api/resume/current").status_code == 204
        assert env.db_row("t-gone") is None

    def test_idempotent_when_nothing_to_delete(self, env):
        assert env.client.delete("/api/resume/current").status_code == 204

    def test_other_users_and_knowledge_tasks_untouched(self, env):
        """归属条件不许越界:别人的简历行、本人的知识库任务都不能被删。"""
        other = env.tmp_path / "other.md"
        other.write_text("# 别人的", encoding="utf-8")
        env.seed_task(task_id="t-2", task="parse-resume", status="success", user_id=2,
                      filename="other.docx", result_path=str(other))
        env.seed_task(task_id="tb", task="build-all", status="success", user_id=1)
        env.seed_task(task_id="t-null", task="parse-resume", status="success",
                      filename="legacy.docx")   # 历史无归属行
        assert env.client.delete("/api/resume/current").status_code == 204
        assert env.db_row("t-2") is not None and other.exists()
        assert env.db_row("tb") is not None
        assert env.db_row("t-null") is not None

    def test_requires_auth_401(self):
        assert TestClient(create_app()).delete("/api/resume/current").status_code == 401


class TestParseStream:

    def test_stream_404_before_response(self, env):
        # 404 必须在 StreamingResponse 之前 raise(响应开始流后状态码锁死 200)
        assert env.client.get("/api/resume/parse/nope/stream").status_code == 404

    def test_stream_cross_user_404(self, env):
        env.seed_task(task_id="t-2", task="parse-resume", status="running", user_id=2)
        assert env.client.get("/api/resume/parse/t-2/stream").status_code == 404

    def test_terminal_success_fast_path(self, env):
        """订阅时已终态:快照检查后直接 done,不进轮询(快任务不多等一拍)"""
        env.seed_task(task_id="t-ok", task="parse-resume", status="success",
                      user_id=1, finished_at=datetime.now(UTC))
        t0 = time.time()
        rs = env.client.get("/api/resume/parse/t-ok/stream")
        assert time.time() - t0 < 0.5
        frames = parse_sse(rs.text)
        assert event_names(frames) == ["done"]
        d = frames[0][1]
        assert d["task_id"] == "t-ok" and d["status"] == "success"
        assert isinstance(d["elapsed"], (int, float))

    def test_failed_emits_error_then_done(self, env):
        env.seed_task(task_id="t-bad", task="parse-resume", status="failed",
                      user_id=1, error="MinerU超时", finished_at=datetime.now(UTC))
        rs = env.client.get("/api/resume/parse/t-bad/stream")
        frames = parse_sse(rs.text)
        assert event_names(frames) == ["error", "done"]
        assert frames[0][1]["message"] == "MinerU超时"  # error 列内容,不是进度 message
        assert frames[1][1]["status"] == "failed"

    def test_running_to_success_transition(self, env):
        """真实轮询路径:首帧 progress 快照 → 后台翻 DB → done 收尾"""
        env.seed_task(task_id="t-live", task="parse-resume", status="running",
                      user_id=1, stage="parsing", percent=50, message="MinerU解析中")
        timer = threading.Timer(0.3, lambda: task_store.update_task(
            "t-live", status="success", percent=100, finished_at=time.time()))
        timer.start()
        try:
            rs = env.client.get("/api/resume/parse/t-live/stream")
        finally:
            timer.cancel()
        assert rs.headers["content-type"].startswith("text/event-stream")
        frames = parse_sse(rs.text)
        names = event_names(frames)
        assert names[0] == "progress"
        assert frames[0][1] == {"stage": "parsing", "percent": 50.0,
                                "message": "MinerU解析中"}
        assert names[-1] == "done"
        assert frames[-1][1]["status"] == "success"


class TestKnowledgeFilterRegression:
    """坑①回归:parse-resume 任务不得出现在 knowledge 端点(TaskStatus.task Literal 会 500)。
    Step B 后 knowledge 端点只读 DB,过滤在查询层的 != "parse-resume" 条件。"""

    def test_task_list_excludes_parse_resume(self, env):
        env.seed_task(task_id="tp", task="parse-resume", status="success",
                      user_id=1, filename="a.md")
        env.seed_task(task_id="tb", task="build-all", status="success")
        r = env.client.get("/api/knowledge/tasks")
        assert r.status_code == 200  # 关键:不 500
        body = r.json()
        ids = [t["task_id"] for t in body["tasks"]]
        assert "tp" not in ids and "tb" in ids
        assert body["total"] == 1    # count 查询同样过滤

    def test_task_status_db_path_404(self, env):
        env.seed_task(task_id="tp", task="parse-resume", status="success", user_id=1)
        assert env.client.get("/api/knowledge/tasks/tp").status_code == 404
