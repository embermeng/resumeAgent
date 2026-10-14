"""
知识库端点鉴权测试(阶段 4)

背景:这四个端点此前完全未挂 CurrentUser,任何人都能触发 build-all——
那是全局操作(重扫全部课程 PDF 与所有用户项目文档、重建索引、产生 embedding 开销)。
本次补:全部需登录;build 限管理员;任务查询按 user_id 收敛(管理员看全量)。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import src.database.models  # noqa: F401
from src.api.app import create_app
from src.api.routers import knowledge as knowledge_router
from src.auth.security import get_current_user
from src.database.database import Base, get_async_session, get_session
from src.worker.task_store import insert_task

ADMIN_ID = 9
USER_ID = 10


@pytest.fixture
def client(tmp_path, monkeypatch):
    dbfile = tmp_path / "kb_auth.db"
    sync_engine = create_engine(f"sqlite:///{dbfile}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine)
    Session = sessionmaker(bind=sync_engine)

    async_engine = create_async_engine(
        f"sqlite+aiosqlite:///{dbfile}", connect_args={"check_same_thread": False})
    AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False,
                                           class_=AsyncSession)

    def _override_sync():
        with Session() as s:
            yield s

    async def _override_async():
        async with AsyncSessionLocal() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _override_sync
    app.dependency_overrides[get_async_session] = _override_async

    # task_store 走模块级 SessionLocal,不受 dependency_overrides 影响,必须一并替换
    from src.worker import task_store
    monkeypatch.setattr(task_store, "SessionLocal", Session)

    # Celery 入队全部 mock,不依赖真实 Redis
    class _FakeTask:
        def apply_async(self, *a, **k):
            return None

    monkeypatch.setattr(knowledge_router, "knowledge_build_task", _FakeTask())

    yield TestClient(app)

    app.dependency_overrides.clear()
    sync_engine.dispose()


def _login_as(app, user_id: int, is_superuser: bool):
    user = src.database.models.User(
        id=user_id, username=f"u{user_id}", email=f"u{user_id}@x.com",
        password_hash="x", is_superuser=is_superuser)
    app.dependency_overrides[get_current_user] = lambda: user
    return user


def _seed_task(task_id: str, user_id, task="build-all"):
    insert_task(task_id=task_id, task=task, user_id=user_id)


class TestBuildRequiresSuperuser:
    def test_superuser_can_trigger(self, client):
        _login_as(client.app, ADMIN_ID, is_superuser=True)
        r = client.post("/api/knowledge/build", json={"task": "build-all"})
        assert r.status_code == 202

    def test_normal_user_gets_403(self, client):
        """全局重建会影响他人数据并消耗额度,普通用户不得触发"""
        _login_as(client.app, USER_ID, is_superuser=False)
        r = client.post("/api/knowledge/build", json={"task": "build-all"})
        assert r.status_code == 403
        assert "管理员" in r.json()["detail"]

    def test_unauthenticated_rejected(self, client, monkeypatch):
        """不覆盖 get_current_user 时走真实鉴权,无 token 应被拒"""
        # 避免真实鉴权去查异步 DB 导致测试环境差异,这里只断言非 202
        _login_as(client.app, USER_ID, is_superuser=False)
        monkeypatch.delitem(client.app.dependency_overrides, get_current_user, raising=False)
        r = client.post("/api/knowledge/build", json={"task": "build-all"})
        assert r.status_code != 202


class TestTaskVisibility:
    def test_user_sees_only_own_task(self, client):
        _login_as(client.app, USER_ID, is_superuser=False)
        _seed_task("mine", USER_ID)
        _seed_task("others", ADMIN_ID)

        assert client.get("/api/knowledge/tasks/mine").status_code == 200
        assert client.get("/api/knowledge/tasks/others").status_code == 404

    def test_admin_sees_all_tasks(self, client):
        _login_as(client.app, ADMIN_ID, is_superuser=True)
        _seed_task("by-user", USER_ID)

        assert client.get("/api/knowledge/tasks/by-user").status_code == 200

    def test_list_filters_by_user(self, client):
        _login_as(client.app, USER_ID, is_superuser=False)
        _seed_task("t-mine-1", USER_ID)
        _seed_task("t-mine-2", USER_ID)
        _seed_task("t-admin-1", ADMIN_ID)

        body = client.get("/api/knowledge/tasks").json()
        ids = {t["task_id"] for t in body["tasks"]}
        assert ids == {"t-mine-1", "t-mine-2"}
        assert body["total"] == 2

    def test_admin_list_includes_everyone(self, client):
        _login_as(client.app, ADMIN_ID, is_superuser=True)
        _seed_task("t-a", ADMIN_ID)
        _seed_task("t-b", USER_ID)

        body = client.get("/api/knowledge/tasks").json()
        assert {t["task_id"] for t in body["tasks"]} == {"t-a", "t-b"}

    def test_legacy_task_without_owner_hidden_from_normal_user(self, client):
        """改造前的任务 user_id 为 NULL,不属于任何用户,仅管理员可见"""
        _seed_task("legacy", None)

        _login_as(client.app, USER_ID, is_superuser=False)
        assert client.get("/api/knowledge/tasks/legacy").status_code == 404

        _login_as(client.app, ADMIN_ID, is_superuser=True)
        assert client.get("/api/knowledge/tasks/legacy").status_code == 200
