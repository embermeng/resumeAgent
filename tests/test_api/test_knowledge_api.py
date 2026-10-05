"""知识库构建接口测试(Celery 版):build / tasks/{id} / tasks/{id}/stream / tasks 列表

架构:build 受理后 apply_async 投递(被 patch 拦截,不连真 broker),
DB(build_tasks)是唯一权威状态源;SSE 走 task_stream 适配器(DB 快照+轮询转发)。

覆盖契约(docs/specs/api-contract.md §4.5/§4.6/§4.7):
- build 202 受理:先 insert 后投递(竞态守卫)、args 全 JSON 原始类型、无归属(user_id NULL)
- 非法 task → 422(投递不发生)
- 状态快照 / 404 / 坑①parse-resume 过滤(status+stream+list 三处)
- SSE:404 必须在 StreamingResponse 之前、终态快路径、真实轮询转移(progress...done)
"""
import json
import threading
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

import src.api.routers.knowledge as knowledge_module
import src.database.models as models  # noqa: F401  注册全部表到 Base.metadata
import src.worker.task_store as task_store
from src.api.app import create_app
from src.database.database import Base, get_async_session, get_session
from tests.test_api.helpers import event_names, parse_sse


@pytest.fixture
def env(tmp_path, monkeypatch):
    """SQLite 文件库双 override(异步=状态/列表端点,同步=stream 404 校验)
    + task_store.SessionLocal 重定向(受理 insert 落同一库) + apply_async 拦截。"""
    dbfile = tmp_path / "knowledge_test.db"
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

    def _override_sync():
        with Session(sync_engine) as s:
            yield s

    async def _override_async():
        async with TestSession() as session:
            yield session

    app.dependency_overrides[get_session] = _override_sync
    app.dependency_overrides[get_async_session] = _override_async
    client = TestClient(app)

    def seed_task(**kw):
        """手动往 SQLite 插一条 BuildTask(模拟 worker 已推进的状态)"""
        with SyncSession() as s:
            s.add(models.BuildTask(**kw))
            s.commit()

    def db_row(task_id):
        with SyncSession() as s:
            return s.query(models.BuildTask).filter_by(task_id=task_id).first()

    with patch.object(knowledge_module.knowledge_build_task, "apply_async") as mock_apply:
        yield SimpleNamespace(
            app=app, client=client, tmp_path=tmp_path,
            apply_async=mock_apply, seed_task=seed_task, db_row=db_row,
        )

    async_engine.sync_engine.dispose()
    sync_engine.dispose()


class TestBuild:
    def test_returns_202_db_row_and_serializable_dispatch(self, env):
        r = env.client.post("/api/knowledge/build",
                            json={"task": "build-all", "force": True})
        assert r.status_code == 202
        body = r.json()
        assert body["status"] == "pending"
        tid = body["task_id"]

        # 投递契约:显式 task_id 与回执一致;args 是 BuildRequest 五字段展开,全 JSON 原始类型
        env.apply_async.assert_called_once()
        kw = env.apply_async.call_args.kwargs
        assert kw["task_id"] == tid
        assert kw["args"] == ("build-all", True, 300, 50, False)
        json.dumps(kw["args"])

        # DB 行已就位:pending + queued/0 初值;构建任务无归属无文件名(决策:不扩 scope)
        row = env.db_row(tid)
        assert (row.status, row.task) == ("pending", "build-all")
        assert (row.stage, row.percent) == ("queued", 0)
        assert row.user_id is None and row.filename is None

    def test_insert_before_delay_race_guard(self, env):
        """经典竞态回归:投递那一刻 DB 必须已有 pending 行(先 insert 再 apply_async)"""
        seen = {}

        def _check(*args, **kw):
            seen["row_exists"] = env.db_row(kw["task_id"]) is not None

        env.apply_async.side_effect = _check
        r = env.client.post("/api/knowledge/build", json={"task": "parse-pdfs"})
        assert r.status_code == 202
        assert seen["row_exists"] is True

    def test_invalid_task_422(self, env):
        r = env.client.post("/api/knowledge/build", json={"task": "nope"})
        assert r.status_code == 422
        env.apply_async.assert_not_called()  # Pydantic 校验在受理前拦,不浪费队列消息


