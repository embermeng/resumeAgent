"""简历解析任务化接口测试:/api/resume/parse 三端点 + 并发限流 + 坑①(knowledge 过滤)回归。

端点已从"同步阻塞返回 content"改为"提交后台任务→202+task_id→SSE/轮询→取结果"。
用真实 TaskManager(线程 + 进程级信号量)+ mock parser + 文件版 SQLite(override get_async_session)。

覆盖契约 docs/specs/api-contract.md §4.3(提交)/§4.4(状态)/§4.5(流),以及 7 个高危坑里的:
  ① parse-resume 不得污染 knowledge 列表/状态(否则 TaskStatus.task Literal 校验 500)
  ② SSE 的 404 必须在 StreamingResponse 之前
  ③ 信号量进程级单例(否则限流失效)
  ④ file bytes 在请求内读出传闭包
  ⑤ 归属校验防 IDOR(统一 404)
"""
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

import src.database.models as models  # noqa: F401  注册全部表到 Base.metadata
import src.api.routers.resume as resume_module
from src.api.app import create_app
from src.api.deps import get_resume_parser, get_task_manager
from src.api.task_manager import TaskManager
from src.auth.security import get_current_user
from src.database.database import Base, get_async_session
from tests.test_api.helpers import event_names, parse_sse


