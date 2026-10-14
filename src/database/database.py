from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from src.config import get_config

SQLALCHEMY_DATABASE_URL = get_config().database.database_url

engine = create_engine(SQLALCHEMY_DATABASE_URL, echo=True)
async_engine = create_async_engine(SQLALCHEMY_DATABASE_URL, echo=True)

# expire_on_commit=False：commit 后对象属性仍可读。
# 删除类工具函数（project_docs.py 等）在 commit 后还要返回 ORM 对象供调用方清理磁盘，
# 默认的 expire_on_commit=True 会导致 DetachedInstanceError（DB 已提交、磁盘清理却没执行）。
SessionLocal = sessionmaker(
    autocommit=False, autoflush=False, bind=engine, expire_on_commit=False
)
AsyncSessionLocal = async_sessionmaker(
    async_engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass

def get_session():
    with SessionLocal() as session:
        yield session

async def get_async_session():
    async with AsyncSessionLocal() as session:
        yield session
