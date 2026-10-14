from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.database import Base


# 对话记录
class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(50), nullable=False)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan")
    owner: Mapped[User] = relationship(back_populates="conversations")


# 消息记录
class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    conversation: Mapped[Conversation] = relationship(
        back_populates="messages")


# 构建任务记录
class BuildTask(Base):
    __tablename__ = "build_tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    task_id: Mapped[str] = mapped_column(
        String(50), nullable=False, unique=True, index=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # 任务类型
    task: Mapped[str] = mapped_column(String(50), nullable=False)
    # 任务状态
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # 任务阶段
    stage: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # 任务进度
    percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 最新进度文本
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 失败原因
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 解析简历结果路径
    result_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 简历文件名
    filename: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


# 用户
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    email: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    # 管理员：知识库全局构建（build-all）等影响全站的操作仅限管理员
    is_superuser: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="owner",
        cascade="all, delete-orphan",
    )
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    project_docs: Mapped[list[ProjectDoc]] = relationship(
        back_populates="owner",
        cascade="all, delete-orphan",
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    jti: Mapped[str] = mapped_column(
        String(36), nullable=False, unique=True, index=True)
    family_id: Mapped[str] = mapped_column(String(36), nullable=False)
    revoked: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )
    expire_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    user: Mapped[User] = relationship(back_populates="refresh_tokens")


# 项目文档（合并后的项目亮点/介绍）与用户的归属映射
class ProjectDoc(Base):
    __tablename__ = "project_docs"
    __table_args__ = (
        # user_id 为 NULL 时（公共素材）不参与普通 UNIQUE 比较，需按区分别建部分唯一索引
        Index("uq_project_docs_user_name", "user_id", "project_name",
              unique=True, postgresql_where=text("user_id IS NOT NULL")),
        Index("uq_project_docs_public_name", "project_name",
              unique=True, postgresql_where=text("user_id IS NULL")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    doc_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    # NULL = 公共素材，所有用户可见
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    project_name: Mapped[str] = mapped_column(String(200), nullable=False)
    file_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # 是否含「简历描述」章节；false 时简历生成降级为全文引用
    has_resume_section: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 源文件 mtime，沿用现有增量判断
    mtime: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    owner: Mapped[User] = relationship(back_populates="project_docs")