"""会话端点测试(契约 4.11 / 4.12 / 4.12b)

重点在删除:messages 外键没有 DB 级 ondelete,子行靠 ORM 的
cascade="all, delete-orphan" 清理,因此必须验证删会话后消息真的没了;
越权(他人会话)与不存在的会话同判据 404,不泄露存在性。
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

import src.database.models as models
from src.api.app import create_app
from src.auth.security import get_current_user
from src.database.database import Base, get_async_session

OWNER_ID = 1
OTHER_ID = 2


@pytest.fixture
def env(tmp_path):
    """文件版 SQLite + 覆盖 get_async_session,返回 (client, 同步 Session 工厂)。"""
    dbfile = tmp_path / "conversations.db"
    sync_engine = create_engine(
        f"sqlite:///{dbfile}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine)
    Session = sessionmaker(bind=sync_engine)

    async_engine = create_async_engine(
        f"sqlite+aiosqlite:///{dbfile}", connect_args={"check_same_thread": False})
    AsyncSessionLocal = async_sessionmaker(
        async_engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_async():
        async with AsyncSessionLocal() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_async_session] = _override_async

    with Session() as s:
        for uid in (OWNER_ID, OTHER_ID):
            s.add(models.User(
                id=uid, username=f"u{uid}", email=f"u{uid}@x.com",
                password_hash="x", is_superuser=False))
        s.commit()

    yield SimpleNamespace(app=app, Session=Session)

    app.dependency_overrides.clear()
    sync_engine.dispose()


def _login(env, user_id: int) -> TestClient:
    env.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)
    return TestClient(env.app)


def _seed(env, conv_id: int, user_id: int, msg_count: int = 2) -> None:
    """建一条会话及其消息(标题带 id,便于断言)。"""
    with env.Session() as s:
        conv = models.Conversation(id=conv_id, title=f"会话{conv_id}", user_id=user_id)
        s.add(conv)
        s.flush()
        for i in range(msg_count):
            s.add(models.Message(
                conversation_id=conv_id, role="user" if i % 2 == 0 else "assistant",
                content=f"消息{i}"))
        s.commit()


def _counts(env):
    with env.Session() as s:
        return (
            s.scalar(select(func.count()).select_from(models.Conversation)),
            s.scalar(select(func.count()).select_from(models.Message)),
        )


class TestConversationList:
    def test_list_only_own_and_desc(self, env):
        _seed(env, 1, OWNER_ID)
        _seed(env, 2, OWNER_ID)
        _seed(env, 3, OTHER_ID)
        client = _login(env, OWNER_ID)

        body = client.get("/api/conversations").json()
        assert {c["id"] for c in body["conversations"]} == {1, 2}
        assert body["total"] == 2

    def test_messages_in_creation_order(self, env):
        _seed(env, 1, OWNER_ID, msg_count=3)
        client = _login(env, OWNER_ID)

        body = client.get("/api/conversations/1/messages").json()
        assert [m["content"] for m in body["messages"]] == ["消息0", "消息1", "消息2"]
        assert body["conversation_id"] == 1


class TestDeleteConversation:
    def test_delete_removes_messages_too(self, env):
        """级联删除:会话行与它的消息行一起消失(外键无 DB 级 ondelete)"""
        _seed(env, 1, OWNER_ID, msg_count=2)
        _seed(env, 2, OWNER_ID, msg_count=3)
        client = _login(env, OWNER_ID)

        assert client.delete("/api/conversations/1").status_code == 204
        assert _counts(env) == (1, 3)
        assert client.get("/api/conversations/1/messages").status_code == 404

    def test_delete_others_conversation_404_and_keeps(self, env):
        """他人会话与不存在同判据 404,且数据一点不动"""
        _seed(env, 7, OTHER_ID, msg_count=2)
        client = _login(env, OWNER_ID)

        r = client.delete("/api/conversations/7")
        assert r.status_code == 404
        assert r.json()["detail"] == "conversation not found"
        assert _counts(env) == (1, 2)

    def test_delete_missing_404(self, env):
        client = _login(env, OWNER_ID)
        assert client.delete("/api/conversations/999").status_code == 404

    def test_delete_twice_404(self, env):
        """第二次删同一条:已不在,按不存在处理,不报 500"""
        _seed(env, 5, OWNER_ID)
        client = _login(env, OWNER_ID)

        assert client.delete("/api/conversations/5").status_code == 204
        assert client.delete("/api/conversations/5").status_code == 404

    def test_non_integer_id_422(self, env):
        """路径参数是 int:非数字走 FastAPI 校验,不会被当成"不存在"混进 404"""
        client = _login(env, OWNER_ID)
        assert client.delete("/api/conversations/abc").status_code == 422

    def test_unauthenticated_401(self, env):
        """未登录不覆盖 get_current_user,走真实鉴权:无 token 直接 401"""
        client = TestClient(env.app)
        assert client.delete("/api/conversations/1").status_code == 401