def _wait(tm, tid, timeout=8.0):
    """轮询等待任务结束(线程测试同步辅助)"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        rec = tm.get(tid)
        if rec and rec.status in ("success", "failed"):
            return rec
        time.sleep(0.02)
    raise TimeoutError(f"任务未在 {timeout}s 内结束: {tid}")


def _mk_user(uid=1):
    """假用户:端点只用到 current_user.id"""
    u = MagicMock()
    u.id = uid
    return u


@pytest.fixture
def env(tmp_path, monkeypatch):
    """测试环境:
    - patch resume.settings: resume_uploads_dir→tmp_path(避免污染真实目录), 并发上限=2
    - 真实 TaskManager + 文件版 SQLite(aiosqlite, 避开宿主 psycopg 撞 ProactorEventLoop)
    - 默认认证 user id=1 + mock parser(parse 返回固定 markdown)
    暴露 set_user/set_parser/insert_task 供特化;测试后自动清理 engine 与信号量缓存。
    """
    # resume.py 在 import 时执行 settings = get_config(),这里替换为假 settings
    fake_settings = MagicMock()
    fake_settings.paths.resume_uploads_dir = tmp_path
    fake_settings.semaphore.resume_parse_max_concurrency = 2
    monkeypatch.setattr(resume_module, "settings", fake_settings)
    resume_module._parse_semaphore.cache_clear()  # 让新并发上限生效(坑③单例缓存)

    app = create_app()
    tm = TaskManager()
    app.dependency_overrides[get_task_manager] = lambda: tm

    parser = MagicMock()
    parser.parse.return_value = "# 张三的简历"
    app.dependency_overrides[get_resume_parser] = lambda: parser

    app.dependency_overrides[get_current_user] = lambda: _mk_user(1)

    dbfile = tmp_path / "resume_test.db"
    sync_engine = create_engine(f"sqlite:///{dbfile}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine)
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{dbfile}", connect_args={"check_same_thread": False})
    TestSession = async_sessionmaker(async_engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_session():
        async with TestSession() as session:
            yield session

    app.dependency_overrides[get_async_session] = _override_session
    client = TestClient(app)

    def set_user(uid):
        app.dependency_overrides[get_current_user] = lambda: _mk_user(uid)

    def set_parser(mock):
        app.dependency_overrides[get_resume_parser] = lambda: mock

    def insert_task(**kw):
        """手动往 SQLite 插一条 BuildTask(模拟服务重启后仅存 DB 的历史任务)"""
        with Session(sync_engine) as s:
            s.add(models.BuildTask(**kw))
            s.commit()

    yield SimpleNamespace(
        app=app, client=client, tm=tm, parser=parser,
        sync_engine=sync_engine, async_engine=async_engine, tmp_path=tmp_path,
        set_user=set_user, set_parser=set_parser, insert_task=insert_task,
    )

    async_engine.sync_engine.dispose()
    sync_engine.dispose()
    resume_module._parse_semaphore.cache_clear()  # 不泄漏到其它测试


class TestSupportedExtensions:
    def test_returns_four(self, env):
        r = env.client.get("/api/resume/supported-extensions")
        assert r.status_code == 200
        assert set(r.json()["extensions"]) == {".md", ".txt", ".docx", ".pdf"}


class TestParseSubmit:
    def test_returns_202_and_task_id(self, env):
        r = env.client.post(
            "/api/resume/parse",
            files={"file": ("resume.md", b"# content", "text/markdown")},
        )
        assert r.status_code == 202
        body = r.json()
        assert body["status"] == "pending"
        tid = body["task_id"]
        assert tid

        rec = _wait(env.tm, tid)
        assert rec.status == "success"
        # 坑④:parser 收到的是请求内读出的 filename + bytes
        env.parser.parse.assert_called_once()
        args = env.parser.parse.call_args.args
        assert args[0] == "resume.md"
        assert args[1] == b"# content"
        # 结果落盘到 resume_uploads_dir(tmp_path),result_path 指向它
        assert rec.result_path and rec.result_path.startswith(str(env.tmp_path))

    def test_unsupported_extension_400(self, env):
        r = env.client.post(
            "/api/resume/parse",
            files={"file": ("bad.xyz", b"x", "application/octet-stream")},
        )
        assert r.status_code == 400
        assert "不支持" in r.json()["detail"]
        env.parser.parse.assert_not_called()  # 白名单在提交前拦,不浪费任务槽位

    def test_missing_file_422(self, env):
        r = env.client.post("/api/resume/parse")
        assert r.status_code == 422

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
        tid = env.client.post(
            "/api/resume/parse",
            files={"file": ("resume.md", b"# c", "text/markdown")},
        ).json()["task_id"]
        _wait(env.tm, tid)

        r = env.client.get(f"/api/resume/parse/{tid}")
        assert r.status_code == 200
        body = r.json()
        assert body["task_id"] == tid
        assert body["status"] == "success"
        assert body["content"] == "# 张三的简历"  # 从 result_path 文件读回
        assert body["filename"] == "resume.md"
        assert "task" not in body  # ResumeParseStatus 不含 task 字段(与 knowledge TaskStatus 区分)

    def test_not_found_404(self, env):
        assert env.client.get("/api/resume/parse/nope").status_code == 404

    def test_cross_user_404(self, env):
        # 坑⑤ IDOR:user1 提交,user2 查 → 404
        tid = env.tm.submit("parse-resume", lambda p: None, user_id=1, filename="a.md")
        _wait(env.tm, tid)
        env.set_user(2)
        assert env.client.get(f"/api/resume/parse/{tid}").status_code == 404


class TestParseStream:
    def test_stream_404_before_response(self, env):
        # 坑②:不存在 → 404(不是 200 断流)
        assert env.client.get("/api/resume/parse/nope/stream").status_code == 404

    def test_stream_progress_and_done(self, env):
        tid = env.client.post(
            "/api/resume/parse",
            files={"file": ("resume.md", b"# c", "text/markdown")},
        ).json()["task_id"]

        rs = env.client.get(f"/api/resume/parse/{tid}/stream")
        assert rs.headers["content-type"].startswith("text/event-stream")
        frames = parse_sse(rs.text)
        names = event_names(frames)
        assert names[-1] == "done"
        assert "progress" in names
        stages = [f[1].get("stage") for f in frames if f[0] == "progress"]
        assert "queued" in stages and "parsing" in stages  # 排队态 + 解析态

    def test_stream_cross_user_404(self, env):
        tid = env.tm.submit("parse-resume", lambda p: None, user_id=1)
        _wait(env.tm, tid)
        env.set_user(2)
        assert env.client.get(f"/api/resume/parse/{tid}/stream").status_code == 404


class TestConcurrencyLimit:
    def test_semaphore_limits_concurrency(self, env):
        # 坑③:进程级单例信号量真的限流(若 run_fn 里每次 new Semaphore 则会超限)
        gate = threading.Event()
        state = {"cur": 0, "max": 0}
        lock = threading.Lock()

        def slow_parse(filename, data):
            with lock:
                state["cur"] += 1
                state["max"] = max(state["max"], state["cur"])
            gate.wait(timeout=10)  # 占住槽位直到测试放行
            with lock:
                state["cur"] -= 1
            return "# md"

        parser = MagicMock()
        parser.parse.side_effect = slow_parse
        env.set_parser(parser)

        tids = []
        for i in range(4):
            r = env.client.post(
                "/api/resume/parse",
                files={"file": (f"r{i}.md", b"x", "text/markdown")},
            )
            assert r.status_code == 202
            tids.append(r.json()["task_id"])

        time.sleep(0.6)  # 等线程调度:至多 2 个进入 slow_parse 并阻塞在 gate
        assert state["max"] <= 2, f"并发超过上限 2: {state['max']}"

        gate.set()  # 放行,全部完成
        for tid in tids:
            _wait(env.tm, tid, timeout=10)
        assert state["max"] == 2  # 槽位被用满(既限流又不过度)
        assert parser.parse.call_count == 4


class TestKnowledgeFilterRegression:
    """坑①回归:parse-resume 任务不得出现在 knowledge 端点,否则 TaskStatus.task(Literal) 校验 500。"""

    def test_task_list_excludes_parse_resume(self, env):
        env.insert_task(task_id="tp", task="parse-resume", status="success", user_id=1, filename="a.md")
        env.insert_task(task_id="tb", task="build-all", status="success")
        r = env.client.get("/api/knowledge/tasks")
        assert r.status_code == 200  # 关键:不 500
        body = r.json()
        ids = [t["task_id"] for t in body["tasks"]]
        assert "tp" not in ids and "tb" in ids
        assert body["total"] == 1  # count 查询同样过滤

    def test_task_status_db_parse_resume_404(self, env):
        # DB 路径:内存无 → 回落 DB,where 过滤 parse-resume → 404(不是 Literal 500)
        env.insert_task(task_id="tp", task="parse-resume", status="success", user_id=1)
        assert env.client.get("/api/knowledge/tasks/tp").status_code == 404

    def test_task_status_memory_parse_resume_404(self, env):
        # 内存路径(隐性点b):tm 命中 parse-resume → 直接 404
        tid = env.tm.submit("parse-resume", lambda p: None, user_id=1)
        _wait(env.tm, tid)
        assert env.client.get(f"/api/knowledge/tasks/{tid}").status_code == 404
