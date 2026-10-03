"""auth 测试夹具。

策略:用文件版 SQLite(aiosqlite) 覆盖 get_async_session——
  - aiosqlite 走线程池,不依赖 selector 事件循环原语,故 Windows(Proactor) 与容器都能跑;
  - 文件库(非 :memory:)跨请求/跨会话持久,天然支持"注册→登录→刷新"多请求链路;
  - 完全隔离,不污染开发用 PostgreSQL,无需清理真实数据。
auth 路由与 security.get_current_user 全程走 get_async_session,覆盖后 100% 命中 SQLite。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import src.database.models  # noqa: F401  导入以注册全部表到 Base.metadata
from src.api.app import create_app
from src.database.database import Base, get_async_session


@pytest.fixture
def client(tmp_path):
    dbfile = tmp_path / "auth_test.db"
    sync_url = f"sqlite:///{dbfile}"
    async_url = f"sqlite+aiosqlite:///{dbfile}"

    # 用同步引擎可靠地建表(避免跨事件循环建表的坑),app 运行时用异步引擎读同一文件
    sync_engine = create_engine(sync_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine)

    async_engine = create_async_engine(async_url, connect_args={"check_same_thread": False})
    TestSession = async_sessionmaker(async_engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_get_async_session():
        async with TestSession() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_async_session] = _override_get_async_session

    yield TestClient(app)

    # sync_engine.dispose() 安全;async 引擎经其底层 sync_engine 释放,免跨循环 dispose 报错
    async_engine.sync_engine.dispose()
    sync_engine.dispose()
