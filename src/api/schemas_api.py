"""
API 层请求/响应数据模型

契约见 docs/specs/api-contract.md 第 2 节,与前端 frontend/src/types/events.ts 一一对应。
"""
from typing import List, Literal, Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict, EmailStr, Field

# 构建任务类型(对应现有 CLI 命令)
TaskKind = Literal[
    "build-all",
    "parse-pdfs",
    "extract-summaries",
    "split-chunks",
    "build-indexes",
    "ingest-highlights",
]

# 任务状态机
TaskState = Literal["pending", "running", "success", "failed"]

# 意图类型(对齐 run_stream 的 intent 事件)
Intent = Literal["quick_response", "deep_thinking", "chitchat"]
# 消息角色
MessageRole = Literal["user", "assistant", "system"]


class ChatRequest(BaseModel):
    """流式对话请求(POST /api/chat)"""

    conversation_id: Optional[int] = Field(
        default=None, description="对话 ID,不传则自动生成"
    )
    prompt: str = Field(min_length=1, description="用户输入,非空")
    existing_resume: Optional[str] = Field(
        default=None, description="已有简历 Markdown 文本,深思路径生成时作为事实骨架"
    )


class ParseResponse(BaseModel):
    """简历文件解析结果(POST /api/resume/parse)"""

    filename: str = Field(description="原始文件名")
    content: str = Field(description="解析出的 Markdown 文本")


class SupportedExtensions(BaseModel):
    """支持的简历文件扩展名(GET /api/resume/supported-extensions)"""

    extensions: List[str] = Field(description="扩展名列表,含点号")


class BuildRequest(BaseModel):
    """知识库构建请求(POST /api/knowledge/build)"""

    task: TaskKind = Field(description="构建任务类型")
    force: bool = Field(default=False, description="强制全量重建")
    chunk_size: int = Field(default=300, description="分块大小(token)")
    chunk_overlap: int = Field(default=50, description="重叠 token 数")
    prune: bool = Field(default=False, description="清理孤儿索引")


class BuildAck(BaseModel):
    """构建任务受理响应(POST /api/knowledge/build)"""

    task_id: str = Field(description="任务唯一 ID")
    status: TaskState = Field(description="任务状态,受理时为 pending")


class TaskStatus(BaseModel):
    """任务状态快照(GET /api/knowledge/tasks/{task_id})"""

    task_id: str = Field(description="任务唯一 ID")
    task: TaskKind = Field(description="任务类型")
    status: TaskState = Field(description="任务状态")
    stage: Optional[str] = Field(default=None, description="当前阶段标识")
    percent: Optional[float] = Field(default=None, description="进度百分比 0-100")
    message: Optional[str] = Field(default=None, description="最新进度文本")
    created_at: float = Field(description="创建时间(epoch 秒)")
    finished_at: Optional[float] = Field(
        default=None, description="结束时间(epoch 秒)")
    error: Optional[str] = Field(default=None, description="失败原因")


class TaskList(BaseModel):
    """任务列表(GET /api/knowledge/tasks)"""

    tasks: List[TaskStatus] = Field(description="任务状态列表")
    total: int = Field(description="任务总数")


class ConversationSummary(BaseModel):
    """单个对话摘要"""

    id: int = Field(description="对话 ID")
    title: str = Field(description="对话标题")
    created_at: float = Field(description="创建时间(epoch 秒)")
    updated_at: float = Field(description="更新时间(epoch 秒)")


class ConversationList(BaseModel):
    """对话列表(GET /api/conversations)"""

    conversations: List[ConversationSummary] = Field(description="对话摘要列表")
    total: int = Field(description="对话总数")


class MessageOut(BaseModel):
    """对话消息"""

    id: int = Field(description="消息 ID")
    role: MessageRole = Field(description="消息角色")
    content: str = Field(description="消息内容")
    intent: Optional[Intent] = Field(
        default=None, description="消息意图(仅 assistant 消息有值)")
    created_at: float = Field(description="创建时间(epoch 秒)")


class ConversationMessages(BaseModel):
    """对话消息列表 GET /api/conversations/{id}/messages"""

    conversation_id: int = Field(description="对话 ID")
    messages: List[MessageOut] = Field(description="消息列表")


class UserBase(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    email: EmailStr = Field(max_length=120)


class UserCreate(UserBase):
    password: str = Field(min_length=8)


class UserLoginReq(BaseModel):
    email: EmailStr = Field(max_length=120)
    password: str = Field(min_length=8)


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str


class UserPrivate(UserPublic):
    email: EmailStr


class UserUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=1, max_length=50)
    email: EmailStr | None = Field(default=None, max_length=120)


class Token(BaseModel):
    access_token: str
    token_type: str


class ResumeParseAck(BaseModel):
    """简历解析任务受理(POST /api/resume/parse, 202)"""
    task_id: str = Field(description="任务唯一 ID")
    status: TaskState = Field(description="受理时为 pending")


class ResumeParseStatus(BaseModel):
    """简历解析任务状态/结果(GET /api/resume/parse/{task_id})。不含 task 字段。"""
    task_id: str = Field(description="任务唯一 ID")
    status: TaskState = Field(description="任务状态")
    stage: Optional[str] = Field(default=None)
    percent: Optional[float] = Field(default=None)
    message: Optional[str] = Field(default=None)
    filename: Optional[str] = Field(default=None, description="上传的原始文件名")
    content: Optional[str] = Field(default=None, description="解析出的 Markdown,仅 success 返回")
    error: Optional[str] = Field(default=None)
    created_at: float = Field(description="创建时间(epoch 秒)")
    finished_at: Optional[float] = Field(default=None)