class TestTaskStatus:
    def test_snapshot(self, env):
        env.seed_task(task_id="tb", task="build-all", status="success",
                      stage="build-indexes", percent=100,
                      finished_at=datetime.now(UTC))
        r = env.client.get("/api/knowledge/tasks/tb")
        assert r.status_code == 200
        body = r.json()
        assert body["task_id"] == "tb" and body["task"] == "build-all"
        assert body["status"] == "success" and body["percent"] == 100

    def test_404(self, env):
        assert env.client.get("/api/knowledge/tasks/nope").status_code == 404

    def test_parse_resume_hidden_404(self, env):
        # 坑①:parse-resume 行对 knowledge 状态端点不可见(TaskStatus.task Literal 否则 500)
        env.seed_task(task_id="tp", task="parse-resume", status="success", user_id=1)
        assert env.client.get("/api/knowledge/tasks/tp").status_code == 404


class TestTaskList:
    def test_list_excludes_parse_resume(self, env):
        env.seed_task(task_id="t1", task="build-all", status="success")
        env.seed_task(task_id="t2", task="parse-pdfs", status="failed", error="boom")
        env.seed_task(task_id="tp", task="parse-resume", status="success", user_id=1)
        r = env.client.get("/api/knowledge/tasks")
        assert r.status_code == 200  # 关键:不 500
        body = r.json()
        assert body["total"] == 2    # count 同样过滤
        ids = [t["task_id"] for t in body["tasks"]]
        assert "tp" not in ids and {"t1", "t2"} == set(ids)


class TestTaskStream:
    def test_stream_404_before_response(self, env):
        # 404 必须在 StreamingResponse 之前 raise(响应开始流后状态码锁死 200)
        assert env.client.get("/api/knowledge/tasks/nope/stream").status_code == 404

    def test_stream_parse_resume_404(self, env):
        env.seed_task(task_id="tp", task="parse-resume", status="running", user_id=1)
        assert env.client.get("/api/knowledge/tasks/tp/stream").status_code == 404

    def test_terminal_fast_path_done_only(self, env):
        """订阅时已终态:快照检查后直接 done,不进轮询"""
        env.seed_task(task_id="tb", task="build-all", status="success",
                      percent=100, finished_at=datetime.now(UTC))
        t0 = time.time()
        rs = env.client.get("/api/knowledge/tasks/tb/stream")
        assert time.time() - t0 < 0.5
        frames = parse_sse(rs.text)
        assert event_names(frames) == ["done"]
        assert frames[0][1]["status"] == "success"
        assert isinstance(frames[0][1]["elapsed"], (int, float))

    def test_stream_progress_and_done(self, env):
        """真实轮询路径:queued 快照首帧 → 后台推进五阶段 → done 收尾"""
        env.seed_task(task_id="tb", task="build-all", status="running",
                      stage="queued", percent=0, message="任务已开始")

        def _advance():
            time.sleep(0.3)  # 让 SSE 快照先拿到 queued/0 首帧
            task_store.update_task("tb", stage="parse-pdfs",
                                   message="[1/5] 解析PDF...", percent=10)
            time.sleep(1.2)  # 跨一拍(poll_interval=1.0)确保变化被轮询捕捉
            task_store.update_task("tb", status="success", percent=100,
                                   finished_at=time.time())

        t = threading.Thread(target=_advance, daemon=True)
        t.start()
        try:
            rs = env.client.get("/api/knowledge/tasks/tb/stream")
        finally:
            t.join(timeout=10)
        assert rs.headers["content-type"].startswith("text/event-stream")
        frames = parse_sse(rs.text)
        names = event_names(frames)
        assert names[0] == "progress" and names[-1] == "done"
        percents = [f[1]["percent"] for f in frames if f[0] == "progress"]
        assert 0 in percents and 10 in percents
        assert frames[-1][1]["status"] == "success"
