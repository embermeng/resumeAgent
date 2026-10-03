"""知识库构建接口测试(TDD):build / tasks/{id}/stream / tasks/{id}

用真实 TaskManager(内存任务表 + 线程)+ mock KnowledgeService,验证:
- build 返回 202 + task_id,后台执行调用 svc.run
- 非法 task -> 422
- 状态快照与 404
- SSE 进度流(progress...done)与 404
"""
import time
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import src.database.models  # noqa: F401  注册全部表到 Base.metadata
from src.api.app import create_app
from src.api.deps import get_knowledge_service, get_task_manager
from src.api.task_manager import TaskManager
from src.database.database import Base, get_async_session
from tests.test_api.helpers import event_names, parse_sse


def _override_async_session_with_sqlite(app, tmp_path):
    """把 get_async_session 换成文件版 SQLite(aiosqlite),避开宿主 psycopg 撞 ProactorEventLoop。

    aiosqlite 走线程池不受 Proactor 影响;文件库跨请求持久,空 BuildTask 表 → task_status 回落查库得 None → 404。
    """
    dbfile = tmp_path / "knowledge_test.db"
    sync_engine = create_engine(f"sqlite:///{dbfile}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine)
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{dbfile}", connect_args={"check_same_thread": False})
    TestSession = async_sessionmaker(async_engine, expire_on_commit=False, class_=AsyncSession)

    async def _override():
        async with TestSession() as session:
            yield session

    app.dependency_overrides[get_async_session] = _override
    return sync_engine, async_engine


def _wait(tm, tid, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        rec = tm.get(tid)
        if rec and rec.status in ("success", "failed"):
            return rec
        time.sleep(0.02)
    raise TimeoutError(f"任务未结束: {tid}")


class TestBuild:
    def test_returns_202_and_task_id(self):
        app = create_app()
        tm = TaskManager()
        svc = MagicMock()  # run 立即返回,不做实际构建
        app.dependency_overrides[get_task_manager] = lambda: tm
        app.dependency_overrides[get_knowledge_service] = lambda: svc
        client = TestClient(app)

        r = client.post("/api/knowledge/build", json={"task": "build-all", "force": True})
        assert r.status_code == 202
        body = r.json()
        assert body["status"] == "pending"
        tid = body["task_id"]
        assert tid

        _wait(tm, tid)
        svc.run.assert_called_once()
        assert svc.run.call_args.args[0] == "build-all"
        assert svc.run.call_args.kwargs["force"] is True

    def test_invalid_task_422(self):
        client = TestClient(create_app())
        r = client.post("/api/knowledge/build", json={"task": "nope"})
        assert r.status_code == 422


class TestTaskStatus:
    def test_snapshot(self):
        app = create_app()
        tm = TaskManager()
        app.dependency_overrides[get_task_manager] = lambda: tm
        tid = tm.submit("build-all", lambda p: p("s", "m", 50))
        _wait(tm, tid)

        client = TestClient(app)
        r = client.get(f"/api/knowledge/tasks/{tid}")
        assert r.status_code == 200
        body = r.json()
        assert body["task_id"] == tid
        assert body["task"] == "build-all"
        assert body["status"] == "success"
        assert body["percent"] == 100

    def test_404(self, tmp_path):
        app = create_app()
        app.dependency_overrides[get_task_manager] = lambda: TaskManager()
        sync_engine, async_engine = _override_async_session_with_sqlite(app, tmp_path)
        client = TestClient(app)
        try:
            assert client.get("/api/knowledge/tasks/nope").status_code == 404
        finally:
            async_engine.sync_engine.dispose()
            sync_engine.dispose()


class TestTaskStream:
    def test_stream_404(self):
        app = create_app()
        app.dependency_overrides[get_task_manager] = lambda: TaskManager()
        client = TestClient(app)
        assert client.get("/api/knowledge/tasks/nope/stream").status_code == 404

    def test_stream_progress_and_done(self):
        app = create_app()
        tm = TaskManager()
        app.dependency_overrides[get_task_manager] = lambda: tm

        def run(progress):
            progress("parse-pdfs", "[1/5] 解析PDF...", 10)
            progress("build-indexes", "[5/5] 构建索引...", 90)

        tid = tm.submit("build-all", run)
        client = TestClient(app)
        r = client.get(f"/api/knowledge/tasks/{tid}/stream")

        assert r.headers["content-type"].startswith("text/event-stream")
        frames = parse_sse(r.text)
        names = event_names(frames)
        assert names[-1] == "done"
        assert "progress" in names
        percents = [f[1]["percent"] for f in frames if f[0] == "progress"]
        assert 10 in percents and 90 in percents
