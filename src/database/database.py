from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from src.config import get_config

SQLALCHEMY_DATABASE_URL = get_config().database.database_url

engine = create_engine(SQLALCHEMY_DATABASE_URL, echo=True)
async_engine = create_async_engine(SQLALCHEMY_DATABASE_URL, echo=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
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
